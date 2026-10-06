"""T-L3-005.1 测试：冲突裁决（[05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) + [04 §4](../../docs/技术架构-v2/04-L2-记忆图谱.md)；GWT-1..5）。

用**真 L2**（真 `Store` + `MemoryGraph` + `ConflictQueue`）跑装配——冲突的检测 /
提案 / 裁决落盘本就是 L2 的交付面，这里验的是 L3 的**承接与交回**，不是重造检测。
"""

from __future__ import annotations

import pytest
from l3_helpers import NOW, STOCK_A, TRACE_ID, inferred, node

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l2.memory import (
    ConflictQueue,
    MemoryGraph,
    MemoryWriter,
    ThesisNode,
)
from st_agent.l3.conflict import (
    QUEUE_ABSENT_REASON,
    STANCE_UNDECIDED_LABEL,
    ConflictAdjudication,
    ConflictAdjudicator,
)
from st_agent.l3.dispatch import ROUTE_BY_INTENT, DispatchBus
from st_agent.l3.errors import ConflictValidationError
from st_agent.l3.intent import UNSPECIFIED_LABEL, ConfirmationItem, checked_confirmation


def _card(intent: str, target: str | None = None):
    """一张**已确认**的确认卡（派发的入口凭证）。"""
    return checked_confirmation(
        envelope=ResultEnvelope.ok({"intent": intent}),
        intent=intent, target=target,
        items=(ConfirmationItem(
            text="意图", value=target or UNSPECIFIED_LABEL, source="intent"),),
        values={}, confirmed=True,
    )


def _seed_identity(graph: MemoryGraph, writer: MemoryWriter, value: str = "稳健"):
    """落一个既有 identity 节点（用户自陈），返回它。"""
    return writer.add_node(node("identity", risk_preference=value))


def _proposal(queue: ConflictQueue, kind: str = "identity", **over):
    """落一条待裁决提案（推断类节点触发冲突检测）。"""
    return queue.propose(inferred(kind, **over), trace_id=TRACE_ID)


def _event(conflict_id: str) -> PlatformEvent:
    return PlatformEvent(
        event="MemoryConflictDetected",
        payload={"conflict_id": conflict_id},
        trace_id=TRACE_ID, occurred_at=NOW,
    )


# ───────────────────────── GWT-1 承接事件与裁决卡 ─────────────────────────


class TestGwt1CardAndIngestion:
    def test_card_carries_both_sides_and_actions(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        existing = _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        result = ConflictAdjudicator(queue=queue, graph=graph).card_for(proposal.conflict_id)
        assert result.status == "ok"
        card: ConflictAdjudication = result.data
        assert card.conflict_id == proposal.conflict_id
        assert card.existing is not None and card.existing.node_id == existing.memory_node_id
        assert card.proposed.node_id is None
        assert {a.decision for a in card.actions} == {"accept", "reject"}
        assert card.header and card.question
        assert card.question_line() == f"{card.header}：{card.question}"

    def test_event_is_ingested_by_conflict_id(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        result = ConflictAdjudicator(queue=queue, graph=graph).from_event(
            _event(proposal.conflict_id))
        assert result.status == "ok"
        assert result.data.conflict_id == proposal.conflict_id

    def test_wrong_event_type_is_refused(
        self, graph: MemoryGraph, queue: ConflictQueue
    ) -> None:
        bad = PlatformEvent(event="SignalEmitted", payload={}, trace_id=TRACE_ID,
                            occurred_at=NOW)
        result = ConflictAdjudicator(queue=queue, graph=graph).from_event(bad)
        assert result.status == "validation_failed"

    def test_event_without_conflict_id_is_refused(
        self, graph: MemoryGraph, queue: ConflictQueue
    ) -> None:
        result = ConflictAdjudicator(queue=queue, graph=graph).from_event(
            PlatformEvent(event="MemoryConflictDetected", payload={}, trace_id=TRACE_ID,
                          occurred_at=NOW))
        assert result.status == "validation_failed"

    def test_absent_queue_is_fail_closed(self) -> None:
        result = ConflictAdjudicator().card_for("cf_" + "0" * 20)
        assert result.status == "unavailable"
        assert result.reason == QUEUE_ABSENT_REASON
        assert "T-L2-002" not in result.reason  # 点名的是**未注入**，不甩锅给上游任务

    def test_unknown_conflict_is_empty(
        self, graph: MemoryGraph, queue: ConflictQueue
    ) -> None:
        result = ConflictAdjudicator(queue=queue, graph=graph).card_for("cf_" + "0" * 20)
        assert result.status == "empty"
        assert "cf_" in result.reason

    def test_memory_content_is_carried_verbatim_and_not_neutrality_checked(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        """D-053：记忆本体属**数据展示**——含第一人称也原样承载，不阻断。"""
        writer.add_node(node("thesis", subject=STOCK_A, view="我认为会反转"))
        proposal = _proposal(queue, "thesis", subject=STOCK_A, view="预计承压")
        assert proposal is not None
        result = ConflictAdjudicator(queue=queue, graph=graph).card_for(proposal.conflict_id)
        assert result.status == "ok"
        assert result.data.existing.fields["view"] == "我认为会反转"
        assert result.data.proposed.fields["view"] == "预计承压"

    def test_non_neutral_generated_text_is_blocked(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        """红-绿：生成文案（方向候选）含第一人称即**阻断**（铁律 2 / 01 §6 执行点 2）。"""
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None

        class _LeakyJudge:
            def compare(self, existing, proposed):
                return ("我认为应当改口",)

        adj = ConflictAdjudicator(queue=queue, graph=graph, stance_judge=_LeakyJudge())
        with pytest.raises(ConflictValidationError):
            adj.card_for(proposal.conflict_id)


# ───────────────────────── GWT-2 裁决交回 L2 ─────────────────────────


class TestGwt2SubmitToL2:
    def test_accept_adds_a_node_and_an_evolution_edge(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        existing = _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        adj = ConflictAdjudicator(queue=queue, graph=graph)
        card = adj.card_for(proposal.conflict_id).data
        result = adj.submit(card, decision="accept", reason="偏好确实变了", now=NOW)
        assert result.status == "ok"
        resolution = result.data
        assert resolution.node is not None and resolution.node.memory_node_id != existing.memory_node_id
        assert resolution.edge is not None and resolution.edge.edge_type == "evolves_from"
        assert resolution.edge.source_id == resolution.node.memory_node_id
        assert resolution.edge.target_id == existing.memory_node_id
        # 旧节点内容一字不改（04 §4「不存在任何静默覆盖」）
        assert graph.get_node(existing.memory_node_id).risk_preference == "稳健"

    def test_reject_discards_the_proposal(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        adj = ConflictAdjudicator(queue=queue, graph=graph)
        card = adj.card_for(proposal.conflict_id).data
        result = adj.submit(card, decision="reject", now=NOW)
        assert result.status == "ok"
        assert result.data.node is None and result.data.edge is None
        assert queue.get(proposal.conflict_id).status == "rejected"

    def test_absent_queue_is_fail_closed_on_submit(self) -> None:
        result = ConflictAdjudicator().submit(_bare_card(), decision="accept")
        assert result.status == "unavailable"
        assert result.reason == QUEUE_ABSENT_REASON


def _bare_card() -> ConflictAdjudication:
    """只带 id 的裁决卡（``submit`` 只用 ``conflict_id``）。"""
    from st_agent.l3.conflict import ConflictSide
    return ConflictAdjudication(
        conflict_id="cf_" + "0" * 20, kind="preference_divergence",
        dimension="identity", trace_id=TRACE_ID,
        proposed=ConflictSide(dimension="identity", fields={}),
    )


# ───────────────────────── GWT-3 重复裁决与非法确认方 ─────────────────────────


class TestGwt3Refusals:
    def test_double_resolution_is_refused(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        adj = ConflictAdjudicator(queue=queue, graph=graph)
        card = adj.card_for(proposal.conflict_id).data
        assert adj.submit(card, decision="accept", now=NOW).status == "ok"
        again = adj.submit(card, decision="accept", now=NOW)
        assert again.status == "validation_failed"
        assert "已裁决" in again.reason

    def test_non_user_confirmation_is_refused(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        adj = ConflictAdjudicator(queue=queue, graph=graph)
        card = adj.card_for(proposal.conflict_id).data
        result = adj.submit(card, decision="accept", confirmed_by="agent")
        assert result.status == "validation_failed"
        assert queue.get(proposal.conflict_id).status == "pending"  # 未落任何裁决

    def test_vanished_target_is_empty_not_a_silent_downgrade(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue, store
    ) -> None:
        existing = _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        adj = ConflictAdjudicator(queue=queue, graph=graph)
        card = adj.card_for(proposal.conflict_id).data
        store.delete("memory", graph.node_path(existing.memory_node_id))
        result = adj.submit(card, decision="accept", now=NOW)
        assert result.status == "empty"


# ───────────────────────── GWT-4 方向判定归 L3 ─────────────────────────


class TestGwt4StanceOwnership:
    def test_no_judge_means_undecided_and_no_candidates(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        card = ConflictAdjudicator(queue=queue, graph=graph).card_for(proposal.conflict_id).data
        assert card.stance_undecided is True
        assert card.directions == ()

    def test_injected_judge_supplies_directions(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None

        class _Judge:
            def compare(self, existing, proposed):
                return ("风险偏好由稳健转为激进",)

        card = ConflictAdjudicator(
            queue=queue, graph=graph, stance_judge=_Judge()
        ).card_for(proposal.conflict_id).data
        assert card.stance_undecided is False
        assert card.directions == ("风险偏好由稳健转为激进",)

    def test_no_existing_side_means_undecided(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue, policy
    ) -> None:
        """``dimension_requires_consent`` 无冲突对象——无既有方可比，故不判定。"""
        from st_agent.l2.memory import DEFAULT_WRITE_WHITELIST
        policy.set_dimensions(list(DEFAULT_WRITE_WHITELIST))
        proposal = _proposal(queue, "attention", holdings=("sz.000002",))
        assert proposal is not None and proposal.target_node_id is None

        class _Judge:
            def compare(self, existing, proposed):
                return ("不该被调用",)

        card = ConflictAdjudicator(
            queue=queue, graph=graph, stance_judge=_Judge()
        ).card_for(proposal.conflict_id).data
        assert card.existing is None
        assert card.stance_undecided is True and card.directions == ()

    def test_l2_thesis_model_gains_no_stance_field(self) -> None:
        """册内 `B1` 的结论：L2 节点模型**不增字段**（04 §1 零返工）。"""
        assert "stance" not in ThesisNode.model_fields
        assert STANCE_UNDECIDED_LABEL  # 未判定有显式标注，不是静默留空


# ───────────────────────── GWT-5 memory_op 去向接线 ─────────────────────────


class TestGwt5MemoryOpRoute:
    def test_route_is_wired(self) -> None:
        spec = ROUTE_BY_INTENT["memory_op"]
        assert spec.wired is True and spec.owner is None

    def test_dispatch_yields_the_card(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        proposal = _proposal(queue, risk_preference="激进")
        assert proposal is not None
        adj = ConflictAdjudicator(queue=queue, graph=graph)
        outcome = DispatchBus(adjudications=adj).dispatch(
            _card("memory_op", proposal.conflict_id), now=NOW)
        assert outcome.envelope.status == "ok"
        assert outcome.wired is True
        assert [c.conflict_id for c in outcome.adjudications] == [proposal.conflict_id]

    def test_dispatch_without_target_lists_all_pending(
        self, graph: MemoryGraph, writer: MemoryWriter, queue: ConflictQueue
    ) -> None:
        _seed_identity(graph, writer)
        assert _proposal(queue, risk_preference="激进") is not None
        outcome = DispatchBus(adjudications=ConflictAdjudicator(queue=queue, graph=graph)).dispatch(
            _card("memory_op", None), now=NOW)
        assert outcome.envelope.status == "ok"
        assert len(outcome.adjudications) == 1

    def test_absent_adjudications_is_fail_closed(self) -> None:
        outcome = DispatchBus().dispatch(_card("memory_op", "cf_" + "0" * 20))
        assert outcome.envelope.status == "unavailable"
        assert outcome.envelope.reason == QUEUE_ABSENT_REASON

    def test_empty_pending_is_reported_as_empty(
        self, graph: MemoryGraph, queue: ConflictQueue
    ) -> None:
        outcome = DispatchBus(adjudications=ConflictAdjudicator(queue=queue, graph=graph)).dispatch(
            _card("memory_op", None), now=NOW)
        assert outcome.envelope.status == "empty"

    def test_other_routes_keep_their_own_fail_closed_paths(self) -> None:
        """`memory_op` 的接线不牵连其余去向——未注入各自端口时，各去向**各报各的**原因，
        互不冒充。`train` 由 [T-L6-002.1] 接活后口径**与 `analyze` 完全相同**
        （`wired=True`，缺端口仍 fail-closed），故不再有「未接入去向」可比。"""
        reasons = {}
        for intent in ("train", "analyze", "configure"):
            outcome = DispatchBus().dispatch(_card(intent, "sk_probe_v1.0"))
            assert outcome.envelope.status == "unavailable"
            assert outcome.wired is True
            reasons[intent] = outcome.envelope.reason
        assert len(set(reasons.values())) == len(reasons)          # 各报各的，不串用别家原因
        assert all(ROUTE_BY_INTENT[i].owner is None for i in reasons)
