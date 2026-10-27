"""M1 运行时：L0 → L1 → L2 → L3 的装配。"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from st_agent.contracts.neutrality import (
    RULEPACK_KIND,
    NeutralityGuard,
    default_rulepack,
    set_official_rulepack,
)
from st_agent.contracts.registry_types import SemVer
from st_agent.l1.pack import OfficialPack, ResourceEntry, load_official_pack
from st_agent.l1.runtime import (
    DOTENV_NAME,
    L1Runtime,
    open_runtime,
)
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l2.memory import (
    ONBOARDING_KIND,
    SHARE_CONFIRMATION,
    ConfidenceModel,
    ConflictQueue,
    FragmentImporter,
    MemoryDeleter,
    MemoryGraph,
    MemoryReader,
    MemoryShare,
    MemoryWriter,
    OnboardingProtocol,
    SliceQuery,
    WritePolicy,
    checked_node,
    memory_policy_family,
    new_node_id,
    official_onboarding_entry,
)
from st_agent.l3.chat import SessionStore
from st_agent.l3.commands import COMMAND_KIND, CommandRegistry, official_command_entry
from st_agent.l3.config import ConfigDraftHandling, ConfigDraftProtocol, WorkflowDraftBuilder
from st_agent.l3.conflict import ConflictAdjudicator
from st_agent.l3.dispatch import DispatchBus, DispatchOutcome
from st_agent.l3.feedback import FeedbackCollector
from st_agent.l3.intent import IntentDraft, IntentProtocol, LlmIntentUnderstander
from st_agent.l3.runtime import (
    ActionGate,
    AgentRunReport,
    LoopBounds,
    NativeToolCallProtocol,
    conclude_agent_run,
    investigate_family,
    run_agent_loop,
)

from st_agent.composition.common import (
    _LLM_ENDPOINT_ID,
    _now,
)
from st_agent.composition.facades.chat import (
    DialogFacade,
)

@dataclass(frozen=True)
class M1Runtime:
    """一次 M1 装配的全部句柄（不可变；由 :func:`build_m1_runtime` 构造）。"""

    runtime: L1Runtime
    graph: MemoryGraph
    writer: MemoryWriter
    reader: MemoryReader
    policy: WritePolicy
    queue: ConflictQueue
    confidence: ConfidenceModel
    deleter: MemoryDeleter
    onboarding: OnboardingProtocol
    sessions: SessionStore
    commands: CommandRegistry
    intent: IntentProtocol
    configs: ConfigDraftProtocol
    handling: ConfigDraftHandling
    adjudicator: ConflictAdjudicator
    feedback: FeedbackCollector
    bus: DispatchBus
    chat: DialogFacade
    bounds: LoopBounds

    @property
    def store(self):
        return self.runtime.store


@dataclass(frozen=True)
class _L2Stack:
    """L2 面（同一 `store`，`memory` 分区）——M1 / M2 共用同一段装配（[`T-INT-003`]）。"""

    graph: MemoryGraph
    writer: MemoryWriter
    reader: MemoryReader
    policy: WritePolicy
    queue: ConflictQueue
    confidence: ConfidenceModel
    deleter: MemoryDeleter
    onboarding: OnboardingProtocol


@dataclass(frozen=True)
class _L3Stack:
    """L3 面（会话落 `chat_history`）——`deliberations` 是总线的 `analyze` 端口（鸭子面）。"""

    sessions: SessionStore
    commands: CommandRegistry
    intent: IntentProtocol
    configs: ConfigDraftProtocol
    handling: ConfigDraftHandling
    adjudicator: ConflictAdjudicator
    feedback: FeedbackCollector
    bus: DispatchBus
    chat: DialogFacade
    bounds: LoopBounds


def _as_kwargs(stack: Any) -> dict[str, Any]:
    """栈对象 → 关键字字典（字段名与 `M1Runtime` 一致，故可直接展开构造；不做深拷贝）。"""
    return {f.name: getattr(stack, f.name) for f in dataclasses.fields(stack)}


def _bind_market(market_query: Any, store: Any) -> None:
    """官方 Pack 的取数面若采用「延迟绑定」（装配期先存根、运行时返回后绑到自持
    `Store`，见 `rig.MarketData`），由本根在唯一 `Store` 属主就位后绑上——与 M0
    `rig.assemble` 同一时序（组合根在此打开运行时，故绑定也归此）。"""
    bind = getattr(market_query, "bind", None)
    if callable(bind):
        bind(store)


def _build_l2(runtime: L1Runtime, *, now: Any, events: Any = None) -> _L2Stack:
    """叠 L2（`memory` 分区）并把 `memory-policy` 族注入 01 §7 统一配置注册表。

    L1 不 import L2（[铁律 7](../../项目管理/工程宪法.md)），故适配器住 L2、在组合根注册；
    该族**只读**——取值为对象 / 列表，不在统一标量落值面内，写面归各 owner 的 `set_*` API。

    :param events: 事件发布端口（鸭子类型 `publish`）；给了就由 `ConflictQueue` 在落盘新提案
        那一跳**自动**发布 `MemoryConflictDetected`（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)
        的投递口径），层间不必手递。缺省 ``None`` ＝该端**不注入**，行为与注入前逐字节相同。
    """
    store = runtime.store
    graph = MemoryGraph(store)
    writer = MemoryWriter(graph)
    confidence = ConfidenceModel(graph, now=now)
    reader = MemoryReader(graph, now=now(), confidence=confidence)
    policy = WritePolicy(store, now=now)
    queue = ConflictQueue(graph, writer, policy, now=now, events=events)
    deleter = MemoryDeleter(graph, now=now)
    onboarding = OnboardingProtocol(graph, writer, now=now)
    runtime.config_registry.register_family(
        memory_policy_family(
            store,
            declarations=lambda: (
                confidence.baseline_entry(),
                confidence.dynamics_entry(),
                policy.entry(),
                onboarding.question_entry(),
            ),
        )
    )
    return _L2Stack(
        graph=graph, writer=writer, reader=reader, policy=policy, queue=queue,
        confidence=confidence, deleter=deleter, onboarding=onboarding,
    )


def _build_l3(
    runtime: L1Runtime,
    l2: _L2Stack,
    *,
    now: Any,
    understander: Any,
    deliberations: Any = None,
    events: Any = None,
    feedback_sink: Any = None,
    trainings: Any = None,
    callbacks: Any = None,
    investigations: Any = None,
    bounds: Any = None,
) -> _L3Stack:
    """叠 L3（会话落 `chat_history`）并装载官方资源包（01 §13）。

    :param deliberations: 注入总线的 `analyze` 去向端口（鸭子面，`None` 即该去向
        fail-closed + 点名）；M2 由 :func:`build_m2_runtime` 传 L4 的 `AnalyzeService`，
        本层**不 import L4**（铁律 7）。
    :param events: 事件总线（鸭子类型 `subscribe`）——给了就把裁决承接面**订阅**上去
        （`MemoryConflictDetected` ⇒ 本面），层间不再靠调用方手递。
    :param feedback_sink: 反馈采集面的事件接收端口（鸭子类型 `publish`）——给了则
        每条 `FeedbackRecorded` 真的送达总线（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)）；
        缺省 ``None`` ⇒ 采集照常但**不送达**并如实标注（既有行为，逐字节不变）。
    :param trainings: 注入总线的 `train` 去向端口（鸭子面，`None` 即该去向 fail-closed
        + 点名）；M4 由 :func:`build_m4_runtime` 传 L6 的 `TrainingProtocol`，本层
        **不 import L6**（铁律 7）。
    :param callbacks: 训练**回访面**（鸭子类型 `callbacks()` / `acknowledge(...)`）——给了则
        对话往返带上待回访项（[08 §3](../../docs/技术架构-v2/08-L6-反思演进.md) 第 3 步）；
        缺省 ``None`` ⇒ 返回**不加**该键（既有行为，逐字节不变）。
    :param investigations: 注入总线的 `investigate` 去向端口（鸭子面，`None` 即该去向
        fail-closed + 点名）；缺省 ``None`` ⇒ **本层不装配循环**——循环端口的实际装配
        （protocol / runner / tools / gate / store）与 M5 组合根归 `T-INT-006`（[05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md)：
        「循环的装配归 M5 集成关卡」），本参数即那批装配落进总线的**注射点**。
    :param bounds: 循环双上界的 owner（:class:`~st_agent.l3.runtime.bounds.LoopBounds`）；
        缺省 ``None`` ⇒ 本层自建一面。M5 组合根传入**同一实例**，使「注册进 01 §7 的
        登记项」与「循环取用的生效上界」是**同一份**（不出现两个各读一遍的句柄）。

    同批把 L3 的 `investigate.*` 族（循环双上界的 01 §7 配置项，`T-AGT-007`）注入
    01 §7 统一配置注册表：L1 不 import L3（[铁律 7](../../项目管理/工程宪法.md)），
    故适配器住 L3、在组合根注册；`bounds` 句柄经 `_L3Stack` 供下游（循环装配）取用。
    """
    store = runtime.store
    bounds = LoopBounds(store) if bounds is None else bounds
    runtime.config_registry.register_family(investigate_family(bounds))
    sessions = SessionStore(store)
    commands = CommandRegistry()
    if understander is None:
        understander = LlmIntentUnderstander(runtime.llm, _LLM_ENDPOINT_ID)
    intent = IntentProtocol(
        understander=understander, commands=commands,
        matcher=runtime.runner, descriptors=runtime.skills,
    )
    configs = ConfigDraftProtocol(
        descriptors=runtime.skills,
        workflow=WorkflowDraftBuilder(descriptors=runtime.skills),
    )
    # 统一配置注册表门面已交付（T-L1-012 / D-067）→ 双通道同源由「声明面回落」
    # 升为「真登记项」（accept 经门面落值并产生真 change_id）
    handling = ConfigDraftHandling(
        descriptors=runtime.skills, registry=runtime.config_registry
    )
    adjudicator = ConflictAdjudicator(queue=l2.queue, graph=l2.graph)
    if events is not None:
        adjudicator.attach(events)               # L2 一产生就自动承接到本面
    feedback = FeedbackCollector(now=now, sink=feedback_sink)
    bus = DispatchBus(
        runner=runtime.runner, configs=configs, adjudications=adjudicator,
        deliberations=deliberations, trainings=trainings,
        investigations=investigations,
    )

    # ── 官方资源包（01 §13）：类型化容器按 kind 分发到各消费方 ─────────────────
    # 容器自带 L1 自有的 `skill`；本根贡献 `rulepack`（取自 contracts 的官方缺省）
    # 与 L3 的 `command`、L2 的 `onboarding_questions`。每条 kind 一个 loader，
    # 未注册即由容器显式拒——这里给全，故装载必成。
    # `skill` 的播种已在 `open_runtime` 内经 `install_official_pack` 完成，此处
    # `ensure_official_pack` 幂等复入（同一真相源，不重复落盘）。
    pack = OfficialPack()
    pack.add(ResourceEntry(
        kind=RULEPACK_KIND, version=SemVer(major=1, minor=0), payload=default_rulepack(),
    ))
    pack.add(official_command_entry())
    pack.add(official_onboarding_entry())
    load_official_pack(pack, {
        "skill": lambda _entry: ensure_official_pack(runtime.skills),
        COMMAND_KIND: lambda entry: commands.register_source("official", entry.payload),
        RULEPACK_KIND: lambda entry: set_official_rulepack(entry.payload),
        ONBOARDING_KIND: lambda entry: l2.onboarding.set_official_default(entry.payload),
    })

    chat = DialogFacade(
        sessions=sessions, reader=l2.reader, writer=l2.writer, intent=intent,
        bus=bus, adjudicator=adjudicator, feedback=feedback, callbacks=callbacks, now=now,
    )
    return _L3Stack(
        sessions=sessions, commands=commands, intent=intent, configs=configs,
        handling=handling, adjudicator=adjudicator, feedback=feedback, bus=bus,
        chat=chat, bounds=bounds,
    )


def build_m1_runtime(
    root: Path | str,
    passphrase: str,
    *,
    create: bool = False,
    market_query: Any = None,
    sender: Any = None,
    transport: Any = None,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    understander: Any = None,
    now: Any = _now,
    **l1_kwargs: Any,
) -> M1Runtime:
    """装配 M1 全栈（L0/L1 复用 `open_runtime`，在其 `store` 上层叠 L2 + L3）。

    :param understander: 意图理解端口；缺省用真实 :class:`LlmIntentUnderstander`
        （经 `runtime.llm`）。**离线关卡**传注入的确定性理解器（任务 A4），
        以免 CI 随本机 `.env` 有无而变。
    :param market_query / sender / transport / llm_env / dotenv_path / l1_kwargs:
        透传给 `open_runtime` / `build_l1_runtime`（[T-L1-011] 的装配入口）。

    各 L3 编排器**用真实依赖构造**：`matcher=` `runtime.runner`、`descriptors=`
    `runtime.skills`（[05 §3] 注释所称「如 L1 的 SkillRunner / SkillRegistry」，任务 A1），
    `DispatchBus` 注满 `runner` / `configs` / `adjudications` 三面（`analyze` 去向
    留空 → fail-closed + 点名；真链路见 :func:`build_m2_runtime`）。
    """
    runtime = open_runtime(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env,
        dotenv_path=dotenv_path, **l1_kwargs,
    )
    _bind_market(market_query, runtime.store)
    l2 = _build_l2(runtime, now=now)
    l3 = _build_l3(runtime, l2, now=now, understander=understander)
    return M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3))
