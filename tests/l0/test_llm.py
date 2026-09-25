"""T-L0-003 测试：02-L0 §4 LLM 端点抽象。

GWT 对照（任务文件 5 条）：
- GWT-1 多端点并存：配置落 config 分区，优先级排序生效
- GWT-2 能力协商：档位不足明确降级，不静默失败
- GWT-3 流式返回：chunk* → done；取消/超时显式上报
- GWT-4 零中转与用量：Key 经 CredentialVault 取用；用量只记计数；
  prompt/response 不落盘；传输收到的 Key 与凭据一致（直连口径）
- GWT-5 失败语义：失败一律走 ResultEnvelope（unavailable/failed/
  dependency_failed/validation_failed）
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from st_agent.l0.llm import (
    CapabilityRequirement,
    EndpointCapability,
    EndpointRegistry,
    LlmClient,
    LlmExistsError,
    LlmNotFoundError,
    LlmUsageRecord,
    LlmValidationError,
    StreamEvent,
    TransportTimeoutError,
    TransportUnavailableError,
    estimate_tokens,
)
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

CLOUD_CAP = {"max_context_tokens": 128_000, "supports_structured_output": True}
LOCAL_CAP = {"max_context_tokens": 4_096, "supports_structured_output": False}


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def vault(store: Store) -> CredentialVault:
    return CredentialVault(store)


@pytest.fixture()
def registry(store: Store) -> EndpointRegistry:
    return EndpointRegistry(store)


@pytest.fixture()
def local_client(store: Store, registry: EndpointRegistry, vault: CredentialVault) -> LlmClient:
    registry.register("local-main", kind="local", provider="local-llama",
                      capability=LOCAL_CAP, priority=10, purpose="日常")
    def transport(endpoint, prompt, key, timeout_ms):
        assert key is None  # 本地端点无 Key
        yield "你好，"
        yield "这是本地推理。"
    return LlmClient(store, registry, vault, transport=transport)


@pytest.fixture()
def cloud_client(store: Store, registry: EndpointRegistry, vault: CredentialVault) -> LlmClient:
    vault.add("openai-main", "llm_api_key", "sk-proj-abcdef1234567890")
    registry.register("cloud-main", kind="cloud", provider="openai-compatible",
                      capability=CLOUD_CAP, priority=5, purpose="日常",
                      credential_id="openai-main")
    seen: dict = {}
    def transport(endpoint, prompt, key, timeout_ms):
        seen["key"] = key
        seen["prompt"] = prompt
        yield "云端"
        yield "回答"
    client = LlmClient(store, registry, vault, transport=transport)
    client._seen = seen  # type: ignore[attr-defined]
    return client


def root_of(client: LlmClient) -> Path:
    return client._store._root


def drain(events):
    return list(events)


# ───────────────────────── GWT-1 多端点并存 ─────────────────────────


class TestGwt1MultiEndpoint:
    def test_register_lists_by_priority(self, registry):
        registry.register("cloud-a", kind="local", provider="p",
                          capability=LOCAL_CAP, priority=50)
        registry.register("cloud-b", kind="local", provider="p",
                          capability=LOCAL_CAP, priority=5)
        assert [e.endpoint_id for e in registry.list_endpoints()] == ["cloud-b", "cloud-a"]

    def test_config_in_config_partition(self, registry, store):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        names = [n for n in store.list_files("config")]
        assert names == ["llm-endpoint/e1.json"]
        raw = (store._root / "config" / "llm-endpoint/e1.json").read_bytes()
        assert b"credential" not in raw.lower() or b"credential_id" in raw  # 仅引用键名
        payload = json.loads(store.get("config", "llm-endpoint/e1.json").decode())
        assert "value" not in payload  # 配置无明文字段

    def test_duplicate_rejected(self, registry):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        with pytest.raises(LlmExistsError, match="已存在"):
            registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)

    def test_cloud_requires_credential(self, registry):
        with pytest.raises(LlmValidationError, match="凭据"):
            registry.register("c1", kind="cloud", provider="p", capability=CLOUD_CAP)

    def test_local_forbids_credential(self, registry):
        with pytest.raises(LlmValidationError, match="凭据"):
            registry.register("l1", kind="local", provider="p",
                              capability=LOCAL_CAP, credential_id="k")

    def test_update_priority_reorders(self, registry):
        registry.register("a", kind="local", provider="p", capability=LOCAL_CAP, priority=1)
        registry.register("b", kind="local", provider="p", capability=LOCAL_CAP, priority=2)
        registry.update_priority("b", priority=0)
        assert [e.endpoint_id for e in registry.list_endpoints()] == ["b", "a"]

    def test_remove_returns_snapshot(self, registry):
        registry.register("a", kind="local", provider="p", capability=LOCAL_CAP)
        snap = registry.remove("a")
        assert snap.endpoint_id == "a"
        with pytest.raises(LlmNotFoundError):
            registry.get("a")

    def test_bad_id_rejected(self, registry):
        for bad in ("", "../escape", "x" * 65):
            with pytest.raises(LlmValidationError, match="标识"):
                registry.register(bad, kind="local", provider="p", capability=LOCAL_CAP)


# ───────────────────────── GWT-2 能力协商 ─────────────────────────


class TestGwt2Negotiation:
    def test_satisfied(self, registry):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        ok, reason = registry.negotiate("e1", CapabilityRequirement(min_context_tokens=100))
        assert ok and reason == ""

    def test_context_shortage(self, registry):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        ok, reason = registry.negotiate(
            "e1", CapabilityRequirement(min_context_tokens=1_000_000))
        assert not ok and "上下文不足" in reason

    def test_structured_output_missing(self, registry):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        ok, reason = registry.negotiate(
            "e1", CapabilityRequirement(needs_structured_output=True))
        assert not ok and "结构化输出" in reason

    def test_unsatisfied_yields_validation_failed(self, local_client):
        events = drain(local_client.invoke(
            "local-main", "hi", initiator="t", purpose="p",
            requirement=CapabilityRequirement(needs_structured_output=True)))
        assert len(events) == 1 and events[0].kind == "error"
        assert events[0].error_envelope.status == "validation_failed"

    def test_pick_first_satisfying(self, registry):
        registry.register("weak", kind="local", provider="p",
                          capability=LOCAL_CAP, priority=1)
        registry.register("strong", kind="local", provider="p",
                          capability=CLOUD_CAP, priority=2)
        picked = registry.pick(CapabilityRequirement(needs_structured_output=True))
        assert picked is not None and picked.endpoint_id == "strong"

    def test_pick_none_when_no_match(self, registry):
        registry.register("weak", kind="local", provider="p", capability=LOCAL_CAP)
        assert registry.pick(CapabilityRequirement(needs_structured_output=True)) is None


# ───────────────────────── GWT-3 流式返回 ─────────────────────────


class TestGwt3Streaming:
    def test_chunks_then_done(self, local_client):
        events = drain(local_client.invoke(
            "local-main", "讲个故事", initiator="l3-chat", purpose="日常问答"))
        assert [e.kind for e in events] == ["chunk", "chunk", "done"]
        assert "".join(e.text for e in events if e.kind == "chunk") == "你好，这是本地推理。"
        done = events[-1]
        assert done.usage is not None and done.usage.status == "ok"
        assert done.usage.total_tokens == (
            done.usage.prompt_tokens + done.usage.completion_tokens)

    def test_cancel_before_start(self, local_client):
        flag = threading.Event()
        flag.set()
        events = drain(local_client.invoke(
            "local-main", "hi", initiator="t", purpose="p", cancel=flag))
        assert len(events) == 1 and events[0].kind == "error"
        assert events[0].error_envelope.status == "failed"
        assert "取消" in events[0].error_envelope.reason

    def test_cancel_mid_stream(self, store, registry, vault):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        flag = threading.Event()
        def transport(endpoint, prompt, key, timeout_ms):
            yield "第一块"
            flag.set()  # 首块后取消
            yield "第二块（不应出现）"
        client = LlmClient(store, registry, vault, transport=transport)
        events = drain(client.invoke("e1", "hi", initiator="t", purpose="p", cancel=flag))
        kinds = [e.kind for e in events]
        assert kinds[0] == "chunk" and kinds[-1] == "error"
        assert "取消" in events[-1].error_envelope.reason
        assert "第二块" not in "".join(e.text for e in events if e.kind == "chunk")

    def test_timeout_reported(self, store, registry, vault):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        def slow(endpoint, prompt, key, timeout_ms):
            raise TransportTimeoutError("传输层超时（>100ms）")
        client = LlmClient(store, registry, vault, transport=slow)
        events = drain(client.invoke("e1", "hi", initiator="t", purpose="p", timeout_ms=100))
        assert len(events) == 1 and events[0].kind == "error"
        assert events[0].error_envelope.status == "failed"
        assert "超时" in events[0].error_envelope.reason


# ───────────────────────── GWT-4 零中转与用量 ─────────────────────────


class TestGwt4ZeroRelayAndUsage:
    def test_cloud_key_passed_to_transport(self, cloud_client):
        events = drain(cloud_client.invoke(
            "cloud-main", "分析这只股票", initiator="l4-lens", purpose="多视角调用"))
        assert events[-1].kind == "done"
        assert cloud_client._seen["key"] == "sk-proj-abcdef1234567890"
        assert cloud_client._seen["prompt"] == "分析这只股票"

    def test_usage_recorded_locally(self, cloud_client):
        before = datetime.now().astimezone() - timedelta(seconds=1)
        events = drain(cloud_client.invoke(
            "cloud-main", "hello world", initiator="l1-skill", purpose="测试",
            requirement={"min_context_tokens": 10}))
        done = events[-1]
        assert done.kind == "done"
        (entry,) = cloud_client.query_usage("cloud-main")
        assert entry.timestamp >= before and entry.timestamp.tzinfo is not None
        assert entry.initiator == "l1-skill" and entry.purpose == "测试"
        assert entry.total_tokens == entry.prompt_tokens + entry.completion_tokens
        assert entry.prompt_tokens == estimate_tokens("hello world")
        assert entry.completion_tokens == estimate_tokens("云端回答")
        assert entry.status == "ok"

    def test_usage_lives_in_execution_log(self, cloud_client):
        drain(cloud_client.invoke("cloud-main", "hi", initiator="t", purpose="p"))
        names = cloud_client._store.list_files("execution_log")
        llm_names = [n for n in names if n.startswith("llm-usage/cloud-main/")]
        assert len(llm_names) == 1  # 另有一条 cred-usage（凭据取用留痕，GWT-3 口径）
        assert cloud_client._store.list_files("config") == ("llm-endpoint/cloud-main.json",)

    def test_prompt_response_never_on_disk(self, cloud_client):
        prompt = "这是秘密prompt-7f3a9c"
        drain(cloud_client.invoke("cloud-main", prompt, initiator="t", purpose="p"))
        root = root_of(cloud_client)
        for f in root.rglob("*"):
            if f.is_file() and f.name != "keyfile.json":
                blob = f.read_bytes()
                assert prompt.encode() not in blob, f.name
                assert "云端回答".encode() not in blob, f.name

    def test_usage_has_no_content_fields(self, cloud_client):
        drain(cloud_client.invoke("cloud-main", "hi", initiator="t", purpose="p"))
        (entry,) = cloud_client.query_usage("cloud-main")
        blob = entry.model_dump_json()
        for forbidden in ("prompt", "response", "text", "value"):
            assert f'"{forbidden}"' not in blob

    def test_key_never_in_usage(self, cloud_client):
        drain(cloud_client.invoke("cloud-main", "hi", initiator="t", purpose="p"))
        (entry,) = cloud_client.query_usage("cloud-main")
        assert "sk-proj-abcdef1234567890" not in entry.model_dump_json()

    def test_missing_credential_is_dependency_failed(self, store, registry, vault):
        registry.register("c1", kind="cloud", provider="p", capability=CLOUD_CAP,
                          credential_id="gone-key")
        client = LlmClient(store, registry, vault, transport=lambda *a: iter(()))
        events = drain(client.invoke("c1", "hi", initiator="t", purpose="p"))
        assert len(events) == 1 and events[0].kind == "error"
        assert events[0].error_envelope.status == "dependency_failed"

    def test_no_transport_is_unavailable(self, store, registry, vault):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        client = LlmClient(store, registry, vault)  # 无传输（等 T-L0-004 网关）
        events = drain(client.invoke("e1", "hi", initiator="t", purpose="p"))
        assert len(events) == 1 and events[0].kind == "error"
        env = events[0].error_envelope
        assert env.status == "unavailable"
        assert env.last_updated_at is not None  # 01 §5 不变量


# ───────────────────────── GWT-5 失败语义 ─────────────────────────


class TestGwt5FailureSemantics:
    def test_endpoint_down_is_unavailable(self, store, registry, vault):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        def down(endpoint, prompt, key, timeout_ms):
            raise TransportUnavailableError("端点连接被拒绝")
        events = drain(LlmClient(store, registry, vault, transport=down).invoke(
            "e1", "hi", initiator="t", purpose="p"))
        assert events[0].error_envelope.status == "unavailable"
        assert events[0].error_envelope.last_updated_at is not None

    def test_failed_carries_log_ref(self, store, registry, vault):
        registry.register("e1", kind="local", provider="p", capability=LOCAL_CAP)
        def slow(endpoint, prompt, key, timeout_ms):
            raise TransportTimeoutError("超时")
        events = drain(LlmClient(store, registry, vault, transport=slow).invoke(
            "e1", "hi", initiator="t", purpose="p"))
        env = events[0].error_envelope
        assert env.status == "failed" and env.log_ref
        (entry,) = LlmClient(store, registry, vault).query_usage("e1")
        assert entry.status == "failed" and entry.fail_reason == "超时"

    def test_empty_prompt_rejected(self, local_client):
        events = drain(local_client.invoke("local-main", "", initiator="t", purpose="p"))
        assert events[0].error_envelope.status == "validation_failed"

    def test_prompt_over_context_rejected(self, local_client):
        big = "字" * 20_000  # 约 5000 tokens > 本地端点 4096 上限
        assert estimate_tokens(big) > 4_096
        events = drain(local_client.invoke("local-main", big, initiator="t", purpose="p"))
        assert events[0].error_envelope.status == "validation_failed"
        assert "上下文" in events[0].error_envelope.reason

    def test_unknown_endpoint_raises(self, local_client):
        with pytest.raises(LlmNotFoundError):
            drain(local_client.invoke("nope", "hi", initiator="t", purpose="p"))

    def test_stream_event_shapes(self):
        ok_usage = LlmUsageRecord(
            endpoint_id="e1", timestamp=datetime.now().astimezone(),
            initiator="t", purpose="p", prompt_tokens=1, completion_tokens=1,
            total_tokens=2, duration_ms=1)
        assert StreamEvent(kind="chunk", text="x").text == "x"
        assert StreamEvent(kind="done", usage=ok_usage).usage is ok_usage
        # 模型直构造时 pydantic 把值错误包成 ValidationError（经注册表/调用器
        # 入口则统一为 LlmValidationError，此处两种形态都接受）
        with pytest.raises((LlmValidationError, PydanticValidationError)):
            StreamEvent(kind="chunk", text="")
        with pytest.raises((LlmValidationError, PydanticValidationError)):
            StreamEvent(kind="done")

    def test_offline_level_declared(self, registry):
        registry.register("c1", kind="local", provider="p", capability=LOCAL_CAP,
                          offline_level="full")
        assert registry.get("c1").offline_level == "full"
        vault_registry_ep = registry.get("c1")
        assert vault_registry_ep.kind == "local"
