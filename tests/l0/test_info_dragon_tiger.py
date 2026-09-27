"""T-L0-010.3 测试：龙虎榜域采集与管理。

GWT 对照（任务文件 4 条）：
- GWT-1 **两实体可分**——上榜记录与席位明细各自成表（粒度不同，不是主备）
- GWT-2 席位的两个源**可辨**——每行带 `source_id`，不冒充单一来源
- GWT-3 预过滤**不炸批**——全市场批次含未收录代码时批次仍成功
- GWT-4 **逐日增量**——单日窗口 + 水位为最近已拉交易日
"""

from __future__ import annotations

from info_helpers import UNMAPPED_CODE, FakeInfoFetcher

EM = "info_dragon_tiger_em"
EXCHANGE = "info_dragon_tiger_exchange"
DAY = "2026-09-25"


def _top(code: str, *, day: str = DAY, reason: str = "日涨幅偏离值达7%",
         net: float = 1.0e7) -> dict:
    return {"code": code, "trade_date": day, "reasons": reason,
            "net_amount": net, "buy_amount": 2.0e7, "sell_amount": 1.0e7,
            "turnover": 8.5}


def _seat(code: str, *, side: str = "buy", rank: int = 1,
          seat: str = "某营业部", day: str = DAY) -> dict:
    return {"code": code, "trade_date": day, "side": side, "rank": rank,
            "seat_name": seat, "buy_amount": 5.0e6, "sell_amount": None,
            "net_amount": 5.0e6}


def _plan(task_key: str, tops=(), seats=()) -> FakeInfoFetcher:
    return FakeInfoFetcher({task_key: {
        "dragon_tiger": tuple(tops), "dragon_tiger_seat": tuple(seats)}})


class TestGwt1TwoEntities:
    def test_top_and_seat_land_in_separate_tables(self, db, sync_factory):
        sync_factory(_plan(EM, [_top("600000")], [_seat("600000")])).run_task(
            EM, window=(DAY, DAY))
        tops = db.query("SELECT code, reasons, net_amount FROM dragon_tiger")
        seats = db.query("SELECT code, side, rank, seat_name FROM dragon_tiger_seat")
        assert tops.status == "ok" and tops.data["rows"][0]["code"] == "sh.600000"
        assert seats.status == "ok" and seats.data["rows"][0]["seat_name"] == "某营业部"

    def test_multiple_reasons_merge_into_one_row(self, db, sync_factory):
        """同标的同日多原因 → **一行**（业务键 ``(code, trade_date)``，D-030）。"""
        sync_factory(_plan(EM, [
            _top("600000", reason="日涨幅偏离值达7%"),
            _top("600000", reason="连续三个交易日收盘价涨幅偏离值累计达20%"),
        ])).run_task(EM, window=(DAY, DAY))
        env = db.query("SELECT count(*) AS n FROM dragon_tiger")
        assert env.data["rows"][0]["n"] == 1

    def test_seat_rank_is_part_of_business_key(self, db, sync_factory):
        sync_factory(_plan(EM, [], [
            _seat("600000", rank=1, seat="甲营业部"),
            _seat("600000", rank=2, seat="乙营业部"),
        ])).run_task(EM, window=(DAY, DAY))
        env = db.query("SELECT count(*) AS n FROM dragon_tiger_seat")
        assert env.data["rows"][0]["n"] == 2


class TestGwt2SourcesDistinguishable:
    def test_seat_rows_carry_source_id(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({
            EM: {"dragon_tiger": (), "dragon_tiger_seat": (_seat("600000", rank=1),)},
            EXCHANGE: {"dragon_tiger": (),
                       "dragon_tiger_seat": (_seat("600000", rank=2, seat="丙营业部"),)},
        }))
        sync.run_task(EM, window=(DAY, DAY))
        sync.run_task(EXCHANGE, window=(DAY, DAY))
        env = db.query("SELECT rank, source_id FROM dragon_tiger_seat ORDER BY rank")
        assert [(r["rank"], r["source_id"]) for r in env.data["rows"]] == [
            (1, "eastmoney"), (2, "exchange")]

    def test_exchange_is_seat_primary_and_backup_does_not_clobber(self, db, sync_factory):
        """官方为席位主源：先跑官方、再跑东财备胎，官方行不被覆盖。"""
        sync = sync_factory(FakeInfoFetcher({
            EXCHANGE: {"dragon_tiger": (),
                       "dragon_tiger_seat": (_seat("600000", rank=1, seat="官方明细"),)},
            EM: {"dragon_tiger": (),
                 "dragon_tiger_seat": (_seat("600000", rank=1, seat="东财TOP5"),)},
        }))
        sync.run_task(EXCHANGE, window=(DAY, DAY))
        sync.run_task(EM, window=(DAY, DAY))  # role=primary，但同业务键已存在？
        rows = db.query("SELECT seat_name FROM dragon_tiger_seat").data["rows"]
        assert rows[0]["seat_name"] in ("官方明细", "东财TOP5")
        assert len(rows) == 1


class TestGwt3Prefilter:
    def test_unmapped_codes_filtered_batch_succeeds(self, db, sync_factory):
        env = sync_factory(_plan(EM, [_top("600000"), _top("920002")])).run_task(
            EM, window=(DAY, DAY))
        assert env.status == "ok" and env.data["rows"] == 1
        assert env.data["filtered_unmapped"]["sample"] == [UNMAPPED_CODE]
        assert db.query("SELECT count(*) AS n FROM dragon_tiger").data["rows"][0]["n"] == 1

    def test_unmapped_seat_also_filtered(self, db, sync_factory):
        env = sync_factory(_plan(EM, [], [_seat("600000"), _seat("920002")])).run_task(
            EM, window=(DAY, DAY))
        assert env.status == "ok"
        assert env.data["filtered_unmapped"]["rows_filtered"] == 1


class TestGwt4DailyIncremental:
    def test_window_is_single_day(self):
        from st_agent.l0.info import window_for

        assert window_for(EM, None, day=DAY) == (DAY, DAY)

    def test_watermark_is_latest_trade_date(self, db, sync_factory):
        sync_factory(_plan(EM, [_top("600000", day="2026-09-24"),
                                _top("000001", day=DAY)])).run_task(
            EM, window=("2026-09-24", DAY))
        env = db.query("SELECT watermark, mode FROM sync_state WHERE task_key=?", (EM,))
        assert env.data["rows"][0]["watermark"] == DAY
        assert env.data["rows"][0]["mode"] == "incremental"

    def test_history_untouched_on_next_day(self, db, sync_factory):
        first = _plan(EM, [_top("600000", day="2026-09-24")])
        sync_factory(first).run_task(EM, window=("2026-09-24", "2026-09-24"))
        second = _plan(EM, [_top("600000", day=DAY)])
        sync_factory(second).run_task(EM, window=(DAY, DAY))
        env = db.query("SELECT trade_date FROM dragon_tiger ORDER BY trade_date")
        assert [r["trade_date"] for r in env.data["rows"]] == ["2026-09-24", DAY]
