"""数据总览读面（[D-129]）：``MarketDb.sync_state_rows`` / ``table_counts``。

只读面的两条硬口径：**一次**只读连接内计数；「表缺」不出现在结果里（与「表空」分开）。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from st_agent.l0.market import BaoStockSync, MarketDb


@pytest.fixture()
def market_db(store, gateway) -> MarketDb:
    db = MarketDb(store)
    db.init_db()
    BaoStockSync(db, gateway, fetcher=object()).setup()
    return db


def test_table_counts_returns_zero_for_empty_tables(market_db: MarketDb):
    counts = market_db.table_counts(["k_line_daily", "announcement"])

    assert counts == {"announcement": 0, "k_line_daily": 0}


def test_table_counts_ignores_missing_tables_and_metadata(market_db: MarketDb):
    counts = market_db.table_counts(["k_line_daily", "no_such_table"])

    assert counts == {"k_line_daily": 0}
    all_counts = market_db.table_counts()
    assert "sync_state" not in all_counts and "data_source" not in all_counts


def test_table_counts_reads_real_rows_once(market_db: MarketDb, monkeypatch):
    with market_db.transact() as con:
        con.execute(
            "INSERT INTO security(code, code_name, ipo_date, out_date, type, status)"
            " VALUES ('sh.600000', '浦发银行', '1999-11-10', NULL, 1, 1)"
        )
        con.execute(
            "INSERT INTO k_line_daily(code, trade_date, open, high, low, close,"
            " preclose, volume, amount, turn, trade_status, pct_chg,"
            " pe_ttm, pb_mrq, ps_ttm, pcf_ncf_ttm, is_st)"
            " VALUES ('sh.600000', '2026-10-24', 1, 1, 1, 1, 1, 1, 1, 1, 1, 0,"
            " 1, 1, 1, 1, 0)"
        )
        con.commit()
    calls = []
    original = market_db._readonly

    def counted():
        calls.append(1)
        return original()

    monkeypatch.setattr(market_db, "_readonly", counted)
    counts = market_db.table_counts(["security", "k_line_daily"])

    assert counts == {"security": 1, "k_line_daily": 1}
    assert len(calls) == 1


def test_sync_state_rows_returns_all_rows(market_db: MarketDb):
    with market_db.transact() as con:
        con.execute(
            "UPDATE sync_state SET last_status='ok', last_success_at=?, watermark=?,"
            " last_row_count=3 WHERE task_key='bs_calendar'",
            (datetime.now().astimezone().isoformat(), "2026-10-24"),
        )
        con.commit()

    rows = {row["task_key"]: row for row in market_db.sync_state_rows()}

    assert rows["bs_calendar"]["last_status"] == "ok"
    assert rows["bs_calendar"]["last_row_count"] == 3
    assert rows["bs_k_daily"]["last_status"] is None


def test_missing_db_returns_empty_overview_inputs(store):
    db = MarketDb(store)

    assert db.exists() is False
    assert db.table_counts(["security"]) == {}
    assert db.sync_state_rows() == ()
