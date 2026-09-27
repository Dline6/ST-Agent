"""T-L0-010.4 测试：股东变化域采集与管理。

GWT 对照（任务文件 4 条）：
- GWT-1 季度序列可查——按 `stat_date` 读股东户数与环比
- GWT-2 单源不可用**不编造**——`unavailable` 信封（**不**返回空表冒充「无数据」）
- GWT-3 长期不可用**可见**——陈旧状态可判定，且与「正常但无更新」区分
- GWT-4 预过滤不炸批
"""

from __future__ import annotations

from info_helpers import UNMAPPED_CODE, FakeInfoFetcher

EM = "info_shareholder_num_em"
WINDOW = ("2025-01-01", "2026-09-25")


def _row(code: str = "600000", *, stat: str = "2026-06-30", holders: int = 120000,
         ratio: float = -3.5) -> dict:
    return {"code": code, "stat_date": stat, "holder_num": holders,
            "change_num": -4350, "change_ratio": ratio, "avg_shares": 12345.6}


def _plan(rows) -> FakeInfoFetcher:
    return FakeInfoFetcher({EM: {"shareholder_num": tuple(rows)}})


class TestGwt1QuarterlySeries:
    def test_series_queryable_by_stat_date(self, db, sync_factory):
        sync_factory(_plan([
            _row(stat="2026-03-31", holders=124350, ratio=1.2),
            _row(stat="2026-06-30", holders=120000, ratio=-3.5),
        ])).run_task(EM, window=WINDOW)
        env = db.query("SELECT stat_date, holder_num, change_ratio FROM shareholder_num"
                       " ORDER BY stat_date")
        assert env.status == "ok"
        assert [r["stat_date"] for r in env.data["rows"]] == ["2026-03-31", "2026-06-30"]
        assert env.data["rows"][1]["change_ratio"] == -3.5

    def test_upsert_window_reevaluates_same_quarter(self, db, sync_factory):
        """``upsert_window``：同 ``(code, stat_date)`` 重拉覆盖（源端修正自愈）。"""
        sync_factory(_plan([_row(holders=120000)])).run_task(EM, window=WINDOW)
        sync_factory(_plan([_row(holders=118888)])).run_task(EM, window=WINDOW)
        env = db.query("SELECT count(*) AS n, max(holder_num) AS h FROM shareholder_num")
        assert env.data["rows"][0] == {"n": 1, "h": 118888}


class TestGwt2SingleSourceNoFabrication:
    def test_unavailable_source_yields_unavailable(self, db, gateway, sync_factory):
        env = sync_factory(FakeInfoFetcher({}, unavailable_on=(EM,))).run_task(
            EM, window=WINDOW)
        assert env.status == "unavailable"
        # 表仍为空——**不**插入占位行冒充「无数据」
        assert db.query("SELECT count(*) AS n FROM shareholder_num"
                        ).data["rows"][0]["n"] == 0

    def test_no_backup_task_registered(self, db, sync_factory):
        """单源口径：该域**只有**一条任务（无备胎可登记）。"""
        sync_factory(FakeInfoFetcher({}))
        env = db.query("SELECT count(*) AS n FROM sync_state"
                       " WHERE table_name = 'shareholder_num'")
        assert env.data["rows"][0]["n"] == 1

    def test_sole_role_uses_replace(self, db, sync_factory):
        sync_factory(_plan([_row(holders=1)])).run_task(EM, window=WINDOW)
        sync_factory(_plan([_row(holders=2)])).run_task(EM, window=WINDOW)
        env = db.query("SELECT holder_num FROM shareholder_num")
        assert env.data["rows"][0]["holder_num"] == 2


class TestGwt3LongUnavailabilityVisible:
    def test_stale_state_visible_via_freshness(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({}, unavailable_on=(EM,)))
        sync.run_task(EM, window=WINDOW)  # 失败留痕
        bad = [r for r in db.freshness() if r.get("task_key") == EM]
        assert bad and bad[0]["last_status"] == "failed"
        verdict = sync.freshness_verdict("shareholder_num")
        assert verdict.stale is True and "shareholder_num" in verdict.detail

    def test_healthy_domain_is_not_stale(self, db, sync_factory):
        sync = sync_factory(_plan([_row()]))
        sync.run_task(EM, window=WINDOW)
        assert sync.freshness_verdict("shareholder_num").stale is False

    def test_watermark_is_latest_stat_date(self, db, sync_factory):
        sync_factory(_plan([_row(stat="2026-03-31"), _row(stat="2026-06-30")])).run_task(
            EM, window=WINDOW)
        env = db.query("SELECT watermark FROM sync_state WHERE task_key=?", (EM,))
        assert env.data["rows"][0]["watermark"] == "2026-06-30"


class TestGwt4Prefilter:
    def test_unmapped_filtered_batch_succeeds(self, db, sync_factory):
        env = sync_factory(_plan([_row("600000"), _row("920002")])).run_task(
            EM, window=WINDOW)
        assert env.status == "ok" and env.data["rows"] == 1
        assert env.data["filtered_unmapped"]["sample"] == [UNMAPPED_CODE]
