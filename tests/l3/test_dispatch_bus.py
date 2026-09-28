"""T-L3-002.2 测试：任务派发总线（[05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md)；GWT-1..5）。

`query` 去向用**真 L1**（`SkillRegistry` + `SkillRunner` + 真 `Store`）跑装配，
其余去向与未接入态用最小构造的确认卡；`explain` 去向直接给一条真 `Trace`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.result_envelope import EnvelopeStatus, ResultEnvelope
from st_agent.contracts.trace import Trace, TraceStep, digest_of
from st_agent.l0.storage import Store
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l3.commands import INTENT_KINDS
from st_agent.l3.dispatch import (
    LLM_DEGRADED_NOTICE,
    RENDER_SEMANTICS,
    ROUTE_BY_INTENT,
    ROUTE_SPECS,
    DispatchBus,
    render_semantics,
)
from st_agent.l3.intent import (
    UNSPECIFIED_LABEL,
    ConfirmationItem,
    checked_confirmation,
)

from typing import get_args

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=CST)
BASE = "sk_probe_aggregate"
SKILL = f"{BASE}_v1.0"


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    registry = SkillRegistry(store)
    registry.register(
        BASE, version="1.0", name="数据聚合探针", description="按范围聚合数据并出报表",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        parameters=({"name": "window_days", "type": "integer", "default": 30,
                     "description": "统计窗口天数"},),
        dependencies=(), source="user-built",
    )
    return registry


def _card(intent: str, target: str | None = None):
    """一张**已确认**的确认卡（派发的入口凭证）。"""
    return checked_confirmation(
        envelope=ResultEnvelope.ok({"intent": intent}),
        intent=intent, target=target,
        items=(ConfirmationItem(
            text="意图", value=target or UNSPECIFIED_LABEL, source="intent"),),
        values={}, confirmed=True,
    )


def _trace() -> Trace:
    return Trace(trace_id=TraceId.generate()).append_step(TraceStep(
        step_type="skill_run", ref="run_" + "0" * 20,
        input_digest=digest_of("in"), output_digest=digest_of("out"),
        duration_ms=3, timestamp=NOW,
    ))


def _runner(store: Store, skills: SkillRegistry, handler) -> SkillRunner:
    runner = SkillRunner(store, skills)
    runner.register_executor(SKILL, handler)
    return runner


# ───────────────────────── GWT-1 trace_id 与确认门 ─────────────────────────


class TestGwt1TraceAndConfirmationGate:
    def test_unconfirmed_card_is_refused(self) -> None:
        card = _card("query", SKILL)
        draft_only = checked_confirmation(
            envelope=card.envelope, intent=card.intent, target=card.target,
            items=card.items, values={}, confirmed=False,
        )
        outcome = DispatchBus().dispatch(draft_only)
        assert outcome.envelope.status == "validation_failed"
        assert outcome.trace_id.startswith("tr_")

    def test_pending_route_gets_its_own_empty_chain(self) -> None:
        outcome = DispatchBus().dispatch(_card("configure", SKILL))
        assert outcome.envelope.status == "unavailable"
        assert outcome.trace_id.startswith("tr_")
        assert outcome.trace is not None and outcome.trace.steps == ()  # 不伪造步骤

    def test_query_route_yields_a_replayable_chain(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        runner = _runner(store, skills, lambda ctx, params: ResultEnvelope.ok({"rows": 1}))
        outcome = DispatchBus(runner=runner).dispatch(_card("query", SKILL))
        assert outcome.trace_id == outcome.run.trace.trace_id.value
        assert [s[0] for s in outcome.trace.replay()] == ["skill_run"]


# ───────────────────────── GWT-2 query 去向 ─────────────────────────


class TestGwt2QueryRoute:
    def test_envelope_passes_through_untouched(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        original = ResultEnvelope.empty("给定范围内无命中记录")

        class _Runner:
            def run(self, skill_id, values=None, **kw):
                from st_agent.l1.runner import RunOutcome
                return RunOutcome(skill_id=skill_id, skill_run_id="run_" + "1" * 20,
                                  envelope=original, trace=_trace())

        outcome = DispatchBus(runner=_Runner()).dispatch(_card("query", SKILL))
        assert outcome.envelope is original
        assert outcome.envelope.reason == "给定范围内无命中记录"

    def test_confirmation_values_are_forwarded(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        seen: dict = {}

        def handler(ctx, params):
            seen.update(params)
            return ResultEnvelope.ok({})

        runner = _runner(store, skills, handler)
        card = checked_confirmation(
            envelope=ResultEnvelope.ok({}), intent="query", target=SKILL,
            items=(ConfirmationItem(text="意图", source="intent"),),
            values={"window_days": 7}, confirmed=True,
        )
        DispatchBus(runner=runner).dispatch(card)
        assert seen == {"window_days": 7}

    def test_missing_target_is_validation_failed(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        runner = _runner(store, skills, lambda ctx, params: ResultEnvelope.ok({}))
        outcome = DispatchBus(runner=runner).dispatch(_card("query", None))
        assert outcome.envelope.status == "validation_failed"

    def test_absent_runner_is_fail_closed(self) -> None:
        outcome = DispatchBus().dispatch(_card("query", SKILL))
        assert outcome.envelope.status == "unavailable"
        assert outcome.envelope.last_updated_at is not None


# ───────────────────────── GWT-3 未接入去向 ─────────────────────────


class TestGwt3PendingRoutes:
    def test_route_table_matches_05_3_1(self) -> None:
        assert tuple(spec.intent for spec in ROUTE_SPECS) == INTENT_KINDS

    @pytest.mark.parametrize("intent", ["configure", "analyze", "memory_op", "train"])
    def test_pending_route_names_its_owner(self, intent: str) -> None:
        outcome = DispatchBus().dispatch(_card(intent, SKILL))
        assert outcome.envelope.status == "unavailable"
        assert outcome.wired is False
        assert outcome.owner == ROUTE_BY_INTENT[intent].owner
        assert outcome.owner in outcome.envelope.reason

    def test_pending_route_disclaims_the_timestamp(self) -> None:
        outcome = DispatchBus( ).dispatch(_card("analyze", SKILL), now=NOW)
        assert outcome.envelope.last_updated_at == NOW
        assert "不代表数据截止时间" in outcome.envelope.reason

    def test_wired_routes_are_only_query_and_explain(self) -> None:
        assert {s.intent for s in ROUTE_SPECS if s.wired} == {"query", "explain"}


# ───────────────────────── explain 去向 ─────────────────────────


class TestExplainRoute:
    def test_explain_returns_the_given_trace(self) -> None:
        chain = _trace()
        outcome = DispatchBus().dispatch(_card("explain"), trace=chain)
        assert outcome.envelope.status == "ok"
        assert outcome.envelope.data is chain
        assert outcome.trace_id == chain.trace_id.value

    def test_explain_without_trace_is_empty(self) -> None:
        outcome = DispatchBus().dispatch(_card("explain"))
        assert outcome.envelope.status == "empty"
        assert outcome.trace_id.startswith("tr_")


# ───────────────────────── GWT-4 六态渲染语义 ─────────────────────────


class TestGwt4RenderSemantics:
    def test_semantics_cover_every_envelope_status(self) -> None:
        assert set(RENDER_SEMANTICS) == set(get_args(EnvelopeStatus))

    @pytest.mark.parametrize(
        "status,presentation,must_show",
        [
            ("ok", "normal", ()),
            ("empty", "empty_state", ("原因",)),
            ("unavailable", "delayed", ("最后更新时间", "原因")),
            ("dependency_failed", "error", ("原因", "日志入口")),
            ("failed", "error", ("原因", "日志入口")),
            ("validation_failed", "input_error", ("理由",)),
        ],
    )
    def test_each_status_maps_to_its_ui_semantics(
        self, status: str, presentation: str, must_show: tuple[str, ...]
    ) -> None:
        envelope = _envelope(status)
        semantics = render_semantics(envelope)
        assert semantics.presentation == presentation
        assert semantics.must_show == must_show

    def test_unknown_status_is_rejected(self) -> None:
        with pytest.raises(KeyError):
            render_semantics(ResultEnvelope.model_construct(status="unknown"))


# ───────────────────────── GWT-5 LLM 降级告知 ─────────────────────────


class TestGwt5LlmDegradation:
    def test_notice_passes_neutrality(self) -> None:
        assert NeutralityGuard().check_output(LLM_DEGRADED_NOTICE).passed

    def test_degraded_unavailable_carries_the_notice(self) -> None:
        semantics = render_semantics(_envelope("unavailable"), llm_degraded=True)
        assert semantics.notice == LLM_DEGRADED_NOTICE
        assert "最后更新时间" in semantics.must_show

    def test_plain_unavailable_has_no_notice(self) -> None:
        assert render_semantics(_envelope("unavailable")).notice is None

    def test_ok_never_carries_the_notice(self) -> None:
        assert render_semantics(_envelope("ok"), llm_degraded=True).notice is None

    def test_llm_independent_route_still_runs(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        """`query` 不依赖 LLM：即使 LLM 降级，派发照常执行（可用路径不被掐断）。"""
        runner = _runner(store, skills, lambda ctx, params: ResultEnvelope.ok({"rows": 1}))
        outcome = DispatchBus(runner=runner).dispatch(_card("query", SKILL))
        assert outcome.envelope.status == "ok"
        assert render_semantics(outcome.envelope, llm_degraded=True).notice is None


def _envelope(status: str) -> ResultEnvelope:
    """按态造一个**合法**信封（01 §5 的分支语义：非 ok 必带对应字段）。"""
    if status == "ok":
        return ResultEnvelope.ok({"x": 1})
    if status == "empty":
        return ResultEnvelope.empty("范围内无记录")
    if status == "unavailable":
        return ResultEnvelope.unavailable("源延迟", last_updated_at=NOW)
    if status == "failed":
        return ResultEnvelope.failed("执行失败", log_ref="execution_log/x.json")
    if status == "dependency_failed":
        return ResultEnvelope.dependency_failed("上游失败")
    return ResultEnvelope.validation_failed("参数非法")
