"""L6 层测试夹具件（三支用例共用；同 ``tests/l5/l5_helpers.py`` 的范式）。

时间锚点固定（``NOW`` ＝ 2026-10-11 周日 20:00 CST，正是缺省的到点时刻），故事件时刻、
报告窗口与幂等断言都可复算；ID 全为 [01 §1](../../docs/技术架构-v2/01-平台共享契约.md)
形态的确定性字符串（不经随机生成）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from st_agent.contracts.time_events import PlatformEvent
from st_agent.l3.feedback import FeedbackCollector, FeedbackTarget

_UNSET = object()
"""哨兵：区分「未给 raw」与「raw 显式为 None」（后者＝端口明确表示理解不出）。"""

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


# ───────────────────────── T-L6-002 批次的夹具 ─────────────────────────

CORRECTION = "我觉得估值已经偏高，这条推送不该按机会处理"
"""用户给出的修正原话（**带第一人称**——用于钉住「用户数据不过 01 §6」的边界）。"""

RESTATEMENT = "用户认为该标的估值偏高，不应按机会类推送处理"
"""概念性复述样例（**生成性文案**，中性）。"""

PATTERN_TEXT = "对高估值标的的机会类推送持否定倾向"
"""模式陈述样例（**生成性文案**，中性）。"""

SUGGESTION_REASON = "该类推送被反复否定，建议收紧判定口径"
"""建议理由样例（**生成性文案**，中性）。"""


def understander(
    *, restatement: str = RESTATEMENT, pattern: str = PATTERN_TEXT,
    suggestion: Mapping[str, Any] | None = None, raw: Any = _UNSET,
) -> Any:
    """训练对话的理解端口替身（`understand(correction, *, target="")`）。

    ``raw`` 覆盖返回体本身（用于钉住「端口返回非法结构即显式失败」与「给不出复述 ⇒
    `unavailable`」两条降级路径）；缺省返回一份 **合法** 的理解草案。
    """
    payload: Any = raw if raw is not _UNSET else {
        "restatement": restatement, "pattern": pattern, "suggestion": suggestion,
    }
    return SimpleNamespace(understand=lambda correction, target="": payload)


def memory_writer() -> Any:
    """L2 `MemoryWriter` 的鸭子替身（只用到 `add_node`，原样回吐节点）。"""
    nodes: list[Any] = []
    return SimpleNamespace(add_node=lambda node: nodes.append(node) or node, nodes=nodes)


def train_confirmation(
    *, correction: str = CORRECTION, target: str | None = None,
    values: Mapping[str, Any] | None = None, confirmed: bool = True,
) -> Any:
    """一张 `train` 去向的意图确认卡（经**真实** `checked_confirmation` 构造）。"""
    from st_agent.contracts.result_envelope import ResultEnvelope
    from st_agent.l3.intent.protocol import ConfirmationItem, checked_confirmation

    return checked_confirmation(
        envelope=ResultEnvelope.ok({"correction": correction}), intent="train", target=target,
        items=(ConfirmationItem(text="修正说明", value=correction, source="stated"),),
        values=dict(values if values is not None else {"correction": correction}),
        confirmed=confirmed,
    )


NEXT_WEEK_NOW = NOW + timedelta(days=7)
"""下一 ISO 周的时刻（`NOW` 是 2026-W41 的周日 20:00，加 7 天即 W42 的周日 20:00）。"""

SKILL_DRAFT: dict[str, Any] = {
    "name": "估值复核流程",
    "description": "对高估值标的的机会判定做复核的工作流",
    "nodes": ({"node_id": "n1", "skill_id": "sk_valuation_check_v1.0"},),
    "flow_name": "valuation_review",
}
"""一份合法的工作流草稿（[03 §4](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 的 `workflow_draft` 形态）。"""

PROPOSAL_TEXT = "该类问题重复出现超过阈值，可考虑为其创建专门能力"
"""提案理由样例（**生成性文案**，中性）。"""


def observation(
    *, key: str = "high-valuation", count: int = 6, sample: str = "这个标的是不是太贵了",
    refs: tuple[str, ...] = (TRACE_ID,), draft: Any = _UNSET, reason: str = PROPOSAL_TEXT,
) -> dict[str, Any]:
    """一条模式观察（映射形态；`draft=None` 即**显式不附草稿**）。"""
    return {
        "key": key, "count": count, "sample": sample, "refs": refs,
        "draft": SKILL_DRAFT if draft is _UNSET else draft, "reason": reason,
    }


def observer(*items: Any) -> Any:
    """模式观察面的鸭子替身（`observations()`）。"""
    return SimpleNamespace(observations=lambda: tuple(items))


def entry_reader(**defaults: Any) -> Any:
    """01 §7 条目读面的鸭子替身（`entry(config_id) -> 含 default 的条目`）。"""
    def entry(config_id: str) -> Any:
        if config_id in defaults:
            return SimpleNamespace(config_id=config_id, default=defaults[config_id])
        return None

    return SimpleNamespace(entry=entry)


WEEKLY_EVIDENCE: dict[str, Any] = {
    "week_deliveries": 4,
    "feedback_total": 5,
    "rejected": 1,
    "ignored": 5,
    "delivery_ids": (DLV_ID,),
    "feedback_ids": (FB_IGNORED,),
}
"""一份周报 `advisor` 的注入观察（`ignored` 占多数且达阈值 ⇒ 命中缺省规则 R1）。"""
