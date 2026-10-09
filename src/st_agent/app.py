"""生产组合根（[00 §5 反向流](../../docs/技术架构-v2/00-架构总览.md)）：M1 关卡 [`T-INT-002`] 与
M2 关卡 [`T-INT-003`]。

现仓库此前**没有任何生产装配根**——L2/L3 各编排器只在测试 fixture 里拼。本模块把它们
装配到**同一个 `Store`** 上：

    Store → open_runtime(L0/L1) → MemoryGraph/Writer/Reader/…(L2)
          → SessionStore/IntentProtocol/DispatchBus/…(L3) → 对话门面 chat
          →（M2）LensRoster/Deliberation/CrossExaminer/DivergenceViewer → analyze 去向

两个入口共用同一段 L2/L3 装配（本模块的私有栈构造器）：

- :func:`build_m1_runtime`——M1 面（「首次可对话」）：L0→L1→L2→L3。`analyze` 去向
  留空 → 该去向 fail-closed + 点名（当时的真实状态）。
- :func:`build_m2_runtime`——M2 面（「多视角决策闭环」＝ MVP）：M1 面 **+** L4，
  并把 L4 的 `AnalyzeService` 注入总线，使 `analyze` 去向成为真链路。

**`app` 不是第七层**（同 `ui`，[D-060] ⑤）：它只**向下** import 各层做装配，任何层不得
反向 import 它——方向由 [`tests/test_layering.py`](../../tests/test_layering.py) 的
`test_app_is_composition_root_only` 钉住（不进 `LAYER_ORDER`，避免把「不是层」反向编码，
见 [`T-INT-002`] A2；M2 起允许集含 `l4`，见 [`T-INT-003`] A1）。

对话门面额外承接 [05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) / [D-062](../../../项目管理/决策日志.md)
显式指派给 M1 关卡的 **`memory_op` 偏好写入支**：`source=user_stated` 直写经 `MemoryWriter`
（[04 §3.2](../../docs/技术架构-v2/04-L2-记忆图谱.md)），**不改** `DispatchBus` / `ConflictAdjudicator`
已交付代码（裁决支路径不变）。
"""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from st_agent.contracts.neutrality import (
    RULEPACK_KIND,
    NeutralityGuard,
    default_rulepack,
    set_official_rulepack,
)
from st_agent.contracts.registry_types import SemVer
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import PlatformEvent
from st_agent.contracts.trace import Trace
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
from st_agent.l1.pack import OfficialPack, ResourceEntry, load_official_pack
from st_agent.l1.runtime import L1Runtime, open_runtime
from st_agent.l1.skills.ids import parse_skill_id
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l1.studio import (
    DraftAcceptError,
    DraftIntake,
    DraftSessionError,
    DraftShapeError,
)
from st_agent.l2.memory import (
    ONBOARDING_KIND,
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
from st_agent.l3.commands import COMMAND_KIND, CommandRegistry, official_command_entry
from st_agent.l3.config import ConfigDraftHandling, ConfigDraftProtocol, WorkflowDraftBuilder
from st_agent.l3.conflict import ConflictAdjudicator
from st_agent.l3.dispatch import DispatchBus, DispatchOutcome
from st_agent.l3.feedback import FeedbackCollector
from st_agent.l3.home import build_context_card
from st_agent.l3.intent import IntentDraft, IntentProtocol, LlmIntentUnderstander
from st_agent.l3.render import (
    describe_adjudication,
    describe_context_card,
    describe_divergence_map,
    describe_draft,
    describe_trace,
)
from st_agent.l3.runtime import (
    ActionGate,
    AgentRunReport,
    LoopBounds,
    NativeToolCallProtocol,
    conclude_agent_run,
    investigate_family,
    run_agent_loop,
)
from st_agent.l4.analyze import AnalyzeService
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.deliberation import Deliberation
from st_agent.l4.divergence_view import DivergenceViewer
from st_agent.l4.ports import (
    LlmEvidenceReviewer,
    LlmOpinionSynthesizer,
    MarketDimensionCatalog,
)
from st_agent.l4.roster import LensRoster
from st_agent.l5.channels import (
    ChannelPayload,
    DesktopChannel,
    EmailChannel,
    ImWebhookChannel,
    TtsChannel,
)
from st_agent.l5.errors import SignalEmissionError
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

__all__ = [
    "DialogFacade",
    "InvestigationOutcome",
    "LlmPatternObserver",
    "LlmTrainingUnderstander",
    "M1Runtime",
    "M2Runtime",
    "M3Runtime",
    "M4Runtime",
    "M5Runtime",
    "ReflectionFacade",
    "TickResult",
    "build_m1_runtime",
    "build_m2_runtime",
    "build_m3_runtime",
    "build_m4_runtime",
    "build_m5_runtime",
]

_LLM_ENDPOINT_ID = "cloud-main"
"""引导装载端点的固定标识（[l1/runtime.py](../l1/runtime.py) 的 `LLM_ENDPOINT_ID`，T-L1-011）。"""


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True)
class TurnResult:
    """一次「输入 → 理解 → 澄清」往返的产出（确认卡**未确认**，等用户动作）。"""

    session_id: str
    draft: IntentDraft | None
    clarification: Any  # ClarificationRound
    confirmation: Any  # IntentConfirmation | None（draft.intent is None 时不产卡）

    @property
    def needs_confirmation(self) -> bool:
        return self.confirmation is not None


class DialogFacade:
    """对话门面：把 L2/L3 编排器串成 [00 §5] 反向流的一条调用面。

    只**装配与转发**，不加域逻辑——各段语义仍由各编排器负责（失败一律走信封、
    不伪造）。在途确认卡按 `session_id` 持于**进程内**（任务 A6）。
    """

    def __init__(
        self,
        *,
        sessions: SessionStore,
        reader: MemoryReader,
        writer: MemoryWriter,
        intent: IntentProtocol,
        bus: DispatchBus,
        adjudicator: ConflictAdjudicator,
        feedback: FeedbackCollector,
        callbacks: Any = None,
        now: Any = _now,
    ) -> None:
        self._sessions = sessions
        self._reader = reader
        self._writer = writer
        self._intent = intent
        self._bus = bus
        self._adjudicator = adjudicator
        self._feedback = feedback
        self._callbacks = callbacks
        self._now = now
        self._pending: dict[str, IntentDraft] = {}

    # ───────────────────── §3 输入 → 理解 → 澄清 → 确认卡 ─────────────────────

    def post(
        self, text: str, *, session_id: str | None = None, topic: str = ""
    ) -> TurnResult | ResultEnvelope:
        """落一条 user 消息并收敛意图；未收敛时返回理解信封（`unavailable` / `empty`）。"""
        sid = session_id or self._sessions.create(now=self._now()).session_id
        self._sessions.append(sid, text, now=self._now())
        understood = self._intent.understand(text)
        if understood.status != "ok" or not isinstance(understood.data, IntentDraft):
            return understood
        draft = understood.data
        round_ = self._intent.clarify(draft)
        if draft.intent is None:  # 未收敛——给方向候选，不产卡（§3.2 不硬猜）
            self._pending.pop(sid, None)
            return TurnResult(sid, draft, round_, None)
        self._pending[sid] = draft
        return TurnResult(sid, draft, round_, self._intent.confirm(draft))

    def confirm_and_dispatch(
        self,
        session_id: str,
        *,
        answers: Mapping[str, Any] | None = None,
        values: Mapping[str, Any] | None = None,
        structure: Mapping[str, Any] | None = None,
    ) -> DispatchOutcome | ResultEnvelope:
        """用户确认 → 派发（[05 §4]）。确认卡缺位 → `validation_failed`，不伪造。"""
        draft = self._pending.get(session_id)
        if draft is None:
            return ResultEnvelope.validation_failed(
                "该会话没有待确认的意图卡，无法派发（05 §3.2）",
            )
        confirmation = self._intent.confirm(draft, answers).mark_confirmed()
        outcome = self._bus.dispatch(
            confirmation, values=values, structure=structure, now=self._now(),
        )
        if outcome.envelope.status == "ok":
            self._pending.pop(session_id, None)
        return outcome

    # ───────────────────── §9 memory_op 偏好写入支（本关卡承接的欠账） ─────────────────────

    def write_preference(
        self, node_fields: Mapping[str, Any], *, privacy_level: str = "private"
    ) -> ResultEnvelope:
        """用户**显式**表达偏好 → `user_stated` 直写 L2（[04 §3.2]；[05 §9] 的偏好写入支）。

        不经白名单门、不产提案——这是用户自陈，与副驾推断（`inferred`）分途。
        """
        fields = dict(node_fields)
        fields.setdefault("memory_node_id", new_node_id())
        fields.setdefault("source", "user_stated")
        fields.setdefault("privacy_level", privacy_level)
        moment = self._now()
        fields.setdefault("created_at", moment)
        fields.setdefault("updated_at", moment)
        try:
            node = self._writer.add_node(checked_node(**fields))
        except Exception as exc:  # 节点非法（缺专属字段 / 值越界）→ 显式失败，不静默
            return ResultEnvelope.validation_failed(f"偏好写入未过节点校验：{exc}")
        return ResultEnvelope.ok(
            {"memory_node_id": node.memory_node_id, "type": node.type},
            as_of=moment,
        )

    def pending_conflicts(self) -> ResultEnvelope:
        """待裁决冲突（`memory_op` 裁决支的入口，交裁决面出卡）。"""
        return self._adjudicator.cards()

    def adjudicate(
        self, card: Any, *, decision: str, reason: str | None = None
    ) -> ResultEnvelope:
        """提交一张裁决卡（accept / reject），落地只经 L2 裁决面（[05 §9]）。"""
        return self._adjudicator.submit(
            card, decision=decision, reason=reason, now=self._now(),  # type: ignore[arg-type]
        )

    # ───────────────────── §2 / §6 / §7 展示面 ─────────────────────

    def context_card(self, *, topic: str = "") -> ResultEnvelope:
        return describe_context_card(build_context_card(self._reader, topic=topic))

    def memory_slice(self, *, topic: str = "", task_type: str = "chat") -> ResultEnvelope:
        return self._reader.query(SliceQuery(task_type=task_type, topic=topic)).envelope

    @staticmethod
    def describe_trace_of(trace: Any) -> ResultEnvelope:
        return describe_trace(trace)

    @staticmethod
    def describe_draft_of(draft: Any) -> ResultEnvelope:
        return describe_draft(draft)

    @staticmethod
    def describe_adjudication_of(card: Any) -> ResultEnvelope:
        return describe_adjudication(card)

    @staticmethod
    def describe_divergence_of(view: Any) -> ResultEnvelope:
        return describe_divergence_map(view)

    @staticmethod
    def lens_trace_of(analysis: Any, trace_id: str) -> ResultEnvelope:
        """追问接口（[06 §5](../../docs/技术架构-v2/06-L4-多视角推理.md)）：按矩阵行的 `trace_id`
        展开该视角的完整推理链（复用 [05 §7](../../docs/技术架构-v2/05-L3-对话主入口.md) 渲染）。

        取数面是 `analyze` 载荷里的 `traces`（鸭子类型）——锚点不存在即 `empty` + 原因，
        **不臆造**一条链（同 [`describe_trace`] 的缺链口径）。
        """
        for trace in tuple(getattr(analysis, "traces", ()) or ()):
            if getattr(getattr(trace, "trace_id", None), "value", None) == trace_id:
                return describe_trace(trace)
        return ResultEnvelope.empty(
            f"本次多视角分析里没有锚点 {trace_id!r} 的推理链（06 §5）"
        )

    def record_feedback(self, target: Any, action: str, **kw: Any) -> ResultEnvelope:
        return self._feedback.record(target, action, **kw)

    # ───────────────────── 回环对话面的翻译（供 `ui` 的 `POST /api/chat` 消费） ─────────────────────

    def turn(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """一次 HTTP 对话请求（[`_turn`] 的产出 + 待回访项，若接有回访面）。

        「下次对话主动提及」（[08 §3] 第 3 步）在**对话面**的落点：装配了训练回访面时，
        每次往返都带上**待回访项**并逐条 `acknowledge`（提及即消费，不重复问）；未接回访面
        时**不加该键**（M1/M2/M3 的返回逐字节不变）。渲染归表现层。
        """
        payload = self._turn(body)
        callbacks = self._collect_callbacks()
        if callbacks is not None:
            payload["callbacks"] = callbacks
        return payload

    def _collect_callbacks(self) -> list[dict[str, Any]] | None:
        """取**待回访项**并逐条置「已提及」；未接回访面 ⇒ ``None``（不加键，不假装）。"""
        if self._callbacks is None:
            return None
        out: list[dict[str, Any]] = []
        for note in tuple(self._callbacks.callbacks()):
            out.append({
                "training_id": note.training_id,
                "text": note.text,
                "correction": note.correction,
                "restatement": note.restatement,
            })
            self._callbacks.acknowledge(note.training_id)
        return out

    def _turn(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """把一次 HTTP 对话请求翻译成反向流的一段，回**契约对象字典**（无 `ui` 依赖）。

        `ui` 侧只需对返回里的 `ResultEnvelope` 走 `envelope_payload`（附六态渲染
        语义）、对可选的 `UiDescription` 走中性化门——序列化与出站点校验都留在
        `ui`，本门面不 import 表现层（A2）。
        """
        action = str(body.get("action") or "post")
        sid = body.get("session_id")
        if action == "dispatch":
            outcome = self.confirm_and_dispatch(
                str(sid or ""),
                answers=body.get("answers"),
                values=body.get("values"),
                structure=body.get("structure"),
            )
            if isinstance(outcome, ResultEnvelope):  # 无待确认卡等前置失败
                return {"reply": outcome, "session_id": sid, "needs_confirmation": False,
                        "description": None}
            description = None
            if outcome.analysis is not None:  # analyze 去向：分歧图（06 §5）
                view = getattr(outcome.analysis, "view", None)
                described = describe_divergence_map(view, now=self._now())
                description = described.data if described.status == "ok" else None
            elif outcome.run is not None and outcome.trace is not None:
                described = describe_trace(outcome.trace, now=self._now())
                description = described.data if described.status == "ok" else None
            elif outcome.draft is not None:
                described = describe_draft(outcome.draft, now=self._now())
                description = described.data if described.status == "ok" else None
            elif outcome.adjudications:
                described = describe_adjudication(
                    outcome.adjudications[0], now=self._now(),
                )
                description = described.data if described.status == "ok" else None
            return {"reply": outcome.envelope, "session_id": sid,
                    "needs_confirmation": False, "description": description}
        # 默认：输入 → 理解 → 澄清 → 确认卡
        result = self.post(str(body.get("text") or ""), session_id=sid or None)
        if isinstance(result, ResultEnvelope):  # 理解失败 / 未落会话
            return {"reply": result, "session_id": sid, "needs_confirmation": False,
                    "description": None}
        if result.confirmation is None:  # 未收敛——回方向候选（clarification 的 empty 信封）
            return {"reply": result.clarification.envelope, "session_id": result.session_id,
                    "needs_confirmation": False, "description": None}
        return {"reply": result.confirmation.envelope, "session_id": result.session_id,
                "needs_confirmation": True, "description": None}


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
class M2Runtime:
    """一次 M2 装配的全部句柄（M1 全套 + L4 面；由 :func:`build_m2_runtime` 构造）。

    L4 面之所以**并列**在 :attr:`m1` 之外而非塞进 `M1Runtime`：`M1Runtime` 是 M1
    关卡的冻结交付物，改造它会让 M1 的用例与本对象互相牵连；M2 = M1 + L4 这层
    组合关系本身就是本关卡要证明的装配事实（[`T-INT-003`]）。
    """

    m1: M1Runtime
    roster: LensRoster
    deliberation: Deliberation
    examiner: CrossExaminer
    viewer: DivergenceViewer
    analyze: AnalyzeService

    @property
    def store(self):
        return self.m1.store

    @property
    def chat(self) -> DialogFacade:
        """对话门面（L3 侧；已含 `analyze` 去向的分歧图渲染支）。"""
        return self.m1.chat

    @property
    def reader(self) -> MemoryReader:
        return self.m1.reader

    @property
    def writer(self) -> MemoryWriter:
        return self.m1.writer

    @property
    def bus(self) -> DispatchBus:
        return self.m1.bus


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


@dataclass(frozen=True)
class _L4Stack:
    """L4 面（[`T-INT-003`]）：阵容 + 编排 + 对照 + 视图 + `analyze` 去向的装配件。"""

    roster: LensRoster
    deliberation: Deliberation
    examiner: CrossExaminer
    viewer: DivergenceViewer
    analyze: AnalyzeService


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


def _build_l4(
    runtime: L1Runtime,
    l2: _L2Stack,
    *,
    now: Any,
    market_query: Any,
    synthesizer: Any,
    reviewer: Any,
    catalog: Any,
    executor: Any = None,
) -> _L4Stack:
    """叠 L4（[06](../../docs/技术架构-v2/06-L4-多视角推理.md) 视角 → 编排 → 对照 → 视图）。

    三个鸭子端口**默认装真实现**（[`l4/ports`](../src/st_agent/l4/ports.py)，[`T-INT-003`] A3）：
    方向研判与证据重研判经 `runtime.llm` 出网、盲点目录读本地市场库。**离线关卡**经
    关键字参数覆写为确定性件（同 M1 关卡注入确定性理解器的口径）。
    """
    store = runtime.store
    # 视角阵容：7 内置幂等播种 + 用户可增删；`skill_bundle` 的存在性校验经 L1 注册表
    # （向下依赖合法，铁律 7）。
    roster = LensRoster(store, skills=runtime.skills)
    roster.seed_builtin()

    if synthesizer is None:
        synthesizer = LlmOpinionSynthesizer(runtime.llm, _LLM_ENDPOINT_ID)
    if reviewer is None:
        reviewer = LlmEvidenceReviewer(runtime.llm, _LLM_ENDPOINT_ID)
    if catalog is None:
        catalog = MarketDimensionCatalog(market_query, skills=runtime.skills)

    deliberation = Deliberation(
        roster=roster, runner=runtime.runner, reader=l2.reader, writer=l2.writer,
        synthesizer=synthesizer, executor=executor, now=now,
    )
    examiner = CrossExaminer(roster=roster, catalog=catalog, reviewer=reviewer, now=now)
    viewer = DivergenceViewer(roster=roster)
    analyze = AnalyzeService(
        deliberation=deliberation, examiner=examiner, viewer=viewer,
    )
    return _L4Stack(
        roster=roster, deliberation=deliberation, examiner=examiner,
        viewer=viewer, analyze=analyze,
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


def _build_m2_pieces(
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
    now: Any = _now,
    events: Any = None,
    feedback_sink: Any = None,
    deliberations_of: Any = None,
    **l1_kwargs: Any,
) -> tuple[L1Runtime, _L2Stack, _L3Stack, _L4Stack]:
    """M2 / M3 共用的装配段：打开运行时 → L2 → L4 → L3（[`T-INT-004`] 抽自 `build_m2_runtime`）。

    抽出它只为让 **M3 组合根复用同一段**（[`T-INT-004`] A1）：M2 侧传的实参逐条不变，
    故 [`build_m2_runtime`] 的行为与抽出前一致。

    :param events / feedback_sink: 事件面（M3 传真总线；M2 缺省 ``None`` ＝逐字节不变）。
    :param deliberations_of: 把 L4 的 `analyze` 端口**包一层**再注入总线的钩子
        （M3 用它接「Deliberation ⇒ 信号」的产生点）；缺省直接注入原件。
    """
    runtime = open_runtime(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env,
        dotenv_path=dotenv_path, **l1_kwargs,
    )
    _bind_market(market_query, runtime.store)
    l2 = _build_l2(runtime, now=now, events=events)
    l4 = _build_l4(
        runtime, l2, now=now, market_query=market_query,
        synthesizer=synthesizer, reviewer=reviewer, catalog=catalog, executor=executor,
    )
    deliberations = l4.analyze if deliberations_of is None else deliberations_of(l4.analyze)
    l3 = _build_l3(
        runtime, l2, now=now, understander=understander, deliberations=deliberations,
        events=events, feedback_sink=feedback_sink,
    )
    return runtime, l2, l3, l4


def build_m2_runtime(
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
    now: Any = _now,
    **l1_kwargs: Any,
) -> M2Runtime:
    """装配 M2 全栈（＝ M1 的 L0–L3 + L4；[`T-INT-003`] 的**生产组合根**）。

    与 :func:`build_m1_runtime` 走**同一段** L2/L3 装配（本模块的私有栈构造器），
    差别只有两处：① 在 L2 之上叠 L4 面；② 把 L4 的 `AnalyzeService` 作为
    `analyze` 去向端口注入总线——[铁律 7](../../项目管理/工程宪法.md) 禁 L3 import L4，故
    该端口以**鸭子面**过界（总线只认其中的 `ResultEnvelope`）。

    :param synthesizer / reviewer / catalog: 三个 L4 鸭子端口的覆写口；缺省由
        :func:`_build_l4` 装真实现（`runtime.llm` 出网 / 读本地市场库）。
        **离线关卡**传确定性件，使 CI 不随本机 `.env` 有无而变（同 M1 A4 口径）。
    :param executor: 编排的并行执行器（鸭子类型 `map(fn, seq)`）——**测试可传同线程件
        保确定性**；缺省 `ThreadPoolExecutor`。
    """
    runtime, l2, l3, l4 = _build_m2_pieces(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env, dotenv_path=dotenv_path,
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        catalog=catalog, executor=executor, now=now, **l1_kwargs,
    )
    return M2Runtime(
        m1=M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3)),
        roster=l4.roster, deliberation=l4.deliberation, examiner=l4.examiner,
        viewer=l4.viewer, analyze=l4.analyze,
    )


# ───────────────────── M3：主动触达面的装配与常驻职责 ─────────────────────


@dataclass(frozen=True)
class TickResult:
    """一次 ``tick`` 的产出（[00 §5](../../docs/技术架构-v2/00-架构总览.md) 步 4–8 的当轮切片）。"""

    now: datetime
    runs: tuple[Any, ...] = ()
    """本轮调度触发的执行结果（``ScheduledRun``）。"""
    signals: tuple[PlatformEvent, ...] = ()
    """本轮**发布出去**的 ``SignalEmitted``（逐条目一条，按发布顺序）。"""
    escalations: tuple[Any, ...] = ()
    """本轮升级链新产生的留痕（新一级投递 / 待汇总项）。"""
    report: Any = None
    """本轮的日报（未到点 ⇒ ``None``；当天已投 ⇒ 已投的那份）。"""
    inquiries: tuple[Any, ...] = ()
    """本轮疲劳面新产生的询问（达阈值且未在等答复者）。"""
    weekly_report: Any = None
    """本轮的**每周反思报告**（M4 面；未到点 / 本周已投 ⇒ ``None``）——只增字段，
    M1/M2/M3 的 ``tick`` 恒不带它。"""


class _SignalEmittingAnalyze:
    """把 L4 的 ``AnalyzeService`` 包一层：分析照常返回，随后按规则发布一条信号。

    总线只按**鸭子面**消费该端口（``analyze(...)`` → 含 ``envelope: ResultEnvelope`` 的
    载荷），故包装件对总线与渲染面**完全透明**（返回的是原件返回的同一个对象）。

    **信号生成失败不损坏分析结果**——分析是用户显式发起的动作，不该被一次文案校验或
    无人受理拖垮；失败原因记进 ``failures`` 并在 :class:`M3Runtime` 上**可查**
    （[00 §6](../../docs/技术架构-v2/00-架构总览.md) 失败显式化：不吞、不静默降级）。
    """

    def __init__(self, *, analyze: Any, publish: Any, failures: list) -> None:
        self._analyze = analyze
        self._publish = publish
        self._failures = failures

    def analyze(self, confirmation: Any, *, values: Any = None, now: Any = None) -> Any:
        outcome = self._analyze.analyze(confirmation, values=values, now=now)
        try:
            event = signal_event_for_analysis(outcome)
        except SignalEmissionError as exc:
            self._failures.append(f"多视角分析未产生信号：{exc}")
            return outcome
        if event is not None:
            self._publish(event)
        return outcome


def _publish_signal(events: Any, event: PlatformEvent, failures: list) -> None:
    """发布一条信号到 [01 §11](../../docs/技术架构-v2/01-平台共享契约.md) 的投递面；**未被受理即显式记因**。

    「发布出去了」与「有人受理了」是两回事（[01 §11] 的失败显式化口径）：没有订阅者、
    或订阅者拒收（如负载不合契约），都记进 ``failures`` —— 不假装送达、也不静默丢弃。
    """
    result = events.publish(event)
    if result.delivered:
        return
    detail = " / ".join(
        f"{outcome.subscriber_id}:{outcome.error or '未受理'}" for outcome in result.outcomes
    )
    failures.append(
        f"信号 {event.payload.get('dedup_key', '')!r} 发布后无人受理"
        f"（{detail or '无订阅者'}）"
    )


@dataclass(frozen=True)
class M3Runtime:
    """一次 M3 装配的全部句柄（M2 全套 + L5 面 + 事件总线；由 :func:`build_m3_runtime` 构造）。

    L5 面与总线**并列**在 :attr:`m2` 之外（同 M2 对 M1 的做法）：M2 是 M2 关卡的冻结交付物，
    改造它会让 M2 的用例与本对象互相牵连；而「M2 + L5 经一条总线接起来」正是本关卡要
    证明的装配事实（[`T-INT-004`]）。

    :meth:`tick` 是本层唯一的**职责循环**（[00 §5] 步 4–7 的当轮切片）：判定全在里面、
    时刻按次传入，故可离线复算；「多久 tick 一次」不在这里（住生产入口的常驻循环）。
    """

    m2: M2Runtime
    l5: L5Stack
    events: EventBus
    monitor_rules: tuple = DEFAULT_MONITOR_RULES
    """上游产生规则表（[`l5.sources`](../l5/sources.py)）——**离线关卡**可注入替代表。"""
    emission_failures: list = field(default_factory=list)
    """信号产生 / 发布的显式失败记录（**可查**，不吞）。"""
    now: Any = _now

    # ── 与 M1/M2 同形的便捷取用（入口与用例按同一面取句柄） ──────────────────

    @property
    def m1(self) -> M1Runtime:
        return self.m2.m1

    @property
    def store(self):
        return self.m2.store

    @property
    def chat(self) -> DialogFacade:
        return self.m2.chat

    @property
    def reader(self) -> MemoryReader:
        return self.m2.reader

    @property
    def scheduler(self) -> Any:
        """L1 调度面（定时监控的到期判定与执行，[03 §6](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。"""
        return self.m1.runtime.scheduler

    # ── 职责循环（[00 §5] 步 4–7） ────────────────────────────────────────

    def tick(self, now: datetime | None = None) -> TickResult:
        """推进一轮主动服务：**调度 → 信号 → 升级链 → 日报 → 疲劳巡查**。

        每一步都不做「兜底式」的猜测：未命中规则不产信号、未到点不生成日报、
        当天已投过不重复投、未达阈值不发问；各类失败一律留痕（``emission_failures`` /
        各面自己的可读面），**不静默略过**。
        """
        moment = self.now() if now is None else now
        runs = tuple(self.scheduler.tick(moment))
        signals: list[PlatformEvent] = []
        for run in runs:
            try:
                produced = signal_events_for_run(run, rules=self.monitor_rules)
            except SignalEmissionError as exc:
                self.emission_failures.append(
                    f"调度运行 {getattr(run, 'target_id', '')!r} 未产生信号：{exc}"
                )
                continue
            for event in produced:
                _publish_signal(self.events, event, self.emission_failures)
                signals.append(event)
        escalations = tuple(self.l5.delivery.advance(now=moment))
        report = self._daily_report(moment)
        inquiries = (
            tuple(self.l5.fatigue.sweep(moment)) if self.l5.fatigue is not None else ()
        )
        return TickResult(
            now=moment, runs=runs, signals=tuple(signals), escalations=escalations,
            report=report, inquiries=inquiries,
        )

    def _daily_report(self, moment: datetime) -> Any:
        """到点则生成并投出当日报告；**「一天一次」的去重从盘上读**（重启安全）。

        ``DailyReportBuilder.due`` 的判据只是「此刻是否已过配置时刻」（[07 §5]
        的纯函数口径），故调用方须自己接住「今天已经发过」——本面把它接在**留痕**上：
        该日报告已存在且 ``delivered_channel`` 非空即不再投（未投出则照常重试，不丢）。
        """
        reports = self.l5.reports
        if reports is None or not reports.due(moment):
            return None
        day = moment.date()
        already = reports.stored(day)
        if already is not None and already.delivered_channel:
            return already
        return reports.deliver(reports.build(day, now=moment), now=moment)


def _build_l5(
    runtime: L1Runtime,
    l2: _L2Stack,
    *,
    now: Any,
    events: Any,
    channels: Mapping[str, Any] | None = None,
    notify: Any = None,
    synthesize: Any = None,
    cloud_transport: Any = None,
    email_host: str = "",
    webhook_host: str = "",
    credentials: Mapping[str, str] | None = None,
) -> L5Stack:
    """叠 L5 面（[07](../../docs/技术架构-v2/07-L5-主动触达.md)）：四类渠道接线 + 薄装配缝。

    渠道接线口径（[D-082](../../项目管理/决策日志.md) ③ / [07 §3](../../docs/技术架构-v2/07-L5-主动触达.md)）：

    - **本地渠道收注入的原生端口**——``notify`` / ``synthesize`` 由**生产入口**从表现层取来
      （``app`` 不 import ``ui``，层序表里也没有 ``ui``）。端口缺省 ``None`` ⇒ 该渠道
      ``health().available is False`` 并点名缺口，**不假装能送**。
    - **云端渠道收传输端口 + 主机**——两者都给齐才接线；缺任一个即该渠道走**显式不可用**
      （空主机不臆造端点，[`T-INT-004`] A5）。凭据经 ``credentials`` 按渠道给
      ``credential_id``，明文**只经 ``CredentialVault.use()``** 一跳动用（不进网关、不进审计）。
    """
    resolved: dict[str, Any] = dict(channels or {})
    gateway = runtime.gateway
    credential_ids = dict(credentials or {})

    resolved.setdefault("desktop", DesktopChannel(notify))
    resolved.setdefault("tts", TtsChannel(synthesize))
    if "email" not in resolved:
        resolved["email"] = EmailChannel(
            gateway, host=email_host,
            transport=cloud_transport if (cloud_transport is not None and email_host) else None,
            vault=runtime.vault, credential_id=credential_ids.get("email"),
        )
    if "im_webhook" not in resolved:
        resolved["im_webhook"] = ImWebhookChannel(
            gateway, host=webhook_host,
            transport=cloud_transport if (cloud_transport is not None and webhook_host) else None,
            vault=runtime.vault, credential_id=credential_ids.get("im_webhook"),
        )
    return build_l5(
        runtime.store, registry=runtime.config_registry, events=events,
        channels=resolved, reader=l2.reader, now=now,
    )


def build_m3_runtime(
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
    now: Any = _now,
    **l1_kwargs: Any,
) -> M3Runtime:
    """装配 M3 全栈（＝ M2 的 L0–L4 + L5；[`T-INT-004`] 的**生产组合根**）。

    与 :func:`build_m2_runtime` 走**同一段** L2/L3/L4 装配（:func:`_build_m2_pieces`），
    差别是三处接线（都在组合根、不改任何层）：

    1. **装一条进程内事件总线**，把四端接上——L2 冲突队列的 ``events`` 端口（新提案自动
       上报）、L3 裁决承接面（``attach``）、L3 反馈采集面（``sink=总线``）、L5 的采纳面与
       疲劳答复面（``build_l5`` 自订阅）；
    2. **把 L4 的 ``analyze`` 端口包一层**，令一次多视角分析按 [06 §6](../../docs/技术架构-v2/06-L4-多视角推理.md)
       产出「多视角摘要」形态的信号（:class:`_SignalEmittingAnalyze`）；
    3. **叠 L5 面**（:func:`_build_l5`）——四类渠道、个性化、投递编排、日报、频控、疲劳。

    信号产生点、到点触发与常驻循环的分工见 [`T-INT-004`]：本函数只**装配**，
    「何时推进一轮」由生产入口按 :meth:`M3Runtime.tick` 的节奏决定。

    :param notify / synthesize: 原生能力端口（桌面通知 / TTS），由入口从表现层注入。
    :param cloud_transport / email_host / webhook_host / credentials: 云端渠道的接线与凭据
        （缺任一项即该渠道显式不可用，见 :func:`_build_l5`）。
    :param channels: 完整的渠道映射覆写口（**离线关卡**用它注入记录型替身，同 M2 的
        确定性注入口径）；给了即不与上面几项合并——替身说了算。
    """
    bus = EventBus()
    failures: list[str] = []
    runtime, l2, l3, l4 = _build_m2_pieces(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env, dotenv_path=dotenv_path,
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        catalog=catalog, executor=executor, now=now,
        events=bus, feedback_sink=bus,
        deliberations_of=lambda analyze: _SignalEmittingAnalyze(
            analyze=analyze,
            publish=lambda event: _publish_signal(bus, event, failures),
            failures=failures,
        ),
        **l1_kwargs,
    )
    l5 = _build_l5(
        runtime, l2, now=now, events=bus, channels=channels, notify=notify,
        synthesize=synthesize, cloud_transport=cloud_transport,
        email_host=email_host, webhook_host=webhook_host, credentials=credentials,
    )
    return M3Runtime(
        m2=M2Runtime(
            m1=M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3)),
            roster=l4.roster, deliberation=l4.deliberation, examiner=l4.examiner,
            viewer=l4.viewer, analyze=l4.analyze,
        ),
        l5=l5, events=bus, monitor_rules=monitor_rules,
        emission_failures=failures, now=now,
    )


# ────────────── M4：反思演进与生态面的装配（[00 §5] 步 7–8 · [09]） ──────────────


_CHANGE_EVENTS = ("ChangeApplied", "ChangeRolledBack")
"""演进变更的告知事件（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)；发布方＝L6 变更流，
见 [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)）。"""

_INTENT_JSON_HINT = "仅输出一行 JSON，不得输出 JSON 以外的任何字符。"


def _strip_code_fence(raw: str) -> str:
    """剥掉 LLM 回复里常见的 ``` 围栏（模型偶发；不剥会误判为解析失败）。"""
    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return body.strip()


# ── 训练对话「概念性理解」的真实 LLM 适配器（[08 §3]） ──────────────────────────

_TRAINING_JSON_CONTRACT = (
    '{"restatement": "对用户修正逻辑的结构化复述", "pattern": "提炼出的模式陈述", '
    '"suggestion": {"config_id": "点分条目 id", "current": 现值或 null, '
    '"suggested": 建议值, "reason": "中性陈述式理由"} 或 null}'
)


def training_understanding_prompt(correction: str, *, target: str = "") -> str:
    """构造训练对话概念性理解的结构化提示词（实现细节，非契约）。"""
    focus = f"（被修正的对象：{target}）" if target else ""
    return (
        "任务：把用户对系统结论的修正整理为结构化理解，供用户确认后写入记忆。\n"
        f"仅输出一行 JSON，结构为：{_TRAINING_JSON_CONTRACT}\n"
        "规则：restatement 与 pattern 用中性陈述，不得出现人称代词、情感措辞或对话体；"
        "无法给出配置建议时 suggestion 置 null；"
        f"{_INTENT_JSON_HINT}\n"
        f"用户修正{focus}：{correction}"
    )


def _parse_training_draft(raw: str) -> TrainingDraft | None:
    """把端点回的文本解析为 :class:`TrainingDraft`；任何不合形态 ⇒ ``None``（端口明示「理解不出」）。"""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    restatement = str(payload.get("restatement") or "").strip()
    pattern = str(payload.get("pattern") or "").strip()
    if not restatement or not pattern:
        return None
    suggestion: SuggestionDraft | None = None
    raw_suggestion = payload.get("suggestion")
    if isinstance(raw_suggestion, dict) and raw_suggestion.get("config_id"):
        try:
            suggestion = SuggestionDraft(
                config_id=str(raw_suggestion["config_id"]),
                current=raw_suggestion.get("current"),
                suggested=raw_suggestion.get("suggested"),
                reason=str(raw_suggestion.get("reason") or ""),
            )
        except (ValueError, TypeError):
            return None
    try:
        return TrainingDraft(restatement=restatement, pattern=pattern, suggestion=suggestion)
    except (ValueError, TypeError):
        return None


class LlmTrainingUnderstander:
    """训练对话「概念性理解」的真实 LLM 实现（鸭子端口 ``understand``；[08 §3]）。

    住**组合根**而非 L6——L6 层的机器守卫断言本层不 import `l0.llm` / `l0.net`
    （[`T-L6-002.1`] A1 / [08 §7] 红线），故适配器只能由同时可 import `l0.llm` 与 L6
    鸭子面的组合根承载（先例＝ :class:`_SignalEmittingAnalyze` 住 `app.py`）。

    端点不可用 / 回复不合形态 ⇒ 返回 ``None``，由 [`TrainingProtocol`] 走 `unavailable`
    （**不硬猜**）；明文 prompt 不落盘（`LlmClient` 口径）。
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        initiator: str = "l6-training",
        purpose: str = "训练对话概念性理解",
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._initiator = initiator
        self._purpose = purpose

    def understand(self, correction: str, *, target: str = "") -> TrainingDraft | None:
        events = self._llm.invoke(
            self._endpoint_id,
            training_understanding_prompt(correction, target=target),
            initiator=self._initiator, purpose=self._purpose,
        )
        chunks: list[str] = []
        for event in events:
            if event.kind == "chunk":
                chunks.append(event.text)
            elif event.kind == "error":
                return None
        return _parse_training_draft("".join(chunks))


# ── 模式观察面的真实 LLM 适配器（[08 §4]） ─────────────────────────────────────

_PATTERN_JSON_CONTRACT = (
    '[{"key": "同类问题的稳定英文键", "count": 出现次数, "sample": "一条样本原话", '
    '"reason": "中性陈述式理由", "draft": {"name": "Skill 名", "description": "说明", '
    '"nodes": [{"node_id": "n1", "skill_id": "官方 skill id", "params": {}}], '
    '"edges": [], "flow_name": "ascii_name"}}]'
)


def pattern_observation_prompt(samples: tuple[str, ...]) -> str:
    """构造模式观察的结构化提示词（实现细节，非契约）。"""
    joined = "\n".join(f"- {text}" for text in samples)
    return (
        "任务：把下列用户提问归并为若干「同类问题」，只有反复出现的类别才值得建专门 Skill。\n"
        f"仅输出一行 JSON 数组，元素结构为：{_PATTERN_JSON_CONTRACT}\n"
        "规则：key 用不含空格的小写英文；count 为该类在下列提问中的出现次数；"
        "name 与 description 用中性命名，不得出现人称代词、情感措辞或对话体；"
        "draft.nodes 至少一个节点；"
        f"{_INTENT_JSON_HINT}\n"
        f"用户提问：\n{joined}"
    )


def _parse_observations(raw: str, *, guard: NeutralityGuard) -> tuple[PatternObservation, ...]:
    """把端点回的文本解析为观察序列；**文过一次 §6 门**的项才保留（不合形态的项丢弃、不静默放行）。"""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError):
        return ()
    if not isinstance(payload, list):
        return ()
    out: list[PatternObservation] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            draft = _draft_of(item.get("draft"))
        except (ValueError, TypeError):
            continue
        if draft is None:
            continue
        reason = str(item.get("reason") or "")
        if not guard.check_name(draft.name, description=draft.description).passed:
            continue
        if reason and not guard.check_output(reason).passed:
            continue
        try:
            observation = PatternObservation(
                key=str(item.get("key") or "").strip(),
                count=int(item.get("count") or 0),
                sample=str(item.get("sample") or ""),
                refs=(),
                draft=draft,
                reason=reason,
            )
        except (ValueError, TypeError):
            continue
        if not observation.key:
            continue
        out.append(observation)
    return tuple(out)


def _draft_of(raw: Any) -> SkillDraft | None:
    """把 LLM 给出的草稿对象转成 :class:`SkillDraft`（无节点 / 无名称 ⇒ ``None``）。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    nodes = raw.get("nodes")
    if not name or not description or not isinstance(nodes, list) or not nodes:
        return None
    return SkillDraft(
        name=name,
        description=description,
        nodes=tuple(dict(node) for node in nodes if isinstance(node, dict)),
        edges=tuple(dict(e) for e in (raw.get("edges") or ()) if isinstance(e, dict)),
        groups=tuple(dict(g) for g in (raw.get("groups") or ()) if isinstance(g, dict)),
        flow_name=str(raw.get("flow_name") or ""),
    )


class LlmPatternObserver:
    """模式观察面的真实 LLM 实现（鸭子端口 ``observations``；[08 §4]）。

    数据源＝L3 的对话史：本面**盘外**自持一个 `SessionStore` 读面（同一 `chat_history`
    分区；[08 §4] 明写「L6 不自行扫描对话记录」⇒ 观察面应把取样与归并放在 L6 之外），
    取最近的用户消息交 LLM 归并同类。L6 只消费其产出，不感知本适配器。

    端点不可用 / 回复不合形态 ⇒ 返回**空序列**（观察面本就没有「有模式」的证据）。
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        sessions: SessionStore,
        limit: int = 40,
        initiator: str = "l6-pattern-observer",
        purpose: str = "对话史模式识别",
        neutrality: NeutralityGuard | None = None,
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._sessions = sessions
        self._limit = limit
        self._initiator = initiator
        self._purpose = purpose
        self._guard = NeutralityGuard() if neutrality is None else neutrality

    def observations(self) -> tuple[PatternObservation, ...]:
        samples = self._samples()
        if not samples:
            return ()
        events = self._llm.invoke(
            self._endpoint_id, pattern_observation_prompt(samples),
            initiator=self._initiator, purpose=self._purpose,
        )
        chunks: list[str] = []
        for event in events:
            if event.kind == "chunk":
                chunks.append(event.text)
            elif event.kind == "error":
                return ()
        return _parse_observations("".join(chunks), guard=self._guard)

    def _samples(self) -> tuple[str, ...]:
        """最近的用户消息（跨会话按落盘顺序；超过上限取最后 `limit` 条）。"""
        texts: list[str] = []
        for session_id in self._sessions.sessions():
            session = self._sessions.load(session_id)
            for message in self._sessions.chain(session):
                if message.role == "user":
                    texts.append(message.text)
        return tuple(texts[-self._limit:])


# ── 演进变更的告知面（[08 §5]：每次变更经 L5 通道显式告知） ──────────────────────


class _ChangeNotifier:
    """订阅 `ChangeApplied` / `ChangeRolledBack`，把一次变更投成一条**中性告知**。

    沿 [`ChannelPolicies`](../l5/channel_policy.py) 的**有序链**投出 `ChannelPayload`
    （同周报 `deliver` 的口径，不另造 L5 面）；文案由本面产出并过 [01 §6] 执行点 2。
    未接线 / 渠道链全不可用 / 文案未过门 ⇒ **如实记因**，不假装告知（[01 §11] 同款口径）。
    """

    def __init__(
        self,
        *,
        dispatcher: Any,
        policies: Any,
        level: str = "important",
        failures: list | None = None,
        neutrality: NeutralityGuard | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._policies = policies
        self._level = level
        self._failures = failures if failures is not None else []
        self._guard = NeutralityGuard() if neutrality is None else neutrality
        self.notices: list[Any] = []
        self.subscription: tuple[Any, ...] = ()

    def attach(self, events: Any, *, subscriber_id: str = "m4:change-notifier") -> tuple[Any, ...]:
        """把本面订阅到总线的两个变更事件上（[01 §11]）。"""
        self.subscription = tuple(
            events.subscribe(name, self._handle, subscriber_id=subscriber_id)
            for name in _CHANGE_EVENTS
        )
        return self.subscription

    def _handle(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        change_id = str(getattr(event, "change_id", None) or payload.get("change_id") or "")
        config_id = str(payload.get("config_id") or "")
        tier = str(payload.get("tier") or "")
        rolled_back = str(getattr(event, "event", "")) == "ChangeRolledBack"
        title = "演进变更已回滚" if rolled_back else "演进建议已生效"
        body = (
            f"配置条目 {config_id} 的取值由 {payload.get('old_value')!r} 改为 "
            f"{payload.get('new_value')!r}（授权档：{tier}）。该改动已记入变更历史，可一键回滚。"
        )
        label = change_id or config_id or "（未知变更）"
        if not self._neutral(title, body):
            self._failures.append(f"变更 {label} 的告知文案未过 01 §6 中性化校验，未投出")
            return
        chain = tuple(self._policies.get(self._level).channels)
        if not chain:
            self._failures.append(
                f"变更 {label} 的告知无可用渠道链（级别 {self._level}），未投出"
            )
            return
        notice = ChannelPayload(
            signal_id=change_id or f"change:{config_id}" or "change",
            level=self._level, title=title, body=body,
            trace_id=str(getattr(event, "trace_id", None) or change_id or f"change:{config_id}"),
        )
        try:
            dispatch = self._dispatcher.deliver(list(chain), notice)
        except Exception as exc:  # noqa: BLE001 —— 投出端缺陷：显式记因，不阻断其余订阅者
            self._failures.append(f"变更 {label} 的告知投出失败：{exc}")
            return
        if getattr(dispatch, "delivered", None) is None:
            self._failures.append(f"变更 {label} 的告知未投出（渠道链全不可用）")
            return
        self.notices.append(notice)

    def _neutral(self, *texts: str) -> bool:
        return all(self._guard.check_output(text).passed for text in texts)


# ── ECO 面的装配（[09] 三类分享动作） ─────────────────────────────────────────


_DECISION_ACTIONS = ("accept", "reject", "defer")
"""提案处置的三动作（[08 §5]：接受 / 否决 / 延后——**排队而非丢弃**）。"""

_STUDIO_ACTIONS = ("accept", "reject")
"""已交 Studio 提案的两动作（[`T-L6-004.2`]）——**微调**走画布编辑面（`/api/studio/edit`）。"""

_CANVAS_EDIT_OPS = ("add_node", "remove_node", "connect", "disconnect", "group", "ungroup")
"""Studio 画布的六个结构编辑操作（[`T-UI-005.1`]；与 `CanvasEditor` 的公开写面一一对应）。

**不含** `params`（节点参数绑定）——那是 Skill 级表单面（story-06 的参数面板），
本批不开放（[`T-UI-005.1` A3](tasks/T-UI-005.1-后端画布读面与编辑转交.md)）。"""

_NO_FACTORY_RESET_REASON = "未接入出厂重置面，无法清空演进状态（装配归组合根）"
"""出厂重置子面缺席时的点名原因（同 [08 §5] 的「未接判据即 fail-closed」口径）。"""

_NOT_FILED_NOTE = (
    "该提案未进入待批准队列（当前档位或风险类不予立案），无可否决 / 延后的项"
    "——「手动」档下提案止步于建议面（08 §5）"
)
"""对未立案提案做否决 / 延后时的如实说明（**不是**失败：确实无事可做）。"""


def _require_week(week: str) -> str:
    """校验周键形态——非法即 ``ValueError``（用户可修正的输入，表现层回 `validation_failed`）。

    复用 L6 的 :func:`week_window` 作判据（**不另写一份正则**：周键口径只有一处）。
    """
    try:
        week_window(week)
    except L6Error as exc:
        raise ValueError(str(exc)) from exc
    return week


def _change_proposal(raw: Mapping[str, Any]) -> ChangeProposal:
    """请求体 → :class:`ChangeProposal`（形态非法 ⇒ pydantic 的 `ValidationError`，属 `ValueError`）。"""
    payload = dict(raw)
    for required in ("config_id", "current", "suggested", "reason"):
        if required not in payload:
            raise ValueError(f"提案缺少 {required!r}（见 08 §5 的提案形态）")
    return ChangeProposal(**payload)


def _grading_rules(raw: Any) -> tuple[EvolutionRiskRule, ...]:
    """请求体 → 风险分级规则表（逐条构造即校验，不吃裸 dict）。"""
    if not isinstance(raw, (list, tuple)):
        raise ValueError("风险分级清单须为条目列表")
    rules: list[EvolutionRiskRule] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("风险分级条目须为对象（prefix / risk_class）")
        rules.append(EvolutionRiskRule(**dict(item)))
    return tuple(rules)


class ReflectionFacade:
    """L6 反思演进栈的**表现层适配面**（组合根所有；`ui` 只经鸭子端口消费，[`T-UI-004.2`]）。

    两件事都在组合根做完，故表现层不必（也不得）import 任何 L6 类型：

    - **取数**——把 L6 读面的 pydantic 形态转成 JSON 就绪的普通结构；
    - **输入类失败归一**——用户可修正的输入（周键形态 / 档位取值 / 待批准项不存在 / 确认次数不足）
      在**调到 L6 之前**就地转 ``ValueError``，表现层据此回 ``validation_failed``（[01 §5]）；
      **状态损坏**这类内部失败**不吞、不上抛为输入错**，原样传出，由表现层回 ``failed`` + `log_ref`
      （[00 §6] 失败显式化）。判据一律复用 L6 自己的常量 / 校验函数，不另写一份。

    **子面未接线**（`authorization` / `change_flow` / `factory_reset` 缺）时返回
    ``{"available": False, "reason": …}``——表现层据此回 ``unavailable`` + 点名，与「面在但无数据」
    分开（[01 §5] 六态不可混用）。
    """

    def __init__(self, *, l6: L6Stack, feedback: Any, skills: Any = None) -> None:
        self._l6 = l6
        self._feedback = feedback
        """L3 的反馈采集面（`FeedbackCollector.record` 的绑定方法）——反馈的**产生方是交互层**，
        本面只转发（[01 §1] / [05 §9]）。"""
        self._skills = skills
        """L1 `SkillRegistry`（鸭子类型 `tool_catalog()`）——Studio 画布页「加节点」的可选 Skill
        清单来源（[`T-UI-005.1`]）；缺省 ``None`` ⇒ 该子面 fail-closed 并点名，**不伪造**清单。"""

    # ── 周报 ────────────────────────────────────────────────────────────────
    def report_weeks(self) -> tuple[str, ...]:
        """已生成过的周键（升序）——表现层列历史与取最近一期。"""
        return tuple(self._l6.reports.stored_weeks())

    def report(self, week: str) -> dict[str, Any] | None:
        """某周的报告；从未生成 ⇒ ``None``（表现层据此给「尚未生成」而非空报告）。"""
        stored = self._l6.reports.stored(_require_week(week))
        return None if stored is None else stored.model_dump(mode="json")

    # ── 反馈采集 ────────────────────────────────────────────────────────────
    def record_feedback(
        self,
        *,
        target: Any,
        action: str,
        reason: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> ResultEnvelope:
        """转交 L3 采集面（载荷不合契约时它自己回 `validation_failed`，本面不预判）。"""
        if not isinstance(action, str) or not action:
            raise ValueError("反馈动作须为非空字符串（08 §1 的五类之一）")
        if target is None:
            raise ValueError("反馈对象（target）必填——无法寻址的反馈一律拒收")
        return self._feedback(target, action, reason=reason, context=context)

    # ── 提案与待批准项 ──────────────────────────────────────────────────────
    def proposals(self) -> list[dict[str, Any]]:
        """已检出的主动提案（含 Skill 草稿）。"""
        return [item.model_dump(mode="json") for item in self._l6.proposals.all()]

    # ── 主动提案落 Studio（[`T-L6-004.2`] / [08 §4]「衔接 story-06」） ──────
    def studio_handoff(self, proposal_id: str) -> dict[str, Any]:
        """把一条主动提案的 Skill 草稿交 **Studio 草稿接收面**（落画布）。

        组合根在此把 L6 的 ``SkillDraft`` 翻译为 L1 的 ``WorkflowDraft`` 并转交
        [`DraftIntake.receive`](../l1/studio/draft.py)——表现层**不 import** 任何层类型。

        子面未接线（`L6Stack.studio is None`）→ ``{"available": False, "reason": …}``
        （与「面在但无提案」分开）；输入类失败（提案不存在 / 草稿形状非法 / 未接接收面）
        **就地转 ``ValueError``**，表现层据此回 `validation_failed`。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        try:
            view = studio.handoff(proposal_id)
        except StudioHandoffError as exc:
            raise ValueError(str(exc)) from exc
        except (DraftShapeError, DraftAcceptError) as exc:
            raise ValueError(str(exc)) from exc
        return {**view.model_dump(mode="json"), "available": True}

    def studio_decide(self, *, proposal_id: str, action: str) -> dict[str, Any]:
        """处置一条**已交 Studio** 的提案：接受（落 v1.0 创建 Skill）/ 否决。

        两个动作都**委托** L1 `DraftIntake`（本面不复制其判定、不另造落盘）。**画布微调**
        是另一条路（[`studio_edit`] 经 `CanvasEditor`），故此处只认这两动作。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if action not in _STUDIO_ACTIONS:
            raise ValueError(
                f"处置动作须为 {'/'.join(_STUDIO_ACTIONS)}（画布微调走 /api/studio/edit，08 §4）"
            )
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        try:
            if action == "accept":
                accepted = studio.accept(proposal_id)
                return {"available": True, "action": action, "applied": True,
                        "flow_id": accepted.flow_id, "change_id": accepted.change_id}
            rejected = studio.reject(proposal_id)
            return {"available": True, "action": action, "rejected": rejected.rejected,
                    "reason": rejected.reason}
        except StudioHandoffError as exc:
            raise ValueError(str(exc)) from exc
        except (DraftSessionError, DraftAcceptError) as exc:
            raise ValueError(str(exc)) from exc

    # ── Studio 画布（[`T-UI-005.1`] / story-06 主画布） ─────────────────────

    def studio_sessions(self) -> dict[str, Any]:
        """仍在编辑中的 Studio 会话一览（画布页列「已交 Studio 未处置」的那些）。"""
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        sessions: list[dict[str, Any]] = []
        for proposal_id in studio.opened():
            session = studio.session(proposal_id)
            if session is None:                     # 理论上不可达；真发生则不臆造一行
                continue
            sessions.append(self._canvas_session(proposal_id, session))
        return {"available": True, "sessions": sessions}

    def studio_canvas(self, proposal_id: str) -> dict[str, Any]:
        """某会话的画布视图（节点 / 连线 / 分组 / 校验违规）——画布页的取数面。

        未交 Studio（无会话）⇒ ``ValueError``（输入类失败，表现层回 `validation_failed`）；
        子面未接线 ⇒ ``{"available": False, …}``（表现层回 `unavailable` + 点名）。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        session = studio.session(proposal_id)
        if session is None:
            raise ValueError(f"该提案尚未交 Studio，无可查看的画布：{proposal_id!r}（08 §4）")
        return {"available": True, "canvas": self._canvas_payload(proposal_id, session)}

    def studio_skills(self) -> dict[str, Any]:
        """画布页「加节点」的可选 Skill 清单（01 §2 描述体的投影——**唯一来源**是注册表）。

        取 `SkillRegistry.tool_catalog()` 的**能力条目**（同 base 折叠、取最高版本），
        只回名称 / 执行目标 / 描述——前端据它填下拉，不自己拼 `skill_id`。
        """
        if self._skills is None:
            return {"available": False, "reason": "未接入 Skill 注册表（装配归组合根）"}
        return {
            "available": True,
            "skills": [
                {"name": entry.name, "skill_id": entry.skill_id,
                 "description": entry.description}
                for entry in self._skills.tool_catalog()
            ],
        }

    def studio_edit(self, *, proposal_id: str, op: str, args: Mapping[str, Any] | None = None
                    ) -> dict[str, Any]:
        """把一次**画布结构编辑**转交该会话的 `CanvasEditor`（同一个句柄，不分裂）。

        `op` ∈ [`_CANVAS_EDIT_OPS`]；`CanvasEditor` 是**全函数**（用户级冲突一律
        `applied=False`，不抛异常），故本面把 `EditResult` 归一成载荷并**附编辑后的画布**——
        表现层一次请求即可重渲染。缺键 / 非法 `op` / 无可编辑会话 ⇒ ``ValueError``。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        if op not in _CANVAS_EDIT_OPS:
            raise ValueError(
                f"画布编辑操作须为 {'/'.join(_CANVAS_EDIT_OPS)}，收到 {op!r}（08 §4）")
        params = args if args is not None else {}
        if not isinstance(params, Mapping):
            raise ValueError("画布编辑参数须为对象（{参数名: 值}）")
        session = studio.session(proposal_id)
        if session is None:
            raise ValueError(f"该提案尚未交 Studio，无可编辑的画布：{proposal_id!r}（08 §4）")
        try:
            editor = studio.tune(proposal_id)       # 同一个 CanvasEditor 句柄
        except (StudioHandoffError, DraftSessionError) as exc:
            raise ValueError(str(exc)) from exc
        result = _apply_canvas_edit(editor, op, params)
        edit = {
            "action": result.action,
            "applied": result.applied,
            "message": result.message,
            "blocked_by": [str(issue) for issue in result.blocked_by],
        }
        return {
            "available": True,
            "canvas": self._canvas_payload(proposal_id, session, edit=edit),
        }

    def _canvas_session(self, proposal_id: str, session: Any) -> dict[str, Any]:
        """会话小结（一览用）——身份与规模，不含节点明细。"""
        dag = session.dag
        return {
            "proposal_id": proposal_id,
            "base": session.base,
            "flow_id": dag.flow_id,
            "node_count": len(dag.nodes),
            "status": session.status,
        }

    def _canvas_payload(self, proposal_id: str, session: Any, *, edit: dict | None = None
                        ) -> dict[str, Any]:
        """画布 JSON 就绪结构（`studio_canvas` 的 `canvas` 槽）。

        **校验结论取当前态**（`editor.validate_current()`）而非落画布时的首轮——编辑后
        违规清单会随之变化，画布页要看到的是**现在**这一版（未编辑时两者逐字相同）。
        """
        dag = session.dag
        validation = session.editor.validate_current()
        catalog = self.studio_skills()
        canvas: dict[str, Any] = {
            "session": self._canvas_session(proposal_id, session),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "skill_id": node.skill_id,
                    "params": {name: binding.model_dump(mode="json")
                               for name, binding in node.params.items()},
                }
                for node in dag.nodes
            ],
            "edges": [
                {"edge_id": edge.edge_id, "from_node": edge.from_node,
                 "to_node": edge.to_node}
                for edge in dag.edges
            ],
            "groups": [
                {"group_id": group.group_id, "name": group.name,
                 "node_ids": list(group.node_ids)}
                for group in dag.groups
            ],
            "schedule": dag.schedule.model_dump(mode="json"),
            "violations": [str(issue) for issue in validation.issues],
            "validation_ok": validation.ok,
            "skill_options": list(catalog.get("skills") or ()) if catalog.get("available") else [],
        }
        if edit is not None:
            canvas["edit"] = edit
        return canvas

    def pending(self) -> list[dict[str, Any]]:
        """待批准 / 已延后的提案（**留痕即事实**，处置过的也在）。"""
        return [item.model_dump(mode="json") for item in self._l6.change_flow.pending()]

    def decide(
        self,
        *,
        action: str,
        proposal: Mapping[str, Any] | None = None,
        pending_id: str = "",
    ) -> dict[str, Any]:
        """逐条处置（接受 / 否决 / 延后）——**唯一通道是变更流**（08 §5 / §7 红线）。

        接受：立案件**经 01 §7 门面落值生效**并产生 `change_id`；否决：留痕而值逐字节不变；
        延后：排队而非丢弃（可再处置）。三者的落点都在 L6，本面只把结论归一成一份结构。
        """
        if action not in _DECISION_ACTIONS:
            raise ValueError(f"处置动作须为 {'/'.join(_DECISION_ACTIONS)}，收到 {action!r}")
        if not pending_id and proposal is None:
            raise ValueError("需给出待批准项 id 或提案内容")
        if pending_id:
            return self._decide_pending(action, pending_id)
        flow = self._l6.change_flow
        candidate = _change_proposal(proposal or {})
        if action == "accept":
            run = flow.submit(candidate)
            if run.pending is not None:
                return self._decide_pending("accept", run.pending.pending_id)
            return _run_payload(run, action)
        # 否决 / 延后只对**已立案**的项有意义：先问审批门，未立案就别去 submit——
        # 自主档下 submit 会直接落值生效，那样「否决」反而改了值（08 §5 的档位语义）。
        if not flow.gate(candidate).filed:
            return _run_payload(None, action, note=_NOT_FILED_NOTE)
        run = flow.submit(candidate)
        if run.pending is None:
            return _run_payload(run, action)
        return self._decide_pending(action, run.pending.pending_id)

    def _decide_pending(self, action: str, pending_id: str) -> dict[str, Any]:
        flow = self._l6.change_flow
        pending = flow.get_pending(pending_id)
        if pending is None:
            raise ValueError(f"待批准项不存在：{pending_id!r}")
        if pending.status in ("accepted", "rejected"):
            # 重复处置是**输入类**失败（用户点重了），不是内部错——就地转 `ValueError`，
            # 与 L6 自己的「显式失败而非吞错」一致（08 §5）。
            raise ValueError(f"该提案已处置过（{pending.status}），不可重复处置")
        if action == "accept":
            return _run_payload(flow.accept(pending_id), action)
        return _run_payload(
            None, action,
            pending=flow.reject(pending_id) if action == "reject" else flow.defer(pending_id),
        )

    # ── A/B 实验 ────────────────────────────────────────────────────────────
    def experiments(self) -> list[dict[str, Any]]:
        """全部实验（五字段 + 状态 + 决策；判定**保守**，不产 p 值）。"""
        return [item.model_dump(mode="json") for item in self._l6.experiments.all()]

    # ── 训练对话 ────────────────────────────────────────────────────────────
    def trainings(self) -> list[dict[str, Any]]:
        """训练会话留痕（含「已确认 / 未确认」）。"""
        return [item.model_dump(mode="json") for item in self._l6.training.all()]

    def callbacks(self) -> list[dict[str, Any]]:
        """待回访项（含已提及的）——「下次对话主动提及」的取材面。"""
        return [
            item.model_dump(mode="json")
            for item in self._l6.training.callbacks(pending_only=False)
        ]

    # ── 变更历史 ────────────────────────────────────────────────────────────
    def changes(self) -> list[dict[str, Any]]:
        """演进变更时间线（含回滚记录与已回滚标注）。"""
        flow = self._l6.change_flow
        if flow is None:
            return []
        return [item.model_dump(mode="json") for item in flow.history()]

    def rollback(self, change_id: str) -> dict[str, Any]:
        """按该变更的**生效前取值**回放（本身也是一次变更，产生新 `change_id`）。"""
        flow = self._l6.change_flow
        if flow is None:
            raise ValueError(NO_REGISTRY_REASON)
        if not change_id:
            raise ValueError("需给出要回滚的 change_id")
        if flow.get(change_id) is None:
            raise ValueError(f"变更不存在：{change_id!r}")
        return _run_payload(flow.rollback(change_id), "rollback")

    # ── 演进授权 ────────────────────────────────────────────────────────────
    def authorization(self) -> dict[str, Any]:
        """档位 + 风险分级清单 + 三档取值（表现层据此渲染设置页）。"""
        auth = self._l6.authorization
        if auth is None:
            return {"available": False, "reason": NO_AUTHORIZATION_REASON}
        return {
            "available": True,
            "tier": auth.tier(),
            "tiers": list(TIERS),
            "grading": [rule.model_dump(mode="json") for rule in auth.grading()],
            "risk_classes": list(RISK_CLASSES),
            "default_tier": DEFAULT_TIER,
            "tier_config_id": TIER_CONFIG_ID,
            "grading_config_id": GRADING_CONFIG_ID,
        }

    def set_authorization(
        self, *, tier: Any = None, grading: Any = None
    ) -> dict[str, Any]:
        """切换档位 / 替换风险分级清单——**经统一标量落值面生效并留痕**（08 §5）。"""
        auth = self._l6.authorization
        if auth is None:
            return {"available": False, "reason": NO_AUTHORIZATION_REASON}
        if tier is not None:
            if tier not in TIERS:
                raise ValueError(f"档位须为 {'/'.join(TIERS)}，收到 {tier!r}")
            record = auth.set_tier(tier)
            return {"available": True, "tier": auth.tier(), "changed": record is not None}
        if grading is not None:
            rules = _grading_rules(grading)
            if not rules:
                raise ValueError("风险分级清单不得为空（08 §5）")
            record = auth.set_grading(rules)
            return {
                "available": True,
                "grading": [rule.model_dump(mode="json") for rule in auth.grading()],
                "changed": record is not None,
            }
        raise ValueError("需给出 tier 或 grading")

    # ── 出厂重置 ────────────────────────────────────────────────────────────
    def factory_reset(self, confirmations: Any) -> dict[str, Any]:
        """「回滚到出厂设置」——**三次确认**是硬门：不足即**就地拒**，一步都不做。"""
        reset = self._l6.factory_reset
        if reset is None:
            return {"available": False, "reason": _NO_FACTORY_RESET_REASON}
        if isinstance(confirmations, bool) or not isinstance(confirmations, int):
            raise ValueError("确认次数须为整数")
        if confirmations != RESET_CONFIRMATIONS:
            raise ValueError(
                f"出厂重置需 {RESET_CONFIRMATIONS} 次确认，收到 {confirmations}"
                "——不足即拒、不留痕、不执行任何动作（08 §6）"
            )
        return reset.request(confirmations).model_dump(mode="json")


def _apply_canvas_edit(editor: Any, op: str, args: Mapping[str, Any]) -> Any:
    """把一次画布编辑转交 `CanvasEditor`（[`T-UI-005.1`]）。

    只做**参数解包与必填校验**——判定件（成环 / 契约 / 标识形态 / 连带删除）一律在
    `CanvasEditor` 里，**不在此复制**（[03 §4] 编辑面落地口径）。缺必填参数 ⇒ ``ValueError``
    （输入类失败，表现层回 `validation_failed`）。
    """
    def need(key: str) -> str:
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"画布编辑 {op} 缺少必填参数 {key!r}")
        return str(value)

    if op == "add_node":
        return editor.add_node(need("node_id"), need("skill_id"))
    if op == "remove_node":
        return editor.remove_node(need("node_id"))
    if op == "connect":
        return editor.connect(need("edge_id"), need("from_node"), need("to_node"))
    if op == "disconnect":
        return editor.disconnect(need("edge_id"))
    if op == "group":
        raw = args.get("node_ids") or ()
        if not isinstance(raw, (list, tuple)):
            raise ValueError("画布编辑 group 的 node_ids 须为数组")
        return editor.group(need("group_id"), need("name"), tuple(str(n) for n in raw))
    return editor.ungroup(need("group_id"))          # op 已在 facade 侧校验为 ungroup


def _run_payload(
    run: ChangeRun | None,
    action: str,
    *,
    pending: PendingChange | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """把变更流的一次动作归一成一份结构（提交 / 接受 / 否决 / 延后 / 回滚共用）。"""
    item = pending if pending is not None else (run.pending if run is not None else None)
    return {
        "action": action,
        "applied": bool(run.applied) if run is not None else False,
        "decision": run.decision.model_dump(mode="json") if run is not None else None,
        "change": run.change.model_dump(mode="json") if run is not None and run.change else None,
        "pending": item.model_dump(mode="json") if item is not None else None,
        "note": note if note is not None else (run.note if run is not None else ""),
    }


class _ViolationRecorder:
    """越界行为（`BehaviorViolation`）的**表现层取数面**：订阅留痕、按需拉取。

    [01 §11] 明写「**跨进程**（常驻后端 → 可分离 UI 客户端）的推送是表现层消费面的事，
    **不在本口径内**」——故本批取**拉取式**（进程内记最近若干条，表现层按需取），
    不引入推式通道（SSE / 长轮询）。跨进程推送仍待。

    未接订阅面时 :attr:`attached` 为假——表现层据此**如实标注未受理 + 点名**（不假装送达）。
    """

    EVENT = "BehaviorViolation"

    def __init__(self, *, limit: int = 50, failures: list | None = None) -> None:
        self._records: deque[dict[str, Any]] = deque(maxlen=limit)
        self._failures = failures if failures is not None else []
        self.subscription: tuple[Any, ...] = ()

    @property
    def attached(self) -> bool:
        return bool(self.subscription)

    def attach(self, events: Any, *, subscriber_id: str = "m4:violation-recorder") -> tuple[Any, ...]:
        """把本面订阅到总线（[01 §11] 的 `BehaviorViolation`）。"""
        self.subscription = (events.subscribe(self.EVENT, self._handle, subscriber_id=subscriber_id),)
        return self.subscription

    def _handle(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        self._records.append({
            "skill_id": str(payload.get("skill_id") or ""),
            "violation": str(payload.get("violation") or ""),
            "trace_id": str(getattr(event, "trace_id", None) or ""),
            "occurred_at": str(getattr(event, "occurred_at", None) or ""),
        })

    def records(self) -> tuple[dict[str, Any], ...]:
        """已记下的越界（新者在前）——**留痕即事实**，不重排、不合并。"""
        return tuple(reversed(self._records))


class EcoFacade:
    """ECO 生态面的**表现层适配面**（组合根所有；`ui` 只经鸭子端口消费，[`T-UI-004.4`]）。

    与 :class:`ReflectionFacade` 同口径：取数转 JSON 就绪结构 · **输入类失败就地转 `ValueError`** ·
    子面未接线回 ``{"available": False, "reason": …}``。

    **文件面**（[09 §6] 的表现层口径）：导出 / 导入**只在两处目录内**读写——导出物落
    `exports_dir`、待导入文件放进 `inbox_dir`。本面**不提供**「按任意路径读 / 写」的原语：
    文件名一律按**单段名**校验，路径分隔符 / 绝对路径 / 上跳一律拒。
    """

    def __init__(
        self,
        *,
        eco: Any,
        approvals: Any,
        violations: Any,
        sandbox: Any,
        exports_dir: Path,
        inbox_dir: Path,
    ) -> None:
        self._eco = eco
        self._approvals = approvals
        self._violations = violations
        self._sandbox = sandbox
        self._exports_dir = Path(exports_dir)
        self._inbox_dir = Path(inbox_dir)

    # ── 导出（[09 §2] 的三步：清单 → 用户确认 → 生成） ────────────────────────
    def export_plan(self) -> dict[str, Any]:
        """「本次导出包含以下公开信息」清单——**直接取 L2 的计划**，本面不自行筛。"""
        plan = self._eco.exporter.plan_memory()
        return {
            "included": list(plan.included_summary),
            "excluded_nodes": int(plan.excluded_nodes),
            "confirmed_by_required": IMPORT_CONFIRMATION,
            "exports_dir": str(self._exports_dir),
        }

    def export(
        self, *, kind: str, ref: str = "", author: str = "", confirmed_by: str = ""
    ) -> dict[str, Any]:
        """按 `kind` 导出到**导出目录**，回路径 + 分享卡片。"""
        if kind not in SHARE_KINDS:
            raise ValueError(f"分享物种类须为 {'/'.join(SHARE_KINDS)}，收到 {kind!r}")
        if not str(author).strip():
            raise ValueError("导出须给出 author（manifest 的作者声明，09 §1）")
        exporter = self._eco.exporter
        if kind == "mem":
            if confirmed_by != IMPORT_CONFIRMATION:
                raise ValueError(
                    "导出记忆片段须由用户确认（三步的第 3 步，09 §2）——未确认即不生成文件"
                )
            container = exporter.export_memory(author=author, confirmed_by=confirmed_by)
        else:
            if not str(ref).strip():
                raise ValueError(f"导出 {kind} 须给出 ref（其 skill_id / flow_id / lens_id）")
            container = {
                "skill": exporter.export_skill,
                "flow": exporter.export_flow,
                "lens": exporter.export_lens,
            }[kind](str(ref), author=author)
        card = exporter.card_for(container)
        target = container.save_to(self._exports_dir / card.file_name)
        return {
            "kind": kind,
            "path": str(target),
            "checksum": card.checksum,
            "card": card.model_dump(mode="json"),
        }

    # ── 导入（[09 §3] 的五段） ────────────────────────────────────────────────
    def inbox(self) -> list[dict[str, Any]]:
        """收件目录里的待导入文件（只列文件名与大小，**不读内容**）。"""
        if not self._inbox_dir.is_dir():
            return []
        return [
            {"file_name": path.name, "size": path.stat().st_size}
            for path in sorted(self._inbox_dir.iterdir())
            if path.is_file()
        ]

    def review(self, *, file_name: str, received_from: str = "") -> dict[str, Any]:
        """格式校验 + 依赖解析 + 权限登记后的审核面（[09 §3] 的第 1–3 段，**尚未安装**）。"""
        plan = self._plan(file_name, received_from=received_from)
        return {
            "file_name": file_name,
            "kind": plan.kind,
            "identity": plan.identity(),
            "wants": list(plan.what_it_wants()),
            "permissions": self._approval_items(plan),
            "missing": [gap.model_dump(mode="json") for gap in plan.missing],
            "provenance": plan.provenance.model_dump(mode="json"),
        }

    def decide_import(self, *, file_name: str, permission: str, decision: str = "approve") -> dict[str, Any]:
        """逐项批准 / 拒绝一条已声明的权限（[01 §10] 的逐项批准，经 L3 审批面落账）。"""
        if decision not in ("approve", "reject"):
            raise ValueError(f"决定须为 approve / reject，收到 {decision!r}")
        plan = self._plan(file_name)
        if not plan.permissions:
            raise ValueError("该导入物未声明任何权限，无可批准项")
        if permission not in plan.permissions:
            raise ValueError(f"该导入物未声明这条权限：{permission!r}")
        request = self._approval_request(plan)
        envelope = (
            self._approvals.approve(request, permission)
            if decision == "approve"
            else self._approvals.reject(request, permission)
        )
        if envelope.status != "ok":
            raise ValueError(envelope.reason or "权限审批面未受理本次决定")
        return {
            "permission": permission,
            "decision": decision,
            "permissions": self._approval_items(plan),
        }

    def install(
        self, *, file_name: str, confirmed_by: str = "", received_from: str = ""
    ) -> dict[str, Any]:
        """用户确认后安装（[09 §3] 第 4–5 段）——`.stskill` 另需权限声明**全部已批准**。"""
        if confirmed_by != IMPORT_CONFIRMATION:
            raise ValueError("安装须由用户确认（09 §3 第 4 段）")
        plan = self._plan(file_name, received_from=received_from)
        try:
            outcome = self._eco.importer.install(plan, confirmed_by=confirmed_by)
        except EcoError as exc:
            raise ValueError(f"安装未通过：{exc}") from exc
        return outcome.model_dump(mode="json")

    def imports_ledger(self) -> dict[str, Any]:
        """来源追溯：全部导入留痕（**空即空态**，用 L1 的中性文本，不硬凑列表）。"""
        records = [item.model_dump(mode="json") for item in self._eco.importer.ledger_records()]
        return {"records": records, "empty_text": "" if records else EMPTY_STATE_TEXT}

    # ── 官方索引（[09 §4]） ───────────────────────────────────────────────────
    def index(self) -> dict[str, Any]:
        """只读浏览；**不可用即如实不可用**（离线 / 未配端点），不留半截数据。"""
        try:
            entries = self._eco.index.browse()
        except Exception as exc:  # noqa: BLE001 —— 未配端点 / 离线：如实记因，不吞成空列表
            return {"available": False, "reason": f"官方 Skill 索引不可用：{exc}"}
        return {
            "available": True,
            "entries": [entry.model_dump(mode="json") for entry in entries],
            "boundary": ECOSYSTEM_BOUNDARY,
        }

    # ── 越界行为警示（[09 §3] 的运行时防护） ──────────────────────────────────
    def violations(self) -> dict[str, Any]:
        if self._violations is None:
            return {
                "available": False,
                "reason": "未接入越界行为的订阅面（装配归组合根）",
            }
        return {
            "available": True,
            "attached": bool(self._violations.attached),
            "records": list(self._violations.records()),
            "disabled": list(self._sandbox.disabled_skills()),
        }

    def disable(self, *, skill_id: str) -> dict[str, Any]:
        """禁用越界能力（[03 §1.5] 的禁用面）——写面在沙箱，本面只转交。"""
        if not str(skill_id).strip():
            raise ValueError("须给出要禁用的 skill_id")
        self._sandbox.disable(str(skill_id))
        return {"skill_id": skill_id, "disabled": True}

    # ── 内部工具 ─────────────────────────────────────────────────────────────
    def _plan(self, file_name: str, *, received_from: str = "") -> Any:
        path = self._inbox_path(file_name)
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"收件目录里读不到该文件：{file_name!r}") from exc
        try:
            return self._eco.importer.inspect(blob, received_from=received_from or None)
        except EcoError as exc:
            # 生态面的自有失败都源于**输入**（文件形态 / 缺来源 / 版本不兼容）⇒ 转成输入类失败
            raise ValueError(f"导入校验未通过：{exc}") from exc

    def _inbox_path(self, file_name: str) -> Path:
        """**只在收件目录内**解析文件名（单段名；分隔符 / 绝对路径 / 上跳一律拒）。"""
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("须给出收件目录里的文件名")
        if Path(file_name).name != file_name:
            raise ValueError(f"只接受收件目录内的文件名（不接受路径）：{file_name!r}")
        return self._inbox_dir / file_name

    def _approval_request(self, plan: Any) -> Any:
        payload = plan.container.payload
        skill_id = str(getattr(payload, "skill_id", "") or "")
        base, _version = parse_skill_id(skill_id)
        return ApprovalRequest(key=base, source="imported", permissions=plan.permissions)

    def _approval_items(self, plan: Any) -> list[dict[str, Any]]:
        """逐条权限的批准态（经 **L3 审批面**取，不自行读账本）。"""
        if not plan.permissions:
            return []
        envelope = self._approvals.open(self._approval_request(plan))
        if envelope.status != "ok":
            raise ValueError(envelope.reason or "权限审批面不可用")
        return [item.model_dump(mode="json") for item in envelope.data.items]


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



EXPORTS_DIR_NAME = "exports"
"""导出目录名（存储根下；表现层文件面的**固定常量**，非 [01 §7] 条目——见 [09 §6]）。"""

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


# ────────────── M5：受控自主运行时（循环装配 + 授权面注入 + 探测接线） ──────────────
# [`T-INT-006`] 的**生产组合根**：把 [`T-AGT-*`] 的七件交付（通道 / 探测 / 目录 / 循环三叶 /
# 闸门 / 意图去向 / 上界配置）接起来，使 `investigate` 意图（[05 §3.1]）在**真运行时**上可被
# 用户触发。循环本体住 L3 自身（[D-090] ②），跨层（L1 执行面 / L6 授权面）一律经**鸭子端口**
# 注入（[铁律 7](../../项目管理/工程宪法.md)）——本段是那些端口在组合根里的**唯一**接线点。

M5_ABSENT_TASK_REASON = (
    "investigate 去向缺少任务描述（05 §3.2：无 target Skill 的意图，其任务经确认卡取值 "
    "`task` 或 `target` 承载）；未运行自主查证循环（不臆造一个任务去跑）"
)
"""`investigate` 去向缺任务文本时的**显式**失败原因（不静默跑一个空任务）。"""


@dataclass(frozen=True)
class InvestigationOutcome:
    """`investigate` 去向的**载荷形态**（总线只认 `.envelope`；[05 §4] 的鸭子面）。

    除信封外另携两件**装配级**产物，供表现层与集成套件按属性取值（总线不看它们）：
    ``report``（[`AgentRunReport`]，含 `agent_run_id` 与留痕记录）与 ``trace``（本次循环
    的完整链，`explain` 去向据此逐步展开）。

    ``report`` / ``trace`` 在**未运行**的终止（缺任务文本）时为 ``None``——那时确实没有
    留痕与链，不拿空壳冒充。
    """

    envelope: ResultEnvelope
    report: AgentRunReport | None = None
    trace: Trace | None = None

    @property
    def agent_run_id(self) -> str:
        """本次循环的留痕标识（未运行时为空串）。"""
        return "" if self.report is None else self.report.agent_run_id


class _AgentInvestigator:
    """`investigate` 去向的**循环端口**（[05 §4] 的鸭子面：``investigate(confirmation, …)``）。

    把一张已确认的意图卡收敛为**一次循环执行**，三件都在既有面上完成、本类**不重造**：

    - **任务**取确认卡的取值（`values["task"]`）或 `target`；两者皆空即 `validation_failed`
      （[05 §3.2] 的「无 target Skill 的意图」——任务由该两处之一承载，不臆造）；
    - **执行** = [`run_agent_loop`]（真 L1 流水线：参数 / 权限 / 沙箱 / 留痕全沿用）；闸门是
      [`ActionGate`]，判据取**注入的** L6 运行期授权面（`:data:`AGENT_DECISION_METHOD`）；双上界
      取 `LoopBounds`（[01 §7] 登记项，[`T-AGT-007`]）；
    - **收口** = [`conclude_agent_run`]（铸 `agent_run_id` → 落留痕 → 装证据包信封）。

    缺省不注入授权面时 [`ActionGate`] 一律 `denied`（fail-closed，[05 §10]）——本类不因此
    替它放行；端点不支持工具调用时循环自己回 `endpoint_unavailable`（[02 §4] 能力诚实性）。
    """

    def __init__(
        self,
        *,
        store: Any,
        runner: Any,
        skills: Any,
        bounds: Any,
        protocol: Any,
        endpoint_id: str,
        authorization: Any = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._skills = skills
        self._bounds = bounds
        self._protocol = protocol
        self._endpoint_id = endpoint_id
        self._authorization = authorization

    def investigate(
        self, confirmation: Any, *, values: Mapping[str, Any] | None = None, now: Any = None
    ) -> InvestigationOutcome:
        """跑一次自主查证循环并把产出装成信封（[05 §10]；载荷见 :class:`InvestigationOutcome`）。"""
        merged = {
            **dict(getattr(confirmation, "values", {}) or {}),
            **dict(values or {}),
        }
        task = str(merged.get("task") or getattr(confirmation, "target", "") or "").strip()
        if not task:
            return InvestigationOutcome(
                envelope=ResultEnvelope.validation_failed(M5_ABSENT_TASK_REASON)
            )
        outcome = run_agent_loop(
            task=task,
            endpoint_id=self._endpoint_id,
            protocol=self._protocol,
            runner=self._runner,
            tools=self._skills.tool_catalog(),
            gate=ActionGate(self._authorization),
            max_steps=self._bounds.steps(),
            max_llm_calls=self._bounds.llm_calls(),
        )
        report = conclude_agent_run(self._store, outcome, now=now)
        return InvestigationOutcome(
            envelope=report.envelope, report=report, trace=outcome.trace
        )


@dataclass(frozen=True)
class M5Runtime:
    """一次 M5 装配的全部句柄（M4 全套 + 循环端口；由 :func:`build_m5_runtime` 构造）。

    与 M4 的关系同 M3→M4 的做法：**裹**住 :class:`M4Runtime` 而非改造它——M4 是 M4 关卡的
    冻结交付物；而「L3 循环经真 L1 执行、经真 L6 授权、由组合根装配并接进 `investigate`
    去向」正是本关卡要证明的装配事实（[`T-INT-006`]）。

    ``investigations`` 是注入总线的循环端口（:class:`_AgentInvestigator`）——**装配痕迹**，
    供集成套件断言「循环端口真的在链上」（GWT-7），而非表现层的取数面。
    """

    m4: M4Runtime
    investigations: Any

    # ── 与 M1–M4 同形的便捷取用 ──────────────────────────────────────────

    @property
    def m1(self) -> M1Runtime:
        return self.m4.m1

    @property
    def m2(self) -> M2Runtime:
        return self.m4.m2

    @property
    def m3(self) -> M3Runtime:
        return self.m4.m3

    @property
    def l6(self) -> L6Stack:
        return self.m4.l6

    @property
    def events(self) -> EventBus:
        return self.m4.events

    @property
    def store(self):
        return self.m4.store

    @property
    def chat(self) -> DialogFacade:
        return self.m4.chat

    @property
    def reflection(self) -> ReflectionFacade:
        return self.m4.reflection

    @property
    def ecosystem(self):
        return self.m4.ecosystem

    def tick(self, now: datetime | None = None) -> TickResult:
        """推进一轮主动服务与每周反思（同 :meth:`M4Runtime.tick`；M5 不新增节奏）。"""
        return self.m4.tick(now)

    def training_callbacks(self) -> tuple[Any, ...]:
        """待回访项读面（同 :meth:`M4Runtime.training_callbacks`）。"""
        return self.m4.training_callbacks()


def build_m5_runtime(
    root: Path | str,
    passphrase: str,
    **kwargs: Any,
) -> M5Runtime:
    """装配 M5 全栈（＝ M4 的 L0–L6 + ECO + **受控自主运行时**；[`T-INT-006`] 的**生产组合根**）。

    与 :func:`build_m4_runtime` 复用**同一段**装配，差别只有一处接线（在组合根、不改任何层）：
    经 `investigations_of` 缝装出循环端口并注入总线——故 `investigate` 去向从「未接入
    （fail-closed + 点名）」变为真链路（[05 §4]）。

    循环端口 = :class:`_AgentInvestigator`，其依赖全部**向下取用**（[铁律 7](../../项目管理/工程宪法.md)）：
    L1 的 `runner` / `skills`（工具目录）/ `endpoints` / `llm`（经 :class:`NativeToolCallProtocol`）、
    L3 的 `bounds`（双上界）、L6 的 `agent_authorization`（授权判据，经**鸭子端口**）。

    其余关键字透传 :func:`build_m4_runtime` 的同名项（渠道 / 原生端口 / 云端参数 / 时钟 /
    `llm_env` / `llm_post` 等）。**「端点能力启动探测」不需本根接线**——它住在 `build_l1_runtime`
    的引导装载分支里，装配 M5 时自然执行（[`T-AGT-002`]）。
    """
    holder: list[_AgentInvestigator] = []          # 装配缝唯一被调一次，取回端口句柄

    def investigations_of(runtime: L1Runtime, l6_stack: L6Stack, bounds: LoopBounds):
        """造循环端口（在此处而非调用方，因为这些句柄在 `open_runtime` 之后才存在）。"""
        port = _AgentInvestigator(
            store=runtime.store,
            runner=runtime.runner,
            skills=runtime.skills,
            bounds=bounds,
            protocol=NativeToolCallProtocol(
                llm=runtime.llm, endpoints=runtime.endpoints,
                endpoint_id=_LLM_ENDPOINT_ID,
            ),
            endpoint_id=_LLM_ENDPOINT_ID,
            authorization=l6_stack.agent_authorization,
        )
        holder.append(port)
        return port

    m4 = build_m4_runtime(
        root, passphrase, investigations_of=investigations_of, **kwargs
    )
    return M5Runtime(m4=m4, investigations=holder[0])
