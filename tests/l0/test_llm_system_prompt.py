"""T-AGT-031.1 / .2 测试：系统提示词与两段超时（02 §4 / D-165）。

覆盖：
- 请求体携带 ``system`` 消息；为空时逐字节退回单条 user；
- 端点 ``supports_system_role=False`` 时以确定性前缀并入 user 消息并标注；
- 空闲时限传给 HTTP 读超时、总时限在流中逐行检查（阶段：流中）；
- 传输失败带结构化类别 / 阶段 / 是否可重试；
- 用量记录写入生效时限、失败类别与系统提示词版本/长度（不落明文）；
- 发送器抛出的超时也落出网审计（此前 `except EgressError: raise` 会漏记）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.llm import (
    EndpointRegistry,
    LlmClient,
    OpenAiRoute,
    openai_compat_sender_factory,
    provider_hosts_of,
)
from st_agent.l0.llm.client import TransportError, TransportTimeoutError
from st_agent.l0.net import EgressGateway, EgressTimeoutError
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store

PASS = "system-prompt-test-passphrase"
BASE_URL = "https://api.example.com/v1"
CAP = {"max_context_tokens": 128_000, "supports_structured_output": True}


def sse(*contents: str) -> list[str]:
    return [
        f"data: {json.dumps({'choices': [{'delta': {'content': c}}]}, ensure_ascii=False)}"
        for c in contents
    ] + ["data: [DONE]", ""]


class Recorder:
    def __init__(self, lines: list[str]) -> None:
        self.calls: list[dict] = []
        self._lines = lines

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({"body": body, "timeout_s": timeout_s})
        return iter(self._lines)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def make_client(store: Store, post, *, capability: dict | None = None):
    vault = CredentialVault(store)
    vault.add("main-key", "llm_api_key", "sk-test-123456")
    registry = EndpointRegistry(store)
    registry.register(
        "cloud-main", kind="cloud", provider="openai-compatible",
        capability=capability or CAP, priority=1, credential_id="main-key",
    )
    route = OpenAiRoute(base_url=BASE_URL, model="test-model")
    routes = {"openai-compatible": route}
    gateway = EgressGateway(store, audit=True)
    transport = gateway.llm_transport(
        provider_hosts_of(routes),
        sender_factory=openai_compat_sender_factory(routes, post=post),
    )
    return LlmClient(store, registry, vault, transport), gateway


def payload_of(call: dict) -> dict:
    return json.loads(call["body"].decode("utf-8"))


def test_system_message_is_sent_and_recorded_without_plaintext(store):
    post = Recorder(sse("好"))
    client, _ = make_client(store, post)
    events = list(client.invoke(
        "cloud-main", "用户问题", initiator="t", purpose="p",
        system_prompt="系统指令：中性输出", system_prompt_version="1",
    ))
    assert [e.kind for e in events] == ["chunk", "done"]
    (call,) = post.calls
    messages = payload_of(call)["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == "系统指令：中性输出"
    assert messages[1]["content"] == "用户问题"
    (usage,) = client.query_usage("cloud-main")
    assert usage.system_prompt_version == "1"
    assert usage.system_prompt_chars == len("系统指令：中性输出")
    assert "系统指令" not in usage.model_dump_json()


def test_system_role_degrades_to_annotated_prefix(store):
    post = Recorder(sse("好"))
    capability = {**CAP, "supports_system_role": False}
    client, _ = make_client(store, post, capability=capability)
    list(client.invoke(
        "cloud-main", "用户问题", initiator="t", purpose="p",
        system_prompt="系统指令：中性输出",
    ))
    (call,) = post.calls
    messages = payload_of(call)["messages"]
    assert [m["role"] for m in messages] == ["user"]
    content = messages[0]["content"]
    assert content.startswith("系统提示词（端点不支持 system 角色，已并入用户消息）：")
    assert "系统指令：中性输出" in content
    assert content.endswith("用户问题")


def test_idle_timeout_is_used_for_http_read_and_total_is_checked_in_stream(store):
    post = Recorder(sse("第一块", "第二块"))
    client, gateway = make_client(store, post)
    events = list(client.invoke(
        "cloud-main", "hi", initiator="t", purpose="p",
        timeout_ms=5000, idle_timeout_ms=1200,
    ))
    assert [e.kind for e in events] == ["chunk", "chunk", "done"]
    (call,) = post.calls
    assert call["timeout_s"] == pytest.approx(1.2)
    (usage,) = client.query_usage("cloud-main")
    assert usage.timeout_ms == 5000
    assert usage.idle_timeout_ms == 1200


def test_total_timeout_is_checked_between_lines(store):
    """注入单调时钟：心跳行也算活动，但总时限到点仍在下一行处中止。"""
    route = OpenAiRoute(base_url=BASE_URL, model="test-model")
    ticks = iter([0.0, 0.1, 0.2, 9.0])
    factory = openai_compat_sender_factory(
        {"openai-compatible": route}, post=Recorder([": ping", ": ping", ": ping"]),
        monotonic=lambda: next(ticks),
    )
    registry = EndpointRegistry(store)
    registry.register(
        "cloud-main", kind="cloud", provider="openai-compatible",
        capability=CAP, priority=1, credential_id="k",
    )  # capability 未带 system 角色字段 → 缺省 True
    endpoint = registry.get("cloud-main")
    sender = factory(endpoint, "hi", None, 500, None)
    _, _, chunks = sender("llm_call", route.host, 500)
    with pytest.raises(EgressTimeoutError) as caught:
        list(chunks)
    assert getattr(caught.value, "stage", "") == "stream"
    assert "总时限" in str(caught.value)


def _direct_client(store: Store, transport, endpoint_id: str = "local-main") -> LlmClient:
    vault = CredentialVault(store)
    registry = EndpointRegistry(store)
    registry.register(
        endpoint_id, kind="local", provider="local",
        capability={"max_context_tokens": 8192}, priority=1,
    )
    return LlmClient(store, registry, vault, transport)


def test_timeout_failure_is_structured_and_recorded(store):
    def slow(endpoint, prompt, key, timeout_ms, tools=None, **kwargs):
        raise TransportTimeoutError("空闲超时（>1200ms 无活动，阶段：流中）", stage="stream")

    client = _direct_client(store, slow)
    events = list(client.invoke(
        "local-main", "hi", initiator="t", purpose="p",
        timeout_ms=5000, idle_timeout_ms=1200,
    ))
    (event,) = events
    assert event.kind == "error"
    assert event.failure is not None
    assert event.failure.kind == "timeout"
    assert event.failure.retryable is True
    assert event.failure.stage == "stream"
    (usage,) = client.query_usage("local-main")
    assert usage.fail_kind == "timeout"
    assert usage.fail_stage == "stream"
    assert usage.timeout_ms == 5000
    assert usage.idle_timeout_ms == 1200


def test_rate_limit_retryable_auth_not_retryable(store):
    def rate(endpoint, prompt, key, timeout_ms, tools=None, **kwargs):
        raise TransportError("提供方返回 HTTP 429", status_code=429, retryable=True)
    rate_events = list(_direct_client(store, rate, "rate-main").invoke(
        "rate-main", "hi", initiator="t", purpose="p",
    ))
    assert rate_events[0].failure.kind == "rate_limited"
    assert rate_events[0].failure.retryable is True

    def auth(endpoint, prompt, key, timeout_ms, tools=None, **kwargs):
        raise TransportError("提供方返回 HTTP 401", status_code=401)
    auth_events = list(_direct_client(store, auth, "auth-main").invoke(
        "auth-main", "hi", initiator="t", purpose="p",
    ))
    assert auth_events[0].failure.kind == "auth"
    assert auth_events[0].failure.retryable is False


def test_sender_timeout_is_audited(store):
    gateway = EgressGateway(store, audit=True)

    def sender(kind, host, timeout_ms):
        def chunks():
            raise EgressTimeoutError("空闲超时（>1200ms 无活动，阶段：流中）", stage="stream")
        return (10, 0, chunks())

    with pytest.raises(EgressTimeoutError):
        list(gateway.stream(
            "llm_call", "api.example.com", initiator="t", purpose="p",
            bytes_out=10, timeout_ms=5000, sender=sender,
        ))
    (entry,) = gateway.query(kind="llm_call")
    assert entry.status == "failed"
    assert "空闲超时" in entry.detail


def test_cancel_wins_over_simultaneous_timeout(store):
    import threading

    cancel = threading.Event()

    def slow(endpoint, prompt, key, timeout_ms, tools=None, **kwargs):
        cancel.set()
        raise TransportTimeoutError("总时限超时", stage="stream")

    events = list(_direct_client(store, slow, "cancel-main").invoke(
        "cancel-main", "hi", initiator="t", purpose="p", cancel=cancel,
        timeout_ms=5000, idle_timeout_ms=1200,
    ))
    assert events[0].kind == "error"
    assert events[0].failure.kind == "cancelled"
    assert events[0].failure.retryable is False
