"""T-AGT-011.2 测试：``ask_user`` 循环内提问（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT 对照：
- GWT-1：模型调用 ``ask_user`` 且闸门放行 → 循环终止并返回结构化问项（一问一参、取值域明确）
- GWT-2：带答案续跑归 ``T-AGT-011.3``（本文件只钉「问项本身完整可作答」）
- GWT-3：问项超预算 / 无法收敛 → 显式给出方向候选或截断说明，不强行猜

另钉：提问原语同样过闸门、不产 ``skill_run``；形态非法时显式终止、不臆造问项。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l0.storage import Store
from st_agent.l1.builtin import install_builtins
from st_agent.l1.builtin.ask_user import ASK_USER, MAX_ASK_QUESTIONS
from st_agent.l1.builtin.plan_update import PLAN_UPDATE
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import (
    ASK_USER_TOOL,
    GateDecision,
    TurnResult,
    conclude_agent_run,
    run_agent_loop,
)

EP = "ep_ask"
TASK = "查证最近热门板块，但窗口未定"
PASS = "ask-test-passphrase"


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


def ask_call(arguments: dict, *, call_id: str = "c_ask") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=ASK_USER_TOOL, arguments=arguments),
    ))


def drive(runner, tools, protocol, *, gate=None, **kwargs):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=gate if gate is not None else RecordingGate(),
        history=(), memory=(), **kwargs,
    )


# ───────────────────────── GWT-1 · 终止并交还结构化问项 ─────────────────────────


def test_gwt1_ask_terminates_with_structured_questions(rig):
    store, runner, tools = rig
    outcome = drive(runner, tools, ScriptedProtocol([ask_call({
        "questions": [
            {"parameter": "window", "prompt": "统计窗口（天）", "choices": ["7", "30"],
             "default": 30},
            {"parameter": "market", "prompt": "市场范围"},
        ],
    })]))

    assert outcome.termination == "needs_user_input"
    assert [(q.parameter, q.prompt) for q in outcome.questions] == [
        ("window", "统计窗口（天）"), ("market", "市场范围"),
    ]
    assert outcome.questions[0].choices == ("7", "30")
    assert outcome.questions[0].default == 30
    assert outcome.steps == ()                       # 没有执行任何动作
    assert [name for name in store.list_files("execution_log")
            if name.startswith("skill-run/")] == []

    report = conclude_agent_run(store, outcome)
    assert report.envelope.status == "ok"            # 问项本身就是交付物
    data = report.envelope.data
    assert data.component_type == "table"
    assert data.slots["termination"] == "待用户输入"
    assert data.slots["questions"][0]["parameter"] == "window"
    assert report.record.termination == "needs_user_input"
    assert report.record.questions[0].parameter == "window"


def test_ask_passes_the_gate_like_any_action(rig):
    _store, runner, tools = rig
    gate = RecordingGate()
    drive(runner, tools, ScriptedProtocol([ask_call({
        "questions": [{"parameter": "window", "prompt": "窗口"}],
    })]), gate=gate)
    assert gate.seen == [ASK_USER_TOOL]


# ───────────────────── GWT-3 · 超预算 / 无法收敛不硬猜 ─────────────────────


def test_gwt3_over_budget_keeps_three_and_notes_the_rest(rig):
    _store, runner, tools = rig
    raw = [{"parameter": f"p{index}", "prompt": f"问 {index}"} for index in range(5)]
    outcome = drive(runner, tools, ScriptedProtocol([ask_call({"questions": raw})]))

    assert outcome.termination == "needs_user_input"
    assert len(outcome.questions) == MAX_ASK_QUESTIONS
    assert "p3" in outcome.ask_note and "p4" in outcome.ask_note


def test_gwt3_directions_are_passed_through_without_choosing(rig):
    _store, runner, tools = rig
    outcome = drive(runner, tools, ScriptedProtocol([ask_call({
        "questions": [],
        "directions": ["近一周", "近一月", "近一年"],
    })]))
    assert outcome.termination == "needs_user_input"
    assert outcome.questions == ()
    assert outcome.directions == ("近一周", "近一月", "近一年")


def test_invalid_questions_terminate_with_explicit_reason(rig):
    _store, runner, tools = rig
    outcome = drive(runner, tools, ScriptedProtocol([ask_call({
        "questions": [{"prompt": "缺少参名的问句"}],
    })]))
    assert outcome.termination == "needs_user_input"
    assert outcome.questions == ()
    assert "parameter" in outcome.reason
    assert outcome.ask_note == outcome.reason       # 说明同时进留痕字段


def test_zero_questions_and_no_directions_is_explicit(rig):
    _store, runner, tools = rig
    outcome = drive(runner, tools, ScriptedProtocol([ask_call({"questions": []})]))
    assert outcome.termination == "needs_user_input"
    assert "既没有有效问项也没有方向候选" in outcome.reason
