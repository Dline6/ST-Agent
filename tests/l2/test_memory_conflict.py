"""T-L2-002.2 · 冲突检测 · 提案队列与裁决落盘（04 §4；GWT-1..6）。"""

from __future__ import annotations

import pytest
from memory_helpers import NOW, STOCK_ID, TRACE_ID, inferred, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    DEFAULT_WRITE_WHITELIST,
    ConflictQueue,
    MemoryGraph,
    MemoryNotFoundError,
    MemoryValidationError,
    MemoryWriter,
    WritePolicy,
)

STAMP_KEYS = ("memory_node_id", "created_at", "updated_at", "revision_history")


def widened(policy: WritePolicy, *extra: str) -> None:
    """把某几个维度放进白名单——用于验证「冲突检测独立于白名单」。"""
    policy.set_dimensions([*DEFAULT_WRITE_WHITELIST, *extra])


class TestGwt1DetectionProducesAnIdempotentProposal:
    """GWT-1：冲突被检出并落成提案；同一冲突重复检测幂等。"""

    def test_proposal_is_persisted(self, store: Store, queue: ConflictQueue) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        assert proposal is not None
        assert proposal.status == "pending"
        assert f"memory-conflict/{proposal.conflict_id}.json" in store.list_files("execution_log")

    def test_same_conflict_detected_twice_yields_one_proposal(self, queue: ConflictQueue) -> None:
        first = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        second = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        assert first.conflict_id == second.conflict_id
        assert len(queue.all()) == 1

    def test_whitelisted_dimension_with_no_rival_is_not_a_conflict(
        self, queue: ConflictQueue
    ) -> None:
        assert queue.propose(inferred("pattern", pattern="总在周一冲动加仓"),
                             trace_id=TRACE_ID) is None

    def test_divergence_outranks_the_consent_gate(self, graph: MemoryGraph,
                                                  writer: MemoryWriter,
                                                  policy: WritePolicy) -> None:
        writer.add_node(node("identity", risk_preference="稳健"))
        widened(policy, "identity")
        proposal = ConflictQueue(graph, writer, policy, now=lambda: NOW).propose(
            inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        assert proposal.kind == "preference_divergence"


class TestGwt2ConflictDetectionIsIndependentOfTheWhitelist:
    """GWT-2：§4 两条规则并列——白名单放行也拦不住真冲突；事件可被 L3 消费。"""

    def test_same_subject_thesis_conflicts_even_when_whitelisted(
        self, graph: MemoryGraph, writer: MemoryWriter, policy: WritePolicy
    ) -> None:
        old = node("thesis", subject=STOCK_ID, subject_kind="stock", view="看好反转")
        writer.add_node(old)
        widened(policy, "thesis")
        proposal = ConflictQueue(graph, writer, policy, now=lambda: NOW).propose(
            inferred("thesis", subject=STOCK_ID, subject_kind="stock", view="看空反转"),
            trace_id=TRACE_ID)
        assert proposal is not None
        assert proposal.kind == "same_subject_thesis"
        assert proposal.target_node_id == old.memory_node_id

    def test_a_different_subject_thesis_is_not_a_conflict(
        self, graph: MemoryGraph, writer: MemoryWriter, policy: WritePolicy
    ) -> None:
        writer.add_node(node("thesis", subject="sz.000001", subject_kind="stock"))
        widened(policy, "thesis")
        assert ConflictQueue(graph, writer, policy, now=lambda: NOW).propose(
            inferred("thesis", subject=STOCK_ID, subject_kind="stock"),
            trace_id=TRACE_ID) is None

    def test_user_stated_is_never_proposed(self, graph: MemoryGraph, writer: MemoryWriter,
                                           policy: WritePolicy) -> None:
        """用户自陈即使与既有节点冲突也不进本门（Story 3：用户自陈 thesis 是「自动写入」）。"""
        writer.add_node(node("thesis", subject=STOCK_ID, subject_kind="stock", view="看好反转"))
        widened(policy, "thesis")
        assert ConflictQueue(graph, writer, policy, now=lambda: NOW).propose(
            node("thesis", subject=STOCK_ID, subject_kind="stock", view="看空反转"),
            trace_id=TRACE_ID) is None

    def test_event_carries_trace_id_and_points_at_the_proposal(
        self, queue: ConflictQueue
    ) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        event = queue.event_for(proposal)
        assert event.event == "MemoryConflictDetected"
        assert event.trace_id == TRACE_ID
        assert event.payload["conflict_id"] == proposal.conflict_id
        assert event.occurred_at == proposal.created_at


class TestGwt3AcceptWritesANewVersionWithEvolutionEdge:
    """GWT-3：确认变更 → 新增节点 + ``evolves_from``；旧节点内容一字不改。"""

    def test_accept_adds_node_and_evolution_edge(self, graph: MemoryGraph, writer: MemoryWriter,
                                                 queue: ConflictQueue) -> None:
        old = node("thesis", subject=STOCK_ID, subject_kind="stock", view="看好反转")
        writer.add_node(old)
        proposal = queue.propose(
            inferred("thesis", subject=STOCK_ID, subject_kind="stock", view="看空反转"),
            trace_id=TRACE_ID)
        before = graph.get_node(old.memory_node_id)

        resolution = queue.resolve(proposal.conflict_id, decision="accept", confirmed_by="user")

        assert resolution.node is not None
        assert resolution.node.memory_node_id != old.memory_node_id
        assert graph.get_node(resolution.node.memory_node_id).view == "看空反转"
        assert resolution.edge is not None
        assert resolution.edge.edge_type == "evolves_from"
        assert resolution.edge.source_id == resolution.node.memory_node_id
        assert resolution.edge.target_id == old.memory_node_id
        assert graph.get_node(old.memory_node_id) == before         # 旧节点一字不改
        assert queue.get(proposal.conflict_id).status == "accepted"

    def test_consent_only_proposal_accepts_without_an_edge(self, graph: MemoryGraph,
                                                           queue: ConflictQueue) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="稳健"), trace_id=TRACE_ID)
        assert proposal.kind == "dimension_requires_consent"
        assert proposal.target_node_id is None
        resolution = queue.resolve(proposal.conflict_id, decision="accept", confirmed_by="user")
        assert resolution.node is not None
        assert resolution.edge is None


class TestGwt4RejectDiscardsButRecords:
    """GWT-4：确认不变 → 图谱不变、提案落为已裁决。"""

    def test_reject_keeps_the_graph_untouched(self, graph: MemoryGraph, writer: MemoryWriter,
                                              queue: ConflictQueue) -> None:
        old = node("identity", risk_preference="稳健")
        writer.add_node(old)
        before_nodes = graph.nodes()
        before_edges = graph.edges()
        proposal = queue.propose(inferred("identity", risk_preference="激进"),
                                 trace_id=TRACE_ID)

        resolution = queue.resolve(proposal.conflict_id, decision="reject",
                                   confirmed_by="user", reason="口径未变")

        assert resolution.node is None and resolution.edge is None
        assert graph.nodes() == before_nodes
        assert graph.edges() == before_edges
        recorded = queue.get(proposal.conflict_id)
        assert recorded.status == "rejected"
        assert recorded.reason == "口径未变"
        assert recorded.resolved_at == NOW
        assert queue.pending() == ()


class TestGwt5NoSilentOverwrite:
    """GWT-5：提案载荷不带 ID / 时间戳，接受路径只能新增。"""

    def test_payload_omits_identity_stamps(self, queue: ConflictQueue) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        for key in STAMP_KEYS:
            assert key not in proposal.proposed

    def test_payload_keeps_the_proposed_content(self, queue: ConflictQueue) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        assert proposal.proposed["risk_preference"] == "激进"
        assert proposal.proposed["source"] == "inferred"
        assert proposal.proposed["provenance"]["trace_id"] == TRACE_ID


class TestGwt6OutOfRangeAndMissingAreExplicit:
    """GWT-6：越界裁决与缺失目标显式失败（不留半截状态）。"""

    def test_non_user_confirmation_is_rejected(self, queue: ConflictQueue) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        with pytest.raises(MemoryValidationError, match="只接受 'user'"):
            queue.resolve(proposal.conflict_id, decision="accept", confirmed_by="inferred")

    def test_unknown_conflict_id_is_rejected(self, queue: ConflictQueue) -> None:
        with pytest.raises(MemoryNotFoundError):
            queue.resolve("cf_" + "0" * 20, decision="accept", confirmed_by="user")

    def test_double_resolution_is_rejected(self, queue: ConflictQueue) -> None:
        proposal = queue.propose(inferred("identity", risk_preference="激进"), trace_id=TRACE_ID)
        queue.resolve(proposal.conflict_id, decision="reject", confirmed_by="user")
        with pytest.raises(MemoryValidationError, match="已裁决"):
            queue.resolve(proposal.conflict_id, decision="accept", confirmed_by="user")

    def test_vanished_target_fails_before_any_write(self, graph: MemoryGraph,
                                                    writer: MemoryWriter, store: Store,
                                                    queue: ConflictQueue) -> None:
        old = node("identity", risk_preference="稳健")
        writer.add_node(old)
        proposal = queue.propose(inferred("identity", risk_preference="激进"),
                                 trace_id=TRACE_ID)
        store.delete("memory", f"node/{old.memory_node_id}.json")

        with pytest.raises(MemoryNotFoundError, match="已不存在"):
            queue.resolve(proposal.conflict_id, decision="accept", confirmed_by="user")

        assert len(graph.nodes()) == 0                              # 未落半截新节点
        assert queue.get(proposal.conflict_id).status == "pending"
