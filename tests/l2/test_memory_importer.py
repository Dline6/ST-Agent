"""T-L2-004.3 · 片段导入 / 继承：他人公开片段入库（04 §8；GWT-1..7）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from memory_helpers import NOW, STOCK_ID, TRACE_ID, edge as make_edge, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    IMPORT_RECORD_PREFIX,
    ConfidenceModel,
    DeletionOutcome,
    FragmentImporter,
    FragmentPayload,
    ImportOrigin,
    MemoryDeleter,
    MemoryEdge,
    MemoryGraph,
    MemoryValidationError,
    MemoryWriter,
    Provenance,
    checked_node,
    checked_origin,
    new_import_id,
)

ORIGIN = ImportOrigin(sharer="user-a", imported_at=NOW, checksum="a" * 64)
LATER_ORIGIN = ImportOrigin(sharer="user-a", imported_at=NOW + timedelta(days=1),
                            checksum="a" * 64, origin_chain=("user-x",))


def fragment(*nodes, edges=()) -> FragmentPayload:
    return FragmentPayload(nodes=nodes, edges=edges)


def public_thesis(**over):
    fields = {"privacy_level": "public", "view": "看好反转"}
    fields.update(over)
    return node("thesis", **fields)


def _inferred_node(**over):
    """构造一个推断类节点（经 `checked_node` 走真实构造路径，故错误类型是本层错误）。"""
    fields = dict(type="thesis", memory_node_id="mn_" + "0" * 20, confidence=0.5,
                  source="inferred", privacy_level="public",
                  created_at=NOW, updated_at=NOW, view="x")
    fields.update(over)
    return checked_node(**fields)


class TestGwt1ProvenanceCarriesExactlyOneOrigin:
    """GWT-1：§1 不变量放宽——trace_id / imported 恰有其一。"""

    def test_local_trace_still_works(self) -> None:
        assert _inferred_node(provenance={"trace_id": TRACE_ID}).provenance.trace_id == TRACE_ID

    def test_import_origin_is_accepted(self) -> None:
        provenance = _inferred_node(provenance={"imported": ORIGIN}).provenance
        assert provenance.imported == ORIGIN
        assert provenance.trace_id is None

    def test_import_chain_is_recorded(self) -> None:
        node_with_chain = _inferred_node(provenance={"imported": LATER_ORIGIN})
        assert node_with_chain.provenance.imported.origin_chain == ("user-x",)

    def test_neither_origin_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="恰有其一"):
            _inferred_node(provenance={})

    def test_both_origins_are_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="恰有其一"):
            _inferred_node(provenance={"trace_id": TRACE_ID, "imported": ORIGIN})

    def test_bad_trace_id_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="trace_id 非法"):
            _inferred_node(provenance={"trace_id": "not-a-trace-id"})

    def test_bad_checksum_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="64 位十六进制"):
            checked_origin(sharer="s", imported_at=NOW, checksum="abc")

    def test_node_invariants_still_hold(self) -> None:
        # inferred 必带 provenance；user_stated 不得带（既有不变量一字未改）
        with pytest.raises(MemoryValidationError, match="必带 provenance"):
            checked_node(type="thesis", memory_node_id="mn_" + "0" * 20, confidence=0.5,
                         source="inferred", privacy_level="public",
                         created_at=NOW, updated_at=NOW, view="x")
        with pytest.raises(MemoryValidationError, match="不得带 provenance"):
            checked_node(type="thesis", memory_node_id="mn_" + "0" * 20, confidence=0.5,
                         source="user_stated", provenance=Provenance(imported=ORIGIN),
                         privacy_level="public", created_at=NOW, updated_at=NOW, view="x")


class TestGwt2ImportedNodesBecomeOtherPeopleStatements:
    """GWT-2：入库为 `source: inferred`（他人陈述），溯源四字段齐备。"""

    def test_source_is_rewritten_and_origin_attached(
        self, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis()
        outcome = importer.import_fragment(fragment(source), origin=ORIGIN,
                                           confirmed_by="user")

        imported = graph.get_node(source.memory_node_id)
        assert imported.source == "inferred"
        assert imported.provenance.imported == ORIGIN
        assert imported.provenance.imported.origin_chain == ()
        assert imported.provenance.trace_id is None
        assert [n.memory_node_id for n in outcome.nodes] == [source.memory_node_id]

    def test_own_fields_and_timestamps_survive(
        self, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis(subject=STOCK_ID, subject_kind="stock")
        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")
        imported = graph.get_node(source.memory_node_id)
        assert imported.view == "看好反转"
        assert imported.subject == STOCK_ID
        assert (imported.created_at, imported.updated_at) == (NOW, NOW)

    def test_edges_from_the_fragment_land_too(
        self, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        a, b = public_thesis(view="A"), node("attention", privacy_level="public",
                                             sector_preferences=("银行",))
        link = make_edge("related_to", a.memory_node_id, b.memory_node_id)
        outcome = importer.import_fragment(fragment(a, b, edges=(link,)), origin=ORIGIN,
                                           confirmed_by="user")
        assert [e.source_id for e in outcome.edges] == [a.memory_node_id]
        assert len(graph.edges()) == 1

    def test_edge_with_a_missing_endpoint_is_skipped(
        self, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        a = public_thesis(view="A")
        orphan_edge = make_edge("related_to", a.memory_node_id, "mn_" + "9" * 20)
        outcome = importer.import_fragment(fragment(a, edges=(orphan_edge,)), origin=ORIGIN,
                                          confirmed_by="user")
        assert outcome.edges == ()
        assert outcome.record.edges_skipped == (
            f"{a.memory_node_id}__related_to__mn_{'9' * 20}",
        )
        assert graph.edges() == ()       # 无悬空边


class TestGwt3ConfidenceIsCappedAtTheInferredBaseline:
    """GWT-3：置信度不高于推断基线（⇒ 必然低于本人表达）。"""

    def test_high_original_confidence_is_capped(
        self, graph: MemoryGraph, importer: FragmentImporter, confidence: ConfidenceModel
    ) -> None:
        source = public_thesis(confidence=0.95)
        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")
        imported = graph.get_node(source.memory_node_id)
        assert imported.confidence == confidence.baseline("inferred")
        assert imported.confidence < confidence.baseline("user_stated")

    def test_lower_original_confidence_is_kept(
        self, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis(confidence=0.2)
        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")
        assert graph.get_node(source.memory_node_id).confidence == 0.2


class TestGwt4ImportedNodesAreEditableAndDeletable:
    """GWT-4：导入节点可编辑可删除（不是只读异物）。"""

    def test_editable_through_the_user_confirmation_gate(
        self, writer: MemoryWriter, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis()
        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")

        edited = writer.edit_node(source.memory_node_id, changes={"view": "改后的观点"},
                                  confirmed_by="user", reason="导入后修正")
        assert edited.view == "改后的观点"
        assert edited.source == "inferred"                 # 编辑不改变来源
        assert len(edited.revision_history) == 1
        with pytest.raises(MemoryValidationError, match="只接受 'user'"):
            writer.edit_node(source.memory_node_id, changes={"view": "x"},
                             confirmed_by="assistant")

    def test_deletable_through_the_audited_path(
        self, deleter: MemoryDeleter, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis()
        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")
        outcome: DeletionOutcome = deleter.delete(source.memory_node_id, confirmed_by="user")
        assert outcome.record.node_ids == (source.memory_node_id,)
        assert graph.nodes() == ()


class TestGwt5ReimportingIsIdempotent:
    """GWT-5：重复导入幂等——已存在者跳过，不产生副本、不静默覆盖。"""

    def test_second_import_creates_nothing_and_reports_skips(
        self, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis()
        payload = fragment(source)
        first = importer.import_fragment(payload, origin=ORIGIN, confirmed_by="user")
        before = graph.nodes()

        second = importer.import_fragment(payload, origin=ORIGIN, confirmed_by="user")

        assert first.record.created == (source.memory_node_id,)
        assert second.record.created == ()
        assert second.record.skipped == (source.memory_node_id,)
        assert second.nodes == ()
        assert graph.nodes() == before

    def test_edited_content_is_not_silently_overwritten(
        self, writer: MemoryWriter, graph: MemoryGraph, importer: FragmentImporter
    ) -> None:
        source = public_thesis()
        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")
        writer.edit_node(source.memory_node_id, changes={"view": "本机改过"}, confirmed_by="user")

        importer.import_fragment(fragment(source), origin=ORIGIN, confirmed_by="user")

        assert graph.get_node(source.memory_node_id).view == "本机改过"

    def test_no_op_import_does_not_rewrite_the_record(
        self, store: Store, importer: FragmentImporter
    ) -> None:
        payload = fragment(public_thesis())
        importer.import_fragment(payload, origin=ORIGIN, confirmed_by="user")
        written = store.get("execution_log", f"{IMPORT_RECORD_PREFIX}"
                            f"{new_import_id(ORIGIN)}.json")

        importer.import_fragment(payload, origin=ORIGIN, confirmed_by="user")

        assert store.get("execution_log", f"{IMPORT_RECORD_PREFIX}"
                         f"{new_import_id(ORIGIN)}.json") == written
        assert len(importer.records()) == 1


class TestGwt6ThePrivacyGradingIsEnforcedOnImport:
    """GWT-6：载荷含非公开节点即拒（§8「导入数据同样过隐私分级」）。"""

    @pytest.mark.parametrize("level", ["private", "sensitive"])
    def test_non_public_payload_is_rejected_without_partial_import(
        self, graph: MemoryGraph, importer: FragmentImporter, level
    ) -> None:
        good = public_thesis(view="A")
        bad = node("thesis", privacy_level=level, view="B")
        with pytest.raises(MemoryValidationError, match="非公开节点"):
            importer.import_fragment(fragment(good, bad), origin=ORIGIN, confirmed_by="user")
        assert graph.nodes() == ()          # 拒在动手之前——图谱零变更
        assert graph.edges() == ()

    def test_confirmation_gate_is_required(self, importer: FragmentImporter) -> None:
        with pytest.raises(MemoryValidationError, match="只接受 'user'"):
            importer.import_fragment(fragment(public_thesis()), origin=ORIGIN,
                                     confirmed_by="assistant")


class TestGwt7EveryImportLeavesATraceableRecord:
    """GWT-7：溯源留痕——落 `memory-import/<import_id>.json`，id 是业务键的确定性摘要。"""

    def test_record_lands_in_the_execution_log_partition(
        self, store: Store, importer: FragmentImporter
    ) -> None:
        source = public_thesis()
        outcome = importer.import_fragment(fragment(source), origin=ORIGIN,
                                           confirmed_by="user")

        path = f"{IMPORT_RECORD_PREFIX}{new_import_id(ORIGIN)}.json"
        assert path in store.list_files("execution_log")
        assert outcome.record.import_id == new_import_id(ORIGIN)
        assert outcome.record.origin == ORIGIN
        assert outcome.record.recorded_at == NOW
        assert importer.record_for(outcome.record.import_id) == outcome.record

    def test_payload_digest_identifies_the_payload(
        self, importer: FragmentImporter
    ) -> None:
        payload = fragment(public_thesis())
        first = importer.import_fragment(payload, origin=ORIGIN, confirmed_by="user")
        other = importer.import_fragment(fragment(public_thesis(view="另一个观点")),
                                         origin=LATER_ORIGIN, confirmed_by="user")
        assert len(first.record.payload_digest) == 64
        assert first.record.payload_digest != other.record.payload_digest

    def test_import_id_is_deterministic_on_the_business_key(
        self, importer: FragmentImporter
    ) -> None:
        assert new_import_id(ORIGIN) == new_import_id(
            ImportOrigin(sharer="user-a", imported_at=NOW, checksum="a" * 64)
        )
        assert new_import_id(ORIGIN) != new_import_id(LATER_ORIGIN)

    def test_records_are_listed_in_id_order(self, importer: FragmentImporter) -> None:
        importer.import_fragment(fragment(public_thesis()), origin=ORIGIN,
                                 confirmed_by="user")
        importer.import_fragment(fragment(public_thesis()), origin=LATER_ORIGIN,
                                 confirmed_by="user")
        ids = [r.import_id for r in importer.records()]
        assert ids == sorted(ids) and len(ids) == 2

    def test_missing_record_is_reported(self, importer: FragmentImporter) -> None:
        with pytest.raises(MemoryValidationError, match="无此导入记录"):
            importer.record_for("imp_" + "0" * 20)
