"""T-L0-016 测试：LLM 真实发包（02 §4 / §6）——按次 sender + OpenAI 兼容发送器。

GWT 对照（任务文件 5 条）：

- GWT-1 真实发送器**收到 prompt 与 key**；``chunk* → done``；审计 ``ok``（只含字节数）
- GWT-2 SSE 逐块产出、``[DONE]`` 终止；连接失败 / 超时 / 提供方错误映射 ``Transport*``
- GWT-3 **直达**提供方主机、盘上明文 0 命中、审计无内容字段
- GWT-4 provider 未登记请求面 / 未登记主机 → ``unavailable``，不触碰网络
- GWT-5 离线 → ``unavailable`` + ``pending_reconnect``

**全离线**：HTTP 发送口经 ``post`` 注入替身（同 [MCP ``HttpSseTransport``](../l1/_mcp_stub_server.py)
的做法），故 CI 可重复；真实端点由 ``tests/live/test_llm_live.py`` 单跑。
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from st_agent.l0.llm import (
    EndpointRegistry,
    LlmClient,
    OpenAiRoute,
    openai_compat_sender_factory,
    provider_hosts_of,
)
from st_agent.l0.net import EgressGateway, EgressTimeoutError, EgressUnavailableError
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

CLOUD_CAP = {"max_context_tokens": 128_000, "supports_structured_output": True}
BASE_URL = "https://api.example.com/v1"
MODEL = "test-model"
KEY = "sk-proj-abcdef1234567890-KEYSECRET"
PROMPT_MARKER = "PROMPT-MARKER-9137"


def sse_lines(*contents: str) -> list[str]:
    """构造一段 OpenAI 兼容的 SSE 响应行。"""
    return [
        f"data: {json.dumps({'choices': [{'delta': {'content': c}}]}, ensure_ascii=False)}"
        for c in contents
    ] + ["data: [DONE]", ""]


class Recorder:
    """注入式 HTTP 发送口替身（记录请求；按需抛错或产出行）。"""

    def __init__(self, lines: list[str] | None = None, error: Exception | None = None,
                 lazy_error: bool = False) -> None:
        self.calls: list[dict] = []
        self._lines = lines if lines is not None else []
        self._error = error
        self._lazy = lazy_error

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({
            "url": url, "headers": dict(headers), "body": body, "timeout_s": timeout_s,
        })
        if self._error is not None and not self._lazy:
            raise self._error

        def _gen():
            if self._error is not None:
                raise self._error
            yield from self._lines

        return _gen()


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "root"


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    """**不**注入构造期 sender——真实路径全靠按次 sender（缺则 ``unavailable``）。"""
    return EgressGateway(store, audit=True)


def make_client(store: Store, gateway: EgressGateway, post, *, routes=None,
                hosts=None, provider: str = "openai-compatible") -> LlmClient:
    vault = CredentialVault(store)
    vault.add("openai-main", "llm_api_key", KEY)
    registry = EndpointRegistry(store)
    registry.register("cloud-main", kind="cloud", provider=provider,
                      capability=CLOUD_CAP, priority=5, purpose="日常",
                      credential_id="openai-main")
    route_map = routes if routes is not None else {
        provider: OpenAiRoute(base_url=BASE_URL, model=MODEL),
    }
    transport = gateway.llm_transport(
        provider_hosts_of(route_map) if hosts is None else hosts,
        sender_factory=openai_compat_sender_factory(route_map, post=post),
    )
    return LlmClient(store, registry, vault, transport)


def invoke(client: LlmClient, prompt: str = "你好"):
    return list(client.invoke("cloud-main", prompt, initiator="t", purpose="意图理解"))


def all_disk_text(root: Path) -> str:
    return "\n".join(
        p.read_bytes().decode("utf-8", errors="replace")
        for p in root.rglob("*") if p.is_file()
    )


# ───────────────────────── GWT-1 真实发送器收到 prompt 与 key ──────────────


class TestGwt1RealSend:
    def test_prompt_and_key_reach_sender_and_stream_completes(self, store, gateway):
        post = Recorder(sse_lines("你好", "，世界"))
        client = make_client(store, gateway, post)
        events = invoke(client, PROMPT_MARKER)

        (call,) = post.calls
        assert call["url"] == f"{BASE_URL}/chat/completions"
        assert call["headers"]["Authorization"] == f"Bearer {KEY}"
        assert call["headers"]["Accept"] == "text/event-stream"
        body = json.loads(call["body"].decode("utf-8"))
        assert body["model"] == MODEL and body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": PROMPT_MARKER}]

        assert [e.kind for e in events] == ["chunk", "chunk", "done"]
        assert "".join(e.text for e in events) == "你好，世界"
        assert events[-1].usage.completion_tokens > 0

    def test_audit_records_bytes_only(self, store, gateway):
        post = Recorder(sse_lines("你好"))
        client = make_client(store, gateway, post)
        invoke(client, PROMPT_MARKER)

        (event,) = gateway.query(kind="llm_call")
        assert event.status == "ok"
        assert event.target_host == "api.example.com"
        assert event.bytes_out == len(PROMPT_MARKER.encode("utf-8"))
        assert event.initiator == "llm-endpoint:cloud-main"

    def test_local_endpoint_sends_without_authorization(self, store, gateway):
        """本地推理端点无凭据（``kind=local``）→ 不带 Authorization 头。"""
        post = Recorder(sse_lines("ok"))
        vault = CredentialVault(store)
        registry = EndpointRegistry(store)
        registry.register("local-1", kind="local", provider="local-llama",
                          capability=CLOUD_CAP)
        routes = {"local-llama": OpenAiRoute(base_url="http://127.0.0.1:11434/v1",
                                            model=MODEL)}
        transport = gateway.llm_transport(
            provider_hosts_of(routes),
            sender_factory=openai_compat_sender_factory(routes, post=post),
        )
        client = LlmClient(store, registry, vault, transport)
        events = list(client.invoke("local-1", "hi", initiator="t", purpose="p"))

        assert events[-1].kind == "done"
        (call,) = post.calls
        assert call["url"] == "http://127.0.0.1:11434/v1/chat/completions"
        assert "Authorization" not in call["headers"]


# ───────────────────────── GWT-2 流式解析与失败映射 ────────────────────────


class TestGwt2StreamAndFailure:
    def test_delta_content_parsed_incrementally(self, store, gateway):
        lines = [
            ": keep-alive",
            "",
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"content":"半"}}]}',
            'data: {"choices":[{"delta":{"content":"句"}}]}',
            "data: [DONE]",
        ]
        client = make_client(store, gateway, Recorder(lines))
        events = invoke(client, PROMPT_MARKER)
        assert "".join(e.text for e in events) == "半句"
        assert events[-1].kind == "done"

    def test_connection_failure_is_unavailable(self, store, gateway):
        client = make_client(store, gateway, Recorder(error=EgressUnavailableError("不可达")))
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"

    def test_lazy_connection_failure_is_unavailable(self, store, gateway):
        """发包口是生成器时异常在迭代期抛出——映射必须一致。"""
        post = Recorder(error=EgressUnavailableError("不可达"), lazy_error=True)
        client = make_client(store, gateway, post)
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"

    def test_timeout_is_failed(self, store, gateway):
        client = make_client(store, gateway, Recorder(error=EgressTimeoutError("超时")))
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "failed"
        assert events[0].error_envelope.log_ref

    def test_provider_error_payload_is_failed_not_empty_success(self, store, gateway):
        """提供方回 ``error`` 载荷 → ``failed``（**不得**呈现为空回复的成功）。"""
        lines = ['data: {"error":{"message":"rate limited"}}', "data: [DONE]"]
        client = make_client(store, gateway, Recorder(lines))
        events = invoke(client)
        assert events[-1].kind == "error"
        assert events[-1].error_envelope.status == "failed"

    @pytest.mark.parametrize("exc,expected", [
        (urllib.error.HTTPError(BASE_URL, 401, "Unauthorized", {}, None), "failed"),
        (urllib.error.URLError(TimeoutError("timed out")), "failed"),
        (urllib.error.URLError(ConnectionRefusedError("refused")), "unavailable"),
    ])
    def test_urllib_exceptions_map_to_envelopes(self, store, gateway, monkeypatch,
                                                exc, expected):
        """``_urllib_post`` 的异常翻译（不发真包：替换 urlopen）。"""
        def _boom(*_a, **_k):
            raise exc
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        client = make_client(store, gateway, post=None)   # post=None → 走缺省 urllib 实现
        events = invoke(client)
        assert events[-1].kind == "error"
        assert events[-1].error_envelope.status == expected


# ───────────────────────── GWT-3 直达 + 零明文 ─────────────────────────────


class TestGwt3DirectAndZeroPlaintext:
    def test_no_plaintext_on_disk_or_in_audit(self, store, gateway, root):
        post = Recorder(sse_lines("回复正文-SECRET"))
        client = make_client(store, gateway, post)
        invoke(client, PROMPT_MARKER)

        blob = all_disk_text(root)
        for secret in (PROMPT_MARKER, KEY, "回复正文-SECRET"):
            assert secret not in blob, f"明文 {secret!r} 出现在落盘文件里"
        assert PROMPT_MARKER not in str(gateway.export_report())

    def test_target_host_derived_from_route(self, store, gateway):
        """审计主机与实际请求主机**同源**（``provider_hosts_of``）。"""
        routes = {"openai-compatible": OpenAiRoute(
            base_url="https://llm.vendor.cn/openai/v1", model=MODEL)}
        post = Recorder(sse_lines("ok"))
        client = make_client(store, gateway, post, routes=routes)
        invoke(client)

        assert post.calls[0]["url"].startswith("https://llm.vendor.cn/")
        (event,) = gateway.query(kind="llm_call")
        assert event.target_host == "llm.vendor.cn"

    def test_audit_host_mismatch_fails_closed(self, store, gateway):
        """审计主机与请求面不一致 → 拒绝发包（不产生「记 A 发 B」的失真）。"""
        routes = {"openai-compatible": OpenAiRoute(base_url=BASE_URL, model=MODEL)}
        post = Recorder(sse_lines("ok"))
        client = make_client(store, gateway, post, routes=routes,
                             hosts={"openai-compatible": "other.example.com"})
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"
        assert post.calls == []          # 未发包


# ───────────────────────── GWT-4 未登记即降级 ──────────────────────────────


class TestGwt4Unregistered:
    def test_unknown_provider_route_is_unavailable(self, store, gateway):
        """provider 有主机映射、但无请求面（base_url/model）→ unavailable。"""
        post = Recorder(sse_lines("ok"))
        client = make_client(store, gateway, post, routes={},
                             hosts={"openai-compatible": "api.example.com"})
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"
        assert post.calls == []

    def test_unknown_host_is_unavailable_without_sending(self, store, gateway):
        post = Recorder(sse_lines("ok"))
        client = make_client(store, gateway, post, hosts={})
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"
        assert post.calls == []


# ───────────────────────── GWT-5 离线 ─────────────────────────────────────


class TestGwt5Offline:
    def test_offline_is_unavailable_with_pending_reconnect(self, store, gateway):
        post = Recorder(sse_lines("ok"))
        client = make_client(store, gateway, post)
        gateway.set_online(False)
        events = invoke(client)
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"
        assert post.calls == []
        assert gateway.pending_reconnect() != ()


# ───────────────────────── 结构面：OpenAiRoute / provider_hosts_of ─────────


class TestRouteShape:
    @pytest.mark.parametrize("base_url,model", [
        ("", "m"), ("/v1", "m"), ("api.example.com/v1", "m"),
        ("ftp://api.example.com/v1", "m"), (BASE_URL, "  "),
    ])
    def test_illegal_route_rejected(self, base_url, model):
        with pytest.raises(ValueError):
            OpenAiRoute(base_url=base_url, model=model)

    def test_url_and_host(self):
        route = OpenAiRoute(base_url="https://api.example.com:8443/v1/", model="m")
        assert route.url == "https://api.example.com:8443/v1/chat/completions"
        assert route.host == "api.example.com"      # 审计主机不含端口（02 §6）

    def test_provider_hosts_of(self):
        routes = {
            "a": OpenAiRoute(base_url="https://a.example.com/v1", model="m"),
            "b": OpenAiRoute(base_url="http://127.0.0.1:11434/v1", model="m"),
        }
        assert provider_hosts_of(routes) == {
            "a": "a.example.com", "b": "127.0.0.1",
        }

    def test_sender_factory_returns_sender_with_gateway_signature(self, store, gateway):
        """工厂产物须能吃网关的三参调用（kind / target_host / timeout_ms）。"""
        routes = {"openai-compatible": OpenAiRoute(base_url=BASE_URL, model=MODEL)}
        factory = openai_compat_sender_factory(routes, post=Recorder(sse_lines("x")))

        class _Endpoint:
            provider = "openai-compatible"

        sender = factory(_Endpoint(), "hi", None, 5000)
        out, inb, chunks = sender("llm_call", "api.example.com", 5000)
        assert out > 0 and inb == 0 and "".join(chunks) == "x"
