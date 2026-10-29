"""手动同步批次与总览（[D-129]）：依赖补齐、单飞、失败不连坐。"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.market import BaoStockSync, MarketDb
from st_agent.l0.market.errors import MarketValidationError
from st_agent.l0.sync_runner import DataSyncRunner, SyncBusyError, SyncJobError


def _ok(rows: int = 1) -> ResultEnvelope:
    return ResultEnvelope.ok({"rows": rows}, as_of=datetime.now().astimezone())


class FakeMarketSync:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.args: dict[str, dict] = {}
        self.fail_on: set[str] = set()

    def setup(self) -> ResultEnvelope:
        return _ok()

    def run_task(
        self, task_key: str, *, enabled=None, watchlist=None, timeout_ms=0,
        codes=None, start=None, end=None, groups=None, quarters=None,
        force_refresh=False, progress=None, cancel=None,
    ):
        self.calls.append(task_key)
        self.args[task_key] = {
            "codes": codes, "start": start, "end": end,
            "groups": groups, "quarters": quarters,
            "force_refresh": force_refresh,
        }
        if progress is not None:
            progress({
                "unit_done": 1, "unit_total": 1, "requests": 1,
                "rows": 1, "phase": "fetch", "label": task_key,
            })
        if task_key in self.fail_on:
            return ResultEnvelope.failed("抓取失败（Fake）", log_ref="fake/ref")
        return _ok()


class FakeInfoSync(FakeMarketSync):
    pass


@pytest.fixture()
def market_db(store, gateway) -> MarketDb:
    db = MarketDb(store)
    db.init_db()
    BaoStockSync(db, gateway, fetcher=object()).setup()
    with db.transact() as con:
        con.execute(
            "INSERT INTO security(code, code_name, ipo_date, out_date, type, status)"
            " VALUES ('sh.600000', '浦发银行', '1999-11-10', NULL, 1, 1)"
        )
        con.execute(
            "UPDATE sync_state SET last_status='ok',"
            " last_success_at='2026-09-25T08:00:00+08:00'"
            " WHERE task_key='bs_security_basic'"
        )
        con.commit()
    return db


def _mark_st(db: MarketDb) -> None:
    """把 ``sh.600000`` 标成 ST 同步池成员（名称匹配快照口径，[D-131]）。"""
    with db.transact() as con:
        con.execute(
            "INSERT INTO st_membership(code, code_name, type, status,"
            " name_prefixed, source_id, captured_at) VALUES"
            " ('sh.600000', 'ST浦发', 1, 1, 1, 'baostock', ?)",
            (datetime.now().astimezone().isoformat(),),
        )
        con.commit()


def _runner(store, gateway, market=None, info=None, watchlist=None) -> DataSyncRunner:
    market_sync = market or FakeMarketSync()
    info_sync = info or FakeInfoSync()
    return DataSyncRunner(
        store=store,
        gateway=gateway,
        watchlist=watchlist,
        market_factory=lambda _db, _gw: (market_sync, lambda: None),
        info_factory=lambda _db, _gw: (info_sync, lambda: None),
    )


def _wait(runner: DataSyncRunner, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.job()
        if job is not None and job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("同步批次未在预期时间内结束")


def test_market_dependencies_are_added_and_ordered(store, gateway, market_db):
    market = FakeMarketSync()
    runner = _runner(store, gateway, market=market)

    job_id = runner.start(["bs_k_daily"])["job_id"]
    job = _wait(runner)

    assert job["job_id"] == job_id
    assert job["order"][:3] == ["bs_calendar", "bs_all_stock", "bs_k_daily"]
    assert set(job["auto_added"]) == {"bs_calendar", "bs_all_stock"}
    # 执行顺序与计划一致（日历 → 全量快照 → 日线）
    assert market.calls[:3] == ["bs_calendar", "bs_all_stock", "bs_k_daily"]
    assert job["results"]["g1:bs_k_daily"]["status"] == "ok"


def test_info_tasks_add_the_master_tasks_when_security_is_empty(store, gateway):
    market = FakeMarketSync()
    info = FakeInfoSync()
    runner = _runner(store, gateway, market=market, info=info)

    runner.start(["info_announcement_cninfo"])
    job = _wait(runner)

    assert set(job["auto_added"]) >= {"bs_calendar", "bs_security_basic", "bs_all_stock"}
    assert job["order"][-1] == "info_announcement_cninfo"
    assert "info_announcement_cninfo" in info.calls


def test_a_second_batch_is_rejected_while_running(store, gateway, market_db):
    release = threading.Event()

    class BlockingMarket(FakeMarketSync):
        def run_task(self, task_key, **kwargs):
            release.wait(3)
            return super().run_task(task_key, **kwargs)

    runner = _runner(store, gateway, market=BlockingMarket())
    runner.start(["bs_calendar"])

    with pytest.raises(SyncBusyError):
        runner.start(["bs_calendar"])

    release.set()
    _wait(runner)


def test_single_failure_does_not_abort_the_rest(store, gateway, market_db):
    market = FakeMarketSync()
    market.fail_on.add("bs_calendar")
    runner = _runner(store, gateway, market=market)

    runner.start(["bs_calendar", "bs_all_stock"])
    job = _wait(runner)

    assert job["state"] == "done"
    assert job["results"]["g1:bs_calendar"]["status"] == "failed"
    assert job["results"]["g2:bs_all_stock"]["status"] == "ok"


def test_unknown_or_empty_selection_is_rejected(store, gateway):
    runner = _runner(store, gateway)

    with pytest.raises(SyncJobError):
        runner.start([])
    with pytest.raises(SyncJobError):
        runner.start(["no-such-task"])


def test_overview_reports_counts_and_states(store, gateway, market_db):
    with market_db.transact() as con:
        con.execute(
            "UPDATE sync_state SET last_status='ok', last_success_at=?, watermark=?"
            " WHERE task_key='bs_calendar'",
            (datetime.now().astimezone().isoformat(), "2026-10-24"),
        )
        con.execute(
            "INSERT INTO k_line_daily(code, trade_date, open, high, low, close, preclose,"
            " volume, amount, turn, trade_status, pct_chg, pe_ttm, pb_mrq, ps_ttm,"
            " pcf_ncf_ttm, is_st) VALUES ('sh.600000', '2026-10-24', 1, 1, 1, 1, 1,"
            " 1, 1, 1, 1, 0, 1, 1, 1, 1, 0)"
        )
        con.commit()
    runner = _runner(store, gateway)

    overview = runner.overview()

    assert overview["summary"]["db_exists"] is True
    assert overview["summary"]["ok_task_count"] == 2
    assert overview["summary"]["total_rows"] >= 2      # security + k_line_daily
    tasks = {task["task_key"]: task for task in overview["tasks"]}
    assert tasks["bs_calendar"]["status"] == "ok"
    assert tasks["bs_k_daily"]["status"] is None
    tables = {item["table"]: item for item in overview["tables"]}
    assert tables["k_line_daily"]["rows"] == 1
    assert "bs_k_daily" in tables["k_line_daily"]["tasks"]


def test_overview_without_db_reports_zero_existing_tables(store, gateway):
    runner = _runner(store, gateway)

    overview = runner.overview()

    assert overview["summary"]["db_exists"] is False
    assert overview["summary"]["table_count"] == 0
    assert overview["summary"]["covered_table_count"] == 24
    assert overview["summary"]["total_rows"] == 0
    assert overview["summary"]["note"]


# ───────────────────── 范围 / 预检 / 取消（[D-130]） ─────────────────────


def test_preflight_reports_order_auto_added_and_first_full(store, gateway, market_db):
    runner = _runner(store, gateway)

    plan = runner.preflight(["bs_k_daily"])

    assert plan["order"][:3] == ["bs_calendar", "bs_all_stock", "bs_k_daily"]
    assert set(plan["auto_added"]) == {"bs_calendar", "bs_all_stock"}
    assert plan["errors"] == []
    assert any(warning["kind"] == "first_full" for warning in plan["warnings"])
    assert plan["plan"][0]["time"]["kind"] == "date_range"


def test_range_is_passed_only_to_supporting_tasks(store, gateway, market_db):
    market = FakeMarketSync()
    runner = _runner(store, gateway, market=market)

    runner.start(["bs_k_daily", "bs_calendar"], start="2024-01-01", end="2024-01-31")
    job = _wait(runner)

    args = market.args["bs_k_daily"]
    assert args["codes"] == ["sh.600000"]
    assert args["start"] == "2024-01-01"
    assert args["end"] == "2024-01-31"
    assert market.args["bs_calendar"]["start"] is None
    assert market.args["bs_calendar"]["end"] is None
    assert any(
        group["dataset"] == "k_daily"
        and group["time"]["start"] == "2024-01-01"
        and group["time"]["end"] == "2024-01-31"
        for group in job["groups"]
    )


def test_st_target_uses_sync_pool_and_custom_time(store, gateway, market_db):
    _mark_st(market_db)
    market = FakeMarketSync()
    runner = _runner(store, gateway, market=market)

    runner.start_items([{
        "target": "st", "dataset": "k_daily", "groups": ["price"],
        "time": {"kind": "date_range", "mode": "custom",
                 "start": "2024-01-01", "end": "2024-01-31"},
    }])
    job = _wait(runner)

    args = market.args["bs_k_daily"]
    assert args["codes"] == ["sh.600000"]
    assert args["start"] == "2024-01-01"
    assert args["end"] == "2024-01-31"
    assert args["groups"] == ["price"]
    assert job["targets"]["st"]["count"] == 1


def test_preflight_items_resolves_quarter_range(store, gateway, market_db):
    runner = _runner(store, gateway)

    plan = runner.preflight_items([{
        "target": "all_a", "dataset": "fin_quarter", "groups": ["profit"],
        "time": {"kind": "quarter_range", "mode": "range",
                 "from": "2020Q1", "to": "2020Q4"},
    }])

    assert plan["errors"] == []
    assert plan["plan"][0]["time"]["start"] == "2020-01-01"
    assert plan["plan"][0]["time"]["end"] == "2020-12-31"
    assert plan["plan"][0]["groups"] == ["profit"]


def test_st_scope_rejects_empty_pool(store, gateway, market_db):
    runner = _runner(store, gateway)

    with pytest.raises(SyncJobError, match="ST"):
        runner.start(["bs_k_daily"], scope="st", start="2024-01-01")


def test_watchlist_scope_uses_injected_watchlist(store, gateway, market_db):
    market = FakeMarketSync()
    runner = _runner(
        store, gateway, market=market, watchlist=lambda: ("sh.600000",)
    )

    runner.start(["bs_k_daily"], scope="watchlist", start="2024-01-01")
    job = _wait(runner)

    assert market.args["bs_k_daily"]["codes"] == ["sh.600000"]
    assert job["targets"]["watchlist"]["count"] == 1


def test_watchlist_scope_deduplicates_overlapping_holdings_and_watchlist(
    store, gateway, market_db,
):
    market = FakeMarketSync()
    runner = _runner(
        store, gateway, market=market,
        watchlist=lambda: ("sh.600000", "sh.600000", "sz.000001"),
    )

    runner.start(["bs_k_daily"], scope="watchlist", start="2024-01-01")
    _wait(runner)

    assert market.args["bs_k_daily"]["codes"] == ["sh.600000", "sz.000001"]



def test_cancel_stops_between_tasks_and_keeps_finished_results(store, gateway, market_db):
    entered = threading.Event()
    release = threading.Event()

    class SlowMarket(FakeMarketSync):
        def run_task(self, task_key, **kwargs):
            entered.set()
            release.wait(3)
            return super().run_task(task_key, **kwargs)

    runner = _runner(store, gateway, market=SlowMarket())
    runner.start(["bs_calendar", "bs_all_stock"])
    assert entered.wait(2)

    result = runner.cancel()
    assert result["cancelled"] is True
    release.set()
    job = _wait(runner)

    assert job["state"] == "cancelled"
    assert job["results"]["g1:bs_calendar"]["status"] == "ok"
    assert job["results"]["g2:bs_all_stock"]["status"] == "cancelled"


def test_overview_reports_capabilities_and_scope(store, gateway, market_db):
    _mark_st(market_db)
    runner = _runner(store, gateway)

    overview = runner.overview()
    tasks = {task["task_key"]: task for task in overview["tasks"]}

    assert tasks["bs_k_daily"]["per_code"] is True
    assert tasks["bs_k_daily"]["supports_range"] is True
    assert tasks["bs_calendar"]["supports_range"] is False
    assert overview["scope"]["st"]["count"] == 1
    assert overview["targets"]["st"]["count"] == 1
    assert overview["targets"]["st"]["as_of"]
    assert "items" not in overview["summary"]


def test_invalid_window_is_rejected(store, gateway, market_db):
    runner = _runner(store, gateway)

    with pytest.raises(SyncJobError, match="晚于"):
        runner.start(["bs_k_daily"], start="2024-02-01", end="2024-01-01")
    with pytest.raises(SyncJobError, match="YYYY-MM-DD"):
        runner.start(["bs_k_daily"], start="2024/01/01")
    with pytest.raises(SyncJobError, match="证券范围"):
        runner.start(["bs_k_daily"], scope="unknown")


def test_invalid_quarter_and_year_range_are_preflight_errors(store, gateway, market_db):
    runner = _runner(store, gateway)

    quarter = runner.preflight_items([{
        "target": "all_a", "dataset": "fin_quarter",
        "time": {"kind": "quarter_range", "mode": "range",
                 "from": "", "to": "2024Q1"},
    }])
    assert quarter["errors"]

    year = runner.preflight_items([{
        "target": "all_a", "dataset": "dividend",
        "time": {"kind": "year_range", "mode": "range"},
    }])
    assert year["errors"]


def test_progress_snapshot_reports_counts(store, gateway, market_db):
    runner = _runner(store, gateway, market=FakeMarketSync())

    runner.start(["bs_calendar"])
    job = _wait(runner)

    assert job["progress"]["unit_done"] == 1
    assert job["progress"]["unit_total"] == 1
    assert job["progress"]["requests"] == 1


def test_clear_market_keeps_other_partitions_and_cache(store, gateway, market_db):
    store.put("memory", "probe.txt", b"keep")
    store.put("data_cache", "market.db", b"market")
    store.put("data_cache", "info/sh.600000/2026-01-01/a.txt", b"text")
    store.put("data_cache", "other.bin", b"keep")
    runner = _runner(store, gateway)

    result = runner.clear_market()

    assert result["removed_count"] == 2
    assert store.list_files("data_cache") == ("other.bin",)
    assert store.get("memory", "probe.txt") == b"keep"


def test_clear_market_refuses_while_a_batch_is_running(store, gateway, market_db):
    release = threading.Event()

    class BlockingMarket(FakeMarketSync):
        def run_task(self, task_key, **kwargs):
            release.wait(3)
            return super().run_task(task_key, **kwargs)

    runner = _runner(store, gateway, market=BlockingMarket())
    runner.start(["bs_calendar"])
    with pytest.raises(SyncBusyError):
        runner.clear_market()
    release.set()
    _wait(runner)


def test_fetch_items_runs_synchronously_and_reports_as_of(store, gateway, market_db):
    market = FakeMarketSync()
    runner = _runner(store, gateway, market=market)

    snapshot = runner.fetch_items(
        [{
            "target": "custom",
            "dataset": "k_daily",
            "time": {"kind": "date_range", "mode": "custom",
                     "start": "2026-09-01", "end": "2026-09-25"},
        }],
        custom_text="sh.600000",
    )

    assert snapshot["state"] == "done"
    assert market.calls[-1] == "bs_k_daily"
    assert market.args["bs_k_daily"]["codes"] == ["sh.600000"]
    assert market.args["bs_k_daily"]["start"] == "2026-09-01"
    assert snapshot["results"]["g1:bs_k_daily"]["status"] == "ok"
    assert snapshot["as_of"] == str(market_db.last_updated_at())
    assert runner.job()["state"] == "done"


def test_fetch_items_refuses_while_a_batch_is_running(store, gateway, market_db):
    release = threading.Event()

    class BlockingMarket(FakeMarketSync):
        def run_task(self, task_key, **kwargs):
            release.wait(3)
            return super().run_task(task_key, **kwargs)

    runner = _runner(store, gateway, market=BlockingMarket())
    runner.start(["bs_calendar"])

    with pytest.raises(SyncBusyError):
        runner.fetch_items([{"target": "other", "dataset": "calendar"}])

    release.set()
    _wait(runner)


def test_fetch_items_rejects_unknown_dataset(store, gateway):
    runner = _runner(store, gateway)
    with pytest.raises(MarketValidationError):
        runner.fetch_items([{"target": "other", "dataset": "no-such-dataset"}])
