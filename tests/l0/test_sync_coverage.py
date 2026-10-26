"""T-L0-019 / .20 / .21：ST 名单、指标组部分写入与逐码覆盖账本。"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.market import BaoStockSync, MarketDb
from st_agent.l0.market.datasets import DATASETS
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"


class FakeFetcher:
    def __init__(self) -> None:
        self.st_rows: list[list[str]] = []
        self.k_calls: list[tuple[str, str, str]] = []
        self.fin_calls: list[str] = []

    def st_basic(self):
        return (
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            self.st_rows,
        )

    def k_daily(self, code, start, end):
        self.k_calls.append((code, start, end))
        return (
            ["date", "code", "open", "high", "low", "close", "preclose",
             "volume", "amount", "adjustflag", "turn", "tradestatus",
             "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST"],
            [[start, code, "1", "1", "1", "10", "1", "1", "1", "3",
              "1", "1", "0", "5", "1", "1", "1", "0"]],
        )

    def financial(self, code, year, quarter, which):
        self.fin_calls.append(which)
        if which == "profit":
            return (
                ["code", "statDate", "pubDate", "roeAvg"],
                [[code, f"{year}-03-31", f"{year}-04-30", "12.5"]],
            )
        return (
            ["code", "statDate", "pubDate", "YOYNI"],
            [[code, f"{year}-03-31", f"{year}-04-30", "8.8"]],
        )


@pytest.fixture()
def rig(tmp_path: Path):
    store = Store.create(tmp_path / "root", PASS)
    gateway = EgressGateway(store, audit=True)
    fake = FakeFetcher()
    db = MarketDb(store)
    sync = BaoStockSync(db, gateway, fake)
    sync.setup()
    with db.transact() as con:
        con.executemany(
            "INSERT INTO trade_calendar(calendar_date, is_trading_day) VALUES (?, 1)",
            [("2020-01-02",), ("2020-01-03",), ("2020-01-06",),
             ("2020-01-07",), ("2020-01-08",)],
        )
        con.execute(
            "INSERT INTO security(code, code_name, ipo_date, out_date, type, status)"
            " VALUES ('sh.600000', '浦发银行', '1999-11-10', NULL, 1, 1)"
        )
        con.execute(
            "UPDATE sync_state SET last_status='ok',"
            " last_success_at='2020-01-01T00:00:00+08:00'"
            " WHERE task_key IN ('bs_calendar', 'bs_all_stock', 'bs_security_basic')"
        )
        con.commit()
    return db, sync, fake


def test_st_list_filters_listed_prefixes_and_replaces(rig):
    db, sync, fake = rig
    fake.st_rows = [
        ["sh.600053", "*ST国贸", "1997-04-18", "", "1", "1"],
        ["sh.600065", "*ST退市", "1997-05-23", "2007-12-13", "1", "0"],
        ["sh.600000", "浦发银行", "1999-11-10", "", "1", "1"],
        ["sz.000001", "ST平安", "1991-04-03", "", "1", "1"],
    ]

    env = sync.run_task("bs_st_list")
    assert env.status == "ok"
    rows = db.query("SELECT code, code_name, type, status FROM st_membership"
                    " ORDER BY code").data["rows"]
    assert [row["code"] for row in rows] == ["sh.600053", "sz.000001"]
    pool = db.st_sync_pool()
    assert pool["available"] is True and pool["count"] == 2

    fake.st_rows = [["sh.600053", "*ST国贸", "1997-04-18", "", "1", "1"]]
    sync.run_task("bs_st_list")
    rows = db.query("SELECT code FROM st_membership ORDER BY code").data["rows"]
    assert [row["code"] for row in rows] == ["sh.600053"]


def test_k_daily_coverage_skips_covered_and_fills_gap(rig):
    db, sync, fake = rig
    args = dict(codes=["sh.600000"], start="2020-01-02", end="2020-01-08")

    sync.run_task("bs_k_daily", **args)
    assert fake.k_calls == [("sh.600000", "2020-01-02", "2020-01-08")]

    fake.k_calls.clear()
    sync.run_task("bs_k_daily", **args)
    assert fake.k_calls == []          # 2020 年区间不在刷新视界内 → 全覆盖跳过

    with db.transact() as con:
        con.execute("DELETE FROM sync_coverage")
        con.executemany(
            "INSERT INTO sync_coverage(code, dataset_id, variant, unit, span_start,"
            " span_end, groups, status, source_id, checked_at) VALUES"
            " ('sh.600000', 'k_daily', '', 'day', ?, ?, 'price,st_flag,valuation,volume',"
            " 'ok', 'baostock', '2020-01-09T00:00:00+08:00')",
            [("2020-01-02", "2020-01-03"), ("2020-01-07", "2020-01-08")],
        )
        con.commit()

    fake.k_calls.clear()
    sync.run_task("bs_k_daily", **args)
    assert fake.k_calls == [("sh.600000", "2020-01-06", "2020-01-06")]

    fake.k_calls.clear()
    sync.run_task("bs_k_daily", force_refresh=True, **args)
    assert fake.k_calls == [("sh.600000", "2020-01-02", "2020-01-08")]


def test_k_daily_group_upgrade_and_partial_write_preserve_other_columns(rig):
    db, sync, fake = rig
    args = dict(codes=["sh.600000"], start="2020-01-02", end="2020-01-02")

    sync.run_task("bs_k_daily", groups=["price", "valuation"], **args)
    row = db.query("SELECT close, pe_ttm FROM k_line_daily").data["rows"][0]
    assert row["close"] == 10 and row["pe_ttm"] == 5

    with db.transact() as con:
        con.execute("DELETE FROM sync_coverage")
        con.commit()
    fake.k_calls.clear()
    sync.run_task("bs_k_daily", groups=["price"], force_refresh=True, **args)
    row = db.query("SELECT close, pe_ttm FROM k_line_daily").data["rows"][0]
    assert row["close"] == 10
    assert row["pe_ttm"] == 5          # 未选列保留原值，不被 NULL 覆盖


def test_financial_group_subset_only_calls_selected_api(rig):
    db, sync, fake = rig
    sync.run_task("bs_fin_quarter", codes=["sh.600000"], groups=["profit"], quarters=1)
    assert fake.fin_calls == ["profit"]
    row = db.query(
        "SELECT roe_avg, yoy_ni, pub_date FROM financial_quarter"
    ).data["rows"][0]
    assert row["roe_avg"] == 12.5
    assert row["yoy_ni"] is None

    fake.fin_calls.clear()
    sync.run_task("bs_fin_quarter", codes=["sh.600000"], groups=["growth"],
                  quarters=1, force_refresh=True)
    assert fake.fin_calls == ["growth"]
    row = db.query(
        "SELECT roe_avg, yoy_ni, pub_date FROM financial_quarter"
    ).data["rows"][0]
    assert row["roe_avg"] == 12.5
    assert row["yoy_ni"] == 8.8


def test_dataset_catalog_covers_every_task_once():
    from st_agent.l0.market.datasets import validate_datasets

    assert validate_datasets() == []
    ids = [dataset.dataset_id for dataset in DATASETS]
    assert len(ids) == len(set(ids))
