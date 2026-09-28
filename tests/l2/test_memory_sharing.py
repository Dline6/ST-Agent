"""T-L2-004.2 · 记忆片段导出：隐私过滤 + 清单 + 确认门（04 §8；GWT-1..5）。"""

from __future__ import annotations

import pytest
from memory_helpers import STOCK_ID, edge, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    INTERNAL_EDGE_TYPES,
    MemoryGraph,
    MemoryShare,
    MemoryValidationError,
    MemoryWriter,
    checked_node,
)


def _public_thesis(**over):
    return node("thesis", privacy_level="public", **over)


class TestGwt1OnlyPublicNodesLeaveTheMachine:
    """GWT-1：片段只含 public 节点，private / sensitive 一条不留。"""

    def test_private_and_sensitive_are_filtered_out(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        kept = writer.add_node(_public_thesis(view="看好反转"))
        writer.add_node(node("identity", privacy_level="private", risk_preference="稳健"))
        writer.add_node(node("attention", privacy_level="sensitive", sector_preferences=("银行",)))

        plan = share.plan()

        assert [n.memory_node_id for n in plan.payload.nodes] == [kept.memory_node_id]
        assert all(n.privacy_level == "public" for n in plan.payload.nodes)
        assert plan.excluded_nodes == 2

    def test_empty_graph_yields_empty_payload(self, share: MemoryShare) -> None:
        plan = share.plan()
        assert plan.payload.nodes == ()
        assert plan.payload.edges == ()
        assert plan.excluded_nodes == 0


class TestGwt2NoDanglingEdges:
    """GWT-2：与滤掉节点相接的边一并剔除（无悬空边）。"""

    def test_edge_touching_a_filtered_node_is_dropped(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        a = writer.add_node(_public_thesis(view="A"))
        b = writer.add_node(node("thesis", privacy_level="private", view="B"))
        c = writer.add_node(node("attention", privacy_level="public",
                                 sector_preferences=("银行",)))
        kept_edge = writer.add_edge(edge("related_to", a.memory_node_id, c.memory_node_id))
        writer.add_edge(edge("related_to", a.memory_node_id, b.memory_node_id))
        writer.add_edge(edge("related_to", b.memory_node_id, c.memory_node_id))

        plan = share.plan()

        assert [e.source_id for e in plan.payload.edges] == [kept_edge.source_id]
        assert plan.payload.edges[0].target_id == c.memory_node_id
        # 无悬空边：每条留存边的内端都在片段内
        allowed = plan.payload.node_ids()
        for e in plan.payload.edges:
            assert e.source_id in allowed
            if e.edge_type in INTERNAL_EDGE_TYPES:
                assert e.target_id in allowed

    def test_refers_to_keeps_external_target_but_not_a_filtered_node(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        a = writer.add_node(_public_thesis(view="A"))
        hidden = writer.add_node(node("thesis", privacy_level="private", view="H"))
        writer.add_edge(edge("refers_to", a.memory_node_id, STOCK_ID))
        writer.add_edge(edge("refers_to", a.memory_node_id, hidden.memory_node_id))

        plan = share.plan()

        assert [e.target_id for e in plan.payload.edges] == [STOCK_ID]
        assert hidden.memory_node_id not in plan.payload.node_ids()


class TestGwt3IncludedSummaryIsNeutral:
    """GWT-3：「本次导出包含的公开信息」清单（中性措辞）。"""

    def test_summary_counts_by_type_and_edges(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        a = writer.add_node(_public_thesis(view="A"))
        b = writer.add_node(node("attention", privacy_level="public",
                                 sector_preferences=("银行",)))
        writer.add_edge(edge("related_to", a.memory_node_id, b.memory_node_id))
        writer.add_node(node("identity", privacy_level="private", risk_preference="稳健"))

        summary = share.plan().included_summary

        assert summary[0] == "公开记忆节点 2 条"
        assert "thesis 节点 1 条" in summary
        assert "attention 节点 1 条" in summary
        assert "identity 节点 1 条" not in summary
        assert "相接边 1 条" in summary
        assert "已剔除私有 / 敏感节点 1 条" in summary

    def test_summary_carries_no_first_person_or_emotion(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        writer.add_node(_public_thesis(view="看好反转"))
        text = "\n".join(share.plan().included_summary)
        assert "我" not in text and "你" not in text
        assert not any(w in text for w in ("担心", "认为", "！", "?"))


class TestGwt4NoPayloadWithoutUserConfirmation:
    """GWT-4：未经用户确认不产出（confirmed_by 门）。"""

    def test_non_user_confirmation_is_rejected(self, share: MemoryShare) -> None:
        for bogus in ("assistant", "system", ""):
            with pytest.raises(MemoryValidationError, match="只接受 'user'"):
                share.export(confirmed_by=bogus)

    def test_user_confirmation_returns_the_planned_payload(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        writer.add_node(_public_thesis(view="看好反转"))
        assert share.export(confirmed_by="user") == share.plan().payload

    def test_planning_and_exporting_write_nothing(
        self, store: Store, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        writer.add_node(_public_thesis(view="看好反转"))
        before = store.list_files("memory")
        share.plan()
        share.export(confirmed_by="user")
        assert store.list_files("memory") == before
        assert store.list_files("execution_log") == ()


class TestGwt5FragmentCarriesCurrentValuesOnly:
    """GWT-5：片段只承载当前值（不含 revision_history，旧私有快照不外泄）。"""

    def test_revision_history_is_cleared_in_the_fragment_but_kept_on_disk(
        self, graph: MemoryGraph, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        created = writer.add_node(_public_thesis(view="旧观点"))
        writer.edit_node(created.memory_node_id, changes={"view": "新观点"},
                         confirmed_by="user", reason="改主意了")

        payload = share.plan().payload
        assert payload.nodes[0].revision_history == ()
        assert len(graph.get_node(created.memory_node_id).revision_history) == 1

    def test_fragment_node_is_still_a_valid_node(
        self, writer: MemoryWriter, share: MemoryShare
    ) -> None:
        writer.add_node(_public_thesis(view="看好反转"))
        fragment_node = share.plan().payload.nodes[0]
        # 片段节点仍是 §1 合法节点（可直接回灌 checked_node，专属字段未丢）
        rebuilt = checked_node(**fragment_node.model_dump(mode="python"))
        assert rebuilt.type == "thesis"
        assert rebuilt.view == "看好反转"
