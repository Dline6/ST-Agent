"""T-AGT-027.1：steer inbox 与上下文注入（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

三条验收面都钉在**真** ``run_agent_loop`` 上（执行面是真 ``SkillRunner`` + 真官方 Pack，
只有协议端口是脚本替身）：

- GWT-1：运行中插话 → 下一步上下文含该消息并带「运行中插入」标记；
- GWT-2：插话与「模型给出结束」竞态 → 不丢，标 ``delivered=False``；
- GWT-3：多条插话按到达顺序全部保留。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from l3_helpers import NOW
from pydantic import ValidationError

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime import (
    GateDecision,
    SteerInbox,
    SteerMessage,
    TurnResult,
    assemble_work_context,
    run_agent_loop,
)

EP = "ep_test"
TASK = "查证 sh.600000 的公告与舆情"
SK = "sk_data_aggregate"


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(f"{SK}_v1.0", lambda ctx, params: ResultEnvelope.ok({"cards": []}))
    return out


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


class FakeProtocol:
    def __init__(self, script) -> None:
        self._script = list(script)
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        self.contexts.append(context)
        item = self._script.pop(0) if self._script else TurnResult(text="（脚本已尽）")
        return item(context) if callable(item) else item


class FakeGate:
    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        return GateDecision(verdict="autonomous-ok")


def call(name: str, arguments: dict | None = None, *, call_id: str = "call_1") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=name, arguments=arguments or {}),
    ))


def finish(text: str = "证据已收集完毕") -> TurnResult:
    return TurnResult(text=text)


def drive(runner, tools, protocol, *, inbox=None, events=None):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=FakeGate(), steer_inbox=inbox,
        emit=(lambda kind, payload: events.append((kind, payload))) if events is not None else None,
    )


# ───────────────────────── GWT-1 · 下一步上下文注入 ─────────────────────────


def test_gwt1_delivered_steer_reaches_the_next_round_with_marker(runner, tools):
    inbox = SteerInbox()
    observed: dict = {}
    events: list = []

    def first(_context):
        inbox.add("补充：只看新能源板块")
        return call(SK)

    def second(context):
        observed["steers"] = [item.text for item in context.steers]
        observed["prompt"] = context.render_prompt()
        return finish()

    outcome = drive(runner, tools, FakeProtocol([first, second]), inbox=inbox, events=events)

    assert outcome.termination == "finished"
    assert observed["steers"] == ["补充：只看新能源板块"]
    assert "[运行中插入] 补充：只看新能源板块" in observed["prompt"]
    assert [(item.text, item.delivered, item.after_steps) for item in outcome.steered] == [
        ("补充：只看新能源板块", True, 1),
    ]
    steer_events = [payload for kind, payload in events if kind == "steer"]
    assert [event["delivered"] for event in steer_events] == [True]
    assert steer_events[0]["count"] == 1 and steer_events[0]["after_steps"] == 1


# ───────────────────────── GWT-2 · 与结束竞态 ─────────────────────────


def test_gwt2_steer_racing_with_finish_is_deferred_not_lost(runner, tools):
    inbox = SteerInbox()
    events: list = []

    def final(_context):
        inbox.add("补充：再补一句")
        return finish()

    outcome = drive(runner, tools, FakeProtocol([final]), inbox=inbox, events=events)

    assert outcome.termination == "finished"
    assert [(item.text, item.delivered) for item in outcome.steered] == [
        ("补充：再补一句", False),
    ]
    steer_events = [payload for kind, payload in events if kind == "steer"]
    assert steer_events and steer_events[-1]["delivered"] is False
    assert "转为下一条普通消息" in steer_events[-1]["reason"]


# ───────────────────────── GWT-3 · 多条按到达顺序 ─────────────────────────


def test_gwt3_multiple_steers_keep_arrival_order(runner, tools):
    inbox = SteerInbox()
    observed: dict = {}

    def first(_context):
        inbox.add("第一条：只看沪市")
        inbox.add("第二条：排除 ST")
        return call(SK)

    def second(context):
        observed["texts"] = [item.text for item in context.steers]
        return finish()

    outcome = drive(runner, tools, FakeProtocol([first, second]), inbox=inbox)

    assert observed["texts"] == ["第一条：只看沪市", "第二条：排除 ST"]
    assert [item.text for item in outcome.steered] == ["第一条：只看沪市", "第二条：排除 ST"]
    assert [item.after_steps for item in outcome.steered] == [1, 1]


# ───────────────────────── 收件箱与预算 ─────────────────────────


def test_inbox_rejects_blank_and_drains_once():
    inbox = SteerInbox(now=lambda: NOW)
    with pytest.raises(AgentRuntimeValidationError):
        inbox.add("   ")
    message = inbox.add("补一句")
    assert message.text == "补一句" and message.received_at == NOW
    assert inbox.pending == 1
    assert [item.text for item in inbox.drain()] == ["补一句"]
    assert inbox.drain() == () and inbox.pending == 0


def test_steer_is_dropped_only_after_memory_and_history(tools):
    base = assemble_work_context(TASK, tools, budget_chars=10**6)
    size = base.data.size_chars
    steer = SteerMessage(text="x" * 200, received_at=NOW)
    sized = assemble_work_context(
        TASK, tools, budget_chars=size + 1, steer_messages=(steer,),
    )
    assert sized.status == "ok"
    assert sized.data.steers == ()
    assert any(item.startswith("steer:") for item in sized.data.dropped)


def test_steer_message_requires_timezone():
    with pytest.raises(ValidationError):
        SteerMessage(text="补一句", received_at=datetime(2026, 11, 2, 12, 0))
