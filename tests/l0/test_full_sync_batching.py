"""T-L0-017.1 测试：分批写入、显式窗口与分片断点续跑（Fake 注入，不碰真网）。

GWT 对照（任务文件 5 条）：
- GWT-1 批内合并写：事务次数 = ⌈N/批大小⌉ 而非 N，落库行数与逐码写等价
- GWT-2 显式窗口（分片可用）：库内水位已到今日时，另一批码仍按给定起点取数；水位不回退
- GWT-3 中断只回退一批：失败批重跑，已完成批不重取、已入库行不重复、未完成批补齐
- GWT-4 脚本可中断续跑：checkpoint 驱动，摘要含批粒度进度；口令不入 argv / 摘要 / checkpoint
- GWT-5 零泄漏与审计不变：抓取逐次经网关留审计（条数 = 抓取次数）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.market import (
    PER_CODE_TASKS,
    BaoStockSync,
    FetchError,
    MarketDb,
    estimate_requests,
)
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from test_market import PASS, FakeFetcher

CODES = ["sh.600000", "sh.601398", "sh.600519", "sz.000001", "sz.300750"]


# ───────────────────────── Fake：计数事务与可中断 ─────────────────────────

class CountingDb(MarketDb):
    """记录 ``transact()`` 次数（GWT-1 的计量口径）。"""

    def __init__(self, store) -> None:
        super().__init__(store)
        self.transacts = 0

    def transact(self):
        self.transacts += 1
        return super().transact()


class WideFetcher(FakeFetcher):
    """主档里带上 ``CODES`` 全部代码——逐码任务有外键约束，码必须在 ``security`` 里。"""

    def security_basic(self):
        self.calls.append("security_basic")
        return (["code", "code_name", "ipoDate", "outDate", "type", "status"],
                [[code, f"证券{code[-4:]}", "1999-11-10", "", "1", "1"]
                 for code in CODES])


class FlakyFetcher(WideFetcher):
    """第 ``fail_at`` 次 ``k_daily`` 调用抛 ``FetchError``（模拟中断）。"""

    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self._fail_at = fail_at
        self._n = 0

    def k_daily(self, code, start, end):
        self._n += 1
        if self._n == self._fail_at:
            self.calls.append("k_daily")
            raise FetchError(f"抓取中断（Fake，第 {self._n} 次）")
        return super().k_daily(code, start, end)


class WindowRecordingFetcher(WideFetcher):
    def __init__(self) -> None:
        super().__init__()
        self.windows: list[tuple[str, str, str]] = []

    def k_daily(self, code, start, end):
        self.windows.append((code, start, end))
        return super().k_daily(code, start, end)


# ───────────────────────── 夹具 ─────────────────────────

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


class CountingSync(BaoStockSync):
    """记录逐码装载面的**写事务**次数（GWT-1 的计量口径）。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.writes = 0

    def _write_k_daily(self, mapped):
        self.writes += 1
        return super()._write_k_daily(mapped)


# ───────────────────────── GWT-1 批内合并写 ─────────────────────────


class TestGwt1BatchedWrites:
    def test_write_transacts_are_per_batch_not_per_code(self, store: Store,
                                                        gateway: EgressGateway):
        db = MarketDb(store)
        sync = CountingSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)

        assert sync.run_task("bs_k_daily", codes=CODES, batch_size=2).status == "ok"
        assert sync.writes == 3                     # ⌈5 / 2⌉，而非 5

    def test_legacy_default_is_one_transact_per_code(self, store: Store,
                                                     gateway: EgressGateway):
        db = MarketDb(store)
        sync = CountingSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)

        assert sync.run_task("bs_k_daily", codes=CODES).status == "ok"
        assert sync.writes == len(CODES)            # 缺省＝既有逐码写，行为不变

    def test_batching_cuts_whole_blob_roundtrips(self, store: Store,
                                                 gateway: EgressGateway):
        """整库往返次数之差恰为 ``N − ⌈N/批⌉``（其余事务是任务级固定开销）。"""
        per_code = CountingDb(store)
        sync_a = BaoStockSync(per_code, gateway, WideFetcher())
        sync_a.setup()
        _prime(sync_a)
        after_prime = per_code.transacts
        assert sync_a.run_task("bs_k_daily", codes=CODES).status == "ok"
        per_code_cost = per_code.transacts - after_prime

        batched = CountingDb(store)
        sync_b = BaoStockSync(batched, gateway, WideFetcher())
        sync_b.setup()
        _prime(sync_b)
        after_prime = batched.transacts
        assert sync_b.run_task("bs_k_daily", codes=CODES, batch_size=2).status == "ok"
        batched_cost = batched.transacts - after_prime

        expected_saving = len(CODES) - 3            # N − ⌈N/批⌉
        assert per_code_cost - batched_cost == expected_saving

    def test_batched_rows_equal_per_code_rows(self, store: Store,
                                             gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=CODES, batch_size=2).status == "ok"
        # 5 只码 × 每只 2 根日线 —— 与逐码写同值
        assert _kline_rows(db) == 2 * len(CODES)
        assert _one(db, "SELECT count(DISTINCT code) AS c FROM k_line_daily")["c"] == len(CODES)

    def test_batch_size_must_be_positive(self, store: Store,
                                         gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)
        env = sync.run_task("bs_k_daily", codes=CODES, batch_size=0)
        assert env.status == "validation_failed"


# ───────────────────────── GWT-2 显式窗口（分片可用） ─────────────────────────


class TestGwt2ExplicitWindow:
    def test_shard_after_watermark_still_gets_full_window(self, store: Store,
                                                          gateway: EgressGateway):
        """分片 1 把全库水位推到今日后，分片 2 仍按显式起点取到数据。"""
        db = MarketDb(store)
        first = BaoStockSync(db, gateway, WideFetcher())
        first.setup()
        _prime(first)
        assert first.run_task("bs_k_daily", codes=CODES[:2], start="2024-01-01").status == "ok"
        assert _watermark(db) == "2024-01-03"

        rec = WindowRecordingFetcher()
        env = BaoStockSync(db, gateway, rec).run_task(
            "bs_k_daily", codes=CODES[2:], start="2024-01-01")
        assert env.status == "ok"
        assert rec.windows, "分片 2 应真的发起抓取，而不是被水位跳成空窗口"
        assert all(w[1] == "2024-01-01" for w in rec.windows)   # 起点＝显式给定
        assert _kline_rows(db) == 2 * len(CODES)

    def test_explicit_start_does_not_regress_watermark(self, store: Store,
                                                       gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=CODES, start="2024-01-01").status == "ok"
        assert _watermark(db) == "2024-01-03"
        # 以更早的起点再跑一次：水位不回退（仍取全库 max）
        assert sync.run_task("bs_k_daily", codes=CODES, start="2023-01-01").status == "ok"
        assert _watermark(db) == "2024-01-03"

    def test_default_window_unchanged_without_start(self, store: Store,
                                                    gateway: EgressGateway):
        """不给 ``start`` 时窗口仍按水位推导（既有语义不动）。"""
        db = MarketDb(store)
        first = BaoStockSync(db, gateway, WideFetcher())
        first.setup()
        _prime(first)
        first.run_task("bs_k_daily", codes=CODES)

        rec = WindowRecordingFetcher()
        BaoStockSync(db, gateway, rec).run_task("bs_k_daily", codes=CODES)
        assert rec.windows[0][1] == "2024-01-04"    # ＝水位次日

    def test_bad_start_is_validation_failed(self, store: Store,
                                            gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", start="2024/01/01").status == "validation_failed"


# ───────────────────────── GWT-3 中断只回退一批 ─────────────────────────


class TestGwt3FailureCostsOneBatch:
    def test_failed_batch_loses_only_that_batch(self, store: Store,
                                                gateway: EgressGateway):
        db = MarketDb(store)
        # 批大小 2：第 1 批（2 只）已提交，第 2 批第 3 只失败 ⇒ 库里只有第 1 批
        sync = BaoStockSync(db, gateway, FlakyFetcher(fail_at=3))
        sync.setup()
        _prime(sync)
        env = sync.run_task("bs_k_daily", codes=CODES, batch_size=2)
        assert env.status == "failed"
        assert _kline_rows(db) == 4                       # 2 只 × 2 根，只丢当前批

        # 续跑：补齐且不重复
        assert sync.run_task("bs_k_daily", codes=CODES,
                             batch_size=2).status == "ok"
        assert _kline_rows(db) == 2 * len(CODES)
        assert _one(db, "SELECT count(DISTINCT code) AS c FROM k_line_daily")["c"] == len(CODES)

    def test_failure_still_recorded_and_watermark_held(self, store: Store,
                                                       gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FlakyFetcher(fail_at=1))
        sync.setup()
        _prime(sync)
        assert sync.run_task("bs_k_daily", codes=CODES, batch_size=2).status == "failed"
        row = _one(db, "SELECT last_status, last_error, watermark FROM sync_state "
                       "WHERE task_key='bs_k_daily'")
        assert row["last_status"] == "failed"
        assert row["last_error"]
        assert row["watermark"] is None                  # 失败不推进水位


# ───────────────────────── GWT-4 脚本可中断续跑 ─────────────────────────


class TestGwt4ScriptResume:
    def test_checkpoint_roundtrip_and_no_passphrase(self, tmp_path: Path):
        from run_full_sync import load_checkpoint, save_checkpoint

        path = tmp_path / "full-sync-checkpoint.json"
        state = {"version": 1, "params": {"start": "2024-01-01"}, "tasks": {
            "bs_k_daily": {"shards_done": [0, 1], "shards_total": 3}}}
        save_checkpoint(path, state)
        blob = path.read_text(encoding="utf-8")
        assert PASS not in blob                          # 口令不入 checkpoint
        assert load_checkpoint(path) == state

    def test_missing_checkpoint_is_empty_skeleton(self, tmp_path: Path):
        from run_full_sync import load_checkpoint

        state = load_checkpoint(tmp_path / "nope.json")
        assert state["tasks"] == {}

    def test_sharded_run_resumes_and_skips_done_shards(self, store: Store,
                                                       gateway: EgressGateway):
        """首跑在第 2 片中断 → 续跑**跳过已完成的 0 号片**，只补剩下的码。"""
        from run_full_sync import run_sharded

        db = MarketDb(store)
        # 首跑：第 3 次 k_daily（＝2 号片的第 1 只）失败 ⇒ 0 号片已完成、1 号片未跑
        broken = FlakyFetcher(fail_at=3)
        sync = BaoStockSync(db, gateway, broken)
        state: dict = {"version": 1, "tasks": {}}
        first = run_sharded(sync, codes=CODES, shard_size=2, checkpoint=state,
                            start="2024-01-01")
        assert first["stopped_reason"]
        assert state["tasks"]["bs_k_daily"]["shards_done"] == [0]
        assert _one(db, "SELECT count(DISTINCT code) AS c FROM k_line_daily")["c"] == 2

        # 续跑：健康抓取器 + 同一 checkpoint ⇒ 0 号片被跳过（只抓剩下的 3 只）
        healthy = WideFetcher()
        resumed = BaoStockSync(db, gateway, healthy)
        second = run_sharded(resumed, codes=CODES, shard_size=2, checkpoint=state,
                             start="2024-01-01")
        assert second["tasks"]["bs_k_daily"]["shards_done"] == 3     # 摘要记片数
        assert state["tasks"]["bs_k_daily"]["shards_done"] == [0, 1, 2]  # checkpoint 记片号
        assert healthy.calls.count("k_daily") == 3          # 0 号片的 2 只没被重取
        assert _one(db, "SELECT count(DISTINCT code) AS c FROM k_line_daily")["c"] == len(CODES)
        assert _kline_rows(db) == 2 * len(CODES)            # 且不重复

    def test_summary_carries_shard_progress(self, store: Store,
                                            gateway: EgressGateway):
        from run_full_sync import run_sharded

        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)
        summary = run_sharded(sync, codes=CODES, shard_size=2, start="2024-01-01")
        kline = summary["tasks"]["bs_k_daily"]
        assert kline["shards_total"] == 3
        assert kline["shards_done"] == 3
        assert kline["status"] == "ok"
        json.dumps(summary, ensure_ascii=False)
        assert PASS not in json.dumps(summary, ensure_ascii=False)

    def test_daily_budget_stops_cleanly(self, store: Store,
                                        gateway: EgressGateway):
        """预算用尽 → 干净收手、写明理由、已完成片留在 checkpoint。

        非逐码任务先各花 1 次（calendar / security_basic / all_stock / industry /
        三个指数成分 = 7），剩下的 2 次正好够 ``bs_k_daily`` 的 0 号片（2 只码）。
        """
        from run_full_sync import run_sharded

        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        state: dict = {"version": 1, "tasks": {}}
        summary = run_sharded(sync, codes=CODES, shard_size=2, checkpoint=state,
                              start="2024-01-01", daily_budget=9)
        assert summary["stopped_reason"]
        assert summary["requests_issued"] == 9            # 收缩到预算内，不多发
        assert state["tasks"]["bs_k_daily"]["shards_done"] == [0]

    def test_budget_none_means_unlimited(self, store: Store,
                                         gateway: EgressGateway):
        from run_full_sync import run_sharded

        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        state: dict = {"version": 1, "tasks": {}}
        summary = run_sharded(sync, codes=CODES, shard_size=2, checkpoint=state,
                              start="2024-01-01", daily_budget=None)
        assert summary["stopped_reason"] == ""
        assert state["tasks"]["bs_k_daily"]["shards_done"] == [0, 1, 2]

    def test_request_estimate_matches_task_shape(self):
        assert estimate_requests("bs_k_daily", code_count=10) == 10
        assert estimate_requests("bs_k_period", code_count=10) == 20
        assert estimate_requests("bs_dividend", code_count=10, years=2) == 20
        assert estimate_requests("bs_fin_quarter", code_count=10, quarters=1) == 60
        assert estimate_requests("bs_fin_quarter", code_count=10) == 480
        assert estimate_requests("bs_calendar", code_count=0) == 1
        assert "bs_k_daily" in PER_CODE_TASKS
        assert "bs_calendar" not in PER_CODE_TASKS


# ───────────────────────── GWT-5 审计不变 ─────────────────────────


class TestGwt5AuditUnchanged:
    def test_every_fetch_is_audited_once(self, store: Store,
                                         gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, WideFetcher())
        sync.setup()
        _prime(sync)
        before = len(gateway.query(kind="data_fetch"))
        attempts_before = sync.fetch_attempts
        assert sync.run_task("bs_k_daily", codes=CODES, batch_size=2).status == "ok"
        after = gateway.query(kind="data_fetch")
        assert len(after) - before == len(CODES)          # 一批一事务不改审计粒度
        assert all(e.target_host == "baostock" for e in after)
        assert sync.fetch_attempts - attempts_before == len(CODES)
