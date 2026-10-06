"""L1 · 进程内事件总线（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的投递口径）。

[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 一直只登记事件的**形态、发布方 / 订阅方
与 `trace_id` 关联要求**——「下层向上层只通过事件通信」的**投递（发布 / 订阅）面**没有承载，
于是 `MemoryConflictDetected` 的两端（L2 的产生与 L3 的消费）齐备却无人接线
（[L3 册 `A2`](../../../项目管理/遗留问题/L3-遗留问题.md)）。本模块是该口径的落地：

| 口径 | 本模块的落点 |
| --- | --- |
| 总线归属 L1 | 就是本模块——理由同 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的门面归属：装配只向下依赖 L0，且免各层各自实现一套（[D-067](../../../项目管理/决策日志.md)） |
| 鸭子端口接入 | 发布方持 :meth:`EventBus.publish`、订阅方持 :meth:`EventBus.subscribe`；**层与层之间不互相 import**（各层只认签名） |
| 同步、按登记顺序派发 | :meth:`EventBus.publish` 逐个同步调用订阅者，顺序即登记顺序 |
| 失败显式化 | 订阅者抛错**不吞、不阻断其余订阅者**：逐条记进 :class:`DispatchResult` |
| 未接通不假装 | :class:`UndeliveredPublisher` 作缺省发布端：受理恒为 ``False`` 并点名归属 |
| 不落盘 | 总线是**进程内通知**、自身零落盘；需要留痕的事件由发布方自行落 |

**跨进程**（常驻后端 → 可分离 UI 客户端）的推送是表现层消费面的事
（[00 §1.1](../../docs/技术架构-v2/00-架构总览.md)），**不在本模块**——本模块只做进程内。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.time_events import PlatformEvent

__all__ = [
    "DispatchResult",
    "EventBus",
    "Subscription",
    "SubscriptionHandle",
    "SubscriptionOutcome",
    "UndeliveredPublisher",
]

Handler = Callable[[PlatformEvent], Any]
"""订阅者：收一条事件，返回值不参与判据（判的是「有没有抛错」）。"""


class SubscriptionOutcome(BaseModel):
    """一次派发给**某个订阅者**的结果（受理 / 异常 + 原因）。"""

    model_config = ConfigDict(frozen=True)

    subscriber_id: str
    accepted: bool
    error: str = ""
    """订阅者抛错时的原因（`accepted=False` 时非空）。"""


class DispatchResult(BaseModel):
    """一次 :meth:`EventBus.publish` 的完整结果（**逐订阅者**可见）。"""

    model_config = ConfigDict(frozen=True)

    event_name: str
    outcomes: tuple[SubscriptionOutcome, ...] = ()

    @property
    def delivered(self) -> bool:
        """是否**至少有一个**订阅者受理（**无订阅者 ⇒ ``False``**：没人收到就是没人收到）。"""
        return any(outcome.accepted for outcome in self.outcomes)

    @property
    def failed(self) -> tuple[SubscriptionOutcome, ...]:
        """抛错的订阅者（异常不被吞成「已送达」，见 01 §11 口径）。"""
        return tuple(o for o in self.outcomes if o.error)


class SubscriptionHandle(BaseModel):
    """一次订阅的注销句柄（由 :meth:`EventBus.subscribe` 给出）。"""

    model_config = ConfigDict(frozen=True)

    token: int
    event_name: str
    subscriber_id: str


class Subscription(BaseModel):
    """一条已登记的订阅（总线内部用；按登记顺序派发即按本序列的顺序）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    handle: SubscriptionHandle
    handler: Handler = Field(exclude=True)


class EventBus:
    """进程内同步事件总线（01 §11 的投递口径；**唯一机制**，各层只认签名）。

    用法（两端都只经鸭子端口，互不 import）::

        bus = EventBus()
        bus.subscribe("MemoryConflictDetected", adjudicator.from_event,
                      subscriber_id="l3:conflict-adjudicator")
        queue.publish(...)   # L2 侧把 bus 作为 ``events`` 端口注入
    """

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._tokens = itertools.count(1)

    # ───────────────────────── 订阅面 ─────────────────────────

    def subscribe(
        self, event_name: str, handler: Handler, *, subscriber_id: str = "",
    ) -> SubscriptionHandle:
        """登记一个订阅者；返回**注销句柄**（按登记顺序派发）。

        :param subscriber_id: 可读标识（缺省用句柄 token 生成）——派发结果里逐条点名
        :raises ValueError: 事件名或处理器不合约（不静默收下）
        """
        if not str(event_name).strip():
            raise ValueError("事件名不得为空（01 §11 的事件清单成员）")
        if not callable(handler):
            raise ValueError(f"订阅者须可调用，收到 {type(handler).__name__}")
        token = next(self._tokens)
        handle = SubscriptionHandle(
            token=token, event_name=str(event_name),
            subscriber_id=subscriber_id or f"subscriber-{token}",
        )
        self._subscriptions.append(Subscription(handle=handle, handler=handler))
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> bool:
        """注销一个订阅者（幂等：已不在册 → ``False``，不报错）。"""
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.handle != handle]
        return len(self._subscriptions) != before

    def subscribers(self, event_name: str | None = None) -> tuple[str, ...]:
        """已登记的订阅者标识（按登记顺序；给定事件名即只看该事件）。"""
        return tuple(
            s.handle.subscriber_id for s in self._subscriptions
            if event_name is None or s.handle.event_name == event_name
        )

    # ───────────────────────── 发布面 ─────────────────────────

    def publish(self, event: PlatformEvent, *, event_name: str | None = None) -> DispatchResult:
        """同步派发给该事件的全部订阅者（按登记顺序），返回**逐订阅者**的结果。

        订阅者抛错**只记不抛**（否则一个坏订阅者会拖垮整条链路），
        其余订阅者**照常收到**——这是 01 §11「失败显式化」口径的落点。

        :param event_name: 派发键覆写（缺省取 ``event.event``）——便于测试与
            「同一负载多事件名」的接法；正常路径不必给
        """
        key = event_name if event_name is not None else str(event.event)
        outcomes: list[SubscriptionOutcome] = []
        for subscription in tuple(self._subscriptions):
            if subscription.handle.event_name != key:
                continue
            try:
                subscription.handler(event)
            except Exception as exc:  # noqa: BLE001 - 显式记因，不吞不阻断
                outcomes.append(SubscriptionOutcome(
                    subscriber_id=subscription.handle.subscriber_id,
                    accepted=False, error=f"{type(exc).__name__}: {exc}",
                ))
                continue
            outcomes.append(SubscriptionOutcome(
                subscriber_id=subscription.handle.subscriber_id, accepted=True,
            ))
        return DispatchResult(event_name=key, outcomes=tuple(outcomes))


class UndeliveredPublisher:
    """**未接入总线**时的显式发布端（01 §11「未接通不假装送达」的落点）。

    受理结果恒为 ``False`` 并点名归属任务——调用方据此如实记「未送达」，
    **既不假装送达、也不静默丢弃**（同 L3 `FeedbackCollector` 的既有取向）。

    :param owner: 归属说明（点名谁该接这条流）
    """

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self._events: list[PlatformEvent] = []

    def publish(self, event: PlatformEvent, **_kw: Any) -> bool:
        """记下这条事件但**不受理**（返回 ``False``）；原因经 :attr:`owner` 点名。"""
        self._events.append(event)
        return False

    def events(self) -> tuple[PlatformEvent, ...]:
        """本端收到过的事件（供调用方自行保留与重试——**不静默丢弃**）。"""
        return tuple(self._events)

    @property
    def note(self) -> str:
        """中性说明（未送达的原因与归属）。"""
        return f"未接入事件总线，本条事件未送达；归属：{self.owner}"
