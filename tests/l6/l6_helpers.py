"""L6 层测试夹具件（三支用例共用；同 ``tests/l5/l5_helpers.py`` 的范式）。

时间锚点固定（``NOW`` ＝ 2026-10-11 周日 20:00 CST，正是缺省的到点时刻），故事件时刻、
报告窗口与幂等断言都可复算；ID 全为 [01 §1](../../docs/技术架构-v2/01-平台共享契约.md)
形态的确定性字符串（不经随机生成）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from st_agent.contracts.time_events import PlatformEvent
from st_agent.l3.feedback import FeedbackCollector, FeedbackTarget

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 11, 20, 0, tzinfo=CST)
"""缺省「现在」：**周日 20:00**——正是缺省的到点日与到点时刻。"""

PASS = "l6-rig-passphrase"

WEEK = "2026-W41"
"""`NOW` 所在的 ISO 周（窗口 2026-10-05 → 2026-10-11）。"""

PREV_WEEK = "2026-W40"
"""前一周（窗口 2026-09-28 → 2026-10-04）。"""

FB_ADOPTED = "fb_" + "a" * 20
FB_REJECTED = "fb_" + "b" * 20
FB_IGNORED = "fb_" + "c" * 20
FB_LIKED = "fb_" + "d" * 20
FB_QUERIED = "fb_" + "e" * 20

DLV_ID = "dlv_" + "1" * 20
SIG_ID = "sig_" + "2" * 20
TRACE_ID = "tr_" + "5" * 20
MN_ID = "mn_" + "4" * 20
CHG_ID = "chg_" + "6" * 20

REJECT_REASON = "我觉得这条不对，因为估值已经偏高"
"""带**第一人称**的用户 `reason`——用于钉住「用户数据不过 01 §6」的边界（D-053）。"""

PROPOSAL_REASON = "本周该类推送被连续忽略达阈值"
"""候选理由样例（**生成性文案**，中性）。"""


def feedback_event(
    *,
    feedback_id: str = FB_ADOPTED,
    action: str = "adopted",
    target_kind: str = "delivery",
    ref: str = DLV_ID,
    reason: str | None = None,
    occurred_at: datetime = NOW,
    context: dict[str, Any] | None = None,
) -> PlatformEvent:
    """一条 ``FeedbackRecorded`` 事件（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)）。

    经 [L3 的采集面](../../src/st_agent/l3/feedback/collector.py) 真实产出——
    负载形态与交互层铸造的一致（`feedback_id` 由 L3 生成，L6 只消费）。
    """
    sink: list[PlatformEvent] = []
    result = FeedbackCollector(sink=SimpleNamespace(publish=lambda e: sink.append(e) or True),
                              now=lambda: occurred_at).record(
        FeedbackTarget(kind=target_kind, ref=ref), action,
        reason=reason, context=context or {}, feedback_id=feedback_id,
    )
    assert result.status == "ok", result.reason
    assert len(sink) == 1
    return sink[0]


def delivery_record(
    *,
    delivery_id: str = DLV_ID,
    status: str = "read",
    route: str = "separate",
    created_at: datetime = NOW,
    signal_id: str = SIG_ID,
) -> SimpleNamespace:
    """一条投递留痕的鸭子替身（[L5 `DeliveryRecord`](../../src/st_agent/l5/delivery_store.py) 的用到的字段）。"""
    return SimpleNamespace(
        delivery_id=delivery_id, signal_id=signal_id, trace_id=TRACE_ID,
        status=status, route=route, created_at=created_at,
        evidence_refs=("ann_1111111111111111111",), title="", body="",
    )


def ledger(*records: SimpleNamespace) -> Any:
    """`DeliveryOrchestrator` 的鸭子面（只用到 `ledger`）。"""
    return SimpleNamespace(ledger=lambda: tuple(records))


def frequency(counts: tuple[tuple[str, int], ...] = ()) -> Any:
    """`FrequencyController` 的鸭子面（`counts()` → `DedupCount` 形态）。"""
    items = tuple(
        SimpleNamespace(dedup_key=key, trigger_count=count, window_start=NOW)
        for key, count in counts
    )
    return SimpleNamespace(counts=lambda: items)


def fatigue(states: tuple[tuple[str, int, bool], ...] = ()) -> Any:
    """`FatigueMonitor` 的鸭子面（`counts()` → `FatigueState` 形态）。"""
    items = tuple(
        SimpleNamespace(dedup_key=key, ignored_streak=streak, threshold=5, muted=muted)
        for key, streak, muted in states
    )
    return SimpleNamespace(counts=lambda: items)


def memory_reader(*, nodes: tuple[dict[str, Any], ...] = (), confidence: float = 0.8) -> Any:
    """L2 `MemoryReader` 的鸭子替身（`query(SliceQuery(...))`）。"""
    items = tuple(
        SimpleNamespace(
            node=SimpleNamespace(
                memory_node_id=node.get("memory_node_id", MN_ID),
                type=node.get("type", "evolution"),
                dimension=node.get("dimension", "关注面"),
                updated_at=node.get("updated_at", datetime(2026, 10, 5, 9, 0, tzinfo=CST)),
            ),
            confidence=node.get("confidence", confidence),
            source=node.get("source", "user_stated"),
        )
        for node in nodes
    )
    return SimpleNamespace(query=lambda spec: SimpleNamespace(slices=items, as_of=NOW))


def advisor(*candidates: dict[str, Any]) -> Any:
    """建议面的鸭子替身（`proposals(evidence) -> Sequence[...]`）。"""
    return SimpleNamespace(proposals=lambda evidence: tuple(candidates))


def recording_channel(channel: str = "desktop", *, status: str = "ok") -> Any:
    """记录投递载荷的渠道替身（[`ChannelAdapter`](../../src/st_agent/l5/channels.py) 协议）。

    `status` 非 ``ok`` 时模拟**不可用**渠道（降级链的末端），用于钉住「不假装送达」。
    """
    from st_agent.l5.channels import ChannelHealth, ChannelResult

    payloads: list[Any] = []

    def deliver(payload: Any) -> Any:
        payloads.append(payload)
        if status != "ok":
            return ChannelResult(channel=channel, status="unavailable", detail="替身：本渠道不可用")
        return ChannelResult(channel=channel, status="ok")

    return SimpleNamespace(
        channel=channel, payloads=payloads, deliver=deliver,
        health=lambda: ChannelHealth(
            channel=channel, available=status == "ok", offline_level="full",
            reason="" if status == "ok" else "替身：本渠道不可用",
        ),
        degrade=lambda reason: ChannelResult(channel=channel, status="unavailable", detail=reason),
    )
