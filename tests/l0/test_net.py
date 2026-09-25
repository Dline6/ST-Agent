"""T-L0-004 测试：02-L0 §6 出网审计网关 + §7 离线能力分级。

GWT 对照（任务文件 5 条）：
- GWT-1 强制经行：五类出网唯一出口为网关 execute/stream；发起前校验失败
  不产生审计记录
- GWT-2 网络活动可查：记录含 §6 六字段；query 条件过滤 + export_report；
  断网仍可查历史；pending_reconnect 待补发查询
- GWT-3 离线分级清单：能力登记/更新/删除；offline_report 三类清单；
  degraded 必带标注
- GWT-4 LLM 接入：llm_transport 经网关发出并记审计；离线/未知 provider 走
  unavailable；prompt/response/key 明文不进审计不落盘
- GWT-5 失败显式化 + 零泄漏：不可达→unavailable；超时/取消→failed 带 log_ref；
  全盘无 prompt/response/key 明文
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from st_agent.l0.llm import EndpointRegistry, LlmClient
from st_agent.l0.net import (
    CapabilityExistsError,
    CapabilityNotFoundError,
    EgressCancelledError,
    EgressError,
    EgressGateway,
    EgressTimeoutError,
    EgressUnavailableError,
    NetValidationError,
    NetworkEvent,
)
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

LOCAL_CAP = {"max_context_tokens": 4_096, "supports_structured_output": False}
CLOUD_CAP = {"max_context_tokens": 128_000, "supports_structured_output": True}


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    def sender(kind, host, timeout_ms):
        return (12, 34, ("hello ", "world"))
    return EgressGateway(store, sender=sender)


def root_of(gateway: EgressGateway) -> Path:
    return gateway._store._root


# ───────────────────────── GWT-1 强制经行 ─────────────────────────


class TestGwt1SingleExit:
    @pytest.mark.parametrize("kind", [
        "data_fetch", "llm_call", "channel_delivery", "remote_mcp", "index_browse",
    ])
    def test_five_kinds_audited(self, gateway, kind):
        env = gateway.execute(kind, "api.example.com",
                              initiator="t", purpose="同步测试")
        assert env.status == "ok"
        assert env.data == {"bytes_in": 34}
        (event,) = gateway.query(kind=kind)
        assert event.target_host == "api.example.com"
        assert event.bytes_out == 0 and event.bytes_in == 34

    def test_stream_chunks_and_audits(self, gateway):
        assert list(gateway.stream("data_fetch", "api.example.com",
                                   initiator="t", purpose="流式拉取")) == ["hello ", "world"]
        (event,) = gateway.query()
        assert event.status == "ok" and event.bytes_in > 0

    def test_target_host_rejects_url(self, gateway):
        env = gateway.execute("data_fetch", "https://api.example.com/v1?q=1",
                              initiator="t", purpose="拉取")
        assert env.status == "validation_failed"
        assert gateway.query() == ()

    def test_blank_initiator_purpose_rejected_without_audit(self, gateway):
        assert gateway.execute("data_fetch", "h.example.com",
                               initiator="", purpose="拉取").status == "validation_failed"
        assert gateway.execute("data_fetch", "h.example.com",
                               initiator="t", purpose=" ").status == "validation_failed"
        assert gateway.execute("data_fetch", "h.example.com",
                               initiator="t", purpose="拉取",
                               timeout_ms=0).status == "validation_failed"
        assert gateway.query() == ()

    def test_unknown_kind_rejected(self, gateway):
        env = gateway.execute("sms_direct", "h.example.com",
                              initiator="t", purpose="直发")
        assert env.status == "validation_failed"
        assert gateway.query() == ()


# ───────────────────────── GWT-2 网络活动可查 ─────────────────────────


class TestGwt2QueryExport:
    def test_record_has_six_fields(self, gateway):
        gateway.execute("data_fetch", "api.example.com",
                        initiator="skill-x", purpose="行情同步", bytes_out=12)
        (event,) = gateway.query()
        assert isinstance(event, NetworkEvent)
        assert event.timestamp.tzinfo is not None
        assert event.initiator == "skill-x"
        assert event.target_host == "api.example.com"
        assert event.purpose == "行情同步"
        assert (event.bytes_out, event.bytes_in, event.status) == (12, 34, "ok")

    def test_query_filters(self, gateway):
        gateway.execute("data_fetch", "a.example.com", initiator="s1", purpose="拉取甲")
        gateway.execute("llm_call", "b.example.com", initiator="s2", purpose="推理乙")
        assert len(gateway.query(kind="data_fetch")) == 1
        assert len(gateway.query(initiator="s2")) == 1
        assert len(gateway.query(status="ok")) == 2
        assert gateway.query(status="failed") == ()

    def test_query_time_bounds_need_tz(self, gateway):
        with pytest.raises(NetValidationError, match="时区"):
            gateway.query(since=datetime.now())

    def test_query_time_bounds(self, gateway):
        gateway.execute("data_fetch", "a.example.com", initiator="t", purpose="拉取")
        (event,) = gateway.query()
        assert gateway.query(since=event.timestamp - timedelta(seconds=1),
                             until=event.timestamp + timedelta(seconds=1)) == (event,)
        assert gateway.query(since=event.timestamp + timedelta(seconds=1)) == ()

    def test_export_report_shape(self, gateway):
        gateway.execute("data_fetch", "a.example.com", initiator="t", purpose="拉取")
        report = gateway.export_report()
        assert report["count"] == 1
        blob = report["events"][0]
        assert set(blob) >= {"timestamp", "initiator", "target_host",
                             "purpose", "bytes_in", "status"}
        for forbidden in ("prompt", "response", "text", "value"):
            assert f'"{forbidden}"' not in str(report)

    def test_offline_history_still_queryable(self, gateway):
        gateway.execute("data_fetch", "a.example.com", initiator="t", purpose="拉取")
        gateway.set_online(False)
        assert len(gateway.query()) == 1
        assert gateway.export_report()["count"] == 1

    def test_pending_reconnect_lists_intercepted(self, gateway):
        gateway.set_online(False)
        env = gateway.execute("channel_delivery", "smtp.example.com",
                              initiator="l5", purpose="触达投递")
        assert env.status == "unavailable"
        (pending,) = gateway.pending_reconnect()
        assert pending.target_host == "smtp.example.com"


# ───────────────────────── GWT-3 离线分级清单 ─────────────────────────


class TestGwt3OfflineLevels:
    def test_register_list_report(self, gateway):
        gateway.register_capability("memory-rw", "记忆读写", "full")
        gateway.register_capability("cloud-chat", "云端对话", "none")
        gateway.register_capability("local-chat", "本地推理对话", "degraded",
                                    "断网时切换本地端点，结果质量下降")
        report = gateway.offline_report()
        assert [c.capability_id for c in report.available] == ["memory-rw"]
        assert [c.capability_id for c in report.limited] == ["local-chat"]
        assert [c.capability_id for c in report.unavailable] == ["cloud-chat"]
        assert report.generated_at.tzinfo is not None
        assert "本地端点" in report.limited[0].degrade_note

    def test_degraded_requires_note(self, gateway):
        with pytest.raises(NetValidationError, match="降级标注"):
            gateway.register_capability("x", "某能力", "degraded")
        with pytest.raises(NetValidationError, match="降级标注"):
            gateway.register_capability("x", "某能力", "degraded", "  ")

    def test_duplicate_update_remove(self, gateway):
        gateway.register_capability("c1", "能力一", "full")
        with pytest.raises(CapabilityExistsError, match="已登记"):
            gateway.register_capability("c1", "能力一", "full")
        updated = gateway.update_capability("c1", offline_level="degraded",
                                            degrade_note="快照过期标注")
        assert updated.offline_level == "degraded"
        assert gateway.get_capability("c1") == updated
        gateway.remove_capability("c1")
        with pytest.raises(CapabilityNotFoundError, match="未登记"):
            gateway.get_capability("c1")

    def test_missing_capability_errors(self, gateway):
        with pytest.raises(CapabilityNotFoundError, match="未登记"):
            gateway.get_capability("nope")
        with pytest.raises(CapabilityNotFoundError, match="未登记"):
            gateway.update_capability("nope", display_name="x")
        with pytest.raises(CapabilityNotFoundError, match="未登记"):
            gateway.remove_capability("nope")

    def test_bad_capability_id(self, gateway):
        with pytest.raises(NetValidationError, match="能力标识"):
            gateway.register_capability("../escape", "逃逸", "full")


# ───────────────────────── GWT-4 LLM 接入 ─────────────────────────


class TestGwt4LlmAdapter:
    def _client(self, store, gateway):
        vault = CredentialVault(store)
        vault.add("openai-main", "llm_api_key", "sk-proj-abcdef1234567890")
        registry = EndpointRegistry(store)
        registry.register("cloud-main", kind="cloud", provider="openai-compatible",
                          capability=CLOUD_CAP, priority=5, purpose="日常",
                          credential_id="openai-main")
        return LlmClient(store, registry, vault,
                         transport=gateway.llm_transport(
                             {"openai-compatible": "api.openai.example.com"}))

    def test_llm_call_audited(self, store, gateway):
        client = self._client(store, gateway)
        events = list(client.invoke("cloud-main", "你好", initiator="t", purpose="p"))
        assert events[-1].kind == "done"
        (audit,) = gateway.query(kind="llm_call")
        assert audit.target_host == "api.openai.example.com"
        assert audit.status == "ok"
        assert audit.bytes_out == len("你好".encode("utf-8"))

    def test_llm_offline_is_unavailable(self, store, gateway):
        client = self._client(store, gateway)
        gateway.set_online(False)
        events = list(client.invoke("cloud-main", "你好", initiator="t", purpose="p"))
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"
        assert gateway.pending_reconnect() != ()

    def test_llm_unknown_provider_is_unavailable(self, store, gateway):
        vault = CredentialVault(store)
        vault.add("k1", "llm_api_key", "sk-proj-abcdef1234567890")
        registry = EndpointRegistry(store)
        registry.register("c1", kind="cloud", provider="mystery",
                          capability=CLOUD_CAP, credential_id="k1")
        client = LlmClient(store, registry, vault,
                           transport=gateway.llm_transport({}))
        events = list(client.invoke("c1", "hi", initiator="t", purpose="p"))
        assert events[0].kind == "error"
        assert events[0].error_envelope.status == "unavailable"

    def test_no_plaintext_in_audit_or_disk(self, store, gateway):
        client = self._client(store, gateway)
        prompt = "SECRET-PROMPT-αβγ-91827364"
        list(client.invoke("cloud-main", prompt, initiator="t", purpose="p"))
        blob = gateway.export_report()
        assert prompt not in str(blob)
        assert "sk-proj-abcdef1234567890" not in str(blob)
        for f in root_of(gateway).rglob("*"):
            if f.is_file() and f.name != "keyfile.json":
                raw = f.read_bytes()
                assert prompt.encode() not in raw, f.name
                assert b"sk-proj-abcdef1234567890" not in raw, f.name


# ───────────────────────── GWT-5 失败显式化 + 零泄漏 ─────────────────────────


class TestGwt5FailureSemantics:
    def test_no_sender_is_unavailable(self, store):
        gateway = EgressGateway(store, sender=None)
        env = gateway.execute("data_fetch", "a.example.com",
                              initiator="t", purpose="拉取")
        assert env.status == "unavailable"
        assert env.last_updated_at is not None
        (event,) = gateway.query()
        assert event.status == "unavailable"

    def test_unreachable_maps_unavailable(self, store):
        def sender(kind, host, timeout_ms):
            from st_agent.l0.net import EgressUnavailableError as EU
            raise EU("连接被拒绝")
        gateway = EgressGateway(store, sender=sender)
        env = gateway.execute("data_fetch", "down.example.com",
                              initiator="t", purpose="拉取")
        assert env.status == "unavailable"
        assert "连接被拒绝" in (env.reason or "")

    def test_timeout_maps_failed_with_log_ref(self, store):
        def sender(kind, host, timeout_ms):
            from st_agent.l0.net import EgressTimeoutError as ET
            raise ET("远端 3000ms 未响应")
        gateway = EgressGateway(store, sender=sender)
        env = gateway.execute("data_fetch", "slow.example.com",
                              initiator="t", purpose="拉取")
        assert env.status == "failed" and env.log_ref
        (event,) = gateway.query()
        assert event.status == "failed"
        assert "远端 3000ms 未响应" in event.detail

    def test_cancel_before_start(self, store, gateway):
        import threading
        cancel = threading.Event()
        cancel.set()
        env = gateway.execute("data_fetch", "a.example.com",
                              initiator="t", purpose="拉取", cancel=cancel)
        assert env.status == "failed"
        with pytest.raises(EgressCancelledError):
            list(gateway.stream("data_fetch", "a.example.com",
                                initiator="t", purpose="拉取", cancel=cancel))

    def test_stream_sender_crash_mapped(self, store):
        def sender(kind, host, timeout_ms):
            raise RuntimeError("socket boom")
        gateway = EgressGateway(store, sender=sender)
        with pytest.raises(EgressError, match="socket boom"):
            list(gateway.stream("data_fetch", "a.example.com",
                                initiator="t", purpose="拉取"))
        (event,) = gateway.query()
        assert event.status == "failed"

    def test_stream_timeout_at_chunk_boundary(self, store):
        def sender(kind, host, timeout_ms):
            def chunks():
                yield "x"
                import time as _t
                _t.sleep(0.05)
                yield "y"
            return (0, 0, chunks())
        gateway = EgressGateway(store, sender=sender)
        with pytest.raises(EgressTimeoutError):
            list(gateway.stream("data_fetch", "a.example.com",
                                initiator="t", purpose="拉取", timeout_ms=10))

    def test_purpose_with_content_word_rejected(self, store, gateway):
        env = gateway.execute("llm_call", "a.example.com",
                              initiator="t", purpose="发送 prompt 给模型")
        assert env.status == "validation_failed"

    def test_offline_stream_never_touches_sender(self, store):
        touched = []
        def sender(kind, host, timeout_ms):
            touched.append(host)
            return (0, 0, ("x",))
        gateway = EgressGateway(store, sender=sender, online=False)
        with pytest.raises(EgressUnavailableError):
            list(gateway.stream("data_fetch", "a.example.com",
                                initiator="t", purpose="拉取"))
        assert touched == []
        assert gateway.pending_reconnect() != ()
