"""L5 渠道适配器（07 §3）的验收用例。

对齐任务文件 [`T-L5-002.1`](../项目管理/tasks/T-L5-002.1-渠道适配器与渠道偏好.md) 的
GWT-3 / GWT-4 / GWT-6——链内即时降级、离线标注、云端经网关与凭据纪律、未接线显式不可用。
"""

from __future__ import annotations

import pytest

from l5_helpers import NOW, PASS, TRACE_ID
from st_agent.contracts.identifiers import digest_id
from st_agent.l0.net.errors import EgressUnavailableError
from st_agent.l0.net.gateway import EgressGateway
from st_agent.l0.secrets.vault import CredentialVault
from st_agent.l0.storage import Store
from st_agent.l5.channels import (
    CHANNEL_DELIVERY_KIND,
    CHANNEL_KINDS,
    OFFLINE_NOTE,
    ChannelDispatcher,
    ChannelPayload,
    ChannelResult,
    DesktopChannel,
    EmailChannel,
    ImWebhookChannel,
    TtsChannel,
)
from st_agent.l5.errors import ChannelNotWiredError, ChannelValidationError

SIGNAL_ID = digest_id("sig", "important", "k", TRACE_ID)
SMTP_HOST = "smtp.example.com"
WEBHOOK_HOST = "hooks.example.com"


def payload(**over) -> ChannelPayload:
    base = {
        "signal_id": SIGNAL_ID, "level": "important",
        "title": "公告密度异常", "body": "该标的近两日公告密度高于其 30 日均值",
        "trace_id": TRACE_ID, "evidence_refs": ("ann_" + "1" * 20,),
    }
    base.update(over)
    return ChannelPayload(**base)


class FakeTransport:
    """云端传输端口的替身：记下 (host, message)，可配置抛错。"""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self._fail = fail

    def __call__(self, host: str, message: bytes) -> int:
        self.calls.append((host, message))
        if self._fail is not None:
            raise self._fail
        return len(message) // 2


def gateway(store: Store, *, online: bool = True) -> EgressGateway:
    return EgressGateway(store, online=online)


class TestGwt3Failover:
    """GWT-3：渠道失效 → 显式提示 + 自动降级到下一渠道；链尽仍失败即显式失败。"""

    def test_first_channel_succeeds_and_stops(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        transport = FakeTransport()
        dispatcher = ChannelDispatcher({
            "desktop": DesktopChannel(lambda t, b: None),
            "email": EmailChannel(gateway(store), host=SMTP_HOST, transport=transport),
        })
        result = dispatcher.deliver(["desktop", "email"], payload())
        assert result.ok and result.delivered.channel == "desktop"
        assert transport.calls == []                      # 首级成功即止，不碰下一级
        assert result.degraded_channels == ()

    def test_broken_channel_degrades_to_next_with_explicit_reason(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        transport = FakeTransport()
        dispatcher = ChannelDispatcher({
            "desktop": DesktopChannel(None),              # 未接线 → 不可用
            "email": EmailChannel(gateway(store), host=SMTP_HOST, transport=transport),
        })
        result = dispatcher.deliver(["desktop", "email"], payload())
        assert result.ok and result.delivered.channel == "email"
        assert result.degraded_channels == ("desktop",)
        assert "未接线" in result.attempts[0].detail     # 降级原因显式，不静默
        assert "未接线" in result.detail()

    def test_failing_port_is_reported_and_chain_continues(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)

        def boom(_title, _body):
            raise RuntimeError("toast 守护进程未运行")

        dispatcher = ChannelDispatcher({
            "desktop": DesktopChannel(boom),
            "email": EmailChannel(gateway(store), host=SMTP_HOST, transport=FakeTransport()),
        })
        result = dispatcher.deliver(["desktop", "email"], payload())
        assert result.ok
        assert result.attempts[0].status == "failed"
        assert "toast 守护进程未运行" in result.attempts[0].detail

    def test_chain_exhausted_is_explicit_failure(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        dispatcher = ChannelDispatcher({
            "email": EmailChannel(gateway(store), host=SMTP_HOST, transport=None),
        })
        result = dispatcher.deliver(["email"], payload())
        assert not result.ok and result.delivered is None
        assert result.attempts[0].status == "unavailable"

    def test_empty_chain_is_rejected(self, tmp_path):
        dispatcher = ChannelDispatcher({})
        with pytest.raises(ChannelValidationError, match="为空"):
            dispatcher.deliver([], payload())

    def test_unknown_channel_in_chain_is_rejected(self, tmp_path):
        dispatcher = ChannelDispatcher({})
        with pytest.raises(ChannelValidationError, match="未知渠道"):
            dispatcher.deliver(["carrier_pigeon"], payload())

    def test_unknown_channel_has_no_adapter(self):
        dispatcher = ChannelDispatcher({})
        with pytest.raises(ChannelNotWiredError, match="未接线"):
            dispatcher.adapter("email")
        assert dispatcher.health("tts").available is False

    def test_health_map_covers_all_four_channels(self):
        health = ChannelDispatcher({}).health_map()
        assert tuple(h.channel for h in health) == CHANNEL_KINDS
        assert all(not h.available for h in health)


class TestGwt4Offline:
    """GWT-4：断网时本地渠道照常、云端渠道显式标注「离线，暂无法发送」。"""

    def test_local_channel_works_offline(self):
        dispatcher = ChannelDispatcher({
            "desktop": DesktopChannel(lambda t, b: None),
        })
        assert dispatcher.deliver(["desktop"], payload()).ok

    def test_cloud_channel_is_marked_pending_reconnect(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        gw = gateway(store, online=False)
        transport = FakeTransport()
        dispatcher = ChannelDispatcher({
            "email": EmailChannel(gw, host=SMTP_HOST, transport=transport),
        })
        result = dispatcher.deliver(["email"], payload())
        assert not result.ok
        assert result.attempts[0].status == "unavailable"
        assert result.attempts[0].pending_reconnect is True
        assert result.attempts[0].detail == OFFLINE_NOTE
        assert result.pending_reconnect is True
        assert transport.calls == []                      # 离线时根本不发包

    def test_health_reports_offline_for_cloud_channel(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        email = EmailChannel(gateway(store, online=False), host=SMTP_HOST,
                             transport=FakeTransport())
        health = email.health()
        assert health.available is False and health.offline_level == "none"
        assert health.reason == OFFLINE_NOTE

    def test_health_covers_local_full_level(self):
        assert DesktopChannel(lambda t, b: None).health().offline_level == "full"
        assert TtsChannel(lambda s: None).health().offline_level == "full"


class TestGwt6GatewayAndCredentials:
    """GWT-6：云端渠道经网关 `channel_delivery`、凭据只经 vault、未接线显式不可用。"""

    def _rig(self, tmp_path, *, with_credential: bool = True):
        store = Store.create(tmp_path / "root", PASS)
        vault = CredentialVault(store) if with_credential else None
        if with_credential:
            vault.add("smtp-main", "smtp", "s3cr3t-app-password", label="主邮箱")
        transport = FakeTransport()
        gw = gateway(store)
        channel = EmailChannel(
            gw, host=SMTP_HOST, transport=transport,
            vault=vault, credential_id="smtp-main" if with_credential else None,
            recipient="me@example.com", sender_address="agent@example.com",
        )
        return store, gw, vault, transport, channel

    def test_request_goes_through_gateway_as_channel_delivery(self, tmp_path):
        store, gw, _vault, transport, channel = self._rig(tmp_path)
        gw.set_audit(True)                                 # 审计默认关，本用例要查留痕
        assert channel.deliver(payload()).status == "ok"
        assert transport.calls[0][0] == SMTP_HOST
        events = gw.query(kind=CHANNEL_DELIVERY_KIND)
        assert len(events) == 1
        assert events[0].target_host == SMTP_HOST
        assert events[0].initiator == "l5:email"
        assert events[0].bytes_in > 0

    def test_credential_plaintext_never_reaches_the_gateway(self, tmp_path):
        store, gw, _vault, transport, channel = self._rig(tmp_path)
        gw.set_audit(True)
        assert channel.deliver(payload()).status == "ok"
        _host, message = transport.calls[0]
        assert b"s3cr3t-app-password" in message           # 载荷闭包里有凭据（发得出去）
        for name in store.list_files("execution_log"):     # 审计里没有明文
            assert b"s3cr3t-app-password" not in store.get("execution_log", name)

    def test_credential_use_is_recorded(self, tmp_path):
        store, _gw, vault, _transport, channel = self._rig(tmp_path)
        channel.deliver(payload())
        usage = vault.query_usage("smtp-main")
        assert len(usage) == 1 and usage[0].status == "ok"
        assert usage[0].initiator == "l5:email"

    def test_missing_transport_is_explicitly_unavailable(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        channel = EmailChannel(gateway(store), host=SMTP_HOST, transport=None)
        assert channel.health().available is False
        result = channel.deliver(payload())
        assert result.status == "unavailable"
        assert "传输端口未注入" in result.detail

    def test_tts_without_synthesis_port_is_explicitly_unavailable(self):
        channel = TtsChannel(None)
        assert channel.health().available is False
        assert "TTS 渠道未接线" in channel.health().reason
        assert channel.deliver(payload()).status == "unavailable"

    def test_desktop_without_port_names_the_gap(self):
        channel = DesktopChannel(None)
        assert "原生通知端口" in channel.health().reason

    def test_transport_error_maps_to_failed(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        channel = EmailChannel(
            gateway(store), host=SMTP_HOST,
            transport=FakeTransport(fail=EgressUnavailableError("目标不可达")),
        )
        result = channel.deliver(payload())
        assert result.status == "unavailable"              # 网关把不可达映成 unavailable
        assert "目标不可达" in result.detail

    def test_webhook_renders_json_payload(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        vault = CredentialVault(store)
        vault.add("im-main", "webhook", "tok-123")
        transport = FakeTransport()
        channel = ImWebhookChannel(
            gateway(store), host=WEBHOOK_HOST, transport=transport,
            vault=vault, credential_id="im-main", webhook_id="my-bot",
        )
        assert channel.deliver(payload()).status == "ok"
        _host, message = transport.calls[0]
        import json
        body = json.loads(message.decode("utf-8"))
        assert body["signal_id"] == SIGNAL_ID
        assert body["level"] == "important"
        assert body["token"] == "tok-123"
        assert body["webhook_id"] == "my-bot"
        assert body["trace_id"] == TRACE_ID

    def test_degrade_returns_explicit_channel_result(self):
        result = DesktopChannel(None).degrade("用户静音时段")
        assert result == ChannelResult(
            channel="desktop", status="unavailable", detail="用户静音时段",
        )
