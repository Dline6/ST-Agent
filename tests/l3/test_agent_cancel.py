"""T-AGT-024.1 测试：取消透传与 ``cancelled`` 终止（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) / [D-154](../../项目管理/决策日志.md) B6）。

GWT-1 当前工具跑完再停 · GWT-2 在途 LLM 调用被中断 · GWT-3 已执行步骤如实列出、不回滚。
执行面用**真** ``SkillRunner`` + 真 ``Store`` + 真官方 Pack（只有 LLM 端与闸门是替身），
故「已执行的写操作停在留痕里、不被回滚」是在真流水线上验的。
"""

from __future__ import annotations

import threading

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.render.describe import AGENT_TERMINATION_LABELS
from st_agent.l3.runtime import (
    GateDecision,
    TurnResult,
    build_agent_output,
    run_agent_loop,
)

EP = "ep_test"
TASK = "查证 sh.600000 的公告与舆情"
SK_A = "sk_data_aggregate"
SK_B = "sk_sector_heatmap"


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    return SkillRunner(store, skills)


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


class FakeGate:
    """授权端口替身（缺省全放行）。"""

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        return GateDecision(verdict="autonomous-ok")


class CancelProtocol:
    """脚本化协议替身；``on_turn(cancel)`` 可在「调用进行中」置位取消事件。"""

    def __init__(self, script, *, on_turn=None) -> None:
        self._script = list(script)
        self._on_turn = on_turn
        self.turns = 0

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        self.turns += 1
        if self._on_turn is not None:
            self._on_turn(cancel)
        item = self._script.pop(0) if self._script else TurnResult(text="（脚本已尽）")
        return item


def call(name: str, arguments: dict | None = None, *, call_id: str = "call_1") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=name, arguments=arguments or {}),
    ))


def drive(runner, tools, protocol, *, cancel=None, gate=None, parallel_safe=False, **kwargs):
    return run_agent_loop(
        task=TASK,
        endpoint_id=EP,
        protocol=protocol,
        runner=runner,
        tools=tools,
        gate=gate if gate is not None else FakeGate(),
        cancel=cancel,
        parallel_safe=parallel_safe,
        **kwargs,
    )


# ───────────────────────── GWT-1：当前工具跑完再停 ─────────────────────────


def test_cancel_during_tool_run_stops_after_current_step(store, skills, runner, tools) -> None:
    """执行中置取消 → 该步跑完、下一步不启动，终止原因 ``cancelled``，产出照实列出。"""
    event = threading.Event()

    def slow_step(ctx, params):  # noqa: ANN001 —— 执行面签名由 SkillRunner 给定
        event.set()
        return ResultEnvelope.ok({"written": True})

    runner.register_executor(f"{SK_A}_v1.0", slow_step)
    protocol = CancelProtocol([call(SK_A), TurnResult(text="不应走到这一轮")])

    outcome = drive(runner, tools, protocol, cancel=event)

    assert outcome.termination == "cancelled"
    assert protocol.turns == 1                       # 下一步没有再问，也没有再执行
    assert [step.tool_name for step in outcome.steps] == [SK_A]
    assert outcome.executed_steps == 1
    assert "不回滚" in outcome.reason

    envelope = build_agent_output(outcome, agent_run_id="agr_cancel_1")
    assert envelope.status == "ok"                   # 已执行步骤照实呈现，不判失败
    rows = envelope.data.slots["rows"]
    assert [row["tool_name"] for row in rows] == [SK_A]
    assert rows[0]["status"] == "ok"
    assert [ref.ref for ref in envelope.evidence_refs] == [outcome.steps[0].skill_run_id]
    assert envelope.data.slots["termination"] == AGENT_TERMINATION_LABELS["cancelled"]


# ───────────────────────── GWT-2：在途 LLM 调用被中断 ─────────────────────────


def test_cancel_during_llm_call_returns_cancelled_not_failed(runner, tools) -> None:
    """调用中置取消 → 循环按 ``cancelled`` 收口，不把端点侧的中断信封当成 ``llm_failed``。"""

    def cancel_in_call(cancel) -> None:  # noqa: ANN001
        assert isinstance(cancel, threading.Event)
        cancel.set()

    protocol = CancelProtocol(
        [TurnResult(envelope=ResultEnvelope.failed("调用中被取消", log_ref="llm/x.json"))],
        on_turn=cancel_in_call,
    )

    outcome = drive(runner, tools, protocol, cancel=threading.Event())

    assert outcome.termination == "cancelled"
    assert outcome.failure is None                   # 端点侧信封不透出（停止优先）
    assert outcome.llm_calls == 1
    assert outcome.steps == ()

    envelope = build_agent_output(outcome, agent_run_id="agr_cancel_2")
    assert envelope.status == "empty"                # 一步未执行：如实空态 + 原因
    assert "不回滚" in envelope.reason


def test_cancel_before_start_skips_llm_call(runner, tools) -> None:
    """开跑前已置位 → 一次 LLM 都不问。"""
    event = threading.Event()
    event.set()
    protocol = CancelProtocol([TurnResult(text="不应被问")])

    outcome = drive(runner, tools, protocol, cancel=event)

    assert outcome.termination == "cancelled"
    assert protocol.turns == 0
    assert outcome.llm_calls == 0
    assert "已执行 0 步" in outcome.reason


# ───────────────────────── GWT-3：同轮未执行 / 不回滚 ─────────────────────────


def test_cancel_between_calls_of_one_batch_skips_the_rest(store, skills, runner, tools) -> None:
    """同轮两个调用：第一个执行中置取消 → 第二个不启动，并在终止说明里点名。"""
    event = threading.Event()

    def first_step(ctx, params):  # noqa: ANN001
        event.set()
        return ResultEnvelope.ok({"order": 1})

    runner.register_executor(f"{SK_A}_v1.0", first_step)
    protocol = CancelProtocol([
        TurnResult(tool_calls=(
            ToolCall(call_id="call_1", name=SK_A, arguments={}),
            ToolCall(call_id="call_2", name=SK_B, arguments={"top_n": 3}),
        )),
    ])

    outcome = drive(runner, tools, protocol, cancel=event)

    assert outcome.termination == "cancelled"
    assert [step.tool_name for step in outcome.steps] == [SK_A]
    assert "其余 1 个调用未执行" in outcome.reason


def test_no_cancel_argument_keeps_previous_behaviour(runner, tools) -> None:
    """不传取消事件时行为不变（A4）。"""
    protocol = CancelProtocol([TurnResult(text="证据已收集完毕")])

    outcome = drive(runner, tools, protocol)

    assert outcome.termination == "finished"
    assert outcome.answer == "证据已收集完毕"


def test_cancel_event_shape_is_validated(runner, tools) -> None:
    """取消事件须提供 ``is_set()``——错误形态显式拒绝，不静默当成不可取消。"""
    with pytest.raises(AgentRuntimeValidationError):
        drive(runner, tools, CancelProtocol([]), cancel=object())
