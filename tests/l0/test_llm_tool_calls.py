"""T-AGT-001 测试：LLM 工具调用通道（02 §4）——tools 入参 / tool_calls 出参。

GWT 对照（任务文件 5 条）：

- GWT-1 不带 ``tools`` → 请求体**逐字节一致**（无 ``tools`` 字段）
- GWT-2 带 ``tools`` → 请求体含 ``tools`` 且形态合法（名称 / 描述 / 参数 schema）
- GWT-3 流式工具调用增量 → **拼出完整调用**（名称 + 完整参数对象 + 标识），
  且**不混入正文文本**
- GWT-4 端点未声明 ``supports_function_calling`` 而上层要工具面 → **显式拒绝**，
  **不静默发包**
- GWT-5 ``tools`` 形态非法（空名 / 结构坏）→ ``validation_failed`` 且**不发包**

**全离线**：HTTP 发送口经 ``post`` 注入替身（同 `test_llm_http_transport.py`），
故 CI 可重复；真实端点由 ``tests/live/test_llm_live.py`` 单跑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from st_agent.l0.llm import (
    CapabilityRequirement,
    EndpointCapability,
    EndpointRegistry,
    LlmClient,
    LlmValidationError,
    OpenAiRoute,
    ToolCall,
    ToolSpec,
    openai_compat_sender_factory,
    provider_hosts_of,
)
from st_agent.l0.net import EgressGateway
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"
BASE_URL = "https://api.example.com/v1"
MODEL = "test-model"
KEY = "sk-proj-abcdef1234567890-KEYSECRET"
PROMPT = "查一下 600000 的退市风险"
PROVIDER = "openai-compatible"

#: 端点**声明**支持工具调用（真实档位由 T-AGT-002 探测落定，本任务只消费）。
TOOLED_CAP = {
    "max_context_tokens": 128_000,
    "supports_structured_output": True,
    "supports_function_calling": True,
}
#: 端点**未声明**工具调用——缺省即 `False`，门控靠它成立。
PLAIN_CAP = {"max_context_tokens": 128_000, "supports_structured_output": True}

CHECK_TOOL = ToolSpec(
    name="sk_unhat_eligibility_check",
    description="核验单只标的的退市高危状态",
    parameters={
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
)


def sse_text(content: str) -> str:
    """一条文本增量的 SSE 行。"""
    return f"data: {json.dumps({'choices': [{'delta': {'content': content}}]}, ensure_ascii=False)}"


def sse_tool(index: int, *, call_id: str | None = None, name: str | None = None,
             arguments: str | None = None) -> str:
    """一条 ``delta.tool_calls`` 增量的 SSE 行（各字段按需分片）。"""
    function: dict = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    fragment: dict = {"index": index, "type": "function", "function": function}
    if call_id is not None:
        fragment["id"] = call_id
    return f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [fragment]}}]}, ensure_ascii=False)}"


class Recorder:
    """注入式 HTTP 发送口替身（记录请求，按需产出行）。"""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self._lines = lines if lines is not None else []

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({
            "url": url, "headers": dict(headers), "body": body, "timeout_s": timeout_s,
        })
        return iter(self._lines)

    def body(self) -> dict:
        """唯一一次请求的 JSON 请求体。"""
        (call,) = self.calls
        return json.loads(call["body"].decode("utf-8"))


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store, audit=True)


def make_client(store: Store, gateway: EgressGateway, post: Recorder,
                *, capability: dict = TOOLED_CAP) -> LlmClient:
    vault = CredentialVault(store)
    vault.add("openai-main", "llm_api_key", KEY)
    registry = EndpointRegistry(store)
    registry.register("cloud-main", kind="cloud", provider=PROVIDER,
                      capability=capability, priority=5, purpose="日常",
                      credential_id="openai-main")
    routes = {PROVIDER: OpenAiRoute(base_url=BASE_URL, model=MODEL)}
    transport = gateway.llm_transport(
        provider_hosts_of(routes),
        sender_factory=openai_compat_sender_factory(routes, post=post),
    )
    return LlmClient(store, registry, vault, transport)


def invoke(client: LlmClient, *, tools=None, requirement=None, prompt: str = PROMPT):
    return list(client.invoke(
        "cloud-main", prompt, initiator="t", purpose="自主查证",
        tools=tools, requirement=requirement,
    ))


# ───────────────────────── GWT-1 不带 tools 逐字节一致 ─────────────────────


class TestGwt1UntooledBodyUnchanged:
    def test_body_without_tools_is_byte_identical(self, store, gateway):
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        client = make_client(store, gateway, post)
        invoke(client)
        expected = json.dumps(
            {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}],
             "stream": True},
            ensure_ascii=False,
        ).encode("utf-8")
        assert post.calls[0]["body"] == expected

    def test_empty_tools_is_equivalent_to_absent(self, store, gateway):
        """空序列 ≡ 不带（单轮纯文本路径不因传空列表而变形）。"""
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        client = make_client(store, gateway, post)
        invoke(client)
        invoke(client, tools=[])
        assert post.calls[1]["body"] == post.calls[0]["body"]

    def test_unused_tools_leave_audit_bytes_unchanged(self, store, gateway):
        """既有审计口径（``bytes_out`` = prompt 字节）不回归。"""
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        invoke(make_client(store, gateway, post))
        (event,) = gateway.query(kind="llm_call")
        assert event.bytes_out == len(PROMPT.encode("utf-8"))


# ───────────────────────── GWT-2 带 tools 形态合法 ────────────────────────


class TestGwt2TooledBody:
    def test_body_carries_wire_tools(self, store, gateway):
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        assert post.body()["tools"] == [{
            "type": "function",
            "function": {
                "name": "sk_unhat_eligibility_check",
                "description": "核验单只标的的退市高危状态",
                "parameters": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                },
            },
        }]

    def test_mapping_entries_accepted(self, store, gateway):
        """等价映射与模型同路（宽进口径，同 ``requirement`` 的既有做法）。"""
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        invoke(make_client(store, gateway, post),
               tools=[{"name": "sk_x", "description": "d"}])
        assert post.body()["tools"][0]["function"]["name"] == "sk_x"
        assert post.body()["tools"][0]["function"]["parameters"] == {}

    def test_base_with_dot_is_accepted(self, store, gateway):
        """工具名字母表以平台 base 形态为准（``.`` 合法，不按 OpenAI 严格表收紧）。"""
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        invoke(make_client(store, gateway, post),
               tools=[ToolSpec(name="sk_a.b-c_d", description="d")])
        assert post.body()["tools"][0]["function"]["name"] == "sk_a.b-c_d"

    def test_multiple_tools_keep_declared_order(self, store, gateway):
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        invoke(make_client(store, gateway, post),
               tools=[ToolSpec(name="sk_a"), ToolSpec(name="sk_b")])
        assert [t["function"]["name"] for t in post.body()["tools"]] == ["sk_a", "sk_b"]


# ───────────────────────── GWT-3 流式增量拼装 ─────────────────────────────


class TestGwt3StreamingAssembly:
    def test_deltas_assemble_into_complete_call(self, store, gateway):
        """名称与参数串都按增量拼接，收束得完整调用；增量**不外泄为文本**。"""
        post = Recorder([
            sse_text("先查"),
            sse_tool(0, call_id="call_77", name="sk_unhat_elig", arguments='{"code"'),
            sse_tool(0, name="ibility_check", arguments=': "sh.600'),
            sse_tool(0, arguments='000"}'),
            "data: [DONE]",
        ])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])

        assert [e.kind for e in events] == ["chunk", "tool_call", "done"]
        assert "".join(e.text for e in events) == "先查"        # 参数增量没混进正文
        call = events[1].tool_call
        assert call == ToolCall(call_id="call_77", name="sk_unhat_eligibility_check",
                                arguments={"code": "sh.600000"})

    def test_stream_without_done_still_assembles(self, store, gateway):
        """端点直接断流（无 ``[DONE]``）→ 行尽同样收束，不把拼到一半的静默丢掉。"""
        post = Recorder([
            sse_tool(0, call_id="call_1", name="sk_x", arguments='{"a": 1}'),
        ])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        assert [e.kind for e in events] == ["tool_call", "done"]
        assert events[0].tool_call.arguments == {"a": 1}

    def test_empty_arguments_object(self, store, gateway):
        post = Recorder([
            sse_tool(0, call_id="call_2", name="sk_x"),
            "data: [DONE]",
        ])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        assert events[0].tool_call.arguments == {}

    def test_parallel_indices_yield_one_event_each_in_order(self, store, gateway):
        """按 ``index`` 分槽、升序收束（本期不做并行调用，但协议须如实拆开）。"""
        post = Recorder([
            sse_tool(1, call_id="call_b", name="sk_b", arguments="{}"),
            sse_tool(0, call_id="call_a", name="sk_a", arguments="{}"),
            "data: [DONE]",
        ])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        assert [e.tool_call.call_id for e in events[:2]] == ["call_a", "call_b"]

    @pytest.mark.parametrize("lines,needle", [
        ([sse_tool(0, call_id="call_1", name="sk_x", arguments='{"a"')], "不是合法 JSON"),
        ([sse_tool(0, call_id="call_1", name="sk_x", arguments="[1]")], "须为 JSON 对象"),
        ([sse_tool(0, name="sk_x", arguments="{}")], "缺标识"),
        ([sse_tool(0, call_id="call_1", arguments="{}")], "缺名称"),
    ])
    def test_inconsistent_call_is_failed_not_fabricated(self, store, gateway, lines, needle):
        """拼装期的不一致 → ``failed``（**不伪造**空调用糊过去）。"""
        events = invoke(make_client(store, gateway, Recorder(lines)), tools=[CHECK_TOOL])
        assert events[-1].kind == "error"
        assert events[-1].error_envelope.status == "failed"
        assert needle in events[-1].error_envelope.reason

    def test_tool_call_bytes_counted_into_audit(self, store, gateway):
        """工具调用项按其规范序列化计入 ``bytes_in``（不静默按 0 计）。"""
        post = Recorder([
            sse_text("x"),
            sse_tool(0, call_id="call_9", name="sk_x", arguments='{"a": 1}'),
            "data: [DONE]",
        ])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        call = next(e.tool_call for e in events if e.kind == "tool_call")
        (event,) = gateway.query(kind="llm_call")
        assert event.bytes_in == len("x".encode("utf-8")) + len(call.to_wire_bytes())

    def test_tool_call_output_counted_into_usage(self, store, gateway):
        post = Recorder([
            sse_tool(0, call_id="call_9", name="sk_x", arguments='{"code": "sh.600000"}'),
            "data: [DONE]",
        ])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        assert events[-1].usage.completion_tokens > 0


# ───────────────────────── GWT-4 能力门控 ────────────────────────────────


class TestGwt4CapabilityGate:
    def test_undeclared_capability_refuses_without_sending(self, store, gateway):
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        client = make_client(store, gateway, post, capability=PLAIN_CAP)
        events = invoke(client, tools=[CHECK_TOOL])
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "validation_failed"
        assert "工具调用" in events[0].error_envelope.reason
        assert post.calls == []                       # 未静默发包

    def test_default_capability_is_false(self):
        """缺省**不得假定为真**（02 §4 能力诚实性）。"""
        assert EndpointCapability(max_context_tokens=1000).supports_function_calling is False

    def test_requirement_alone_also_refuses(self, store, gateway):
        """不带 ``tools`` 但显式声明需求 → 同样判拒（协商面与入参面同口径）。"""
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        client = make_client(store, gateway, post, capability=PLAIN_CAP)
        events = invoke(client, requirement=CapabilityRequirement(needs_function_calling=True))
        assert events[0].error_envelope.status == "validation_failed"
        assert post.calls == []

    def test_declared_capability_sends(self, store, gateway):
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        events = invoke(make_client(store, gateway, post), tools=[CHECK_TOOL])
        assert events[-1].kind == "done"
        assert len(post.calls) == 1

    def test_negotiate_reports_reason(self, store):
        registry = EndpointRegistry(store)
        registry.register("plain", kind="local", provider="p", capability=PLAIN_CAP)
        ok, reason = registry.negotiate(
            "plain", CapabilityRequirement(needs_function_calling=True)
        )
        assert ok is False and "工具调用" in reason


# ───────────────────────── GWT-5 非法形态拒收 ─────────────────────────────


class TestGwt5ToolShapeValidation:
    @pytest.mark.parametrize("tools", [
        [{"name": ""}],                                   # 空名
        [{"name": "   "}],                                # 空白名
        [{"description": "无名字"}],                       # 缺名
        [{"name": "sk_x", "parameters": "not-an-object"}],  # 参数 schema 结构坏
        [{"name": "sk_x", "parameters": {"type": "array"}}],
        [{"name": "sk_x", "parameters": {"type": "object", "properties": []}}],
        [{"name": "sk_x" * 40}],                          # 超长名
        [42],                                             # 非条目
        "sk_x",                                           # 非序列
    ])
    def test_illegal_tools_refused_without_sending(self, store, gateway, tools):
        post = Recorder([sse_text("ok"), "data: [DONE]"])
        events = invoke(make_client(store, gateway, post), tools=tools)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "validation_failed"
        assert "工具条目非法" in events[0].error_envelope.reason
        assert post.calls == []                           # 不发包

    def test_tool_spec_rejects_empty_name_directly(self):
        with pytest.raises((LlmValidationError, PydanticValidationError)):
            ToolSpec(name="")

    def test_tool_spec_rejects_non_object_parameters(self):
        with pytest.raises((LlmValidationError, PydanticValidationError)):
            ToolSpec(name="sk_x", parameters={"type": "string"})

    def test_tool_spec_reexported_from_package(self):
        assert ToolSpec(name="sk_x").name == "sk_x"
