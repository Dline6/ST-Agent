"""手动同步批次与总览（[D-129]）：依赖补齐、单飞、失败不连坐。"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.market import BaoStockSync, MarketDb
from st_agent.l0.sync_runner import DataSyncRunner, SyncBusyError, SyncJobError


def _ok(rows: int = 1) -> ResultEnvelope:
    return ResultEnvelope.ok({"rows": rows}, as_of=datetime.now().astimezone())


class FakeMarketSync:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_on: set[str] = set()

    def setup(self) -> ResultEnvelope:
        return _ok()

    def run_task(self, task_key: str, *, enabled=None, watchlist=None, timeout_ms=0):
        self.calls.append(task_key)
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
        con.commit()
    return db


def _runner(store, gateway, market=None, info=None) -> DataSyncRunner:
    market_sync = market or FakeMarketSync()
    info_sync = info or FakeInfoSync()
    return DataSyncRunner(
        store=store,
        gateway=gateway,
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
    assert job["results"]["bs_k_daily"]["status"] == "ok"


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
        def run_task(self, task_key, *, enabled=None, watchlist=None, timeout_ms=0):
            release.wait(3)
            return super().run_task(task_key, enabled=enabled, watchlist=watchlist)

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
    assert job["results"]["bs_calendar"]["status"] == "failed"
    assert job["results"]["bs_all_stock"]["status"] == "ok"


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
    assert overview["summary"]["ok_task_count"] == 1
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
    assert overview["summary"]["covered_table_count"] == 23
    assert overview["summary"]["total_rows"] == 0
    assert overview["summary"]["note"]
