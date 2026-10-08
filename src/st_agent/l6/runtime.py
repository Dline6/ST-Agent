"""L6 的**薄装配缝**（[08](../../../docs/技术架构-v2/08-L6-反思演进.md) 全层的构造面）。

[`.1`](pool.py) 交反思数据池、[`.2`](weekly.py) 交周报本体、[`.3`](training.py) 交训练对话协议，
[`proposal.py`](proposal.py) 交主动提案、[`experiment.py`](experiment.py) 交 A/B 实验——各自独立
可用；本模块把它们接成一个 `L6Stack`，由组合根调用：

- 把 ``weekly-report.*`` 族注入 [`ConfigRegistryFacade`](../l1/registry/facade.py)
  （**注入而非 import**：适配器住 L6、门面住 L1，方向向下，[铁律 7](../../../项目管理/工程宪法.md)）；
- 把**反思数据池**挂到 [01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的事件总线上
  （`FeedbackRecorded` ⇒ 落池）——反馈经总线**一跳到底**，调用方不必手递；
- 周报的**投出面**（L5 渠道面）与四段取材面（L5 读面 / L2 记忆面 / 建议面）按注入接线；
- 训练对话的**理解端口**与**记忆写入面**按注入接线（缺省即 fail-closed / 显式未写入）；
- 主动提案的**观察面**与 A/B 实验的**授权面**按注入接线（缺省即不产提案 / 不启用）；
- **演进授权档位**（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）由本层自建并向门面登记 `evolution.*` 族，
  **变更流**（[§5](../../../docs/技术架构-v2/08-L6-反思演进.md) / [§6](../../../docs/技术架构-v2/08-L6-反思演进.md)）与**出厂重置**（[§6](../../../docs/技术架构-v2/08-L6-反思演进.md)）
  被接成一层——变更流经注入的 01 §7 门面落值、经注入的事件总线告知。

**边界**：本模块只装配 **L6 一层**。M4 的**跨层组合根** `build_m4_runtime`
（把 L6 与 L0–L5、UI 面接起来、加常驻到点驱动）归关卡
[`T-INT-005`](../../../项目管理/tasks/T-INT-005-M4集成关卡反思演进与生态闭环.md)——
先例＝`build_l5` 住 L5、`build_m3_runtime` 归
[`T-INT-004`](../../../项目管理/tasks/done/M3/T-INT-004-M3集成关卡主动触达投递闭环.md)
（[工作流](../../../项目管理/工作流.md)「跨层装配与端到端验证不按此拆分——归里程碑集成关卡」）。

**本模块不起定时器、不起线程**——「何时推进一轮」归生产入口的常驻循环
（同 [T-INT-004](../../../项目管理/tasks/done/M3/T-INT-004-M3集成关卡主动触达投递闭环.md)
的「判定与推进是显式时刻的纯函数」口径）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from st_agent.l6.authorization import EvolutionAuthorization
from st_agent.l6.change import EvolutionChangeFlow
from st_agent.l6.experiment import DEFAULT_MIN_SAMPLE, ExperimentConsole
from st_agent.l6.pool import FEEDBACK_EVENT, FeedbackPool
from st_agent.l6.proposal import ProposalEngine
from st_agent.l6.registry_adapter import (
    agent_family,
    evolution_family,
    proposal_family,
    weekly_report_family,
)
from st_agent.l6.reset import FactoryReset
from st_agent.l6.runtime_authorization import RuntimeAuthorization
from st_agent.l6.studio_adapter import StudioHandoff
from st_agent.l6.training import TrainingProtocol
from st_agent.l6.weekly import WeeklyReportBuilder

__all__ = ["L6Stack", "build_l6"]

SUBSCRIBER_ID = "l6:feedback-pool"
"""反思数据池在总线上的订阅者标识（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 逐订阅者记因）。"""


@dataclass(frozen=True)
class L6Stack:
    """一次 L6 装配的全部句柄（不可变；由 :func:`build_l6` 构造）。"""

    pool: FeedbackPool
    """反思数据池（[08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""
    reports: WeeklyReportBuilder
    """每周反思报告面（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""
    training: TrainingProtocol
    """训练对话协议面（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md)）；供组合根注入
    [05 §4](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的 `train` 去向（**注入归里程碑集成关卡**）。"""
    proposals: ProposalEngine
    """主动提案面（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）；其 :meth:`ProposalEngine.proposals`
    即周报第四段的 ``advisor``（**两处同源**，故它**直接**充当该端口），:meth:`ProposalEngine.detect` 做模式识别。"""
    experiments: ExperimentConsole
    """A/B 实验面（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）；启用门经**注入的授权面**，
    缺省即用本层的 **演进授权档位面**（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)，
    `authorizer` 显式传入时以后者为准）。"""
    authorization: EvolutionAuthorization | None = None
    """演进授权档位与风险分级清单面（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）；
    :func:`build_l6` 恒自建一面（除非以覆写口另行注入替身）。"""
    agent_authorization: RuntimeAuthorization | None = None
    """**运行期**授权档位与判据面（[08 §5 共享口径](../../../docs/技术架构-v2/08-L6-反思演进.md) /
    [`T-AGT-005.1`](../../项目管理/tasks/T-AGT-005.1-运行期授权档位与共享清单.md)）；档位是独立条目
    `agent.authorization`，清单面取自 :paramref:`authorization`（**同一份** `evolution.risk-grading`）。
    供跨层组合根把它接成循环的授权端口（[`T-AGT-005.2`](../../项目管理/tasks/T-AGT-005.2-逐步闸门与终止交还.md)
    的 `ActionGate`；接线归里程碑集成关卡 [`T-INT-006`](../../项目管理/tasks/T-INT-006-M5集成关卡受控自主闭环.md)）。"""
    change_flow: Any = None
    """变更流面（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) / [§6](../../../docs/技术架构-v2/08-L6-反思演进.md)）——
    所有演进动作的**唯一通道**；未接 01 §7 门面时其生效面被拒（不假装落值）。"""
    factory_reset: Any = None
    """出厂重置面（[08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""
    studio: StudioHandoff | None = None
    """主动提案落 **Studio 草稿接收面**（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)
    「衔接 story-06」）；`DraftIntake` 由组合根注入（缺省 `None` ⇒ 交 Studio 时 fail-closed
    并点名，**不假装已交**）。"""
    events: Any = None
    """事件面（缺省 ``None`` ⇒ **未订阅**，池子仅可经显式 :meth:`FeedbackPool.consume` 写入）。"""
    registry: Any = None
    """注入的 01 §7 门面（未注入时为 ``None``——该族随之未登记）。"""
    feedback_subscription: Any = None
    """`FeedbackRecorded` 的订阅句柄（未接入总线时为 ``None``）。"""


def build_l6(
    store: Any = None,
    *,
    registry: Any = None,
    events: Any = None,
    dispatcher: Any = None,
    orchestrator: Any = None,
    frequency: Any = None,
    fatigue: Any = None,
    reader: Any = None,
    advisor: Any = None,
    understander: Any = None,
    memory_writer: Any = None,
    observer: Any = None,
    rules: Any = None,
    authorizer: Any = None,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    now: Any = None,
    pool: FeedbackPool | None = None,
    reports: WeeklyReportBuilder | None = None,
    training: TrainingProtocol | None = None,
    proposals: ProposalEngine | None = None,
    experiments: ExperimentConsole | None = None,
    authorization: EvolutionAuthorization | None = None,
    agent_authorization: RuntimeAuthorization | None = None,
    change_flow: EvolutionChangeFlow | None = None,
    factory_reset: FactoryReset | None = None,
    intake: Any = None,
) -> L6Stack:
    """装配 L6 面（池子 + 周报 + 训练对话 + 主动提案 + A/B 实验 + 01 §7 族 + 事件订阅）。

    :param store: ``Store`` 句柄（反馈与周报都落它；缺省纯内存态）
    :param registry: 01 §7 的 [`ConfigRegistryFacade`](../l1/registry/facade.py)
        （鸭子类型 ``register_family``）；缺省 ``None`` ⇒ 各族**不登记**（读面仍可用）
    :param events: 事件总线（鸭子类型 ``subscribe``）；缺省 ``None`` ⇒ **不订阅**，
        池子仅可经显式 ``consume`` 写入（**不假装已接线**）
    :param dispatcher: L5 `ChannelDispatcher`（周报的投出面；缺省 ``None`` ⇒ 不投出）
    :param orchestrator / frequency / fatigue: L5 的投递 / 频控 / 疲劳面（周报 ① 的取材面）
    :param reader: L2 `MemoryReader` 鸭子面（周报 ② 的取材面）
    :param advisor: 建议面鸭子端口（周报 ④ 的取材面）；缺省 ``None`` ⇒ 用本层的
        **提案引擎**（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 第四段与
        [§4](../../../docs/技术架构-v2/08-L6-反思演进.md) 同源）——**不传即接上规则表**
    :param understander: **训练对话的理解端口**（鸭子类型
        ``understand(correction, *, target="") -> TrainingDraft | Mapping | None``，
        如组合根注入的真实 LLM 实现）；缺省 ``None`` ⇒ 训练对话 **fail-closed**
        （`unavailable` + 点名），**不硬猜**
    :param memory_writer: L2 ``MemoryWriter`` 鸭子面（训练确认后写 `pattern` 节点）；
        缺省 ``None`` ⇒ 训练落账时**显式标注未写入记忆**，不假装写了
    :param observer: **模式观察面**鸭子端口（``observations() -> Sequence[PatternObservation]``）；
        缺省 ``None`` ⇒ :meth:`ProposalEngine.detect` **不产提案**（fail-closed + 原因）
    :param rules: 提案的规则表（缺省用 [`DEFAULT_RULES`](proposal.py)；声明式数据）
    :param authorizer: **A/B 实验的授权面**鸭子端口（``permits(scope) -> bool``）；
        缺省 ``None`` ⇒ **以本层的演进授权档位面充当**（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 的档位本体，
        即 :paramref:`authorization` 那一面）——档位缺省 `collaborative`，故缺省装配下实验仍**不自动启用**
    :param authorization: 演进授权档位与风险分级清单面的覆写口（缺省自建；见 [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）
    :param agent_authorization: **运行期**授权档位与判据面的覆写口（缺省自建，**清单面取自**
        :paramref:`authorization`——两档共享同一张 `evolution.risk-grading`）；
        **不复用** :paramref:`authorization` 的档位条目（两条独立条目，[D-090](../../项目管理/决策日志.md) ①）
    :param min_sample: A/B 实验的保守判定样本下限（缺省 5）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    :param intake: L1 的 Studio 草稿接收面 [`DraftIntake`](../l1/studio/draft.py)
        （**经组合根装配后注入**，已持 `store` + `SkillRegistry`）；缺省 ``None`` ⇒
        :class:`~st_agent.l6.studio_adapter.StudioHandoff` 交 Studio 时 **fail-closed** 并点名
    :param pool / reports / training / proposals / experiments / authorization /
        change_flow / factory_reset: 覆写口（**测试**注入确定性件；缺省自建）
    """
    resolved_pool = pool if pool is not None else FeedbackPool(store=store, now=now)
    resolved_proposals = proposals if proposals is not None else ProposalEngine(
        store=store, observer=observer, registry=registry, rules=rules, now=now,
    )
    resolved_reports = reports if reports is not None else WeeklyReportBuilder(
        store=store, pool=resolved_pool, orchestrator=orchestrator,
        frequency=frequency, fatigue=fatigue, reader=reader,
        advisor=advisor if advisor is not None else resolved_proposals,
        dispatcher=dispatcher, now=now,
    )
    resolved_training = training if training is not None else TrainingProtocol(
        store=store, understander=understander, memory_writer=memory_writer,
        pool=resolved_pool, now=now,
    )
    resolved_authorization = (
        authorization if authorization is not None
        else EvolutionAuthorization(store=store, registry=registry, now=now)
    )
    resolved_agent_authorization = (
        agent_authorization if agent_authorization is not None
        else RuntimeAuthorization(
            store=store, registry=registry, grading=resolved_authorization, now=now,
        )
    )
    resolved_experiments = experiments if experiments is not None else ExperimentConsole(
        store=store,
        authorizer=authorizer if authorizer is not None else resolved_authorization,
        min_sample=min_sample, now=now,
    )
    resolved_flow = change_flow if change_flow is not None else EvolutionChangeFlow(
        store=store, registry=registry, authorization=resolved_authorization,
        events=events, now=now,
    )
    resolved_reset = factory_reset if factory_reset is not None else FactoryReset(
        change_flow=resolved_flow, authorization=resolved_authorization,
        experiments=resolved_experiments, store=store, now=now,
    )
    resolved_studio = StudioHandoff(intake=intake, proposals=resolved_proposals)
    stack = L6Stack(
        pool=resolved_pool, reports=resolved_reports, training=resolved_training,
        proposals=resolved_proposals, experiments=resolved_experiments,
        authorization=resolved_authorization,
        agent_authorization=resolved_agent_authorization,
        change_flow=resolved_flow,
        factory_reset=resolved_reset,
        studio=resolved_studio,
        events=events, registry=registry,
    )
    if registry is not None:
        registry.register_family(weekly_report_family(resolved_reports))
        registry.register_family(proposal_family(resolved_proposals))
        registry.register_family(evolution_family(resolved_authorization))
        registry.register_family(agent_family(resolved_agent_authorization))
    if events is None:                           # 只有真接了总线才订阅（缺省不假装已接线）
        return stack
    subscription = events.subscribe(
        FEEDBACK_EVENT, resolved_pool.consume, subscriber_id=SUBSCRIBER_ID,
    )
    return dataclasses.replace(stack, feedback_subscription=subscription)
