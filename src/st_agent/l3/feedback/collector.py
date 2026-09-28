"""L3 · 反馈采集点（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 第二条 + [08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。

所有**推送 / 结论 / 建议**的反馈入口（采纳 / 忽略 / 否定 / 追问 / 点赞）在**交互层**
采集，产生 ``FeedbackEvent``（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)
六字段），经 ``FeedbackRecorded`` 事件（[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md)）交 L6。

三段分工：

1. **采集**——:meth:`FeedbackCollector.record` 把「反馈对象 + 动作 + 理由 + 情境」
   收敛为一条 :class:`FeedbackEvent`（``feedback_id`` 由本层生成，
   [01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md) 的产生方据此登记为交互层）。
2. **送达**——经**注入的事件接收面**发出 ``PlatformEvent(event="FeedbackRecorded")``
   （[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md)）。
3. **不落盘**——**L3 不建反馈存储**：反馈全部进 L6 的本地反思数据池
   （[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md) 的 ``reflection`` 分区），
   故本模块**不接** :class:`~st_agent.l0.storage.store.Store`、零落盘。

**未送达不假装**（假设 A3）：未注入事件接收面时**不送达**——采集结果的
:class:`FeedbackDelivery` 里 ``delivered=False``、``owner`` 点名归属任务，由调用方
自行保留与重试；**既不假装已送达、也不静默丢弃**。L6（``T-L6-001``）尚未开工，
故「未送达」是当下的常态而非异常。

**中性视角（铁律 2 / [D-053](../../../项目管理/决策日志.md)）**：``reason`` 与
``context`` 是**用户数据**、属数据展示，**不过** [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)
执行点 2；本模块也**不产**生成性文案（无卡片 / 标签 / 问句），故无 §6 校验点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.identifiers import ChangeId, DeliveryId, FeedbackId, TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l3.config.handling import FEEDBACK_POOL_OWNER

__all__ = [
    "ACTIONS",
    "SINK_ABSENT_NOTE",
    "TARGET_ID_TYPES",
    "FeedbackAction",
    "FeedbackCollector",
    "FeedbackDelivery",
    "FeedbackEvent",
    "FeedbackRecord",
    "FeedbackTarget",
    "FeedbackTargetKind",
]

ACTIONS: tuple[str, ...] = ("adopted", "ignored", "rejected", "queried", "liked")
"""五类动作（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)：采纳 / 忽略 / 否定 / 追问 / 点赞）。"""

FeedbackAction = Literal["adopted", "ignored", "rejected", "queried", "liked"]

FeedbackTargetKind = Literal["delivery", "trace", "change"]
"""反馈对象的契约类型：某条推送 / 某条结论 / 某条建议（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""

TARGET_ID_TYPES: dict[str, type] = {
    "delivery": DeliveryId,
    "trace": TraceId,
    "change": ChangeId,
}
"""反馈对象的 ``ref`` 须是上述 01 §1 的 ID 形态之一（**不合形态即拒**，不静默收下无法寻址的目标）。"""

SINK_ABSENT_NOTE = "未注入事件接收面，本次反馈未送达"


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _first_message(exc: ValidationError) -> str:
    """取校验错的首条人类可读消息（去掉 pydantic 的 ``Value error,`` 包装前缀）。

    模型的 ``reason`` 是**渲染前须过 §6 的中性文案**（[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md)），
    故不把 pydantic 的英文包装串直接透出。
    """
    errors = exc.errors()
    if not errors:
        return str(exc)
    msg = str(errors[0].get("msg") or "").strip()
    return msg.removeprefix("Value error, ").strip() or str(exc)


class FeedbackTarget(BaseModel):
    """反馈对象（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md) 的 ``target``）。

    ``ref`` 按 ``kind`` 走 [01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md) 的
    ID 形态校验——**无法寻址的反馈对象一律拒收**（否则反馈无法回溯到被反馈的推送 /
    结论 / 建议）。
    """

    model_config = ConfigDict(frozen=True)

    kind: FeedbackTargetKind
    ref: str

    @model_validator(mode="after")
    def _ref_shape(self) -> "FeedbackTarget":
        id_type = TARGET_ID_TYPES[self.kind]
        try:
            id_type.of(self.ref)
        except Exception as exc:
            raise ValueError(
                f"反馈对象的 ref {self.ref!r} 不合 {self.kind} 的 ID 形态（01 §1）：{exc}"
            ) from exc
        return self


class FeedbackEvent(BaseModel):
    """一条用户反馈（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md) 六字段的机器可读形态）。"""

    model_config = ConfigDict(frozen=True)

    feedback_id: str
    """[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md) 标识；**产生方＝交互层（L3）**，本机生成。"""
    target: FeedbackTarget
    action: FeedbackAction
    reason: str | None = None
    """用户说明的原因（用户数据，原样承载、不过 §6）；**否定类必填**（见下）。"""
    timestamp: datetime
    """发生时刻（带时区，[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    context: dict[str, Any] = Field(default_factory=dict)
    """情境（供模式识别；用户数据，原样承载）。"""

    @model_validator(mode="after")
    def _shape(self) -> "FeedbackEvent":
        try:
            FeedbackId.of(self.feedback_id)
        except Exception as exc:
            raise ValueError(f"feedback_id {self.feedback_id!r} 不合 01 §1 形态：{exc}") from exc
        if self.timestamp.tzinfo is None:
            raise ValueError("反馈时间必须带时区语义（01 §8）")
        if self.action == "rejected" and not (self.reason or "").strip():
            raise ValueError(
                "否定类反馈（rejected）必须携 reason（08 §1「否定类建议必填入口」）"
            )
        return self

    def to_event(self) -> PlatformEvent:
        """上行通知（[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md) 的 ``FeedbackRecorded``）。

        ``trace_id`` / ``change_id`` **如适用**——反馈对象是结论（``trace``）或建议
        （``change``）时，其 ID 即事件的关联锚点；指向推送（``delivery``）时两者皆无
        （[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md) 未把 ``FeedbackRecorded`` 列入必带 ``trace_id`` 的一类）。
        """
        return PlatformEvent(
            event="FeedbackRecorded",
            payload={
                "feedback_id": self.feedback_id,
                "target": {"kind": self.target.kind, "ref": self.target.ref},
                "action": self.action,
                "reason": self.reason,
                "context": dict(self.context),
            },
            trace_id=self.target.ref if self.target.kind == "trace" else None,
            change_id=self.target.ref if self.target.kind == "change" else None,
            occurred_at=self.timestamp,
        )


class FeedbackDelivery(BaseModel):
    """一次送达的结果（「事件去哪」不靠猜）。"""

    model_config = ConfigDict(frozen=True)

    delivered: bool
    owner: str = FEEDBACK_POOL_OWNER
    """归属任务（未送达时点名；反馈池归 L6，[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""
    note: str = ""


class FeedbackRecord(BaseModel):
    """一次采集的产出（事件本体 + 送达结果）。"""

    model_config = ConfigDict(frozen=True)

    event: FeedbackEvent
    delivery: FeedbackDelivery


class FeedbackCollector:
    """反馈采集面（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param sink: 注入的事件接收面（鸭子类型 ``publish(event: PlatformEvent) -> bool``，
        返回是否受理）；缺省 ``None`` → **不送达**（结果里显式标注 + 点名），
        **不假装送达、也不丢**
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）

    **无落盘句柄**——本类不接 ``Store``，反馈池归 L6（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。
    """

    def __init__(self, *, sink: Any = None, now: Any = None) -> None:
        self._sink = sink
        self._now = _now if now is None else now

    def record(
        self,
        target: FeedbackTarget | dict[str, Any],
        action: str,
        *,
        reason: str | None = None,
        context: dict[str, Any] | None = None,
        feedback_id: str | None = None,
    ) -> ResultEnvelope:
        """采集一次反馈（载荷为 :class:`FeedbackRecord`）。

        参数与动作不合 [08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md) 的契约 →
        ``validation_failed``（**不允许保存**）；采集成功后一律尝试送达，
        送达与否**如实**记进 ``delivery``。
        """
        try:
            event = FeedbackEvent(
                feedback_id=feedback_id or FeedbackId.generate().value,
                target=target if isinstance(target, FeedbackTarget) else FeedbackTarget(**target),
                action=action,  # type: ignore[arg-type]
                reason=reason,
                timestamp=self._now(),
                context=dict(context or {}),
            )
        except ValidationError as exc:
            return ResultEnvelope.validation_failed(f"反馈不合契约：{_first_message(exc)}")
        except ValueError as exc:
            return ResultEnvelope.validation_failed(f"反馈不合契约：{exc}")
        if self._sink is None:
            return ResultEnvelope.ok(
                FeedbackRecord(
                    event=event,
                    delivery=FeedbackDelivery(delivered=False, note=SINK_ABSENT_NOTE),
                ),
                as_of=event.timestamp,
            )
        try:
            accepted = self._sink.publish(event.to_event())
        except Exception as exc:  # 注入端口的实现缺陷 → 显式失败，不静默降级
            return ResultEnvelope.dependency_failed(f"反馈事件接收面异常：{exc}")
        delivered = accepted is not False
        return ResultEnvelope.ok(
            FeedbackRecord(
                event=event,
                delivery=FeedbackDelivery(
                    delivered=delivered,
                    note="" if delivered else "事件接收面未受理本次反馈",
                ),
            ),
            as_of=event.timestamp,
        )
