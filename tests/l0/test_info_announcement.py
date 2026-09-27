"""T-L0-010.2 测试：公告域采集与管理（**本叶子收口 [L1 册 `C1`]**）。

GWT 对照（任务文件 5 条）：
- GWT-1 端到端可查——带 `announcement_id` 与可读正文的记录
- GWT-2 **关键词可评估**——L0 侧前提：公告表可按关键词查询（L1 侧消费面见
  ``tests/l1/test_official_ambient.py::TestC1KeywordCondition``）
- GWT-3 跨源去重——同一公告来自两条源 → **一行**；**备胎不覆盖主源**
- GWT-4 覆盖显式——`coverage` 列与载荷标注，不得让「换了源」看起来像「没有数据」
- GWT-5 不可逆缺口可判——档案型空窗标注 `recoverable=False`
"""

from __future__ import annotations

from info_helpers import FakeInfoFetcher, announcement_row
from st_agent.l0.info import LocalDocSource

CNINFO = "info_announcement_cninfo"
SZSE = "info_announcement_szse"
EM = "info_announcement_em"
WINDOW = ("2026-09-01", "2026-09-30")


def _plan(task_key: str, rows) -> FakeInfoFetcher:
    return FakeInfoFetcher({task_key: {"announcement": tuple(rows)}})


class TestGwt1EndToEndQueryable:
    def test_rows_and_text_readable(self, db, store, sync_factory):
        sync_factory(_plan(CNINFO, [
            announcement_row("600000", title="关于回购公司股份的公告"),
        ])).run_task(CNINFO, window=WINDOW)
        env = db.query("SELECT announcement_id, code, title, pub_date, file_path,"
                       " coverage, source_id FROM announcement")
        assert env.status == "ok"
        row = env.data["rows"][0]
        assert row["code"] == "sh.600000" and row["coverage"] == "all"
        assert row["source_id"] == "cninfo"
        doc = LocalDocSource(db, store).read_doc(row["announcement_id"])
        assert doc.status == "ok" and doc.data["text"] == "回购正文……"

    def test_two_segments_consistent_after_sync(self, db, store, sync_factory):
        sync = sync_factory(_plan(CNINFO, [announcement_row("600000")]))
        sync.run_task(CNINFO, window=WINDOW)
        assert sync.audit_docs().is_consistent


class TestGwt2KeywordQueryable:
    def test_keyword_like_query_hits_announcement(self, db, sync_factory):
        """GWT-2（L0 侧前提）：公告表支持按关键词检索——「关键词」条件的数据面在此。"""
        sync_factory(_plan(CNINFO, [
            announcement_row("600000", title="关于回购公司股份的公告"),
            announcement_row("000001", title="关于高管变动的公告", pub_date="2026-09-21"),
        ])).run_task(CNINFO, window=WINDOW)
        env = db.query("SELECT code FROM announcement WHERE title LIKE ?",
                       ("%回购%",))
        assert env.status == "ok"
        assert [r["code"] for r in env.data["rows"]] == ["sh.600000"]

    def test_search_docs_returns_matching_metadata(self, db, store, sync_factory):
        sync_factory(_plan(CNINFO, [
            announcement_row("600000", title="关于回购公司股份的公告"),
        ])).run_task(CNINFO, window=WINDOW)
        env = LocalDocSource(db, store).search_docs("回购")
        assert env.status == "ok"
        assert env.data["rows"][0]["code"] == "sh.600000"


class TestGwt3CrossSourceDedup:
    def test_same_announcement_from_two_sources_is_one_row(self, db, sync_factory):
        row = announcement_row("600000", title="关于回购公司股份的公告")
        sync = sync_factory(FakeInfoFetcher({
            CNINFO: {"announcement": (row,)},
            SZSE: {"announcement": (row,)},
        }))
        sync.run_task(CNINFO, window=WINDOW)
        sync.run_task(SZSE, window=WINDOW)
        env = db.query("SELECT count(*) AS n FROM announcement")
        assert env.data["rows"][0]["n"] == 1  # 同业务键 → 同 ID → 一行

    def test_backup_does_not_overwrite_primary(self, db, sync_factory):
        """GWT-3（让位）：备胎 ``INSERT OR IGNORE``——主源口径不被覆盖。"""
        row = announcement_row("600000")
        sync = sync_factory(FakeInfoFetcher({
            CNINFO: {"announcement": (row,)},
            SZSE: {"announcement": (row,)},
        }))
        sync.run_task(CNINFO, window=WINDOW)
        sync.run_task(SZSE, window=WINDOW)
        env = db.query("SELECT coverage, source_id FROM announcement")
        got = env.data["rows"][0]
        assert (got["coverage"], got["source_id"]) == ("all", "cninfo")


class TestGwt4CoverageExplicit:
    def test_backup_row_carries_its_own_coverage(self, db, sync_factory):
        """GWT-4：主源不可用而降级分片备胎时，覆盖范围**显式可见**。"""
        sync_factory(_plan(SZSE, [announcement_row("000001")])).run_task(
            SZSE, window=WINDOW)
        env = db.query("SELECT coverage, source_id FROM announcement")
        got = env.data["rows"][0]
        assert (got["coverage"], got["source_id"]) == ("sz", "szse")

    def test_payload_reports_coverage_and_source(self, db, sync_factory):
        sync = sync_factory(_plan(EM, [announcement_row("600000")]))
        env = sync.run_task(EM, window=WINDOW)
        assert env.data["coverage"] == "sh" and env.data["source"] == "eastmoney"
        assert env.data["role"] == "backup"


class TestGwt5IrreversibleGap:
    def test_archival_gap_flagged_not_recoverable(self, db, sync_factory):
        """GWT-5：档案型域的空窗标 ``recoverable=False``（区分「等下次」与「拿不回」）。"""
        fetcher = FakeInfoFetcher({CNINFO: {"announcement": ()}},
                                  unavailable_on=(SZSE,))
        sync = sync_factory(fetcher)
        sync.run_task(SZSE, window=WINDOW)  # 源不可达 → 该任务留下失败痕迹
        gaps = sync.archival_gaps("announcement")
        assert gaps, "公告域为档案型，空窗应被列出"
        assert all(g["recoverable"] is False and g["archival"] is True for g in gaps)
        assert {g["task_key"] for g in gaps} <= {CNINFO, SZSE, EM}

    def test_non_archival_domain_has_no_gap_report(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({}))
        assert sync.archival_gaps("dragon_tiger") == ()

    def test_unavailable_source_yields_unavailable_envelope(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({}, unavailable_on=(CNINFO,)))
        env = sync.run_task(CNINFO, window=WINDOW)
        assert env.status == "unavailable"

    def test_failed_fetch_yields_failed_not_silent(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({}, fail_on=(CNINFO,)))
        env = sync.run_task(CNINFO, window=WINDOW)
        assert env.status == "failed" and env.log_ref


class TestWatermark:
    def test_watermark_is_max_pub_date(self, db, sync_factory):
        sync = sync_factory(_plan(CNINFO, [
            announcement_row("600000", pub_date="2026-09-20"),
            announcement_row("000001", pub_date="2026-09-25",
                             title="关于高管变动的公告"),
        ]))
        sync.run_task(CNINFO, window=WINDOW)
        env = db.query("SELECT watermark FROM sync_state WHERE task_key=?", (CNINFO,))
        assert env.data["rows"][0]["watermark"] == "2026-09-25"

    def test_window_continues_from_watermark(self, db, sync_factory):
        from st_agent.l0.info import window_for

        assert window_for(CNINFO, "2026-09-25")[0] == "2026-09-26"
        assert window_for(CNINFO, None)[0] < "2026-09-26"  # 首次回看基线
