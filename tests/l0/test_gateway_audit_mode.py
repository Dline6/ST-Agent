"""T-L0-018.2 测试：出网审计可选（默认关）与落盘形态批量化（02 §6 / 01 §7）。

GWT 对照（任务文件 5 条）：
- GWT-1 审计默认可关：缺省构造的网关照常执行 / 计时 / 映射结果，但不产生逐次
  记录；显式开启与现状同形
- GWT-2 开关可配：01 §7 条目（默认 ``false``）可读可写，改开关后**不需重启进程**
  即生效
- GWT-3 关闭时的连带口径：离线拦截仍生效；``pending_reconnect`` 走显式
  ``unavailable``；已有历史仍可读（关的是以后，不是清除）
- GWT-4 落盘批量化：``put`` 次数与请求数解耦；``query(since=…)`` 跳段；读接口
  形态与时戳升序不变；旧形态（一请求一文件）仍可读
- GWT-5 契约先行：由 ``verify_docs.py --strict`` 与文档变更覆盖（无代码面）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.l0.net import (
    AUDIT_SEGMENT_SIZE,
    NET_AUDIT_DISABLED_REF,
    EgressGateway,
    EgressTimeoutError,
    get_audit_policy,
    net_audit_config_entries,
    set_audit_policy,
)
from st_agent.l0.storage import Store

FUTURE = datetime.now(timezone.utc) + timedelta(days=365)
PAST = datetime.now(timezone.utc) - timedelta(days=365)


def _sender(kind, host, timeout_ms):
    return (7, 21, ())


class _Sender:
    """记录调用的发包替身（验「关审计不影响发包」用）。"""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, kind, host, timeout_ms):
        self.calls.append(host)
        return (7, 21, ())


def _fire(gateway: EgressGateway, n: int = 1, *, sender=None) -> None:
    for _ in range(n):
        gateway.execute("data_fetch", "example.com", initiator="sync",
                        purpose="抓取日线", sender=sender)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root")          # 默认：明文根（T-L0-018.1）


# ───────────────────────── GWT-1 默认可关 ─────────────────────────


class TestGwt1DefaultOff:
    def test_default_is_off_and_requests_still_run(self, store: Store):
        sender = _Sender()
        gateway = EgressGateway(store, sender=sender)
        assert gateway.audit_enabled is False
        env = gateway.execute("data_fetch", "example.com",
                              initiator="sync", purpose="抓取日线")
        assert env.status == "ok" and env.data == {"bytes_in": 21}
        assert sender.calls == ["example.com"]           # 发包照常
        assert gateway.query() == ()                     # 但无逐次留痕

    def test_explicit_on_matches_current_behavior(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=True)
        _fire(gateway, 2)
        events = gateway.query()
        assert len(events) == 2
        assert all(e.kind == "data_fetch" and e.status == "ok" for e in events)
        assert events[0].initiator == "sync" and events[0].bytes_in == 21

    def test_off_does_not_change_success_payload(self, store: Store):
        off = EgressGateway(store, sender=_sender, audit=False)
        on = EgressGateway(store, sender=_sender, audit=True)
        assert off.execute("llm_call", "api.example.com", initiator="llm",
                           purpose="调用").data == on.execute(
            "llm_call", "api.example.com", initiator="llm", purpose="调用").data

    def test_stream_still_works_when_off(self, store: Store):
        gateway = EgressGateway(store, sender=lambda k, h, t: (0, 0, ("a", "b")),
                                audit=False)
        assert list(gateway.stream("llm_call", "api.example.com", initiator="llm",
                                   purpose="调用")) == ["a", "b"]
        assert gateway.query() == ()

    def test_capability_registry_unaffected_by_audit_off(self, store: Store):
        gateway = EgressGateway(store, sender=_sender)     # 审计关
        gateway.register_capability("memory", "Memory 读写", "full")
        assert [r.capability_id for r in gateway.list_capabilities()] == ["memory"]
        assert gateway.offline_report().available[0].offline_level == "full"


# ───────────────────────── GWT-2 开关可配 ─────────────────────────


class TestGwt2Configurable:
    def test_entry_shape_and_default(self):
        (entry,) = net_audit_config_entries()
        assert entry.config_id == "net-audit/enabled"
        assert entry.default is False
        assert entry.value_schema["type"] == "boolean"
        assert entry.scope == "global"

    def test_hot_switch_without_restart(self, store: Store):
        gateway = EgressGateway(store, sender=_sender)
        _fire(gateway)
        assert gateway.query() == ()
        gateway.set_audit(True)
        _fire(gateway)
        assert len(gateway.query()) == 1
        gateway.set_audit(False)
        _fire(gateway)
        assert len(gateway.query()) == 1                  # 关后不再增

    def test_config_value_is_truth_source(self, store: Store):
        set_audit_policy(store, enabled=True)
        assert get_audit_policy(store).enabled is True
        assert EgressGateway(store, sender=_sender).audit_enabled is True
        set_audit_policy(store, enabled=False)
        assert EgressGateway(store, sender=_sender).audit_enabled is False

    def test_set_audit_none_rereads_config(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=True)
        set_audit_policy(store, enabled=False)
        assert gateway.set_audit(None) is False

    def test_constructor_override_beats_config(self, store: Store):
        set_audit_policy(store, enabled=False)
        assert EgressGateway(store, sender=_sender, audit=True).audit_enabled is True


# ───────────────────────── GWT-3 关闭时的连带口径 ─────────────────────────


class TestGwt3OffSemantics:
    def test_offline_interception_still_works_when_off(self, store: Store):
        sender = _Sender()
        gateway = EgressGateway(store, sender=sender, audit=False)
        gateway.set_online(False)
        env = gateway.execute("data_fetch", "example.com", initiator="sync",
                              purpose="抓取日线")
        assert env.status == "unavailable"                 # 离线拦截是功能，不是留痕
        assert sender.calls == []

    def test_pending_reconnect_is_explicitly_unavailable(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=False)
        env = gateway.pending_reconnect()
        assert env.status == "unavailable"
        assert "审计未开启" in (env.reason or "")
        assert env.last_updated_at is not None

    def test_pending_reconnect_ok_envelope_when_on(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=True)
        gateway.set_online(False)
        gateway.execute("data_fetch", "example.com", initiator="sync", purpose="抓取")
        env = gateway.pending_reconnect()
        assert env.status == "ok" and len(env.data["events"]) == 1

    def test_history_still_readable_after_turning_off(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=True)
        _fire(gateway, 3)
        gateway.set_audit(False)
        assert len(gateway.query()) == 3                   # B4：关的是以后，不是清除

    def test_failed_log_ref_is_explicit_sentinel_when_off(self, store: Store):
        def boom(kind, host, timeout_ms):
            raise EgressTimeoutError("超时")

        gateway = EgressGateway(store, sender=_sender, audit=False)
        env = gateway.execute("data_fetch", "example.com", initiator="sync",
                              purpose="抓取", sender=boom)
        assert env.status == "failed"
        assert env.log_ref == NET_AUDIT_DISABLED_REF
        assert gateway.query() == ()

    def test_failed_log_ref_points_at_real_record_when_on(self, store: Store):
        def boom(kind, host, timeout_ms):
            raise EgressTimeoutError("超时")

        gateway = EgressGateway(store, sender=_sender, audit=True)
        env = gateway.execute("data_fetch", "example.com", initiator="sync",
                              purpose="抓取", sender=boom)
        assert env.log_ref in store.list_files("execution_log")
        assert [e.status for e in gateway.query()] == ["failed"]


# ───────────────────────── GWT-4 落盘批量化 ─────────────────────────


def _counting_store(store: Store) -> dict[str, int]:
    """把 ``Store.put`` 计数包一层（只关心 ``execution_log`` 的写入次数）。"""
    counter = {"puts": 0, "gets": 0}
    real_put, real_get = store.put, store.get

    def put(partition, name, data):
        if partition == "execution_log":
            counter["puts"] += 1
        return real_put(partition, name, data)

    def get(partition, name):
        if partition == "execution_log":
            counter["gets"] += 1
        return real_get(partition, name)

    store.put = put            # type: ignore[method-assign]
    store.get = get            # type: ignore[method-assign]
    return counter


class TestGwt4SegmentedLog:
    def test_put_count_decoupled_from_request_count(self, store: Store):
        counter = _counting_store(store)
        gateway = EgressGateway(store, sender=_sender, audit=True, segment_size=100)
        _fire(gateway, 1000)
        gateway.flush_audit()
        assert counter["puts"] == 10                       # ⌈1000/100⌉，而非 1000
        assert len(gateway.query()) == 1000                # 记录一条不少

    def test_default_segment_size_is_thousand(self):
        assert AUDIT_SEGMENT_SIZE == 1000

    def test_query_skips_segments_outside_time_bounds(self, store: Store):
        counter = _counting_store(store)
        gateway = EgressGateway(store, sender=_sender, audit=True, segment_size=10)
        _fire(gateway, 50)                                 # 5 段
        gateway.flush_audit()
        assert len(gateway.query(since=FUTURE)) == 0
        assert len(gateway.query(since=PAST)) == 50
        reads_future = counter["gets"]
        counter["gets"] = 0
        assert len(gateway.query(until=PAST)) == 0
        assert counter["gets"] < reads_future              # 带边界 ⇒ 不读全量历史

    def test_query_read_shape_unchanged_and_ascending(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=True, segment_size=7)
        _fire(gateway, 20)
        stamps = [e.timestamp for e in gateway.query()]
        assert stamps == sorted(stamps)                    # 时戳升序口径不变
        report = gateway.export_report()
        assert report["count"] == 20 and set(report) == {"generated_at", "count", "events"}

    def test_query_reads_own_buffer(self, store: Store):
        gateway = EgressGateway(store, sender=_sender, audit=True, segment_size=1000)
        _fire(gateway, 3)                                  # 全在缓冲里，未成段
        assert len(gateway.query()) == 3                   # 读己所写

    def test_legacy_single_record_file_still_readable(self, store: Store):
        legacy = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "initiator": "old", "kind": "data_fetch", "target_host": "example.com",
            "purpose": "旧形态记录", "bytes_out": 1, "bytes_in": 2, "status": "ok",
            "detail": "",
        }
        store.put("execution_log", "net/data_fetch/0001780000000000000-abcd1234.json",
                  json.dumps(legacy).encode("utf-8"))
        gateway = EgressGateway(store, sender=_sender, audit=False)
        (event,) = gateway.query()
        assert event.initiator == "old" and event.status == "ok"

    def test_segment_carries_no_content_plaintext(self, store: Store):
        """B3：批量封装不把内容带进留痕（零明文纪律不因批量化放宽）。"""
        from st_agent.l0.llm import EndpointRegistry, LlmClient
        from st_agent.l0.secrets import CredentialVault

        prompt = "SECRET-PROMPT-αβγ-91827364"
        key = "sk-proj-abcdef1234567890"
        gateway = EgressGateway(store, sender=lambda k, h, t: (7, 21, ("ok",)),
                                audit=True)
        store.unlock_secrets("cred-pass")          # 凭据恒加密、惰性解锁（.1 的口径）
        vault = CredentialVault(store)
        vault.add("openai-main", "llm_api_key", key)
        registry = EndpointRegistry(store)
        registry.register("cloud-main", kind="cloud", provider="openai-compatible",
                          capability={"max_context_tokens": 8192,
                                      "supports_structured_output": True},
                          credential_id="openai-main")
        client = LlmClient(store, registry, vault, transport=gateway.llm_transport(
            {"openai-compatible": "api.openai.example.com"}))
        list(client.invoke("cloud-main", prompt, initiator="t", purpose="调用"))
        gateway.flush_audit()
        assert prompt not in str(gateway.export_report())
        for name in store.list_files("execution_log"):
            raw = store.get("execution_log", name)
            assert prompt.encode() not in raw, name
            assert key.encode() not in raw, name
