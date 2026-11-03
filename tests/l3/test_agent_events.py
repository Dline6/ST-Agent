"""T-AGT-022.2 测试：事件模型与 JSONL 追加日志。

GWT-1（按序追加、序号单调）、GWT-2（完整回放）、GWT-3（未知事件类型显式失败）；
另加三条守卫——重开日志续号、序号断档 / 坏行显式失败、事件流不含中间轮文本。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import get_args

import pytest

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace, TraceStep, digest_of
from st_agent.l0.llm import ToolCall
from st_agent.l1.runner import RunOutcome
from st_agent.l1.skills.tool_catalog import ToolEntry
from st_agent.l3.errors import AgentRunNotFoundError, AgentRuntimeValidationError
from st_agent.l3.runtime import (
    RUN_EVENT_TYPES,
    RunEventLog,
    RunEventType,
    TurnResult,
    event_log_ref_of,
    read_run_events,
    run_agent_loop,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
RUN_ID = "agr_00000000000000000001"
FIXED_NOW = lambda: NOW  # noqa: E731 - 测试夹具的确定性时钟

INTERMEDIATE = "我先看看行情，再决定要不要查公告"
FINAL_TEXT = "证据已收集完毕"
TOOL = ToolEntry(name="sk_demo", skill_id="sk_demo_v1.0", description="演示工具")


class _Protocol:
    """两轮：先调一次工具（带中间文本），再给出结束轮文本。"""

    def __init__(self) -> None:
        self._turns = [
            TurnResult(text=INTERMEDIATE, tool_calls=(
                ToolCall(call_id="c1", name="sk_demo", arguments={}),)),
            TurnResult(text=FINAL_TEXT),
        ]

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        return self._turns.pop(0)


class _Runner:
    def __init__(self) -> None:
        self._n = 0

    def run(self, skill_id, values=None, *, inputs=None, trace=None,
            approved_permissions=(), initiator="", purpose=""):
        self._n += 1
        run_id = f"run_{self._n:020x}"
        chain = (trace or Trace(trace_id=TraceId.generate())).append_step(TraceStep(
            step_type="skill_run", ref=run_id, input_digest=digest_of({"s": skill_id}),
            output_digest=digest_of({"ok": True}), duration_ms=1, timestamp=NOW,
        ))
        return RunOutcome(
            skill_id=skill_id, skill_run_id=run_id,
            envelope=ResultEnvelope.ok({"cards": []}), trace=chain,
        )


class _Gate:
    def authorize(self, **kwargs):
        return "autonomous-ok"


# ───────────────────────── 枚举 ─────────────────────────


def test_event_types_are_the_eleven_from_d154_plus_steer():
    assert RUN_EVENT_TYPES == (
        "run_started", "model_turn", "plan_updated", "step_started",
        "step_finished", "tool_search", "sub_agent_started", "sub_agent",
        "gate_pending", "ask_user", "budget_request", "steer", "text_delta",
        "run_finished",
    )
    assert set(RUN_EVENT_TYPES) == set(get_args(RunEventType))


# ───────────────────────── GWT-1 / GWT-2 · 追加与回放 ─────────────────────────


def test_gwt1_emit_appends_lines_with_monotonic_seq(store):
    log = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    log.emit("run_started", {"task": "查证"})
    log.emit("model_turn", {"index": 1})
    log.emit("run_finished", {"termination": "finished"})

    raw = store.get("execution_log", event_log_ref_of(RUN_ID)).decode("utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) == 3
    payloads = [json.loads(line) for line in lines]
    assert [item["seq"] for item in payloads] == [1, 2, 3]
    assert [item["event"] for item in payloads] == [
        "run_started", "model_turn", "run_finished",
    ]
    assert all(item["agent_run_id"] == RUN_ID for item in payloads)


def test_gwt2_replay_returns_complete_run_in_order(store):
    log = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    log.emit("run_started")
    log.emit("model_turn")
    log.emit("run_finished")

    events = read_run_events(store, RUN_ID)
    assert [event.event for event in events] == [
        "run_started", "model_turn", "run_finished",
    ]
    assert [event.seq for event in events] == [1, 2, 3]
    # 断线重连续读：只取序号大于 1 的
    assert [event.seq for event in read_run_events(store, RUN_ID, after_seq=1)] == [2, 3]


def test_gwt2_reopen_continues_sequence(store):
    first = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    first.emit("run_started")
    first.emit("model_turn")

    second = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    assert second.emit("run_finished").seq == 3
    assert [event.seq for event in read_run_events(store, RUN_ID)] == [1, 2, 3]


def test_gwt2_missing_log_is_explicit(store):
    with pytest.raises(AgentRunNotFoundError):
        read_run_events(store, RUN_ID)


# ───────────────────────── GWT-3 · 显式失败 ─────────────────────────


def test_gwt3_unknown_event_type_fails_without_writing(store):
    log = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    with pytest.raises(AgentRuntimeValidationError):
        log.emit("reasoning_delta", {"text": "中间推理"})
    assert event_log_ref_of(RUN_ID) not in store.list_files("execution_log")


def test_gwt3_malformed_line_fails_explicitly(store):
    store.put("execution_log", event_log_ref_of(RUN_ID), b'{"seq": 1}\n')
    with pytest.raises(AgentRuntimeValidationError):
        read_run_events(store, RUN_ID)


def test_gwt3_sequence_gap_fails_explicitly(store):
    log = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    log.emit("run_started")
    raw = store.get("execution_log", event_log_ref_of(RUN_ID))
    store.put(
        "execution_log", event_log_ref_of(RUN_ID),
        raw + (b'{"seq": 3, "event": "run_finished", "agent_run_id": "'
               + RUN_ID.encode() + b'", "at": "2026-11-01T12:00:00Z", "payload": {}}\n'),
    )
    with pytest.raises(AgentRuntimeValidationError):
        read_run_events(store, RUN_ID)


# ───────────────────────── 循环侧埋点 ─────────────────────────


def test_loop_emits_ordered_events_without_intermediate_text(store):
    log = RunEventLog(store, RUN_ID, now=FIXED_NOW)
    outcome = run_agent_loop(
        task="查证 sh.600000 的公告", endpoint_id="ep_test",
        protocol=_Protocol(), runner=_Runner(), tools=(TOOL,), gate=_Gate(),
        emit=log.emit,
    )
    events = read_run_events(store, RUN_ID)

    assert [event.event for event in events] == [
        "run_started", "model_turn", "step_started", "step_finished",
        "model_turn", "text_delta", "run_finished",
    ]
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert all(event.agent_run_id == RUN_ID for event in events)
    # 中间轮文本不落事件流（中性边界 / 过程可见边界）
    blob = json.dumps([event.payload for event in events], ensure_ascii=False)
    assert INTERMEDIATE not in blob
    assert events[5].payload["text"] == FINAL_TEXT
    assert events[-1].payload["termination"] == "finished"
    assert events[-1].payload["steps"] == 1
    assert outcome.termination == "finished"
