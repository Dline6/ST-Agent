"""M4 运行时：M3 面 + L6 反思演进与生态。"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from st_agent.eco import (
    ECOSYSTEM_BOUNDARY,
    EcoError,
    EMPTY_STATE_TEXT,
    IMPORT_CONFIRMATION,
    SHARE_KINDS,
    OfficialIndex,
    ShareExporter,
    ShareImporter,
)
from st_agent.l1.events import EventBus
from st_agent.l1.runtime import (
    DOTENV_NAME,
    L1Runtime,
    open_runtime,
)
from st_agent.l1.studio import (
    DraftAcceptError,
    DraftIntake,
    DraftSessionError,
    DraftShapeError,
    TemplateLibrary,
    draft_from_dag,
    studio_empty_state,
)
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
from st_agent.l3.approval import ApprovalRequest, CapabilityApprovalPanel
from st_agent.l3.chat import SessionStore
from st_agent.l3.runtime import (
    ActionGate,
    AgentRunReport,
    LoopBounds,
    NativeToolCallProtocol,
    conclude_agent_run,
    investigate_family,
    run_agent_loop,
)
from st_agent.l5.runtime import L5Stack, build_l5
from st_agent.l5.sources import (
    DEFAULT_MONITOR_RULES,
    signal_event_for_analysis,
    signal_events_for_run,
)
from st_agent.l6 import (
    DEFAULT_TIER,
    GRADING_CONFIG_ID,
    NO_AUTHORIZATION_REASON,
    NO_REGISTRY_REASON,
    RESET_CONFIRMATIONS,
    RISK_CLASSES,
    TIER_CONFIG_ID,
    TIERS,
    ChangeProposal,
    ChangeRun,
    EvolutionRiskRule,
    L6Error,
    L6Stack,
    PatternObservation,
    PendingChange,
    SkillDraft,
    StudioHandoffError,
    SuggestionDraft,
    TrainingDraft,
    build_l6,
    week_key,
    week_window,
)

from st_agent.composition.adapters import (
    LlmPatternObserver,
    LlmTrainingUnderstander,
)
from st_agent.composition.common import (
    EXPORTS_DIR_NAME,
    _LLM_ENDPOINT_ID,
    _now,
)
from st_agent.composition.facades.chat import (
    DialogFacade,
)
from st_agent.composition.facades.eco import (
    EcoFacade,
    _ViolationRecorder,
)
from st_agent.composition.facades.reflection import (
    ReflectionFacade,
    _ChangeNotifier,
)
from st_agent.composition.runtime.m1 import (
    M1Runtime,
    _L2Stack,
    _as_kwargs,
    _bind_market,
    _build_l2,
    _build_l3,
)
from st_agent.composition.runtime.m2 import (
    M2Runtime,
    _L4Stack,
    _build_l4,
)
from st_agent.composition.runtime.m3 import (
    M3Runtime,
    TickResult,
    _SignalEmittingAnalyze,
    _build_l5,
    _publish_signal,
)

@dataclass(frozen=True)
class _EcoStack:
    """ECO 三面（[09](../../docs/技术架构-v2/09-生态与分享.md) 跨层生态面）：导出 / 导入校验 / 官方索引。"""

    exporter: ShareExporter
    importer: ShareImporter
    index: OfficialIndex


def _build_eco(
    runtime: L1Runtime,
    l2: _L2Stack,
    l4: _L4Stack,
    *,
    index_host: str = "",
    index_fetch: Any = None,
    now: Any = _now,
) -> _EcoStack:
    """叠 ECO 三面（[09 §2 / §3 / §4](../../docs/技术架构-v2/09-生态与分享.md)）。

    取材面全部来自各层已交付门面（导出：L1 技能 / 工作流库 + L4 视角阵容 + L2 记忆片段面；
    导入：同上 + L1 权限册）。官方索引经 L0 出网网关；`index_fetch` 缺省 ⇒ `browse()`
    显式 `unavailable`（**不内置端点**，端点属用户侧）。
    """
    return _EcoStack(
        exporter=ShareExporter(
            skills=runtime.skills, workflows=runtime.workflows, lenses=l4.roster,
            memory=MemoryShare(l2.graph), now=now,
        ),
        importer=ShareImporter(
            store=runtime.store, skills=runtime.skills, workflows=runtime.workflows,
            lenses=l4.roster, memory=FragmentImporter(l2.graph, l2.writer),
            permissions=runtime.skill_permissions, now=now,
        ),
        index=OfficialIndex(gateway=runtime.gateway, host=index_host, fetch=index_fetch),
    )


def _build_l6(
    runtime: L1Runtime,
    l2: _L2Stack,
    l5: L5Stack,
    *,
    events: Any = None,
    training_understander: Any = None,
    observer: Any = None,
    now: Any = _now,
) -> L6Stack:
    """叠 L6 一层（[08 全层](../../docs/技术架构-v2/08-L6-反思演进.md)）——全部经注入接线。

    周报的投出面 / 四段取材面接 L5 与 L2；训练理解端口与观察面由**组合根**注入（缺省由
    :func:`build_m4_runtime` 装真 LLM 适配器）；A-B 授权面与变更流经 01 §7 门面缺省自建
    （`build_l6` 口径）；`train` 去向与回访面在 :func:`_build_l3` 注入 L3。

    **Studio 草稿接收面**（[`T-L6-004.2`] / [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md)
    「衔接 story-06」）：组合根在此**首装配** [`DraftIntake`](../l1/studio/draft.py)
    （它此前从无装配点）——经 `build_l6(intake=…)` 建成 :class:`~st_agent.l6.studio_adapter.StudioHandoff`
    挂 `L6Stack.studio`，供主动提案与（L3 对话通道的）工作流草稿两处共用。
    """
    intake = DraftIntake(runtime.store, runtime.skills)
    return build_l6(
        runtime.store, registry=runtime.config_registry, events=events,
        dispatcher=l5.dispatcher, orchestrator=l5.delivery,
        frequency=l5.frequency, fatigue=l5.fatigue, reader=l2.reader,
        understander=training_understander, memory_writer=l2.writer,
        observer=observer, now=now, intake=intake,
    )


@dataclass(frozen=True)
class M4Runtime:
    """一次 M4 装配的全部句柄（M3 全套 + L6 面 + ECO 面 + 变更告知面）。

    L6 / ECO 与总线的接线**并列**在 :attr:`m3` 之外（同 M3 对 M2 的做法）：M3 是 M3 关卡的
    冻结交付物，改造它会让 M3 的用例与本对象互相牵连；而「L0–L5 之上的 L6 反思演进 + ECO
    生态面经同一条总线接起来」正是本关卡要证明的装配事实（[`T-INT-005`]）。

    :meth:`tick` 在 M3 的职责循环之上叠**每周反思**（[00 §5] 步 8）：到点且本周尚未投出时，
    先做一次模式识别，再投出周报——两者共用**同一周节奏**。
    """

    m3: M3Runtime
    l6: L6Stack
    eco: _EcoStack
    notifier: Any = None
    ecosystem: Any = None
    """ECO 生态面的**表现层适配面**（[`T-UI-004.4`]；组合根所有，表现层只经鸭子端口消费）。"""
    violations: Any = None
    """越界行为（`BehaviorViolation`）的**订阅留痕面**（表现层经 :attr:`ecosystem` 拉取）。"""
    l6_failures: list = field(default_factory=list)
    """L6 侧推进的显式失败记录（如模式识别未完成；**可查**，不吞）。"""

    # ── 与 M1/M2/M3 同形的便捷取用 ──────────────────────────────────────────

    @property
    def m1(self) -> M1Runtime:
        return self.m3.m1

    @property
    def m2(self) -> M2Runtime:
        return self.m3.m2

    @property
    def l5(self) -> L5Stack:
        return self.m3.l5

    @property
    def events(self) -> EventBus:
        return self.m3.events

    @property
    def store(self):
        return self.m3.store

    @property
    def chat(self) -> DialogFacade:
        return self.m3.chat

    @property
    def reflection(self) -> ReflectionFacade:
        """L6 的**表现层适配面**——表现层入口（反思中心 / 变更历史 / 演进授权）经它取数与处置。

        反馈采集仍走**交互层**的采集面（[01 §1]：`feedback_id` 的产生方是 L3），故这里把
        :meth:`DialogFacade.record_feedback` 一并交给它转发，而**不是**让表现层直写 L6 池。
        """
        return ReflectionFacade(
            l6=self.l6, feedback=self.m3.chat.record_feedback,
            skills=self.m1.runtime.skills,
        )

    def training_callbacks(self) -> tuple[Any, ...]:
        """待回访项读面（[08 §3] 第 3 步；表现层入口见 [`T-UI-004.2`]）。"""
        return tuple(self.l6.training.callbacks())

    # ── 职责循环（[00 §5] 步 4–8） ─────────────────────────────────────────

    def tick(self, now: datetime | None = None) -> TickResult:
        """推进一轮：M3 的主动服务 **+** 每周反思（到点则投出）。"""
        moment = self.m3.now() if now is None else now
        base = self.m3.tick(moment)
        return dataclasses.replace(base, weekly_report=self._weekly(moment))

    def _weekly(self, moment: datetime) -> Any:
        """到点则模式识别 + 投出周报；「一周一次」的判据取**盘上留痕**（重启安全）。"""
        reports = self.l6.reports
        if reports is None or not reports.due(moment):
            return None
        week = week_key(moment.date())
        stored = reports.stored(week)
        if stored is not None and stored.delivered_channel:
            return None
        self._detect_proposals(moment)
        return reports.publish(week, now=moment)

    def _detect_proposals(self, moment: datetime) -> None:
        try:
            self.l6.proposals.detect(now=moment)
        except Exception as exc:  # noqa: BLE001 —— 记因不吞；一次识别失败不该拖垮当轮报告
            self.l6_failures.append(f"模式识别未完成（{type(exc).__name__}）：{exc}")


INBOX_DIR_NAME = "inbox"
"""导入收件目录名（同上）。"""


def _eco_dir(root: Path | str, name: str) -> Path:
    """建好（若缺）表现层的分享文件目录并返回它。

    [09 §6] 的表现层口径：导入导出**只在两处目录内**读写，故目录在装配期先立好——
    导出时若父目录不存在，`ShareContainer.save_to` 会显式报错（它**不自建目录**）。
    """
    target = Path(root) / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_m4_runtime(
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
    synthesizer: Any = None,
    reviewer: Any = None,
    catalog: Any = None,
    executor: Any = None,
    channels: Mapping[str, Any] | None = None,
    notify: Any = None,
    synthesize: Any = None,
    cloud_transport: Any = None,
    email_host: str = "",
    webhook_host: str = "",
    credentials: Mapping[str, str] | None = None,
    monitor_rules: tuple = DEFAULT_MONITOR_RULES,
    training_understander: Any = None,
    observer: Any = None,
    investigations_of: Any = None,
    index_host: str = "",
    index_fetch: Any = None,
    notice_level: str = "important",
    now: Any = _now,
    **l1_kwargs: Any,
) -> M4Runtime:
    """装配 M4 全栈（＝ M3 的 L0–L5 + L6 反思演进 + ECO 生态面；[`T-INT-005`] 的**生产组合根**）。

    与 :func:`build_m3_runtime` 复用同一批私有栈构造器，差别在三处接线（都在组合根、
    不改任何层）：

    1. **叠 L6 一层**（:func:`_build_l6`）并把它接上同一条总线——反馈经 `FeedbackRecorded`
       落反思池，A-B 实验的授权面缺省接本层演进授权档位面，变更流经 01 §7 门面落值并发布
       `ChangeApplied` / `ChangeRolledBack`；
    2. **把 `train` 去向与回访面注入 L3**——`TrainingProtocol` 作总线的 `trainings` 端口、
       同时作对话门面的回访面（[08 §3] 第 3 步）；L3 只认鸭子面，**不 import L6**（[铁律 7]）；
    3. **叠 ECO 三面**（:func:`_build_eco`）与**变更告知面**（:class:`_ChangeNotifier`）——
       后者订阅两个变更事件、经 L5 渠道链显式告知（[08 §5]）。

    :param training_understander / observer: 训练理解端口与模式观察面的覆写口；缺省装真
        LLM 适配器（:class:`LlmTrainingUnderstander` / :class:`LlmPatternObserver`）。
        **离线关卡**传确定性替身，使 CI 不随本机 `.env` 有无而变（同 M1 A4 口径）。
    :param index_host / index_fetch: 官方索引端点的接线（缺任一项 ⇒ `browse()` 显式
        `unavailable`，**不内置端点**）。
    :param investigations_of: `investigate` 去向端口的**装配缝**（鸭子类型
        ``(runtime, l6_stack, bounds) -> port``）——给了即把产出的循环端口注入总线；
        **缺省 ``None`` ⇒ M4 行为逐字节不变**（该去向 fail-closed + 点名）。M5 组合根
        （:func:`build_m5_runtime`）经它装配循环——L3 的 `investigations=` 注射点早在
        [`T-AGT-006`] 就备好，此处只是把「谁来造那个端口」接上。
    其余关键字透传 :func:`build_m3_runtime` 的同名项（渠道 / 原生端口 / 云端参数 / 时钟）。
    """
    bus = EventBus()
    failures: list[str] = []
    runtime = open_runtime(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env,
        dotenv_path=dotenv_path, **l1_kwargs,
    )
    _bind_market(market_query, runtime.store)
    l2 = _build_l2(runtime, now=now, events=bus)
    l4 = _build_l4(
        runtime, l2, now=now, market_query=market_query,
        synthesizer=synthesizer, reviewer=reviewer, catalog=catalog, executor=executor,
    )
    l5 = _build_l5(
        runtime, l2, now=now, events=bus, channels=channels, notify=notify,
        synthesize=synthesize, cloud_transport=cloud_transport,
        email_host=email_host, webhook_host=webhook_host, credentials=credentials,
    )
    if training_understander is None:
        training_understander = LlmTrainingUnderstander(runtime.llm, _LLM_ENDPOINT_ID)
    if observer is None:
        observer = LlmPatternObserver(
            runtime.llm, _LLM_ENDPOINT_ID, sessions=SessionStore(runtime.store),
        )
    l6 = _build_l6(
        runtime, l2, l5, events=bus,
        training_understander=training_understander, observer=observer, now=now,
    )
    # 循环端口的装配缝（缺省不装）：双上界的 owner 在此立起**唯一实例**，既交
    # `_build_l3` 注册进 01 §7、又供端口取用（05 §10 的上界是 01 §7 配置项）。
    bounds = LoopBounds(runtime.store)
    investigations = (
        None if investigations_of is None
        else investigations_of(runtime, l6, bounds)
    )
    # L3 最后叠——它的 `train` 去向与回访面要注入 L6 的面（层间只认鸭子面，铁律 7）。
    l3 = _build_l3(
        runtime, l2, now=now, understander=understander,
        deliberations=_SignalEmittingAnalyze(
            analyze=l4.analyze,
            publish=lambda event: _publish_signal(bus, event, failures),
            failures=failures,
        ),
        events=bus, feedback_sink=bus,
        trainings=l6.training, callbacks=l6.training,
        investigations=investigations, bounds=bounds,
    )
    eco = _build_eco(
        runtime, l2, l4, index_host=index_host, index_fetch=index_fetch, now=now,
    )
    notifier = _ChangeNotifier(
        dispatcher=l5.dispatcher, policies=l5.policies, level=notice_level, failures=failures,
    )
    notifier.attach(bus)
    violations = _ViolationRecorder(failures=failures)
    violations.attach(bus)
    exports_dir = _eco_dir(root, EXPORTS_DIR_NAME)
    inbox_dir = _eco_dir(root, INBOX_DIR_NAME)
    ecosystem = EcoFacade(
        eco=eco,
        approvals=CapabilityApprovalPanel(book=runtime.skill_permissions),
        violations=violations,
        sandbox=runtime.sandbox,
        exports_dir=exports_dir,
        inbox_dir=inbox_dir,
    )
    m3 = M3Runtime(
        m2=M2Runtime(
            m1=M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3)),
            roster=l4.roster, deliberation=l4.deliberation, examiner=l4.examiner,
            viewer=l4.viewer, analyze=l4.analyze,
        ),
        l5=l5, events=bus, monitor_rules=monitor_rules,
        emission_failures=failures, now=now,
    )
    return M4Runtime(
        m3=m3, l6=l6, eco=eco, notifier=notifier,
        ecosystem=ecosystem, violations=violations,
    )
