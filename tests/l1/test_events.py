"""L1 进程内事件总线（01 §11 的投递口径）的验收用例。

对齐任务文件 [`T-L5-002.3`](../项目管理/tasks/T-L5-002.3-平台事件投递机制与L5装配缝.md) 的
GWT-2 / GWT-3 / GWT-6——按登记顺序同步派发、订阅者异常不吞不阻断、注销幂等。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from st_agent.contracts.time_events import PlatformEvent
from st_agent.l1.events import EventBus, UndeliveredPublisher

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 6, 8, 0, tzinfo=CST)


def event(name: str = "MemoryConflictDetected", **payload) -> PlatformEvent:
    body = {"conflict_id": "mc_" + "1" * 20}
    body.update(payload)
    return PlatformEvent(
        event=name, payload=body, trace_id="tr_" + "5" * 20, occurred_at=NOW,
    )


class TestGwt2OrderedDispatch:
    """GWT-2：按登记顺序同步派发；同一事件序列得同一派发序列。"""

    def test_subscribers_receive_in_registration_order(self):
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("MemoryConflictDetected", lambda _e: seen.append("first"),
                      subscriber_id="a")
        bus.subscribe("MemoryConflictDetected", lambda _e: seen.append("second"),
                      subscriber_id="b")
        result = bus.publish(event())
        assert seen == ["first", "second"]
        assert tuple(o.subscriber_id for o in result.outcomes) == ("a", "b")
        assert result.delivered is True

    def test_dispatch_is_deterministic(self):
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("MemoryConflictDetected", lambda _e: seen.append("only"))
        for _ in range(3):
            bus.publish(event())
        assert seen == ["only"] * 3

    def test_only_matching_event_name_is_dispatched(self):
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("SignalEmitted", lambda _e: seen.append("signal"))
        result = bus.publish(event())                 # MemoryConflictDetected
        assert seen == []
        assert result.outcomes == () and result.delivered is False

    def test_no_subscribers_is_not_delivered(self):
        bus = EventBus()
        result = bus.publish(event())
        assert result.delivered is False              # 没人收到就是没人收到
        assert result.outcomes == ()

    def test_event_name_override(self):
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("SignalEmitted", lambda _e: seen.append("signal"))
        result = bus.publish(event(), event_name="SignalEmitted")
        assert seen == ["signal"] and result.event_name == "SignalEmitted"

    def test_subscribers_listing(self):
        bus = EventBus()
        bus.subscribe("SignalEmitted", lambda _e: None, subscriber_id="l5")
        bus.subscribe("MemoryConflictDetected", lambda _e: None, subscriber_id="l3")
        assert set(bus.subscribers()) == {"l5", "l3"}
        assert bus.subscribers("SignalEmitted") == ("l5",)


class TestGwt3FailureIsExplicit:
    """GWT-3：订阅者抛错不被吞、不阻断其余订阅者，逐条记因。"""

    def test_error_is_recorded_and_others_still_receive(self):
        bus = EventBus()

        def boom(_event):
            raise RuntimeError("裁决面不可用")

        seen: list[str] = []
        bus.subscribe("MemoryConflictDetected", boom, subscriber_id="broken")
        bus.subscribe("MemoryConflictDetected", lambda _e: seen.append("ok"),
                      subscriber_id="healthy")
        result = bus.publish(event())
        assert seen == ["ok"]                          # 不阻断
        assert result.delivered is True                # 有人受理
        assert [o.subscriber_id for o in result.failed] == ["broken"]
        assert "裁决面不可用" in result.failed[0].error
        assert "RuntimeError" in result.failed[0].error

    def test_all_failing_means_not_delivered(self):
        bus = EventBus()

        def boom(_event):
            raise ValueError("坏了")

        bus.subscribe("MemoryConflictDetected", boom, subscriber_id="broken")
        result = bus.publish(event())
        assert result.delivered is False
        assert len(result.failed) == 1


class TestGwt6SubscriptionLifecycle:
    """GWT-6：注销生效且幂等。"""

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        seen: list[str] = []
        handle = bus.subscribe("MemoryConflictDetected", lambda _e: seen.append("hit"))
        bus.publish(event())
        assert bus.unsubscribe(handle) is True
        bus.publish(event())
        assert seen == ["hit"]                         # 注销后不再收到

    def test_unsubscribe_is_idempotent(self):
        bus = EventBus()
        handle = bus.subscribe("MemoryConflictDetected", lambda _e: None)
        assert bus.unsubscribe(handle) is True
        assert bus.unsubscribe(handle) is False        # 再注销无害

    def test_only_the_target_subscription_is_removed(self):
        bus = EventBus()
        first = bus.subscribe("MemoryConflictDetected", lambda _e: None, subscriber_id="a")
        bus.subscribe("MemoryConflictDetected", lambda _e: None, subscriber_id="b")
        bus.unsubscribe(first)
        assert bus.subscribers("MemoryConflictDetected") == ("b",)

    def test_subscribe_rejects_bad_input(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="事件名不得为空"):
            bus.subscribe("  ", lambda _e: None)
        with pytest.raises(ValueError, match="须可调用"):
            bus.subscribe("SignalEmitted", "not-callable")  # type: ignore[arg-type]


class TestUndeliveredPublisher:
    """未接入总线时的显式发布端（01 §11「未接通不假装送达」）。"""

    def test_publish_returns_false_and_keeps_the_event(self):
        publisher = UndeliveredPublisher("L5 触达编排")
        assert publisher.publish(event("SignalEmitted")) is False
        assert len(publisher.events()) == 1             # 不静默丢弃
        assert "L5 触达编排" in publisher.note
        assert "未接入事件总线" in publisher.note
