"""L5 薄装配缝（`build_l5`）的验收用例。

对齐任务文件 [`T-L5-002.3`](../项目管理/tasks/T-L5-002.3-平台事件投递机制与L5装配缝.md) 的
GWT-4 / GWT-5——`SignalEmitted` 经总线**自动**被采纳并投递、两个 01 §7 族注入门面、
未接总线不假装送达。
"""

from __future__ import annotations

import pytest

from l5_helpers import NOW, PASS, TRACE_ID, event, payload
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l0.storage import Store
from st_agent.l1.events import EventBus, UndeliveredPublisher
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l5.channels import ChannelDispatcher, ChannelHealth, ChannelResult
from st_agent.l5.delivery import EVENT_OWNER
from st_agent.l5.errors import SignalAdoptionError
from st_agent.l5.runtime import build_l5
from st_agent.l5.signal import signal_event


class EchoChannel:
    """记录载荷的渠道替身。"""

    channel = "desktop"

    def __init__(self) -> None:
        self.payloads = []

    def deliver(self, incoming):
        self.payloads.append(incoming)
        return ChannelResult(channel="desktop", status="ok")

    def health(self):
        return ChannelHealth(channel="desktop", available=True, offline_level="full")

    def degrade(self, reason: str):
        return ChannelResult(channel="desktop", status="unavailable", detail=reason)


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store.create(tmp_path / "root", PASS)


class TestGwt5Assembly:
    """GWT-5：`build_l5` 把两族注入 01 §7 门面，并把各面接成一个栈。"""

    def test_both_families_are_registered(self, store):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        stack = build_l5(store, registry=facade, now=lambda: NOW)
        assert stack.registry is facade
        assert facade.entry("attention-budget.current-mode") is not None
        assert facade.entry("channel-delivery.emergency") is not None
        assert facade.entry("channel-delivery.resend-on-reconnect") is not None
        assert stack.budget is not None and stack.policies is not None
        assert stack.delivery is not None

    def test_registry_is_optional(self, store):
        stack = build_l5(store, now=lambda: NOW)      # 不注入门面
        assert stack.registry is None
        assert stack.policies.get("emergency").channels == (
            "desktop", "email", "im_webhook",
        )

    def test_injected_parts_win(self, store):
        echo = EchoChannel()
        dispatcher = ChannelDispatcher({"desktop": echo})
        stack = build_l5(store, dispatcher=dispatcher, now=lambda: NOW)
        assert stack.dispatcher is dispatcher

    def test_channel_adapters_can_be_injected_directly(self, store):
        echo = EchoChannel()
        stack = build_l5(store, channels={"desktop": echo}, now=lambda: NOW)
        assert stack.dispatcher.health("desktop").available is True


class TestGwt4SignalSubscription:
    """GWT-4：总线上的 `SignalEmitted` 被**采纳**后自动进入投递。"""

    def test_signal_published_on_the_bus_is_delivered(self, store):
        echo = EchoChannel()
        bus = EventBus()
        stack = build_l5(store, events=bus, channels={"desktop": echo}, now=lambda: NOW)
        assert stack.subscription is not None
        result = bus.publish(event(level="emergency"))                  # 一条合 01 §11 的 SignalEmitted
        assert result.delivered is True
        assert result.outcomes[0].subscriber_id == "l5:delivery-orchestrator"
        assert len(stack.accepted) == 1
        assert echo.payloads and echo.payloads[0].signal_id == stack.accepted[0].signal_id

    def test_malformed_payload_is_explicitly_rejected(self, store):
        bus = EventBus()
        stack = build_l5(store, events=bus, channels={"desktop": EchoChannel()},
                         now=lambda: NOW)
        broken = payload(level="whenever")             # 非法级别
        outcome = bus.publish(event(**broken))
        assert outcome.delivered is False              # 拒收**不被吞**（01 §11 投递口径）
        assert "SignalAdoptionError" in outcome.failed[0].error
        assert stack.accepted == []                    # 不产出半截信号

    def test_missing_field_is_rejected(self, store):
        bus = EventBus()
        stack = build_l5(store, events=bus, channels={"desktop": EchoChannel()},
                         now=lambda: NOW)
        incomplete = payload()
        incomplete.pop("dedup_key")                    # 手工去掉一个必填负载字段
        outcome = bus.publish(PlatformEvent(
            event="SignalEmitted", payload=incomplete,
            trace_id=TRACE_ID, occurred_at=NOW,
        ))
        assert "dedup_key" in outcome.failed[0].error
        assert stack.accepted == []

    def test_builder_output_is_adoptable(self, store):
        bus = EventBus()
        stack = build_l5(store, events=bus, channels={"desktop": EchoChannel()},
                         now=lambda: NOW)
        built = signal_event(
            level="important",
            content_ref=payload()["content_ref"],
            evidence_refs=payload()["evidence_refs"],
            dedup_key=payload()["dedup_key"],
            source_trace_id=TRACE_ID, occurred_at=NOW,
        )
        assert bus.publish(built).delivered is True


class TestNoBusIsNotFaked:
    """未接入总线：发布端**不假装送达**，归属点名如实可见。"""

    def test_default_publisher_is_the_undelivered_one(self, store):
        stack = build_l5(store, channels={"desktop": EchoChannel()}, now=lambda: NOW)
        assert isinstance(stack.events, UndeliveredPublisher)
        assert stack.subscription is None              # 没总线就不订阅
        assert stack.events.publish(event(level="emergency")) is False
        assert len(stack.events.events()) == 1         # 不静默丢弃

    def test_settlement_names_the_owner(self, store):
        stack = build_l5(store, channels={"desktop": EchoChannel()}, now=lambda: NOW)
        from st_agent.l5.signal import adopt_signal

        signal = adopt_signal(event(level="emergency"))
        first = stack.delivery.dispatch(signal, now=NOW)
        stack.delivery.mark_read(first.record.delivery_id, now=NOW)
        assert len(stack.delivery.settlements()) == 1        # 事件如实记下（不丢）
        assert isinstance(stack.events, UndeliveredPublisher)
        assert len(stack.events.events()) == 1               # 交到发布端但**不受理**
        assert stack.events.owner == EVENT_OWNER             # 归属点名
        assert "未接入事件总线" in stack.events.note


class TestT003Wiring:
    """T-L5-003：日报 / 频控 / 疲劳三面接进同一个栈，三个新族注入 01 §7 门面。"""

    def test_all_five_families_are_registered(self, store):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        stack = build_l5(store, registry=facade, now=lambda: NOW)
        for config_id in (
            "attention-budget.current-mode",
            "channel-delivery.emergency",
            "frequency.dedup-window-minutes",
            "daily-report.time",
            "daily-report.template",
            "fatigue.ignore-threshold",
        ):
            assert facade.entry(config_id) is not None, config_id
        assert stack.frequency is not None
        assert stack.reports is not None
        assert stack.fatigue is not None

    def test_fatigue_mutes_are_wired_into_frequency(self, store):
        stack = build_l5(store, channels={"desktop": EchoChannel()}, now=lambda: NOW)
        assert stack.frequency._mutes is stack.fatigue          # 静音清单是频控的鸭子端口

    def test_report_reads_the_same_delivery_ledger(self, store):
        stack = build_l5(store, channels={"desktop": EchoChannel()}, now=lambda: NOW)
        from st_agent.l5.signal import adopt_signal

        signal = adopt_signal(event(level="routine"))
        stack.delivery.dispatch(signal, content_type="brief", now=NOW)
        report = stack.reports.build(NOW.date(), now=NOW)
        assert report.trace.signal_ids == (signal.signal_id,)
