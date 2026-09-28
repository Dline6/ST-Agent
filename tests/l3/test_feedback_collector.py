"""T-L3-005.2 测试：反馈采集点（[05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) + [08 §1](../../docs/技术架构-v2/08-L6-反思演进.md)；GWT-1..5）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from st_agent.contracts.identifiers import FeedbackId
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l3.feedback import (
    ACTIONS,
    SINK_ABSENT_NOTE,
    FeedbackCollector,
    FeedbackEvent,
    FeedbackTarget,
)

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=CST)
TRACE = "tr_" + "a" * 20
DELIVERY = "dlv_" + "b" * 20
CHANGE = "chg_" + "c" * 20


class _Sink:
    """记录型事件接收面（``publish`` 语义见 `FeedbackCollector`）。"""

    def __init__(self, accept: bool = True) -> None:
        self.seen: list[PlatformEvent] = []
        self._accept = accept

    def publish(self, event: PlatformEvent) -> bool:
        self.seen.append(event)
        return self._accept


# ───────────────────────── GWT-1 五动作采集 ─────────────────────────


class TestGwt1Collection:
    @pytest.mark.parametrize("action", ACTIONS)
    def test_every_action_is_collected(self, action: str) -> None:
        c = FeedbackCollector(now=lambda: NOW)
        reason = "不再关注该标的" if action == "rejected" else None
        result = c.record({"kind": "trace", "ref": TRACE}, action, reason=reason)
        assert result.status == "ok"
        event: FeedbackEvent = result.data.event
        assert event.action == action
        assert FeedbackId.of(event.feedback_id).value == event.feedback_id
        assert event.timestamp == NOW
        assert event.target.ref == TRACE

    def test_actions_match_08_1_five_kinds(self) -> None:
        assert ACTIONS == ("adopted", "ignored", "rejected", "queried", "liked")

    def test_context_is_carried_verbatim(self) -> None:
        marker = "我在早盘看到的推送"  # 用户数据，属数据展示、不阻断
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "delivery", "ref": DELIVERY}, "liked", context={"note": marker})
        assert result.data.event.context == {"note": marker}

    def test_feedback_id_can_be_supplied_by_the_caller(self) -> None:
        given = "fb_" + "f" * 20
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "change", "ref": CHANGE}, "adopted", feedback_id=given)
        assert result.data.event.feedback_id == given


# ───────────────────────── GWT-2 否定类必填理由 ─────────────────────────


class TestGwt2RejectedNeedsAReason:
    def test_missing_reason_is_refused(self) -> None:
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "rejected")
        assert result.status == "validation_failed"
        assert "否定类" in result.reason and "reason" in result.reason

    def test_blank_reason_is_refused(self) -> None:
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "rejected", reason="   ")
        assert result.status == "validation_failed"

    def test_reason_is_optional_for_other_actions(self) -> None:
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "ignored")
        assert result.status == "ok" and result.data.event.reason is None

    def test_model_enforces_the_same_invariant(self) -> None:
        """直接构造同样被拒（不变量在模型上，不只在采集面上）。"""
        with pytest.raises(ValueError):
            FeedbackEvent(feedback_id=FeedbackId.generate().value,
                          target=FeedbackTarget(kind="trace", ref=TRACE),
                          action="rejected", timestamp=NOW)


# ───────────────────────── GWT-3 target 三类 ─────────────────────────


class TestGwt3Targets:
    @pytest.mark.parametrize("kind,ref", [
        ("delivery", DELIVERY), ("trace", TRACE), ("change", CHANGE),
    ])
    def test_three_target_kinds_are_accepted(self, kind: str, ref: str) -> None:
        result = FeedbackCollector(now=lambda: NOW).record({"kind": kind, "ref": ref}, "liked")
        assert result.status == "ok" and result.data.event.target.kind == kind

    @pytest.mark.parametrize("kind,ref", [
        ("delivery", TRACE), ("trace", DELIVERY), ("change", "not-an-id"),
    ])
    def test_mismatched_id_shape_is_refused(self, kind: str, ref: str) -> None:
        result = FeedbackCollector(now=lambda: NOW).record({"kind": kind, "ref": ref}, "liked")
        assert result.status == "validation_failed"
        assert "01 §1" in result.reason

    def test_unknown_target_kind_is_refused(self) -> None:
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "signal", "ref": TRACE}, "liked")
        assert result.status == "validation_failed"

    def test_unknown_action_is_refused(self) -> None:
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "upvoted")
        assert result.status == "validation_failed"


# ───────────────────────── GWT-4 FeedbackRecorded 事件 ─────────────────────────


class TestGwt4EventDelivery:
    def test_event_is_published_and_anchored(self) -> None:
        sink = _Sink()
        result = FeedbackCollector(sink=sink, now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "adopted")
        assert result.status == "ok" and result.data.delivery.delivered is True
        (event,) = sink.seen
        assert event.event == "FeedbackRecorded"
        assert event.trace_id == TRACE and event.change_id is None
        assert event.payload["feedback_id"] == result.data.event.feedback_id
        assert event.payload["target"] == {"kind": "trace", "ref": TRACE}

    def test_change_target_is_anchored_on_change_id(self) -> None:
        sink = _Sink()
        FeedbackCollector(sink=sink, now=lambda: NOW).record(
            {"kind": "change", "ref": CHANGE}, "adopted")
        (event,) = sink.seen
        assert event.change_id == CHANGE and event.trace_id is None

    def test_delivery_target_carries_neither_anchor(self) -> None:
        sink = _Sink()
        FeedbackCollector(sink=sink, now=lambda: NOW).record(
            {"kind": "delivery", "ref": DELIVERY}, "liked")
        (event,) = sink.seen
        assert event.trace_id is None and event.change_id is None

    def test_absent_sink_is_not_reported_as_delivered(self) -> None:
        result = FeedbackCollector(now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "liked")
        assert result.status == "ok"          # 采集结果照常返回（不丢）
        assert result.data.delivery.delivered is False
        assert result.data.delivery.note == SINK_ABSENT_NOTE
        assert result.data.delivery.owner == "T-L6-001"   # 点名归属任务

    def test_unaccepted_delivery_is_reported_as_such(self) -> None:
        result = FeedbackCollector(sink=_Sink(accept=False), now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "liked")
        assert result.data.delivery.delivered is False
        assert result.data.delivery.note != ""

    def test_sink_defect_is_dependency_failed(self) -> None:
        class _Broken:
            def publish(self, event):
                raise RuntimeError("boom")

        result = FeedbackCollector(sink=_Broken(), now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "liked")
        assert result.status == "dependency_failed"


# ───────────────────────── GWT-5 零落盘 ─────────────────────────


class TestGwt5NoPersistence:
    def test_collector_holds_no_store_handle(self) -> None:
        """L3 不建反馈存储——采集面**不接** ``Store``（反馈池归 L6 的 `reflection` 分区）。"""
        collector = FeedbackCollector(now=lambda: NOW)
        assert not [f for f in vars(collector) if "store" in f.lower()]

    def test_no_partition_is_written(self, store) -> None:
        before = {p: store.list_files(p) for p in ("reflection", "execution_log")}
        FeedbackCollector(sink=_Sink(), now=lambda: NOW).record(
            {"kind": "trace", "ref": TRACE}, "liked")
        after = {p: store.list_files(p) for p in ("reflection", "execution_log")}
        assert before == after
