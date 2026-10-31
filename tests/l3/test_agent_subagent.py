"""T-AGT-018.2 测试：子 Agent 上下文隔离（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

执行面用**真** ``SkillRunner`` + 真 ``Store``；子循环的落痕用**真** ``conclude_agent_run``
（不是替身），故「子链不进主链」「子运行可独立审计」「预算计入父上界」都在真流水线上验。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.builtin import install_builtins
from st_agent.l1.builtin.spawn import SPAWN_AGENT
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import (
    GateDecision,
    NestedRunRef,
    TurnResult,
    agent_log_ref_of,
    conclude_agent_run,
    load_agent_run,
    resume_agent_run,
    run_agent_loop,
)

EP = "ep_subagent"
TASK = "主任务：查证 sh.600000"
CHILD_TASK = "子任务：查证 sh.600000 的公告"

SK_A = "sk_data_aggregate"
SK_B = "sk_sector_heatmap"
SK_GATED = "sk_agent_gated"


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    reg.register(SK_GATED, name="受限能力", description="子 Agent 失败用例的最小能力")
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.ok({"a": True}))
    out.register_executor(f"{SK_B}_v1.0", lambda ctx, params: ResultEnvelope.ok({"b": True}))
    out.register_executor(f"{SK_GATED}_v1.0", lambda ctx, params: ResultEnvelope.ok({"g": True}))
    install_builtins(skills, out, primitives=(SPAWN_AGENT,))
    return out


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


class FakeProtocol:
    def __init__(self, script=()) -> None:
        self._script = list(script)
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        self.contexts.append(context)
        return self._script.pop(0) if self._script else TurnResult(text="（脚本已尽）")


class FakeGate:
    def __init__(self, verdicts: dict[str, str] | None = None) -> None:
        self._verdicts = dict(verdicts or {})
        self.seen: list[str] = []

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        self.seen.append(tool_name)
        return GateDecision(verdict=self._verdicts.get(tool_name, "autonomous-ok"))


def call(name: str, arguments: dict | None = None, *, call_id: str = "call_1") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=name, arguments=arguments or {}),
    ))


def batch(*names: str) -> TurnResult:
    return TurnResult(tool_calls=tuple(
        ToolCall(call_id=f"call_{index}", name=name, arguments={})
        for index, name in enumerate(names, start=1)
    ))


def finish(text: str = "主结论") -> TurnResult:
    return TurnResult(text=text)


def recorder(store):
    def record(outcome):
        report = conclude_agent_run(store, outcome)
        return NestedRunRef(
            agent_run_id=report.agent_run_id,
            log_ref=agent_log_ref_of(report.agent_run_id),
        )

    return record


def drive(runner, tools, protocol, *, gate=None, store=None, **kwargs):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=gate if gate is not None else FakeGate(),
        nested_recorder=recorder(store) if store is not None else None,
        **kwargs,
    )


def test_gwt1_child_runs_in_isolated_context_and_returns_only_answer_and_refs(
    runner, tools, store,
):
    protocol = FakeProtocol([
        call("sk_spawn_agent", {"task": CHILD_TASK}, call_id="spawn_1"),
        batch(SK_A),
        finish("子 Agent 结论：无高危公告"),
        finish("主结论：以子 Agent 引用为准"),
    ])

    outcome = drive(runner, tools, protocol, store=store)

    assert outcome.termination == "finished"
    assert outcome.answer == "主结论：以子 Agent 引用为准"
    assert outcome.steps == ()                                   # 派生不是主链动作步
    assert outcome.trace.steps == ()                             # 子链不并入主链
    assert outcome.executed_steps == 2                           # 派生 1 + 子 Agent 1 步
    assert outcome.llm_calls == 4                                # 父派生 + 子两轮 + 父收尾
    assert len(outcome.sub_agents) == 1
    sub = outcome.sub_agents[0]
    assert sub.task == CHILD_TASK
    assert sub.termination == "finished"
    assert sub.answer == "子 Agent 结论：无高危公告"
    assert sub.steps == 1
    assert sub.trace_id != outcome.trace.trace_id.value
    assert sub.evidence and sub.evidence[0].kind == "skill_run_id"

    # 子上下文：不含主循环历史、已执行步骤、计划，也不提供再次派生的工具
    child_context = protocol.contexts[1]
    assert child_context.task == CHILD_TASK
    assert child_context.steps == ()
    assert child_context.history == ()
    assert child_context.subagent_note == ""
    assert "sk_spawn_agent" not in {entry.name for entry in child_context.tools}

    # 父循环下一轮只看到「结论 + 引用」，看不到子步骤
    parent_after = protocol.contexts[3]
    assert "子 Agent 结果" in parent_after.subagent_note or sub.answer in parent_after.subagent_note
    assert sub.answer in parent_after.subagent_note
    assert sub.evidence[0].ref in parent_after.subagent_note

    # 子运行独立留痕，并以 parent_trace_id 指回主链
    child_record = load_agent_run(store, sub.agent_run_id)
    assert child_record.parent_trace_id == outcome.trace.trace_id.value
    assert child_record.task == CHILD_TASK
    assert child_record.executed_steps == 1
    assert [step.skill_run_id for step in child_record.steps] == [sub.evidence[0].ref]

    # 子 Agent 的引用并入父产出
    parent_report = conclude_agent_run(store, outcome)
    assert sub.evidence[0].ref in {
        ref.ref for ref in parent_report.envelope.evidence_refs
    }


def test_gwt2_child_failure_is_reported_and_parent_can_continue(runner, tools, store):
    protocol = FakeProtocol([
        call("sk_spawn_agent", {"task": CHILD_TASK}, call_id="spawn_1"),
        batch(SK_GATED),
        finish("主结论：改选其他能力"),
    ])

    outcome = drive(
        runner, tools, protocol, store=store,
        gate=FakeGate({SK_GATED: "needs-confirmation"}),
    )

    assert outcome.termination == "finished"                     # 父循环未被拖停
    assert outcome.answer == "主结论：改选其他能力"
    sub = outcome.sub_agents[0]
    assert sub.termination == "needs_confirmation"
    assert sub.steps == 0
    assert outcome.executed_steps == 1                           # 只有派生本身
    assert "未完成" in protocol.contexts[2].subagent_note
    assert "needs_confirmation" in protocol.contexts[2].subagent_note


def test_gwt3_child_steps_count_against_the_parent_budget(runner, tools, store):
    protocol = FakeProtocol([
        call("sk_spawn_agent", {"task": CHILD_TASK}, call_id="spawn_1"),
        batch(SK_A),
        batch(SK_B),
        batch(SK_A),                                             # 子循环的第三动作触界
        finish("主结论"),
    ])

    outcome = drive(runner, tools, protocol, store=store, max_steps=3)

    assert outcome.termination == "finished"
    sub = outcome.sub_agents[0]
    assert sub.termination == "step_limit"
    assert sub.steps == 2                                        # 子循环只剩 2 步预算
    assert outcome.executed_steps == 3                           # 派生 1 + 子 2
    assert outcome.llm_calls == 5                                # 父派生 + 子三轮 + 父收尾


def test_one_level_only(runner, skills, store):
    install_builtins(skills, runner, primitives=(SPAWN_AGENT,))
    tools = skills.tool_catalog()
    outcome = drive(
        runner, tools,
        FakeProtocol([call("sk_spawn_agent", {"task": CHILD_TASK})]),
        store=store, allow_sub_agents=False,
    )
    assert outcome.termination == "unknown_tool"
    assert "只允许一层" in outcome.reason
    assert outcome.sub_agents == ()


def test_missing_recorder_is_explicit_and_does_not_fake_a_child(runner, tools):
    protocol = FakeProtocol([
        call("sk_spawn_agent", {"task": CHILD_TASK}, call_id="spawn_1"),
        finish("主结论"),
    ])
    outcome = drive(runner, tools, protocol)                     # 未注入记录端口

    assert outcome.termination == "finished"
    assert outcome.sub_agents == ()
    assert "未注入子循环留痕端口" in protocol.contexts[1].subagent_note


def test_invalid_child_task_is_reported_without_running(runner, tools, store):
    protocol = FakeProtocol([
        call("sk_spawn_agent", {"task": ""}, call_id="spawn_1"),
        finish("主结论"),
    ])
    outcome = drive(runner, tools, protocol, store=store)
    assert outcome.sub_agents == ()
    assert "task" in protocol.contexts[1].subagent_note


def test_spawn_primitive_direct_call_does_not_fake_a_child(skills, runner):
    install_builtins(skills, runner, primitives=(SPAWN_AGENT,))
    run = runner.run("sk_spawn_agent_v1.0", {}, inputs={"task": CHILD_TASK})
    assert run.envelope.status == "validation_failed"
    assert "只能在自主查证循环内使用" in run.envelope.reason


def test_resume_carries_the_subagent_conclusion_and_audit_link(runner, tools, store):
    """续跑从旧留痕重建子 Agent 的结论与引用；子步骤不重跑，但留痕链接不断。"""
    protocol = FakeProtocol([
        call("sk_spawn_agent", {"task": CHILD_TASK}, call_id="spawn_1"),
        batch(SK_A),
        finish("子 Agent 结论：无高危公告"),
        batch(SK_GATED),                                         # 主循环在这一步被拦
    ])
    blocked = drive(
        runner, tools, protocol, store=store,
        gate=FakeGate({SK_GATED: "needs-confirmation"}),
    )
    assert blocked.termination == "needs_confirmation"
    record = conclude_agent_run(store, blocked).record
    assert len(record.sub_agents) == 1

    resumed_protocol = FakeProtocol([finish("续跑完成")])
    result = resume_agent_run(
        store, record.agent_run_id,
        decision="approve",
        endpoint_id=EP, protocol=resumed_protocol, runner=runner, tools=tools,
        gate=FakeGate(),
        nested_recorder=recorder(store),
    )

    assert result.envelope.status == "ok"
    resumed = result.report.record
    assert resumed.termination == "finished"
    assert [sub.agent_run_id for sub in resumed.sub_agents] == [
        record.sub_agents[0].agent_run_id
    ]
    assert resumed.steps_count == len(resumed.steps) + 1         # 续跑后仍计入子 Agent 步
    context = resumed_protocol.contexts[0]
    assert record.sub_agents[0].answer in context.subagent_note
    assert record.sub_agents[0].evidence[0].ref in context.subagent_note
