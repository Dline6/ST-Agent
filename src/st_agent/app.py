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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from st_agent.contracts.neutrality import (
    RULEPACK_KIND,
    default_rulepack,
    set_official_rulepack,
)
from st_agent.contracts.registry_types import SemVer
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.pack import OfficialPack, ResourceEntry, load_official_pack
from st_agent.l1.runtime import L1Runtime, open_runtime
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l2.memory import (
    ONBOARDING_KIND,
    ConfidenceModel,
    ConflictQueue,
    MemoryDeleter,
    MemoryGraph,
    MemoryReader,
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
from st_agent.l3.home import build_context_card
from st_agent.l3.intent import IntentDraft, IntentProtocol, LlmIntentUnderstander
from st_agent.l3.render import (
    describe_adjudication,
    describe_context_card,
    describe_divergence_map,
    describe_draft,
    describe_trace,
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

__all__ = [
    "DialogFacade",
    "M1Runtime",
    "M2Runtime",
    "build_m1_runtime",
    "build_m2_runtime",
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
        now: Any = _now,
    ) -> None:
        self._sessions = sessions
        self._reader = reader
        self._writer = writer
        self._intent = intent
        self._bus = bus
        self._adjudicator = adjudicator
        self._feedback = feedback
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


def _build_l2(runtime: L1Runtime, *, now: Any) -> _L2Stack:
    """叠 L2（`memory` 分区）并把 `memory-policy` 族注入 01 §7 统一配置注册表。

    L1 不 import L2（[铁律 7](../../项目管理/工程宪法.md)），故适配器住 L2、在组合根注册；
    该族**只读**——取值为对象 / 列表，不在统一标量落值面内，写面归各 owner 的 `set_*` API。
    """
    store = runtime.store
    graph = MemoryGraph(store)
    writer = MemoryWriter(graph)
    confidence = ConfidenceModel(graph, now=now)
    reader = MemoryReader(graph, now=now(), confidence=confidence)
    policy = WritePolicy(store, now=now)
    queue = ConflictQueue(graph, writer, policy, now=now)
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
) -> _L3Stack:
    """叠 L3（会话落 `chat_history`）并装载官方资源包（01 §13）。

    :param deliberations: 注入总线的 `analyze` 去向端口（鸭子面，`None` 即该去向
        fail-closed + 点名）；M2 由 :func:`build_m2_runtime` 传 L4 的 `AnalyzeService`，
        本层**不 import L4**（铁律 7）。
    """
    store = runtime.store
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
    feedback = FeedbackCollector(now=now)
    bus = DispatchBus(
        runner=runtime.runner, configs=configs, adjudications=adjudicator,
        deliberations=deliberations,
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
        bus=bus, adjudicator=adjudicator, feedback=feedback, now=now,
    )
    return _L3Stack(
        sessions=sessions, commands=commands, intent=intent, configs=configs,
        handling=handling, adjudicator=adjudicator, feedback=feedback, bus=bus,
        chat=chat,
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
    runtime = open_runtime(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env,
        dotenv_path=dotenv_path, **l1_kwargs,
    )
    _bind_market(market_query, runtime.store)
    l2 = _build_l2(runtime, now=now)
    l4 = _build_l4(
        runtime, l2, now=now, market_query=market_query,
        synthesizer=synthesizer, reviewer=reviewer, catalog=catalog, executor=executor,
    )
    l3 = _build_l3(
        runtime, l2, now=now, understander=understander, deliberations=l4.analyze,
    )
    return M2Runtime(
        m1=M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3)),
        roster=l4.roster, deliberation=l4.deliberation, examiner=l4.examiner,
        viewer=l4.viewer, analyze=l4.analyze,
    )
