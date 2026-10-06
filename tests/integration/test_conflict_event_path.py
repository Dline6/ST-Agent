"""跨层装配：`MemoryConflictDetected` 的**事件通道**（[L3 册 `A2`](../项目管理/遗留问题/L3-遗留问题.md) 的收口用例）。

[01 §11](../docs/技术架构-v2/01-平台共享契约.md) 的投递口径落地前，L2 的
[`ConflictQueue.event_for`](../src/st_agent/l2/memory/conflict.py)（产生）与 L3 的
[`ConflictAdjudicator.from_event`](../src/st_agent/l3/conflict/adjudication.py)（消费）
**两端齐备却无人接线**——「下层向上层只通过事件通信」没有承载。

本用例跑**真装配**：真 `Store` + 真 L2 图谱/队列 + 真 L3 裁决面 + 真
[`EventBus`](../src/st_agent/l1/events.py)，只把**观察面**留在断言里——
故「L2 一产生、L3 就收到」是装配出来的事实，而不是调用方手递的结果。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l0.storage import Store
from st_agent.l1.events import EventBus, UndeliveredPublisher
from st_agent.l2.memory import (
    ConflictQueue,
    MemoryGraph,
    MemoryWriter,
    WritePolicy,
    checked_node,
)
from st_agent.l3.conflict import ConflictAdjudicator
from st_agent.l5.channel_policy import ChannelPolicies
from st_agent.l5.channels import ChannelDispatcher, ChannelHealth, ChannelResult
from st_agent.l5.delivery import DeliveryOrchestrator
from st_agent.l5.signal import adopt_signal

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 6, 8, 0, tzinfo=CST)
PASS = "integration-rig-passphrase"
TRACE_ID = "tr_" + "5" * 20


def _attention(memory_node_id: str, watchlist: tuple[str, ...], **over):
    fields = {
        "type": "attention", "memory_node_id": memory_node_id, "confidence": 0.8,
        "source": "user_stated", "privacy_level": "private",
        "created_at": NOW, "updated_at": NOW, "watchlist": watchlist,
    }
    fields.update(over)
    return checked_node(**fields)


class TestSignalEventBuilder:
    """发布端调用面：`signal_event(...)` 组出的负载能被采纳面读回（01 §11 五字段）。"""

    def test_signal_event_round_trips_through_adoption(self):
        from st_agent.l5.signal import SIGNAL_EVENT, SignalContent, adopt_signal, signal_event

        content = SignalContent(
            conclusion="该标的近两日公告密度高于其 30 日均值",
            lens_stances=(
                {"lens_id": "lens_" + "a" * 20, "stance": "negative",
                 "summary": "基本面指标走弱"},
            ),
        )
        event = signal_event(
            level="emergency", content_ref=content,
            evidence_refs=("ann_" + "1" * 20,), dedup_key="sh.600000:announcement_density",
            source_trace_id=TRACE_ID, occurred_at=NOW,
        )
        assert event.event == SIGNAL_EVENT and event.trace_id == TRACE_ID
        signal = adopt_signal(event)
        assert signal.level == "emergency"
        assert signal.content_ref.conclusion == content.conclusion
        assert signal.signal_id.startswith("sig_")


class TestConflictEventPath:
    """GWT-1：L2 产生 `MemoryConflictDetected` ⇒ L3 **自动**承接（不再靠调用方手递）。"""

    @pytest.fixture()
    def rig(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        graph = MemoryGraph(store)
        writer = MemoryWriter(graph)
        bus = EventBus()
        queue = ConflictQueue(
            graph, writer, WritePolicy(store, now=lambda: NOW),
            now=lambda: NOW, events=bus,
        )
        adjudicator = ConflictAdjudicator(queue=queue, graph=graph)
        adjudicator.attach(bus)
        return store, graph, writer, queue, bus, adjudicator

    def test_conflict_detected_reaches_l3_without_manual_handover(self, rig):
        _store, _graph, writer, queue, _bus, adjudicator = rig
        writer.add_node(_attention("mn_" + "1" * 20, ("sh.600000",)))
        inferred = _attention(
            "mn_" + "2" * 20, ("sz.000001",),
            source="inferred", provenance={"trace_id": TRACE_ID},
        )
        proposal = queue.propose(inferred, trace_id=TRACE_ID)
        assert proposal is not None                      # 冲突命中 ⇒ 产提案
        received = adjudicator.received()
        assert len(received) == 1                        # **未经调用方手递**就承接到
        assert received[0].status == "ok"
        card = received[0].data
        assert card.conflict_id == proposal.conflict_id

    def test_publish_is_one_shot_per_new_proposal(self, rig):
        _store, _graph, writer, queue, _bus, adjudicator = rig
        writer.add_node(_attention("mn_" + "1" * 20, ("sh.600000",)))
        inferred = _attention(
            "mn_" + "2" * 20, ("sz.000001",),
            source="inferred", provenance={"trace_id": TRACE_ID},
        )
        queue.propose(inferred, trace_id=TRACE_ID)
        queue.propose(inferred, trace_id=TRACE_ID)       # 幂等重放：返回既有提案
        assert len(adjudicator.received()) == 1          # 只在新提案落盘那一跳发一次

    def test_no_conflict_means_no_event(self, rig):
        _store, _graph, _writer, queue, _bus, adjudicator = rig
        # 白名单内的类型（`history`）且无同类分歧 ⇒ 直写，不产提案、不发事件
        clean = checked_node(
            type="history", memory_node_id="mn_" + "3" * 20, confidence=0.8,
            source="inferred", provenance={"trace_id": TRACE_ID},
            privacy_level="private", created_at=NOW, updated_at=NOW,
            event="买入 sh.600000", outcome="unknown",
        )
        assert queue.propose(clean, trace_id=TRACE_ID) is None
        assert adjudicator.received() == ()

    def test_unsubscribed_adjudicator_receives_nothing(self, rig):
        _store, _graph, writer, queue, bus, adjudicator = rig
        assert adjudicator.detach(bus) is True
        writer.add_node(_attention("mn_" + "1" * 20, ("sh.600000",)))
        inferred = _attention(
            "mn_" + "2" * 20, ("sz.000001",),
            source="inferred", provenance={"trace_id": TRACE_ID},
        )
        assert queue.propose(inferred, trace_id=TRACE_ID) is not None
        assert adjudicator.received() == ()

    def test_producing_end_works_without_a_bus(self, tmp_path):
        """不注入发布面时行为与从前一致（只增不改）：提案照落，只是不发送。"""
        store = Store.create(tmp_path / "root", PASS)
        graph = MemoryGraph(store)
        writer = MemoryWriter(graph)
        queue = ConflictQueue(graph, writer, WritePolicy(store), now=lambda: NOW)
        writer.add_node(_attention("mn_" + "1" * 20, ("sh.600000",)))
        inferred = _attention(
            "mn_" + "2" * 20, ("sz.000001",),
            source="inferred", provenance={"trace_id": TRACE_ID},
        )
        assert queue.propose(inferred, trace_id=TRACE_ID) is not None
        assert queue.event_for(queue.get(queue.pending()[0].conflict_id)).event == (
            "MemoryConflictDetected"
        )                                                # 事件仍可由调用方另取

    def test_delivery_settled_event_flows_through_the_same_bus(self, tmp_path):
        """L5 的结算事件走同一总线（01 §11：`DeliverySettled` 的订阅方含 L6）。"""
        store = Store.create(tmp_path / "l5root", PASS)
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("DeliverySettled", lambda e: seen.append(str(e.payload["status"])),
                      subscriber_id="l6:reflection-pool")

        signal = adopt_signal(_signal_emitted())
        orch = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher({"desktop": _DesktopEcho()}),
            policies=ChannelPolicies(store=store, now=lambda: NOW),
            store=store, events=bus, now=lambda: NOW,
        )
        first = orch.dispatch(signal, now=NOW)
        orch.mark_read(first.record.delivery_id, now=NOW)
        assert seen == ["read"]                          # 结算经总线抵达订阅方

        # 未接入总线时**不假装送达**：事件交到缺省发布端但不受理，归属点名可见
        publisher = UndeliveredPublisher("L5 事件发布面")
        silent = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher({"desktop": _DesktopEcho()}),
            policies=ChannelPolicies(store=store, now=lambda: NOW),
            store=store, events=publisher, now=lambda: NOW,
        )
        second = silent.dispatch(signal, now=NOW)
        silent.mark_read(second.record.delivery_id, now=NOW)
        assert len(silent.settlements()) == 1            # 事件如实记下（不丢）
        assert len(publisher.events()) == 1              # 交到发布端而**不受理**
        assert "未接入事件总线" in publisher.note


class _DesktopEcho:
    """桌面渠道的最小替身（集成用例只关心事件流）。"""

    channel = "desktop"

    def deliver(self, payload):
        return ChannelResult(channel="desktop", status="ok")

    def health(self):
        return ChannelHealth(channel="desktop", available=True, offline_level="full")

    def degrade(self, reason: str):
        return ChannelResult(channel="desktop", status="unavailable", detail=reason)


def _signal_emitted():
    """一条合 01 §11 形态的 `SignalEmitted`（真路径：采纳面按它铸 `signal_id`）。"""
    return PlatformEvent(
        event="SignalEmitted",
        payload={
            "level": "important",
            "content_ref": {
                "conclusion": "该标的近两日公告密度高于其 30 日均值",
                "lens_stances": [
                    {"lens_id": "lens_" + "a" * 20, "stance": "negative",
                     "summary": "基本面指标走弱"},
                ],
            },
            "evidence_refs": ["ann_" + "1" * 20],
            "dedup_key": "sh.600000:announcement_density",
            "source_trace_id": TRACE_ID,
        },
        trace_id=TRACE_ID, occurred_at=NOW,
    )
