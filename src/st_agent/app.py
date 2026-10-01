"""M1 集成关卡的**生产组合根**（[00 §5 反向流](../../docs/技术架构-v2/00-架构总览.md)；[T-INT-002]）。

现仓库此前**没有任何生产装配根**——L2/L3 各编排器只在测试 fixture 里拼。本模块把它们
装配到**同一个 `Store`** 上，证明「首次可对话」在装配态成立：

    Store → open_runtime(L0/L1) → MemoryGraph/Writer/Reader/…(L2)
          → SessionStore/IntentProtocol/DispatchBus/…(L3) → 对话门面 chat

**`app` 不是第七层**（同 `ui`，[D-060] ⑤）：它只**向下** import L0–L3 做装配，任何层不得
反向 import 它——方向由 [`tests/test_layering.py`](../../tests/test_layering.py) 的
`test_app_is_root_only` 钉住（不进 `LAYER_ORDER`，避免把「不是层」反向编码，见任务 A2）。

对话门面额外承接 [05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) / [D-062](../../../项目管理/决策日志.md)
显式指派给本关卡的 **`memory_op` 偏好写入支**：`source=user_stated` 直写经 `MemoryWriter`
（[04 §3.2](../../docs/技术架构-v2/04-L2-记忆图谱.md)），**不改** `DispatchBus` / `ConflictAdjudicator`
已交付代码（裁决支路径不变）。
"""

from __future__ import annotations

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
    describe_draft,
    describe_trace,
)

__all__ = ["DialogFacade", "M1Runtime", "build_m1_runtime"]

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
            if outcome.run is not None and outcome.trace is not None:
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
    `DispatchBus` 注满 `runner` / `configs` / `adjudications` 三面。
    """
    runtime = open_runtime(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env,
        dotenv_path=dotenv_path, **l1_kwargs,
    )
    store = runtime.store
    # 官方 Pack 的取数面若采用「延迟绑定」（装配期先存根、open_runtime 返回后绑到
    # 运行时自持的 Store，见 rig.MarketData），由本根在唯一 Store 属主就位后绑上——
    # 与 M0 rig.assemble 同一时序（组合根在此打开运行时，故绑定也归此）。
    bind = getattr(market_query, "bind", None)
    if callable(bind):
        bind(store)

    # ── L2（同一 store；memory 分区） ────────────────────────────────────────
    graph = MemoryGraph(store)
    writer = MemoryWriter(graph)
    confidence = ConfidenceModel(graph, now=now)
    reader = MemoryReader(graph, now=now(), confidence=confidence)
    policy = WritePolicy(store, now=now)
    queue = ConflictQueue(graph, writer, policy, now=now)
    deleter = MemoryDeleter(graph, now=now)
    onboarding = OnboardingProtocol(graph, writer, now=now)

    # ── 01 §7 统一配置注册表：L2 侧 `memory-policy` 族由本根注入 ──────────────
    # L1 不 import L2（铁律 7），故适配器住 L2、在此注册；该族**只读**——取值为
    # 对象 / 列表，不在统一标量落值面内，写面归各 owner 的 `set_*` API（01 §7）。
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

    # ── L3（会话落 chat_history） ────────────────────────────────────────────
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
    adjudicator = ConflictAdjudicator(queue=queue, graph=graph)
    feedback = FeedbackCollector(now=now)
    bus = DispatchBus(runner=runtime.runner, configs=configs, adjudications=adjudicator)

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
        ONBOARDING_KIND: lambda entry: onboarding.set_official_default(entry.payload),
    })

    chat = DialogFacade(
        sessions=sessions, reader=reader, writer=writer, intent=intent,
        bus=bus, adjudicator=adjudicator, feedback=feedback, now=now,
    )

    return M1Runtime(
        runtime=runtime, graph=graph, writer=writer, reader=reader,
        policy=policy, queue=queue, confidence=confidence, deleter=deleter,
        onboarding=onboarding, sessions=sessions, commands=commands,
        intent=intent, configs=configs, handling=handling,
        adjudicator=adjudicator, feedback=feedback, bus=bus, chat=chat,
    )
