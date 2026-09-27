"""T-L0-010.1 测试：信息面基件（多源映射 + 文本承载 + 文档取数面 + 主档预过滤）。

GWT 对照（任务文件 5 条）：
- GWT-1 多源映射**纯增量**——`indicator_dictionary` 主键与既有种子逐字段不变；
  新表可容纳同一 `indicator_key` 的多源行
- GWT-2 文本两段**同源同现**——表在而文件缺 → 损坏；文件在而表缺 → 孤儿
- GWT-3 取数面**不越沙箱**——正文一律经 `Store`，执行面**不直连文件系统**
- GWT-4 主档预过滤**不炸批**——未收录代码被过滤且计数可见；主档不可用**不**退化为全过滤
- GWT-5 **已知空集 ≠ 数据缺失**——过滤命中与源端无记录在载荷上可区分
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from info_helpers import UNMAPPED_CODE, FakeInfoFetcher, announcement_row
from st_agent.l0.info import (
    InfoSync,
    InfoValidationError,
    LocalDocSource,
    audit_docs,
    digest_id,
    filter_unmapped,
    master_codes,
    text_path,
    to_stock_id,
)
from st_agent.l0.info.docs import DocStore
from st_agent.l0.market import MarketDb

_INFO_DIR = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l0" / "info"


def _count(db, table: str) -> int:
    env = db.query(f"SELECT COUNT(*) AS n FROM {table}")
    return 0 if env.status == "empty" else env.data["rows"][0]["n"]


def _announcement_plan(rows) -> FakeInfoFetcher:
    return FakeInfoFetcher({"info_announcement_cninfo": {"announcement": tuple(rows)}})


def _register_cninfo(db) -> None:
    """登记巨潮源（``announcement.source_id`` 是硬外键，插入前须存在）。"""
    with db.transact() as con:
        con.execute("INSERT OR IGNORE INTO data_source(source_id, name, manual_ref)"
                    " VALUES ('cninfo', '巨潮资讯网', NULL)")
        con.commit()


class TestGwt1MultiSourceMap:
    def test_new_table_created_and_dictionary_untouched(self, db):
        assert "indicator_source_map" in db.tables()
        n_before = _count(db, "indicator_dictionary")
        keys_before = db.query("SELECT indicator_key FROM indicator_dictionary")
        with db.transact() as con:
            con.executemany("INSERT INTO data_source(source_id, name, manual_ref)"
                            " VALUES (?, ?, NULL)",
                            [("cninfo", "巨潮"), ("szse", "深交所")])
            for source, api, field in (("cninfo", "hisAnnouncement.query",
                                        "announcementTitle"),
                                       ("szse", "annList", "title")):
                con.execute(
                    "INSERT INTO indicator_source_map(indicator_key, source_id,"
                    " source_api, api_field) VALUES ('ann_title', ?, ?, ?)",
                    (source, api, field))
            con.commit()
        assert _count(db, "indicator_dictionary") == n_before
        after = db.query("SELECT indicator_key FROM indicator_dictionary")
        assert after.status == keys_before.status
        env = db.query("SELECT source_id FROM indicator_source_map"
                       " WHERE indicator_key='ann_title' ORDER BY source_id")
        assert [r["source_id"] for r in env.data["rows"]] == ["cninfo", "szse"]

    def test_same_source_duplicate_rejected(self, db):
        with db.transact() as con:
            con.execute("INSERT INTO data_source(source_id, name, manual_ref)"
                        " VALUES ('cninfo', '巨潮', NULL)")
            con.execute("INSERT INTO indicator_source_map VALUES"
                        " ('k', 'cninfo', 'api', 'field')")
            con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            with db.transact() as con:
                con.execute("INSERT INTO indicator_source_map VALUES"
                            " ('k', 'cninfo', 'api2', 'field2')")
                con.commit()


class TestGwt2TwoSegmentConsistency:
    def test_roundtrip_and_clean_audit(self, store):
        docs = DocStore(store)
        path = docs.write("announcement", "sh.600000", "2026-09-20", "ann_abc", "公告正文")
        assert path == text_path("announcement", "sh.600000", "2026-09-20", "ann_abc")
        assert docs.read(path) == "公告正文"
        assert audit_docs(store, [path]).is_consistent

    def test_missing_file_is_corruption(self, store):
        """GWT-2a：**表在而文件缺 → 损坏**（不被当作「无正文」）。"""
        docs = DocStore(store)
        path = docs.write("announcement", "sh.600000", "2026-09-20", "ann_x", "正文")
        store.delete("data_cache", path)
        audit = audit_docs(store, [path])
        assert audit.missing_file == (path,)
        assert not audit.is_consistent

    def test_orphan_file_is_recoverable(self, store):
        """GWT-2b：**文件在而表缺 → 孤儿**（可回收的残留）。"""
        docs = DocStore(store)
        path = docs.write("announcement", "sh.600000", "2026-09-20", "ann_y", "正文")
        assert audit_docs(store, []).orphan_file == (path,)
        docs.delete(path)
        assert audit_docs(store, []).is_consistent

    def test_path_deterministic_and_bounded(self):
        same = text_path("announcement", "sh.600000", "2026-09-20", "ann_1")
        assert same == text_path("announcement", "sh.600000", "2026-09-20", "ann_1")
        assert same != text_path("announcement", "sh.600000", "2026-09-20", "ann_2")
        assert len(text_path("sentiment_qa", "sz.000001", "2026-09-20",
                             "qa_" + "a" * 20)) < 128
        with pytest.raises(InfoValidationError):
            text_path("announcement", "sh.600000", "2026-09-20", "a/../b")
        with pytest.raises(InfoValidationError):
            text_path("unknown_domain", "sh.600000", "2026-09-20", "x")

    def test_digest_id_deterministic_and_bounded(self):
        first = digest_id("ann", "sh.600000", "标题", "2026-09-20")
        assert first == digest_id("ann", "sh.600000", "标题", "2026-09-20")
        assert first != digest_id("ann", "sh.600000", "另一标题", "2026-09-20")
        assert first.startswith("ann_") and len(first) == 24 <= 128


class TestGwt3DocSourceDoesNotBypassStore:
    def test_execution_face_has_no_direct_filesystem_access(self):
        """GWT-3：执行面**不直连文件系统**——正文一律经 ``Store``（AST 断言）。"""
        offenders: list[str] = []
        for module in sorted(_INFO_DIR.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"), module.name)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "open"):
                    offenders.append(f"{module.name}:{node.lineno} open()")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = ([a.name for a in node.names]
                             if isinstance(node, ast.Import) else [node.module or ""])
                    if any((n or "").split(".")[0] in ("pathlib", "shutil", "tempfile")
                           for n in names):
                        offenders.append(f"{module.name}:{node.lineno} {names}")
        assert offenders == [], f"执行面出现直连文件系统的用法：{offenders}"

    def test_read_doc_via_source(self, db, store):
        _register_cninfo(db)
        docs = DocStore(store)
        path = docs.write("announcement", "sh.600000", "2026-09-20", "ann_z", "正文内容")
        with db.transact() as con:
            con.execute(
                "INSERT INTO announcement(announcement_id, code, title, pub_date,"
                " file_path, coverage, source_id) VALUES"
                " ('ann_z', 'sh.600000', '标题', '2026-09-20', ?, 'all', 'cninfo')",
                (path,))
            con.commit()
        env = LocalDocSource(db, store).read_doc("ann_z")
        assert env.status == "ok" and env.data["text"] == "正文内容"

    def test_missing_file_read_is_unavailable_not_empty(self, db, store):
        """表在而文件缺 → `unavailable`（**不**返回空串冒充）。"""
        _register_cninfo(db)
        with db.transact() as con:
            con.execute(
                "INSERT INTO announcement(announcement_id, code, title, pub_date,"
                " file_path, coverage, source_id) VALUES"
                " ('ann_gone', 'sh.600000', '标题', '2026-09-20',"
                " 'info/announcement/sh.600000/2026-09-20/ann_gone.txt', 'all', 'cninfo')")
            con.commit()
        env = LocalDocSource(db, store).read_doc("ann_gone")
        assert env.status == "unavailable" and "损坏" in (env.reason or "")

    def test_unknown_doc_is_empty(self, db, store):
        assert LocalDocSource(db, store).read_doc("ann_nope").status == "empty"


class TestGwt4PrefilterDoesNotBlowUpBatch:
    def test_unmapped_filtered_with_visible_count(self, db, sync_factory):
        fetcher = _announcement_plan([
            announcement_row("600000"),
            announcement_row("000001", title="关于高管变动的公告", pub_date="2026-09-21"),
            announcement_row("300750", title="业绩预告", pub_date="2026-09-22"),
            announcement_row("920002", title="北交所公告", pub_date="2026-09-22"),
        ])
        sync = sync_factory(fetcher)
        env = sync.run_task("info_announcement_cninfo",
                            window=("2026-09-01", "2026-09-30"))
        assert env.status == "ok"  # 批次**不失败**
        assert env.data["rows"] == 3
        assert env.data["filtered_unmapped"]["rows_filtered"] == 1
        assert env.data["filtered_unmapped"]["sample"] == [UNMAPPED_CODE]
        assert _count(db, "announcement") == 3

    def test_master_unavailable_does_not_filter_everything(self, db, sync_factory):
        """灾难分支：主档不可用 → `unavailable`，**不得**以空集过滤全部。"""
        sync = sync_factory(_announcement_plan([announcement_row("600000")]))
        with db.transact() as con:
            con.execute("DROP TABLE security")
            con.commit()
        env = sync.run_task("info_announcement_cninfo",
                            window=("2026-09-01", "2026-09-30"))
        assert env.status == "unavailable" and "主档" in (env.reason or "")

    def test_master_codes_none_when_db_missing(self, store):
        assert master_codes(MarketDb(store)) is None  # 库未建 → None（非空集）

    def test_filter_counts_distinct_codes(self):
        rows = [{"code": "sh.600000"}, {"code": UNMAPPED_CODE},
                {"code": UNMAPPED_CODE}, {"code": "bj.830001"}]
        kept, count = filter_unmapped(rows, lambda r: r["code"],
                                      frozenset({"sh.600000"}))
        assert kept == [{"code": "sh.600000"}]
        assert count.rows_filtered == 3 and count.codes_distinct == 2
        assert set(count.sample) == {UNMAPPED_CODE, "bj.830001"}
        assert not count.is_clean and "过滤" in count.describe()


class TestGwt5KnownEmptyVsMissing:
    def test_all_rows_filtered_yields_empty_with_filter_note(self, db, sync_factory):
        sync = sync_factory(_announcement_plan([
            announcement_row("920002", title="北交所公告"),
            announcement_row("920002", title="另一条", pub_date="2026-09-21"),
        ]))
        env = sync.run_task("info_announcement_cninfo",
                            window=("2026-09-01", "2026-09-30"))
        assert env.status == "empty" and "过滤" in (env.reason or "")
        assert _count(db, "announcement") == 0

    def test_source_empty_yields_empty_without_filter_note(self, db, sync_factory):
        env = sync_factory(FakeInfoFetcher({})).run_task(
            "info_announcement_cninfo", window=("2026-09-01", "2026-09-30"))
        assert env.status == "empty" and "过滤" not in (env.reason or "")

    def test_filtered_run_marks_partial_not_ok(self, db, sync_factory):
        sync_factory(_announcement_plan([
            announcement_row("600000"), announcement_row("920002")])).run_task(
                "info_announcement_cninfo", window=("2026-09-01", "2026-09-30"))
        env = db.query("SELECT last_status FROM sync_state"
                       " WHERE task_key='info_announcement_cninfo'")
        assert env.data["rows"][0]["last_status"] == "partial"


class TestCodeNormalization:
    def test_exchange_prefix_rules(self):
        assert to_stock_id("600000") == "sh.600000"
        assert to_stock_id("000001") == "sz.000001"
        assert to_stock_id("300750") == "sz.300750"
        assert to_stock_id("688017") == "sh.688017"
        assert to_stock_id("000001", "sz") == "sz.000001"
        assert to_stock_id("SH.600000") == "sh.600000"

    def test_north_exchange_stays_bj_not_mislabelled(self):
        """北交所号段标 ``bj.``——**不得**误标成 ``sh.``（否则掩盖「不在主档」）。"""
        for raw in ("920002", "830001", "870001", "880001", "430001"):
            assert to_stock_id(raw).startswith("bj."), raw


class TestSetupRegistration:
    def test_sources_and_tasks_registered(self, db, sync_factory):
        from st_agent.l0.info import INFO_TASKS

        sync_factory(FakeInfoFetcher({}))
        sources = [r["source_id"] for r in
                   db.query("SELECT source_id FROM data_source").data["rows"]]
        assert "baostock" in sources  # 既有源不被破坏
        assert {"cninfo", "szse", "sse", "eastmoney",
                "cninfo_irm", "sse_e", "ths"} <= set(sources)
        env = db.query("SELECT count(*) AS n FROM sync_state"
                       " WHERE task_key LIKE 'info_%'")
        # 与注册表对账，不写死数字——注册表增删任务时本断言随动（口径：setup 落全量）
        assert env.data["rows"][0]["n"] == len(INFO_TASKS)

    def test_setup_idempotent(self, sync_factory):
        sync = sync_factory(FakeInfoFetcher({}))
        assert sync.setup().status == "ok" and sync.setup().status == "ok"

    def test_setup_requires_market_db(self, store, gateway):
        fresh = InfoSync(MarketDb(store), gateway, FakeInfoFetcher({}))
        assert fresh.setup().status == "unavailable"


class TestGatewayAudit:
    def test_audit_records_real_host_not_source_id(self, db, gateway, sync_factory):
        """审计的 ``target_host`` 必须是**真实主机名**——不得拿 ``source_id`` 冒充。

        回归：``cninfo_irm`` / ``sse_e`` 这类含下划线的源标识不是合法主机名，
        早期实现直接把它当 host 传给网关，被抓成 ``failed``。
        """
        sync_factory(FakeInfoFetcher({
            "info_sentiment_qa_irm": {"sentiment_qa": ()}})).run_task(
                "info_sentiment_qa_irm", window=("2026-09-01", "2026-09-30"))
        hosts = {e.target_host for e in gateway.query()}
        assert hosts == {"irm.cninfo.com.cn"}
        assert "cninfo_irm" not in hosts

    def test_every_source_host_is_a_legal_hostname(self):
        from st_agent.l0.info import INFO_SOURCES

        for source in INFO_SOURCES:
            assert source.hosts, source.source_id
            for host in source.hosts:
                assert "." in host and "_" not in host, (source.source_id, host)
