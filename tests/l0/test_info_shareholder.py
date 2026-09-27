"""T-L0-010.4 测试：股东变化域采集与管理（T-L0-014 扩为**主备两源**）。

GWT 对照（T-L0-010.4 任务文件 4 条 + T-L0-014 追加）：
- GWT-1 季度序列可查——按 `stat_date` 读股东户数与环比
- GWT-2 单源不可用**不编造**——`unavailable` 信封（**不**返回空表冒充「无数据」）
- GWT-3 长期不可用**可见**——陈旧状态可判定，且与「正常但无更新」区分
- GWT-4 预过滤不炸批
- T-L0-014 GWT-1/2/3——备胎（巨潮）补齐历史季末；同键**主源胜出**（`backup` 让位）
"""

from __future__ import annotations

from info_helpers import UNMAPPED_CODE, FakeInfoFetcher

from st_agent.l0.info import INFO_TASKS

EM = "info_shareholder_num_em"
CN = "info_shareholder_num_cninfo"
WINDOW = ("2025-01-01", "2026-09-25")


def _row(code: str = "600000", *, stat: str = "2026-06-30", holders: int = 120000,
         ratio: float = -3.5) -> dict:
    return {"code": code, "stat_date": stat, "holder_num": holders,
            "change_num": -4350, "change_ratio": ratio, "avg_shares": 12345.6}


def _plan(rows) -> FakeInfoFetcher:
    return FakeInfoFetcher({EM: {"shareholder_num": tuple(rows)}})


def _cn_plan(rows) -> FakeInfoFetcher:
    return FakeInfoFetcher({CN: {"shareholder_num": tuple(rows)}})


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

    def test_domain_registers_primary_and_backup(self, db, sync_factory):
        """T-L0-014：该域由「单源」改**主备两源**（原 `sole` 断言的前提已推翻）。"""
        sync_factory(FakeInfoFetcher({}))
        env = db.query("SELECT task_key FROM sync_state"
                       " WHERE table_name = 'shareholder_num' ORDER BY task_key")
        assert [r["task_key"] for r in env.data["rows"]] == [CN, EM]
        roles = {t.task_key: t.role for t in INFO_TASKS
                 if t.domain == "shareholder_num"}
        assert roles == {EM: "primary", CN: "backup"}

    def test_primary_role_uses_replace(self, db, sync_factory):
        sync_factory(_plan([_row(holders=1)])).run_task(EM, window=WINDOW)
        sync_factory(_plan([_row(holders=2)])).run_task(EM, window=WINDOW)
        env = db.query("SELECT holder_num FROM shareholder_num")
        assert env.data["rows"][0]["holder_num"] == 2

    def test_backup_fills_history_without_overwriting_primary(self, db, sync_factory):
        """备胎补齐主源拿不到的更早季末，且**同键让位**（东财值胜出）。"""
        sync_factory(_plan([_row(stat="2026-06-30", holders=120000)])).run_task(
            EM, window=WINDOW)
        sync_factory(_cn_plan([
            _row(stat="2026-03-31", holders=118888),   # 主源没有 → 补齐
            _row(stat="2026-06-30", holders=999999),   # 同键 → 不覆盖
        ])).run_task(CN, window=WINDOW)
        env = db.query("SELECT stat_date, holder_num FROM shareholder_num"
                       " ORDER BY stat_date")
        assert [(r["stat_date"], r["holder_num"]) for r in env.data["rows"]] == [
            ("2026-03-31", 118888), ("2026-06-30", 120000)]

    def test_backup_rerun_does_not_change_existing_rows(self, db, sync_factory):
        """备胎行写一次不再更新（`IGNORE`）——源端修正不回流（任务 A4）。"""
        sync_factory(_cn_plan([_row(stat="2026-03-31", holders=118888)])).run_task(
            CN, window=WINDOW)
        sync_factory(_cn_plan([_row(stat="2026-03-31", holders=111111)])).run_task(
            CN, window=WINDOW)
        env = db.query("SELECT holder_num FROM shareholder_num")
        assert env.data["rows"][0]["holder_num"] == 118888


class TestGwt3LongUnavailabilityVisible:
    def test_stale_state_visible_via_freshness(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({}, unavailable_on=(EM,)))
        sync.run_task(EM, window=WINDOW)  # 失败留痕
        bad = [r for r in db.freshness() if r.get("task_key") == EM]
        assert bad and bad[0]["last_status"] == "failed"
        verdict = sync.freshness_verdict("shareholder_num")
        assert verdict.stale is True and "shareholder_num" in verdict.detail

    def test_healthy_domain_is_not_stale(self, db, sync_factory):
        """两源都 ok 才算健康（05「一域多源取各源水位的保守下界」）。"""
        sync = sync_factory(_plan([_row()]))
        sync.run_task(EM, window=WINDOW)
        sync.run_task(CN, window=WINDOW)
        assert sync.freshness_verdict("shareholder_num").stale is False

    def test_never_run_backup_keeps_domain_stale(self, db, sync_factory):
        """备胎**未跑**时域判 stale——多源取保守下界，不因主源新而掩盖备胎缺口。"""
        sync = sync_factory(_plan([_row()]))
        sync.run_task(EM, window=WINDOW)
        verdict = sync.freshness_verdict("shareholder_num")
        assert verdict.stale is True and CN in verdict.detail

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
