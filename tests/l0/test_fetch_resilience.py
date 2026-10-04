"""T-L0-017.2 测试：错误码保留、错误分类、退避重试、连接重建与截断检测。

GWT 对照（任务文件 5 条）：
- GWT-1 `error_code` 保留：非 0 错误码随异常带出，可机器识别
- GWT-2 错误分类：网络/会话类可重试，参数类不重试
- GWT-3 退避重试：指数退避 + 次数上限；每次重试各经一次网关审计
- GWT-4 连接重建：可重试错误重试前重建会话
- GWT-5 截断检测：行数恰为整页 ⇒ 复核；两次不一致判失败（不静默入库半截）

全部离线（Fake 注入 / 纯函数），不碰真网。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.market import BaoStockSync, FetchError, MarketDb, RetryPolicy
from st_agent.l0.market.fetch import (
    result_shape,
    source_page_size,
    truncation_suspect,
)
from st_agent.l0.market.live import (
    NETWORK_ERROR_CODES,
    RETRYABLE_ERROR_CODES,
    SESSION_ERROR_CODES,
    BaoStockFetcher,
    is_retryable_error,
)
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from test_market import PASS, FakeFetcher

PAGE = 2000


# ───────────────────────── Fake ─────────────────────────

class _ResultSet:
    """baostock 结果集的最小替身（``error_code`` / ``next`` / ``get_row_data``）。"""

    def __init__(self, *, error_code: str = "0", error_msg: str = "success",
                 fields: list[str] | None = None,
                 rows: list[list[str]] | None = None) -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = fields or ["code"]
        self._rows = [list(r) for r in (rows or [])]
        self._i = 0

    def next(self) -> bool:
        if self._i < len(self._rows):
            self._i += 1
            return True
        return False

    def get_row_data(self) -> list[str]:
        return self._rows[self._i - 1]


class RetryFake(FakeFetcher):
    """``k_daily`` 前 ``fail_times`` 次抛错（可指定是否可重试），之后成功。"""

    def __init__(self, *, fail_times: int, retryable: bool = True,
                 error_code: str | None = "10002007") -> None:
        super().__init__()
        self._left = fail_times
        self._retryable = retryable
        self._error_code = error_code
        self.reconnects = 0

    def k_daily(self, code, start, end):
        if self._left > 0:
            self._left -= 1
            self.calls.append("k_daily")
            raise FetchError("BaoStock 查询失败（Fake）", error_code=self._error_code,
                             retryable=self._retryable)
        return super().k_daily(code, start, end)

    def reconnect(self) -> None:
        self.reconnects += 1


class ShapeFake(FakeFetcher):
    """``security_basic`` 每次返回**指定行数**（构造整页 / 非整页形态）。"""

    def __init__(self, row_counts: list[int]) -> None:
        super().__init__()
        self._counts = list(row_counts)
        self._n = 0

    def security_basic(self):
        self.calls.append("security_basic")
        count = self._counts[self._n % len(self._counts)]   # 循环给形态，重试也照旧
        self._n += 1
        fields = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
        rows = [[f"sh.{i:06d}", f"证券{i}", "1999-11-10", "", "1", "1"]
                for i in range(count)]
        return (fields, rows)


# ───────────────────────── 夹具 ─────────────────────────

@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store, audit=True)


def _one(db: MarketDb, sql: str) -> dict:
    return db.query(sql).data["rows"][0]


def _prime(sync: BaoStockSync) -> None:
    assert sync.run_task("bs_calendar").status == "ok"
    assert sync.run_task("bs_all_stock", day="2024-01-02").status == "ok"


# ───────────────────────── GWT-1 error_code 保留 ─────────────────────────


class TestGwt1ErrorCodePreserved:
    def test_network_error_code_survives(self):
        err = pytest.raises(FetchError, BaoStockFetcher._drain,
                            _ResultSet(error_code="10002007",
                                       error_msg="网络接收错误"))
        assert err.value.error_code == "10002007"
        assert err.value.retryable is True
        assert "10002007" in str(err.value)          # 人读侧也能看到码

    def test_param_error_code_not_retryable(self):
        err = pytest.raises(FetchError, BaoStockFetcher._drain,
                            _ResultSet(error_code="10004006", error_msg="参数错误"))
        assert err.value.error_code == "10004006"
        assert err.value.retryable is False

    def test_success_has_no_error_code(self):
        fields, rows = BaoStockFetcher._drain(
            _ResultSet(fields=["code"], rows=[["sh.600000"]]))
        assert fields == ["code"]
        assert rows == [["sh.600000"]]

    def test_error_code_reaches_envelope_reason(self, store: Store,
                                                gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, RetryFake(fail_times=99, retryable=False,
                                                   error_code="10004006"))
        sync.setup()
        _prime(sync)
        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "failed"
        assert "10004006" in (env.reason or "")


# ───────────────────────── GWT-2 错误分类 ─────────────────────────


class TestGwt2Classification:
    @pytest.mark.parametrize("code", sorted(NETWORK_ERROR_CODES))
    def test_network_codes_are_retryable(self, code: str):
        assert is_retryable_error(code) is True

    @pytest.mark.parametrize("code", sorted(SESSION_ERROR_CODES))
    def test_session_codes_are_retryable(self, code: str):
        assert is_retryable_error(code) is True

    @pytest.mark.parametrize("code", ["10004006", "10004007", "10004008",
                                      "10001006", "10001002"])
    def test_param_and_permission_codes_are_not_retryable(self, code: str):
        assert is_retryable_error(code) is False

    def test_unknown_and_missing_codes_are_not_retryable(self):
        assert is_retryable_error("99999999") is False
        assert is_retryable_error(None) is False
        assert "10002007" in RETRYABLE_ERROR_CODES


# ───────────────────────── GWT-3 退避重试 ─────────────────────────


class TestGwt3BackoffRetry:
    def test_backoff_sequence_is_exponential_and_capped(self):
        policy = RetryPolicy(attempts=5, base_seconds=1.0, max_seconds=4.0)
        assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]

    def test_retryable_error_is_retried_then_succeeds(self, store: Store,
                                                      gateway: EgressGateway):
        slept: list[float] = []
        fetcher = RetryFake(fail_times=2)
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, fetcher,
                            retry=RetryPolicy(attempts=3), sleep=slept.append)
        sync.setup()
        _prime(sync)

        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "ok"
        assert slept == [1.0, 2.0]                   # 一次失败 1s、二次 2s
        assert fetcher.reconnects == 2               # 每次重试前重建会话
        assert _one(db, "SELECT count(*) AS n FROM k_line_daily")["n"] == 2

    def test_attempts_exhausted_fails_with_reason(self, store: Store,
                                                  gateway: EgressGateway):
        slept: list[float] = []
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, RetryFake(fail_times=99),
                            retry=RetryPolicy(attempts=3), sleep=slept.append)
        sync.setup()
        _prime(sync)
        before = len(gateway.query(kind="data_fetch"))
        attempts_before = sync.fetch_attempts

        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "failed"
        assert env.reason
        assert slept == [1.0, 2.0]                   # 用尽 attempts 即止
        # 每次尝试各经一次网关审计：3 次尝试 ⇒ 3 条（重试不合并、不隐藏）
        assert len(gateway.query(kind="data_fetch")) - before == 3
        assert sync.fetch_attempts - attempts_before == 3

    def test_non_retryable_error_is_not_retried(self, store: Store,
                                                gateway: EgressGateway):
        slept: list[float] = []
        fetcher = RetryFake(fail_times=99, retryable=False)
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, fetcher, sleep=slept.append)
        sync.setup()
        _prime(sync)
        before = len(gateway.query(kind="data_fetch"))

        assert sync.run_task("bs_k_daily", codes=["sh.600000"]).status == "failed"
        assert slept == []
        assert fetcher.reconnects == 0
        assert len(gateway.query(kind="data_fetch")) - before == 1

    def test_attempts_one_means_no_retry(self, store: Store,
                                         gateway: EgressGateway):
        slept: list[float] = []
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, RetryFake(fail_times=99),
                            retry=RetryPolicy(attempts=1), sleep=slept.append)
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=["sh.600000"]).status == "failed"
        assert slept == []


# ───────────────────────── GWT-4 连接重建 ─────────────────────────


class TestGwt4Reconnect:
    def test_reconnect_called_before_each_retry(self, store: Store,
                                                gateway: EgressGateway):
        fetcher = RetryFake(fail_times=1)
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, fetcher,
                            retry=RetryPolicy(attempts=2), sleep=lambda _s: None)
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=["sh.600000"]).status == "ok"
        assert fetcher.reconnects == 1

    def test_fetcher_without_reconnect_is_tolerated(self, store: Store,
                                                    gateway: EgressGateway):
        """未提供 ``reconnect`` 的抓取器不该因此失败（协议面不变）。"""

        class NoReconnect(RetryFake):
            reconnect = None                # type: ignore[assignment]

        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, NoReconnect(fail_times=1),
                            retry=RetryPolicy(attempts=2), sleep=lambda _s: None)
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=["sh.600000"]).status == "ok"


# ───────────────────────── GWT-5 截断检测 ─────────────────────────


class TestGwt5Truncation:
    def test_suspect_only_on_exact_page_multiples(self):
        assert truncation_suspect(0, PAGE) is False      # 空集不算（退市码本就为空）
        assert truncation_suspect(726, PAGE) is False
        assert truncation_suspect(PAGE, PAGE) is True
        assert truncation_suspect(2 * PAGE, PAGE) is True
        assert truncation_suspect(PAGE + 1, PAGE) is False

    def test_shape_fingerprint_covers_fields_rows_and_tail(self):
        a = (["code"], [["sh.600000"], ["sz.000001"]])
        b = (["code"], [["sh.600000"], ["sz.000001"]])
        c = (["code"], [["sh.600000"]])
        assert result_shape(a) == result_shape(b)
        assert result_shape(a) != result_shape(c)

    def test_source_page_size_is_positive(self):
        assert source_page_size() > 0

    def test_mismatched_refetch_fails_loudly(self, store: Store,
                                             gateway: EgressGateway):
        """两次取回不一致 ⇒ 判失败（半截数据不静默入库）；重试用尽仍不一致 ⇒ 如实失败。"""
        slept: list[float] = []
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, ShapeFake([PAGE, PAGE + 1]),
                            retry=RetryPolicy(attempts=2), sleep=slept.append)
        sync.setup()
        before = len(gateway.query(kind="data_fetch"))
        env = sync.run_task("bs_security_basic")
        assert env.status == "failed"
        assert "截断" in (env.reason or "")
        # 每次尝试都含「复核那一次」，且**都经网关**（不藏第二次 socket 调用）
        assert len(gateway.query(kind="data_fetch")) - before == 4
        assert sync.fetch_attempts == 4
        assert slept == [1.0]                    # 复核不一致属可重试 ⇒ 退避一次后重试

    def test_matching_refetch_is_accepted(self, store: Store,
                                          gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, ShapeFake([PAGE, PAGE]))
        sync.setup()
        env = sync.run_task("bs_security_basic")
        assert env.status == "ok"
        assert _one(db, "SELECT count(*) AS n FROM security")["n"] == PAGE
        assert sync.fetch_attempts == 2

    def test_non_page_shape_is_not_refetched(self, store: Store,
                                             gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, ShapeFake([726]))
        sync.setup()
        assert sync.run_task("bs_security_basic").status == "ok"
        assert sync.fetch_attempts == 1          # 非整页 ⇒ 不复核

    def test_empty_result_is_not_refetched(self, store: Store,
                                           gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, ShapeFake([0]))
        sync.setup()
        env = sync.run_task("bs_security_basic")
        assert env.status in ("ok", "empty")
        assert sync.fetch_attempts == 1
