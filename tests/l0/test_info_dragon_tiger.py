"""T-L0-010.3 + T-L0-012 测试：龙虎榜域采集与管理。

GWT 对照（T-L0-010.3 四条）：
- GWT-1 **两实体可分**——上榜记录与席位明细各自成表（粒度不同，不是主备）
- GWT-2 席位的两个源**可辨**——每行带 `source_id`，不冒充单一来源
- GWT-3 预过滤**不炸批**——全市场批次含未收录代码时批次仍成功
- GWT-4 **逐日增量**——单日窗口 + 水位为最近已拉交易日

T-L0-012 追加（F2 / F3 收口）：
- 官方两条席位路径**同任务写两表**（深交所钻取 / 上交所文本）——见
  `test_info_parsers.py` 的 `TestSzseSeatDrillDown` / `TestSseDailyDisclosure`
  与 `TestDragonTigerFetchers`；本文件覆盖**源登记与落表**一侧。
"""

from __future__ import annotations

import pytest

from info_helpers import UNMAPPED_CODE, FakeInfoFetcher

EM = "info_dragon_tiger_em"
SZSE = "info_dragon_tiger_szse"
SSE = "info_dragon_tiger_sse"
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
    def test_top_records_carry_source_id(self, db, sync_factory):
        """两个源都给上榜记录，逐行带 `source_id`——不冒充单一来源。"""
        sync = sync_factory(FakeInfoFetcher({
            EM: {"dragon_tiger": (_top("600000"),), "dragon_tiger_seat": ()},
            SZSE: {"dragon_tiger": (_top("000001", day=DAY, reason="日跌幅偏离"),),
                   "dragon_tiger_seat": ()},
        }))
        sync.run_task(EM, window=(DAY, DAY))
        sync.run_task(SZSE, window=(DAY, DAY))
        env = db.query("SELECT code, source_id FROM dragon_tiger ORDER BY code")
        assert [(r["code"], r["source_id"]) for r in env.data["rows"]] == [
            ("sh.600000", "eastmoney"), ("sz.000001", "szse")]

    def test_szse_coverage_is_declared_sz(self, db, sync_factory):
        """官方端点实测**仅深市** → `coverage=sz` 显式标注（不冒充沪深）。"""
        env = sync_factory(FakeInfoFetcher({
            SZSE: {"dragon_tiger": (_top("000001"),), "dragon_tiger_seat": ()},
        })).run_task(SZSE, window=(DAY, DAY))
        assert env.data["coverage"] == "sz" and env.data["source"] == "szse"
        assert env.data["role"] == "backup"

    def test_backup_does_not_overwrite_primary(self, db, sync_factory):
        """备胎 `INSERT OR IGNORE`：主源（东财）口径不被覆盖。"""
        sync = sync_factory(FakeInfoFetcher({
            EM: {"dragon_tiger": (_top("000001"),), "dragon_tiger_seat": ()},
            SZSE: {"dragon_tiger": (_top("000001", reason="官方口径原因"),),
                   "dragon_tiger_seat": ()},
        }))
        sync.run_task(EM, window=(DAY, DAY))
        sync.run_task(SZSE, window=(DAY, DAY))
        rows = db.query("SELECT source_id, reasons FROM dragon_tiger").data["rows"]
        assert len(rows) == 1 and rows[0]["source_id"] == "eastmoney"


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


class TestGwt5OfficialSeatSources:
    """T-L0-012：官方两条席位路径的**源登记与落表**（F2 / F3 收口）。

    抓取侧（``bz`` 契约钻取 / 定宽文本解析）由 `test_info_parsers.py` 覆盖；
    本类只钉**登记口径**与**落表**——席位与上榜记录**同源同次抓取**，故同一任务
    须声明写两表，否则 `dragon_tiger_seat` 会没有可判新鲜度的任务（05 的意图）。
    """

    @pytest.mark.parametrize("task_key,coverage,source_id", [
        (SZSE, "sz", "szse"), (SSE, "sh", "sse")])
    def test_official_source_declares_both_tables(self, task_key, coverage, source_id):
        from st_agent.l0.info import get_info_task

        spec = get_info_task(task_key)
        assert spec.written_tables == ("dragon_tiger", "dragon_tiger_seat")
        assert (spec.coverage, spec.role, spec.source_id) == (coverage, "backup", source_id)

    def test_sse_coverage_is_declared_sh(self, db, sync_factory):
        env = sync_factory(FakeInfoFetcher({
            SSE: {"dragon_tiger": (_top("600000"),),
                  "dragon_tiger_seat": (_seat("600000"),)},
        })).run_task(SSE, window=(DAY, DAY))
        assert env.data["coverage"] == "sh" and env.data["source"] == "sse"
        assert env.data["role"] == "backup"

    def test_sse_seats_land_in_the_seat_table(self, db, sync_factory):
        sync_factory(FakeInfoFetcher({
            SSE: {"dragon_tiger": (_top("600000"),),
                  "dragon_tiger_seat": (_seat("600000", seat="某沪市营业部"),)},
        })).run_task(SSE, window=(DAY, DAY))
        env = db.query("SELECT code, side, rank, seat_name, source_id"
                       " FROM dragon_tiger_seat")
        assert env.data["rows"] == [{"code": "sh.600000", "side": "buy", "rank": 1,
                                     "seat_name": "某沪市营业部", "source_id": "sse"}]
