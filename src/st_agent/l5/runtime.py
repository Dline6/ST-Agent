"""L5 的**薄装配缝**（[07](../../../docs/技术架构-v2/07-L5-主动触达.md) 全层的构造面）。

[`T-L5-001.2`](../../../项目管理/tasks/T-L5-001.2-注意力预算与情境模式.md) 交付
`attention_budget_family(...)` 时把「**注入点**」点名交给 [`T-L5-002`](../../../项目管理/tasks/T-L5-002-渠道适配器投递编排与升级链.md)
（该批不先造 M3 组合根）；本模块就是那个注入点的落处——**L5 自持**自己的装配缝，
由组合根调用：

- 把两个 01 §7 族（`attention-budget.*` 与 `channel-delivery.*`）注入
  [`ConfigRegistryFacade`](../../l1/registry/facade.py)（**注入而非 import**：
  适配器住 L5、门面住 L1，方向向下，[铁律 7](../../../项目管理/工程宪法.md)）；
- 把渠道适配器、个性化面与投递编排接成一个 `DeliveryOrchestrator`；
- 把 L5 的**订阅面**挂到 [01 §11](../../docs/技术架构-v2/01-平台共享契约.md) 的事件总线上
  （`SignalEmitted` ⇒ 采纳 ⇒ 投递），并把结算事件发布出去
  （`DeliverySettled`）。

**边界**：本模块只装配 **L5 一层**。M3 的**跨层组合根** `build_m3_runtime`
（把 L5 与 L0–L4、UI 面接起来）归关卡 [`T-INT-004`](../../../项目管理/tasks/T-INT-004-M3集成关卡主动触达投递闭环.md)——
先例＝`build_m1_runtime` / `build_m2_runtime` 由 [`T-INT-002`](../../../项目管理/tasks/done/M1/T-INT-002-M1集成关卡首次可对话.md) /
[`T-INT-003`](../../../项目管理/tasks/done/M2/T-INT-003-M2集成关卡多视角决策闭环.md) 交付
（[工作流](../../../项目管理/工作流.md)「跨层装配与端到端验证不按此拆分——归里程碑集成关卡」）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from st_agent.l1.events import UndeliveredPublisher
from st_agent.l5.budget import AttentionBudget
from st_agent.l5.channel_policy import ChannelPolicies
from st_agent.l5.channel_registry import channel_policy_family
from st_agent.l5.channels import ChannelDispatcher
from st_agent.l5.daily_report import DailyReportBuilder
from st_agent.l5.delivery import EVENT_OWNER, DeliveryDispatch, DeliveryOrchestrator
from st_agent.l5.fatigue import FatigueMonitor
from st_agent.l5.frequency import FrequencyController
from st_agent.l5.personalize import Personalizer
from st_agent.l5.registry_adapter import (
    attention_budget_family,
    daily_report_family,
    fatigue_family,
    frequency_family,
)
from st_agent.l5.signal import SIGNAL_EVENT, adopt_signal

__all__ = ["L5Stack", "build_l5"]


@dataclass(frozen=True)
class L5Stack:
    """一次 L5 装配的全部句柄（不可变；由 :func:`build_l5` 构造）。"""

    budget: AttentionBudget
    policies: ChannelPolicies
    dispatcher: ChannelDispatcher
    personalizer: Personalizer
    delivery: DeliveryOrchestrator
    events: Any
    """事件面（缺省 :class:`~st_agent.l1.events.UndeliveredPublisher`——**不假装送达**）。"""
    frequency: FrequencyController | None = None
    """去重与频控面（07 §6）。"""
    reports: DailyReportBuilder | None = None
    """每日报告面（07 §5）。"""
    fatigue: FatigueMonitor | None = None
    """推送疲劳监控面（07 §7）。"""
    subscription: Any = None
    """`SignalEmitted` 的订阅句柄（未接入总线时为 ``None``）。"""
    registry: Any = None
    """注入的 01 §7 门面（未注入时为 ``None``——两个族随之未登记）。"""
    accepted: list = field(default_factory=list)
    """经总线采纳并已投递的信号（按到达顺序；**可解释**的落点）。"""


def build_l5(
    store: Any = None,
    *,
    registry: Any = None,
    events: Any = None,
    channels: Any = None,
    reader: Any = None,
    now: Any = None,
    budget: AttentionBudget | None = None,
    policies: ChannelPolicies | None = None,
    dispatcher: ChannelDispatcher | None = None,
    frequency: FrequencyController | None = None,
    reports: DailyReportBuilder | None = None,
    fatigue: FatigueMonitor | None = None,
) -> L5Stack:
    """装配 L5 面（两个 01 §7 族 + 渠道 + 编排 + 日报 + 频控 + 疲劳 + 事件订阅）。

    :param store: ``Store`` 句柄（预算 / 渠道偏好条目与投递留痕都落它；缺省纯内存态）
    :param registry: 01 §7 的 [`ConfigRegistryFacade`](../../l1/registry/facade.py)
        （鸭子类型 ``register_family``）；缺省 ``None`` ⇒ 各族**不登记**（读面仍可用）
    :param events: 事件总线（鸭子类型 ``publish`` / ``subscribe``）；缺省 ``None`` ⇒
        用 `UndeliveredPublisher`（**不假装送达**，归属点名）
    :param channels: 渠道 → 适配器 的映射（[`.1`](channels.py) 的四类适配器由组合根接线；
        未接线的渠道走显式不可用）
    :param reader: L2 `MemoryReader` 的鸭子面（个性化与日报的 Memory 更新摘要取材）
    :param frequency: 去重与频控面（缺省自建：与 :paramref:`fatigue` 的静音清单同接）
    :param reports: 日报面（缺省自建：接投出面与频控面）
    :param fatigue: 疲劳监控面（缺省自建：接编排面与渠道偏好写面）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """
    resolved_budget = budget if budget is not None else AttentionBudget(store=store, now=now)
    resolved_policies = (
        policies if policies is not None else ChannelPolicies(store=store, now=now)
    )
    resolved_dispatcher = (
        dispatcher if dispatcher is not None else ChannelDispatcher(channels)
    )
    personalizer = Personalizer(reader=reader)

    if registry is not None:
        # 01 §7 的三个族：注入而非 import（适配器住 L5、门面住 L1）
        registry.register_family(attention_budget_family(resolved_budget))
        registry.register_family(channel_policy_family(resolved_policies))

    resolved_events = events
    if resolved_events is None:
        resolved_events = UndeliveredPublisher(EVENT_OWNER)

    delivery = DeliveryOrchestrator(
        dispatcher=resolved_dispatcher, policies=resolved_policies,
        budget=resolved_budget, personalizer=personalizer,
        store=store, events=resolved_events, now=now,
    )
    resolved_fatigue = fatigue if fatigue is not None else FatigueMonitor(
        store=store, orchestrator=delivery, policies=resolved_policies, now=now,
    )
    resolved_frequency = frequency if frequency is not None else FrequencyController(
        store=store, mutes=resolved_fatigue, now=now,
    )
    resolved_reports = reports if reports is not None else DailyReportBuilder(
        store=store, orchestrator=delivery, reader=reader,
        frequency=resolved_frequency, dispatcher=resolved_dispatcher, now=now,
    )
    accepted: list[Any] = []
    stack = L5Stack(
        budget=resolved_budget, policies=resolved_policies,
        dispatcher=resolved_dispatcher, personalizer=personalizer,
        delivery=delivery, events=resolved_events, registry=registry,
        frequency=resolved_frequency, reports=resolved_reports, fatigue=resolved_fatigue,
        accepted=accepted,
    )
    if registry is not None:
        registry.register_family(frequency_family(resolved_frequency))
        registry.register_family(daily_report_family(resolved_reports))
        registry.register_family(fatigue_family(resolved_fatigue))
    if events is None:                           # 只有真接了总线才订阅（缺省发布端不订阅）
        return stack
    return dataclasses.replace(stack, subscription=events.subscribe(
        SIGNAL_EVENT, _adopting_handler(delivery, accepted),
        subscriber_id="l5:delivery-orchestrator",
    ))


def _adopting_handler(delivery: DeliveryOrchestrator, accepted: list):
    """`SignalEmitted` 的订阅者：**采纳**（[`.1`](signal.py)）后交投递编排。

    负载不合契约即抛 :class:`~st_agent.l5.errors.SignalAdoptionError`——总线按
    [01 §11](../../docs/技术架构-v2/01-平台共享契约.md) 的投递口径**逐订阅者记因**
    （**不吞**），故「拒绝」在派发结果里显式可见。
    """

    def _handle(event: Any) -> DeliveryDispatch:
        signal = adopt_signal(event)             # 缺字段 / 取值非法 ⇒ 显式拒绝
        result = delivery.dispatch(signal, now=None)
        accepted.append(signal)
        return result

    return _handle
