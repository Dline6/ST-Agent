"""T-L0-007.1 测试：全量同步的续跑语义与流程可重复（Fake 注入，不碰真网）。

GWT 对照（任务文件 5 条）：
- GWT-1 中断续跑不重复写：中断后续跑，已入库行不重复、缺失行补齐
- GWT-2 水位单调不回退：失败不推进水位、成功后重跑不后退；窗口起点随之前进
- GWT-3 失败不静默可查：`sync_state` 记 failed + 原因；`freshness_verdict` 相应域 stale
- GWT-4 抓取全经审计：`data_fetch` 审计条数与实际抓取次数一致
- GWT-5 流程可重复（脚本）：摘要含逐任务 status/行数/水位/耗时 + 参数块；口令不入摘要

真实网络路径见 ``tests/live/test_full_sync_live.py``（默认排除）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.market import (
    RUN_ORDER,
    BaoStockSync,
    FetchError,
    MarketDb,
)
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from test_market import PASS, FakeFetcher

CODES = ["sh.600000", "sh.000001"]


# ───────────────────────── Fake：可在中途"中断" ─────────────────────────

class FlakyFetcher(FakeFetcher):
    """前 N 次某方法调用成功、之后抛 ``FetchError``（模拟限流 / 中途中断）。"""

    def __init__(self, fail_after: dict[str, int]) -> None:
        super().__init__()
        self._fail_after = dict(fail_after)
        self._counts: dict[str, int] = {}

    def _tick(self, name: str) -> None:
        n = self._counts.get(name, 0)
        self._counts[name] = n + 1
        limit = self._fail_after.get(name)
        if limit is not None and n >= limit:
            self.calls.append(name)
            raise FetchError(f"抓取中断（Fake，{name}）")

    def k_daily(self, code, start, end):
        self._tick("k_daily")
        return super().k_daily(code, start, end)


class RecordingFetcher(FakeFetcher):
    """记录每次 ``k_daily`` 拿到的窗口（验证窗口起点随水位前进）。"""

    def __init__(self) -> None:
        super().__init__()
        self.windows: list[tuple[str, str, str]] = []

    def k_daily(self, code, start, end):
        self.windows.append((code, start, end))
        return super().k_daily(code, start, end)


# ───────────────────────── 夹具与工具 ─────────────────────────

@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store)


def _prime(sync: BaoStockSync) -> None:
    """跑通 ``bs_k_daily`` 的前置链（calendar → all_stock → security_basic）。"""
    assert sync.run_task("bs_calendar").status == "ok"
    assert sync.run_task("bs_all_stock", day="2024-01-02").status == "ok"
    assert sync.run_task("bs_security_basic").status == "ok"


def _one(db: MarketDb, sql: str) -> dict:
    return db.query(sql).data["rows"][0]


def _kline_rows(db: MarketDb) -> int:
    return _one(db, "SELECT count(*) AS n FROM k_line_daily")["n"]


def _watermark(db: MarketDb) -> str | None:
    return _one(db, "SELECT watermark FROM sync_state "
                    "WHERE task_key='bs_k_daily'")["watermark"]


# ───────────────────────── GWT-1 中断续跑不重复写 ─────────────────────────


class TestGwt1ResumeNoDuplicate:
    def test_interrupt_then_rerun_no_duplicate(self, store: Store,
                                               gateway: EgressGateway):
        db = MarketDb(store)
        # 首次：第 2 只证券抓取中断（第 1 只已入库、不回滚）
        sync = BaoStockSync(db, gateway, FlakyFetcher(fail_after={"k_daily": 1}))
        sync.setup()
        _prime(sync)
        env = sync.run_task("bs_k_daily", codes=CODES)
        assert env.status == "failed"
        assert _kline_rows(db) == 2          # 仅第 1 只证券的 2 根日线

        # 续跑：健康抓取器 → 补齐且不重复
        sync2 = BaoStockSync(db, gateway, FakeFetcher())
        env2 = sync2.run_task("bs_k_daily", codes=CODES)
        assert env2.status == "ok"
        assert _kline_rows(db) == 4          # 2 只 × 2 日，无重复
        assert _one(db, "SELECT count(DISTINCT code) AS c FROM k_line_daily")["c"] == 2

    def test_rerun_is_idempotent(self, store: Store, gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher())
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=CODES).status == "ok"
        assert sync.run_task("bs_k_daily", codes=CODES).status == "ok"
        assert _kline_rows(db) == 4          # 重跑不叠加


# ───────────────────────── GWT-2 水位单调不回退 ─────────────────────────


class TestGwt2WatermarkMonotonic:
    def test_watermark_advances_after_success(self, store: Store,
                                              gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher())
        sync.setup()
        _prime(sync)
        env = sync.run_task("bs_k_daily", codes=CODES)
        assert env.status == "ok"
        assert env.data["watermark"] == "2024-01-03"
        assert _watermark(db) == "2024-01-03"

    def test_failure_neither_advances_nor_regresses(self, store: Store,
                                                    gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher())
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=CODES).status == "ok"

        # 失败一次：水位保持不变
        sync_down = BaoStockSync(db, gateway, FlakyFetcher(fail_after={"k_daily": 0}))
        assert sync_down.run_task("bs_k_daily", codes=CODES).status == "failed"
        assert _watermark(db) == "2024-01-03"

        # 成功后重跑：仍不后退
        assert sync.run_task("bs_k_daily", codes=CODES).status == "ok"
        assert _watermark(db) == "2024-01-03"

    def test_window_start_follows_watermark(self, store: Store,
                                            gateway: EgressGateway):
        db = MarketDb(store)
        first = BaoStockSync(db, gateway, FakeFetcher())
        first.setup()
        _prime(first)
        first.run_task("bs_k_daily", codes=CODES)

        rec = RecordingFetcher()
        BaoStockSync(db, gateway, rec).run_task("bs_k_daily", codes=CODES)
        assert rec.windows, "重跑应至少抓取一次"
        assert rec.windows[0][1] == "2024-01-04"   # 窗口起点 = 水位次日


# ───────────────────────── GWT-3 失败不静默可查 ─────────────────────────


class TestGwt3FailureVisible:
    def test_failure_recorded_with_reason(self, store: Store,
                                          gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FlakyFetcher(fail_after={"k_daily": 0}))
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=CODES).status == "failed"

        row = _one(db, "SELECT last_status, last_error FROM sync_state "
                       "WHERE task_key='bs_k_daily'")
        assert row["last_status"] == "failed"
        assert row["last_error"]              # 原因非空，不静默

    def test_freshness_flags_stale_and_names_task(self, store: Store,
                                                  gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FlakyFetcher(fail_after={"k_daily": 0}))
        sync.setup()
        _prime(sync)
        sync.run_task("bs_k_daily", codes=CODES)

        verdict = sync.freshness_verdict("kline")
        assert verdict.stale is True
        assert "bs_k_daily" in verdict.detail


# ───────────────────────── GWT-4 抓取全经审计 ─────────────────────────


class TestGwt4Audit:
    def test_every_fetch_leaves_audit(self, store: Store,
                                      gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher())
        sync.setup()
        _prime(sync)                          # 3 次成功抓取（各经网关一次）
        events = gateway.query(kind="data_fetch")
        assert len(events) == 3
        assert all(e.target_host == "baostock" for e in events)

    def test_failed_fetch_still_audited(self, store: Store,
                                        gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FlakyFetcher(fail_after={"k_daily": 0}))
        sync.setup()
        _prime(sync)
        before = len(gateway.query(kind="data_fetch"))
        assert sync.run_task("bs_k_daily", codes=CODES).status == "failed"
        assert len(gateway.query(kind="data_fetch")) == before + 1


# ───────────────────────── GWT-5 流程可重复（脚本摘要） ─────────────────────────


class TestGwt5ScriptSummary:
    def test_summary_shape_and_params(self, store: Store,
                                      gateway: EgressGateway):
        from run_full_sync import summarize_run

        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher())
        params = {
            "root": str(store._root),  # noqa: SLF001 — 摘要只记路径，不记口令
            "watchlist": None,
            "enabled": None,
            "timeout_ms": 120_000,
            "passphrase_source": "env:ST_AGENT_PASSPHRASE",
        }
        summary = summarize_run(sync, params=params)

        assert summary["run_order"] == list(RUN_ORDER)
        assert set(summary["tasks"]) == set(RUN_ORDER)
        assert summary["tasks"]["bs_calendar"]["status"] == "ok"
        kline = summary["tasks"]["bs_k_daily"]
        assert kline["status"] == "ok"
        assert kline["rows"] is not None
        assert kline["watermark"] == "2024-01-03"
        assert kline["duration_seconds"] >= 0.0
        assert summary["total_duration_seconds"] >= 0.0
        assert summary["params"]["passphrase_source"].startswith("env:")

    def test_passphrase_never_in_summary(self, store: Store,
                                         gateway: EgressGateway):
        from run_full_sync import summarize_run

        summary = summarize_run(
            BaoStockSync(MarketDb(store), gateway, FakeFetcher()),
            params={"root": "tmp/x", "passphrase_source": "env:ST_AGENT_PASSPHRASE"},
        )
        blob = json.dumps(summary, ensure_ascii=False)
        assert PASS not in blob                    # 口令值不入摘要
        assert "env:ST_AGENT_PASSPHRASE" in blob   # 只记来源口径

    def test_script_order_matches_run_all(self, store: Store,
                                          gateway: EgressGateway):
        """脚本逐任务计时用的顺序必须与 ``run_all`` 同源，防两处漂移。"""
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher())
        assert list(RUN_ORDER) == list(sync.run_all().keys())


# ───────────────────────── 默认排除真实网络路径 ─────────────────────────


def test_live_tests_are_excluded_by_default() -> None:
    """``tests/live/`` 的真实用例须带 ``live`` 标记（默认 ``-m 'not live'`` 排除）。"""
    live_dir = Path(__file__).resolve().parents[1] / "live"
    if not live_dir.exists():
        pytest.skip("尚无 tests/live/")
    for f in live_dir.glob("test_*.py"):
        assert "pytest.mark.live" in f.read_text(encoding="utf-8"), f
