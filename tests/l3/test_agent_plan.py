"""T-AGT-011.1 测试：``plan_update`` 计划原语（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT 对照：
- GWT-1：多步任务里模型调用 ``plan_update`` → 计划进工作上下文并可渲染
- GWT-2：计划更新**不产 Trace 步**（决策不入 Trace），但记入 ``agent_run`` 报告
- GWT-3：空计划**显式清空**，不与「尚未给出」混同

另钉：控制原语同样**过闸门**；非法计划**不终止**循环，而是留给模型下一轮纠正。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l0.storage import Store
from st_agent.l1.builtin import install_builtins
from st_agent.l1.builtin.ask_user import ASK_USER
from st_agent.l1.builtin.plan_update import PLAN_UPDATE
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import (
    GateDecision,
    PLAN_UPDATE_TOOL,
    TurnResult,
    conclude_agent_run,
    run_agent_loop,
)

EP = "ep_plan"
TASK = "查证 sh.600000 最近公告"
PASS = "plan-test-passphrase"


@pytest.fixture()
def rig(store: Store):
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    runner = SkillRunner(store, registry)
    install_builtins(registry, runner, primitives=(PLAN_UPDATE, ASK_USER))
    return store, runner, registry.tool_catalog()


class ScriptedProtocol:
    def __init__(self, replies) -> None:
        self._replies = list(replies)

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        item = self._replies.pop(0) if self._replies else TurnResult(text="（脚本已尽）")
        return item(context) if callable(item) else item


class RecordingGate:
    def __init__(self, verdict: str = "autonomous-ok") -> None:
        self._verdict = verdict
        self.seen: list[str] = []

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        self.seen.append(tool_name)
        return GateDecision(verdict=self._verdict)


def plan_call(plan, *, call_id: str = "c_plan") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=PLAN_UPDATE_TOOL, arguments={"plan": plan}),
    ))


def finish(text: str = "计划已维护完毕") -> TurnResult:
    return TurnResult(text=text)


def drive(runner, tools, protocol, *, gate=None, **kwargs):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=gate if gate is not None else RecordingGate(),
        history=(), memory=(), **kwargs,
    )


# ───────────────────────── GWT-1 · 计划进上下文并可渲染 ─────────────────────────


def test_gwt1_plan_enters_work_context_and_renders(rig):
    _store, runner, tools = rig
    observed: dict = {}

    def second(context):
        observed["plan"] = context.plan
        observed["updated"] = context.plan_updated
        observed["prompt"] = context.render_prompt()
        return finish()

    outcome = drive(runner, tools, ScriptedProtocol([
        plan_call([{"title": "查同步水位"}, {"title": "跑 SQL", "status": "active"}]),
        second,
    ]))

    assert outcome.termination == "finished"
    assert [(step.title, step.status) for step in observed["plan"]] == [
        ("查同步水位", "pending"), ("跑 SQL", "active"),
    ]
    assert observed["updated"] is True
    assert "当前计划（2 步）" in observed["prompt"]
    assert "[pending] 查同步水位" in observed["prompt"]
    assert outcome.plan == observed["plan"]


# ───────────────────── GWT-2 · 决策不入 Trace，但进留痕 ─────────────────────


def test_gwt2_plan_update_produces_no_trace_step_and_no_skill_run(rig):
    store, runner, tools = rig
    outcome = drive(runner, tools, ScriptedProtocol([
        plan_call([{"title": "查同步水位"}]),
        finish(),
    ]))
    report = conclude_agent_run(store, outcome)

    assert outcome.trace.steps == ()                       # 决策不入 Trace
    assert [name for name in store.list_files("execution_log")
            if name.startswith("skill-run/")] == []        # 不产 skill_run 留痕
    assert report.record.plan[0].title == "查同步水位"      # 但记入 agent_run 报告
    assert report.record.plan_updated is True
    assert report.record.termination == "finished"


# ───────────────────────── GWT-3 · 空计划显式清空 ─────────────────────────


def test_gwt3_empty_plan_is_an_explicit_clear(rig):
    _store, runner, tools = rig
    observed: dict = {}

    def observe(context):
        observed["plan"] = context.plan
        observed["updated"] = context.plan_updated
        observed["prompt"] = context.render_prompt()
        return finish()

    outcome = drive(runner, tools, ScriptedProtocol([
        plan_call([{"title": "第一步"}]),
        plan_call([], call_id="c_clear"),
        observe,
    ]))

    assert outcome.termination == "finished"
    assert observed["plan"] == ()
    assert observed["updated"] is True
    assert "已显式清空" in observed["prompt"]
    assert "尚未给出计划" not in observed["prompt"]


def test_gwt3_before_any_plan_the_context_says_not_given(rig):
    _store, runner, tools = rig
    observed: dict = {}

    def first(context):
        observed["prompt"] = context.render_prompt()
        return finish()

    drive(runner, tools, ScriptedProtocol([first]))
    assert "尚未给出计划" in observed["prompt"]
    assert "已显式清空" not in observed["prompt"]


# ───────────────────────── 闸门与非法形态 ─────────────────────────


def test_plan_update_passes_the_gate_like_any_action(rig):
    _store, runner, tools = rig
    gate = RecordingGate()
    drive(runner, tools, ScriptedProtocol([plan_call([{"title": "x"}]), finish()]), gate=gate)
    assert gate.seen == [PLAN_UPDATE_TOOL]


def test_plan_update_needs_confirmation_stops_the_loop(rig):
    _store, runner, tools = rig
    outcome = drive(
        runner, tools, ScriptedProtocol([plan_call([{"title": "x"}])]),
        gate=RecordingGate("needs-confirmation"),
    )
    assert outcome.termination == "needs_confirmation"
    assert outcome.pending is not None and outcome.pending.name == PLAN_UPDATE_TOOL


def test_invalid_plan_does_not_terminate_and_is_reported_next_round(rig):
    _store, runner, tools = rig
    observed: dict = {}

    def after_bad(context):
        observed["prompt"] = context.render_prompt()
        observed["plan"] = context.plan
        return finish()

    outcome = drive(runner, tools, ScriptedProtocol([
        plan_call([{"title": "x", "status": "unknown"}]),
        after_bad,
    ]))

    assert outcome.termination == "finished"
    assert observed["plan"] == ()
    assert "上次计划更新未采用" in observed["prompt"]
    assert "不在枚举" in observed["prompt"]
    assert outcome.plan_error  # 最后一轮里模型仍未纠正，终止产出如实带上
