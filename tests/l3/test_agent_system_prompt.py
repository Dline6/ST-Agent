"""T-AGT-031.1 测试：系统提示词的确定性、覆盖项与循环透传。

请求体层面的 ``system`` 消息与降级路径见 `tests/l0/test_llm_system_prompt.py`；
本文件钉「装配→工作上下文→协议端口」这一段。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import LlmFailure, StreamEvent
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime import (
    NativeToolCallProtocol,
    TurnResult,
    WorkContext,
    run_agent_loop,
)
from st_agent.l3.runtime.system_prompt import SYSTEM_PROMPT_VERSION, build_system_prompt

EP = "ep_sys"
TASK = "查证 sh.600000 的公告"
SK = "sk_data_aggregate"
NOW = datetime(2026, 11, 3, 15, 30, tzinfo=timezone(timedelta(hours=8)))


class CaptureProtocol:
    def __init__(self) -> None:
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None):
        self.contexts.append(context)
        return TurnResult(text="完成")


@pytest.fixture()
def rig(store):
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    runner = SkillRunner(store, registry)
    runner.register_executor(f"{SK}_v1.0", lambda ctx, params: ResultEnvelope.ok({}))
    return runner, registry.tool_catalog()


def test_prompt_is_deterministic_and_covers_required_sections():
    first = build_system_prompt(now=NOW)
    second = build_system_prompt(now=NOW)
    assert first == second
    assert "2026-11-03" in first
    assert "UTC+08:00" in first
    for keyword in ("中性", "工具", "as_of", "fetched_at", "sk_ask_user", "sk_result_read"):
        assert keyword in first
    assert SYSTEM_PROMPT_VERSION in first


def test_prompt_has_no_first_person_or_advice_phrasing():
    text = build_system_prompt(now=NOW)
    for banned in ("我是", "我觉得", "我认为", "买入", "卖出", "推荐买", "强烈建议"):
        assert banned not in text
    assert "我" not in text


def test_prompt_requires_tz_aware_clock():
    with pytest.raises(AgentRuntimeValidationError):
        build_system_prompt(now=datetime(2026, 11, 3, 15, 30))


def test_loop_passes_prompt_into_work_context_and_counts_it(store, rig):
    runner, tools = rig
    protocol = CaptureProtocol()
    prompt = build_system_prompt(now=NOW)
    outcome = run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        system_prompt=prompt, system_prompt_version=SYSTEM_PROMPT_VERSION,
    )
    assert outcome.termination == "finished"
    (context,) = protocol.contexts
    assert context.system_prompt == prompt
    assert context.system_prompt_version == SYSTEM_PROMPT_VERSION
    # 规模口径含系统提示词；不并入 render_prompt 的文本段
    assert prompt not in context.render_prompt()
    assert context.size_chars >= len(prompt)


def test_native_protocol_forwards_prompt_timeouts_and_failure():
    captured: dict = {}

    class FakeLlm:
        def invoke(self, endpoint_id, prompt, **kwargs):
            captured.update({"endpoint_id": endpoint_id, "prompt": prompt, **kwargs})
            yield StreamEvent(
                kind="error",
                error_envelope=ResultEnvelope.failed("超时", log_ref="llm-run/x.json"),
                failure=LlmFailure(kind="timeout", retryable=True, stage="stream"),
            )

    class FakeEndpoints:
        def get(self, endpoint_id):
            return SimpleNamespace(capability=SimpleNamespace(supports_function_calling=True))

    protocol = NativeToolCallProtocol(
        llm=FakeLlm(), endpoints=FakeEndpoints(), endpoint_id=EP,
    )
    context = WorkContext(
        task=TASK, system_prompt="系统指令", system_prompt_version="1",
        budget_chars=1000, size_chars=0,
    )
    result = protocol.invoke_turn(
        context, initiator="t", purpose="p", timeout_ms=5000, idle_timeout_ms=1200,
    )
    assert captured["system_prompt"] == "系统指令"
    assert captured["system_prompt_version"] == "1"
    assert captured["timeout_ms"] == 5000
    assert captured["idle_timeout_ms"] == 1200
    assert result.envelope is not None
    assert result.failure is not None and result.failure.retryable is True
