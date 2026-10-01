"""T-L2-005 · 券商账单导入初始画像（04 §8 末条；GWT-1..5）。"""

from __future__ import annotations

import io
import zipfile

import pytest
from memory_helpers import NOW

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    STATEMENT_IMPORT_PREFIX,
    ColumnMapping,
    MemoryGraph,
    MemoryValidationError,
    MemoryWriter,
    StatementImporter,
    StatementTable,
    checked_mapping,
    new_statement_import_id,
    normalize_stock_id,
    read_table,
)

CSV_TEXT = (
    "对账单（示例券商导出，首行为说明）\n"          # 前导说明行：表头不在第一行
    "证券代码,证券名称,买卖方向,成交日期,成交数量,成交价格\n"
    "600000,浦发银行,买入,2026-09-01,100,10.5\n"
    "000001,平安银行,卖出,2026-09-02,200,12.0\n"
    "600000,浦发银行,买入,2026-09-03,50,10.6\n"
)

MAPPING = {
    "stock_code": "证券代码", "direction": "买卖方向", "date": "成交日期",
    "quantity": "成交数量", "price": "成交价格",
}


def table(text: str = CSV_TEXT) -> StatementTable:
    return read_table(text.encode("utf-8"))


def _xlsx_bytes(sheet_xml: str, shared_xml: str | None = None) -> bytes:
    """把最小 xlsx（zip + XML）打包成字节，供 ``read_table`` 读取。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if shared_xml is not None:
            archive.writestr("xl/sharedStrings.xml", shared_xml)
    return buffer.getvalue()


_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


# ───────────────────────── GWT-1 · 列映射契约与校验 ─────────────────────────


class TestGwt1ColumnMapping:
    """GWT-1：映射按列名给定、表头行自动定位、必需列缺失即报缺。"""

    def test_header_row_is_located_after_a_preamble(self) -> None:
        resolved = checked_mapping(ColumnMapping(fields=MAPPING), table())
        assert resolved.header_index == 1
        assert resolved.columns["stock_code"] == 0
        assert resolved.columns["price"] == 5

    def test_bare_mapping_dict_is_accepted(self) -> None:
        assert checked_mapping(MAPPING, table()).header_index == 1

    def test_unknown_source_field_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="未知源字段"):
            checked_mapping({"stock_code": "证券代码", "市值": "市值"}, table())

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="缺少必需字段"):
            checked_mapping({"price": "成交价格"}, table())

    def test_blank_column_name_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="不得为空白"):
            checked_mapping({"stock_code": "  "}, table())

    def test_missing_column_is_reported_not_guessed(self) -> None:
        with pytest.raises(MemoryValidationError, match="未找到含全部映射列名"):
            checked_mapping({"stock_code": "证券代码", "date": "不存在的列"}, table())

    def test_empty_table_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="账单表格为空"):
            read_table(b"")


# ───────────────────────── GWT-2 · 持仓入库 attention ─────────────────────────


class TestGwt2HoldingsLandInAttention:
    """GWT-2：证券代码集落一个 ``attention`` 节点（去重升序）。"""

    def test_preview_builds_one_attention_node(self, statement_importer: StatementImporter) -> None:
        preview = statement_importer.preview(table(), MAPPING)
        assert preview.holdings == ("sh.600000", "sz.000001")
        attention = [n for n in preview.nodes if n.type == "attention"]
        assert len(attention) == 1
        assert attention[0].holdings == ("sh.600000", "sz.000001")

    def test_import_writes_the_attention_node(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        attention = [n for n in graph.nodes() if n.type == "attention"]
        assert len(attention) == 1
        assert attention[0].holdings == ("sh.600000", "sz.000001")

    def test_unmapped_codes_are_filtered_and_counted(
        self, statement_importer: StatementImporter
    ) -> None:
        text = ("证券代码,买卖方向\n"
                "600000,买入\n"
                "920001,买入\n")                       # 北交所：D-032 口径过滤
        preview = statement_importer.preview(table(text), {"stock_code": "证券代码"})
        assert preview.holdings == ("sh.600000",)
        assert preview.unmapped == ("920001",)

    def test_normalize_accepts_common_broker_spellings(self) -> None:
        assert normalize_stock_id("600000") == "sh.600000"
        assert normalize_stock_id("000001") == "sz.000001"
        assert normalize_stock_id("300750") == "sz.300750"
        assert normalize_stock_id("600000.SH") == "sh.600000"
        assert normalize_stock_id("SZ.000001") == "sz.000001"
        assert normalize_stock_id("sh.600519") == "sh.600519"
        assert normalize_stock_id("920001") is None      # 北交所未收录
        assert normalize_stock_id("") is None
        assert normalize_stock_id("不是代码") is None


# ───────────────────────── GWT-3 · 决策事件入库 history ─────────────────────────


class TestGwt3EventsLandInHistory:
    """GWT-3：逐行事件落 ``history`` 节点（自由文本列优先，否则合成）。"""

    def test_events_are_synthesized_when_no_event_column(
        self, statement_importer: StatementImporter
    ) -> None:
        preview = statement_importer.preview(table(), MAPPING)
        assert preview.events == (
            "2026-09-01 买入 sh.600000 100 10.5",
            "2026-09-02 卖出 sz.000001 200 12.0",
            "2026-09-03 买入 sh.600000 50 10.6",
        )

    def test_a_free_text_event_column_takes_precedence(
        self, statement_importer: StatementImporter
    ) -> None:
        text = ("证券代码,摘要\n"
                "600000,打新中签缴款\n")
        mapping = {"stock_code": "证券代码", "event": "摘要"}
        preview = statement_importer.preview(table(text), mapping)
        assert preview.events == ("打新中签缴款",)

    def test_duplicate_events_are_collapsed(self, statement_importer: StatementImporter) -> None:
        text = ("证券代码,摘要\n"
                "600000,买入\n"
                "600000,买入\n")
        preview = statement_importer.preview(table(text), {"stock_code": "证券代码",
                                                          "event": "摘要"})
        assert preview.events == ("买入",)

    def test_import_writes_one_history_node_per_event(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        history = [n for n in graph.nodes() if n.type == "history"]
        assert {n.event for n in history} == {
            "2026-09-01 买入 sh.600000 100 10.5",
            "2026-09-02 卖出 sz.000001 200 12.0",
            "2026-09-03 买入 sh.600000 50 10.6",
        }


# ───────────────────────── GWT-4 · 来源与写入路径 ─────────────────────────


class TestGwt4SourceAndWritePath:
    """GWT-4：``user_stated`` 直写、不带 ``provenance``、置信度走本人基线。"""

    def test_nodes_are_user_stated_without_provenance(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        nodes = graph.nodes()
        assert nodes and all(n.source == "user_stated" for n in nodes)
        assert all(n.provenance is None for n in nodes)

    def test_confidence_defaults_to_the_stated_baseline(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        assert all(n.confidence == 0.9 for n in graph.nodes())

    def test_no_conflict_proposal_is_created(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        # §4 白名单 / 冲突队列只约束 source=inferred 的写入；用户上传的本人数据不过它。
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        assert [p for p in graph.store.list_files("execution_log")
                if "conflict" in p] == []


# ───────────────────────── GWT-5 · 导入治理 ─────────────────────────


class TestGwt5Governance:
    """GWT-5：隐私分级 + 确认门 + 幂等 + 审计留痕。"""

    def test_public_privacy_is_rejected(self, statement_importer: StatementImporter) -> None:
        with pytest.raises(MemoryValidationError, match="privacy_level"):
            statement_importer.preview(table(), MAPPING, privacy_level="public")

    def test_private_and_sensitive_are_accepted(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user",
                                            privacy_level="sensitive")
        assert all(n.privacy_level == "sensitive" for n in graph.nodes())

    def test_confirmation_gate_is_required(self, statement_importer: StatementImporter) -> None:
        with pytest.raises(MemoryValidationError, match="confirmed_by"):
            statement_importer.import_statement(table(), MAPPING, confirmed_by="system")

    def test_preview_does_not_write(self, statement_importer: StatementImporter,
                                    graph: MemoryGraph) -> None:
        statement_importer.preview(table(), MAPPING)
        assert graph.nodes() == ()

    def test_reimport_is_idempotent(self, statement_importer: StatementImporter,
                                    graph: MemoryGraph) -> None:
        first = statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        count_after_first = len(graph.nodes())
        second = statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        assert len(first.nodes) == 4
        assert second.nodes == ()
        assert len(second.record.skipped) == 4
        assert len(graph.nodes()) == count_after_first

    def test_existing_content_is_reused_across_different_statements(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        before = len(graph.nodes())
        other = ("对账单\n"
                 "证券代码,买卖方向,成交日期,成交数量,成交价格\n"   # 另一份账单、同两只标的
                 "600000,买入,2026-09-01,100,10.5\n"                # 该行与首份账单完全相同
                 "000001,卖出,2026-09-02,200,12.0\n")
        statement_importer.import_statement(table(other), MAPPING, confirmed_by="user")
        assert len(graph.nodes()) == before       # 持仓集与两行事件都已在 → 不产生副本

    def test_an_import_leaves_a_traceable_record(
        self, statement_importer: StatementImporter, graph: MemoryGraph
    ) -> None:
        outcome = statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        paths = [p for p in graph.store.list_files("execution_log")
                 if p.startswith(STATEMENT_IMPORT_PREFIX)]
        assert paths == [f"{STATEMENT_IMPORT_PREFIX}{outcome.record.import_id}.json"]
        record = statement_importer.record_for(outcome.record.import_id)
        assert record.holdings == ("sh.600000", "sz.000001")
        assert len(record.created) == 4
        assert statement_importer.records() == (record,)

    def test_import_id_is_deterministic_on_the_statement(
        self, statement_importer: StatementImporter
    ) -> None:
        assert new_statement_import_id(table()) == new_statement_import_id(table())

    def test_missing_record_is_reported(self, statement_importer: StatementImporter) -> None:
        with pytest.raises(MemoryValidationError, match="无此账单导入记录"):
            statement_importer.record_for("stmt_" + "0" * 20)

    def test_reimport_does_not_rewrite_the_record(
        self, statement_importer: StatementImporter
    ) -> None:
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        statement_importer.import_statement(table(), MAPPING, confirmed_by="user")
        assert len(statement_importer.records()) == 1


# ───────────────────────── 表格解析：CSV / Excel ─────────────────────────


class TestReadTable:
    """``read_table``：CSV（标准库）与 xlsx（标准库 zip + XML，不引第三方依赖）。"""

    def test_csv_with_bom_is_read(self) -> None:
        parsed = read_table("﻿证券代码\n600000\n".encode("utf-8"))
        assert parsed.rows == (("证券代码",), ("600000",))

    def test_non_utf8_csv_is_reported(self) -> None:
        with pytest.raises(MemoryValidationError, match="非 UTF-8"):
            read_table(b"\xff\xfe\x00\x00")

    def test_xlsx_is_detected_and_read(self) -> None:
        sheet = (
            f'<?xml version="1.0"?><worksheet xmlns="{_NS}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>600000</t></is></c>'
            '<c r="B2"><v>100</v></c></row>'
            '</sheetData></worksheet>'
        )
        shared = (f'<?xml version="1.0"?><sst xmlns="{_NS}">'
                  '<si><t>证券代码</t></si><si><t>成交数量</t></si></sst>')
        parsed = read_table(_xlsx_bytes(sheet, shared))
        assert parsed.rows == (("证券代码", "成交数量"), ("600000", "100"))

    def test_sparse_cells_keep_their_columns(self) -> None:
        sheet = (
            f'<?xml version="1.0"?><worksheet xmlns="{_NS}"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>c</t></is></c></row>'
            '</sheetData></worksheet>'
        )
        assert read_table(_xlsx_bytes(sheet)).rows == (("a", "", "c"),)

    def test_broken_zip_is_reported(self) -> None:
        with pytest.raises(MemoryValidationError, match="无法解析"):
            read_table(b"PK\x03\x04not-a-real-zip")

    def test_unknown_format_is_reported(self) -> None:
        with pytest.raises(MemoryValidationError, match="未知的账单文件格式"):
            read_table(b"x", fmt="pdf")


def test_record_uses_the_injected_clock_and_a_stable_digest(store: Store) -> None:
    """审计记录的时刻取自注入时钟；内容摘要为 64 位十六进制（可复核「对应哪份账单」）。"""
    graph = MemoryGraph(store)
    importer = StatementImporter(graph, MemoryWriter(graph), now=lambda: NOW)
    outcome = importer.import_statement(table(), MAPPING, confirmed_by="user")
    assert outcome.record.recorded_at == NOW
    assert len(outcome.record.statement_digest) == 64
    assert all(ch in "0123456789abcdef" for ch in outcome.record.statement_digest)
