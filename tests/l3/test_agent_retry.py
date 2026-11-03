"""T-AGT-031.3 测试：有界退避重试与 ``llm_failed`` 可续跑。

重试判定只看 ``TurnResult.failure.retryable``（由 L0 结构化分类给出），不解析原因文本；
每次尝试计一次 ``llm_calls``、不计步；重试耗尽以 ``llm_failed`` 收口并保留续跑卡。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import LlmFailure
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import TurnResult, run_agent_loop

EP = "ep_retry"
TASK = "查证 sh.600000 的公告"
SK = "sk_data_aggregate"


class ScriptedProtocol:
    def __init__(self, replies) -> None:
        self._replies = list(replies)
        self.calls = 0
        self.kwargs: list[dict] = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None, **kwargs):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        return self._replies.pop(0) if self._replies else TurnResult(text="完成")


@pytest.fixture()
def rig(store):
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    runner = SkillRunner(store, registry)
    runner.register_executor(f"{SK}_v1.0", lambda ctx, params: ResultEnvelope.ok({}))
    return runner, registry.tool_catalog()


def failure(kind="timeout", *, retryable=True):
    envelope = ResultEnvelope.failed("调用失败", log_ref="llm-run/x.json")
    return TurnResult(
        envelope=envelope,
        failure=LlmFailure(kind=kind, retryable=retryable, stage="stream"),
    )


def drive(rig, protocol, **kwargs):
    runner, tools = rig
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        retry_sleep=lambda _seconds: None, **kwargs,
    )


def test_retryable_failure_is_retried_then_succeeds(rig):
    protocol = ScriptedProtocol([failure(), TurnResult(text="完成")])
    events: list[tuple[str, dict]] = []
    outcome = drive(
        rig, protocol, llm_max_attempts=3, emit=lambda kind, payload: events.append((kind, payload)),
    )
    assert outcome.termination == "finished"
    assert outcome.llm_calls == 2
    turns = [payload for kind, payload in events if kind == "model_turn"]
    assert [item["attempt"] for item in turns] == [1, 2]
    assert turns[0]["max_attempts"] == 3
    assert turns[1]["retry_reason"] == "调用失败"


def test_non_retryable_failure_is_not_retried(rig):
    protocol = ScriptedProtocol([failure("auth", retryable=False)])
    outcome = drive(rig, protocol, llm_max_attempts=3)
    assert outcome.termination == "llm_failed"
    assert outcome.llm_calls == 1
    assert protocol.calls == 1


def test_retry_exhaustion_still_llm_failed(rig):
    protocol = ScriptedProtocol([failure(), failure(), failure()])
    outcome = drive(rig, protocol, llm_max_attempts=3)
    assert outcome.termination == "llm_failed"
    assert outcome.llm_calls == 3
    assert outcome.failure is not None


def test_retry_budget_is_limited_by_max_llm_calls(rig):
    protocol = ScriptedProtocol([failure(), failure(), failure()])
    outcome = drive(rig, protocol, llm_max_attempts=5, max_llm_calls=2)
    assert outcome.termination == "llm_failed"
    assert outcome.llm_calls == 2
    assert "调用次数上界" in outcome.reason


def test_timeouts_are_passed_to_protocol(rig):
    protocol = ScriptedProtocol([TurnResult(text="完成")])
    drive(rig, protocol, llm_timeout_ms=123456, llm_idle_timeout_ms=65432)
    assert protocol.kwargs[0]["timeout_ms"] == 123456
    assert protocol.kwargs[0]["idle_timeout_ms"] == 65432
