"""对话门面 `DialogFacade` 与一轮对话的结果类型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
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
from st_agent.l3.conflict import ConflictAdjudicator
from st_agent.l3.dispatch import DispatchBus, DispatchOutcome
from st_agent.l3.errors import SessionNotFoundError, SessionValidationError
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

from st_agent.composition.common import (
    _now,
)

def _clarification_payload(round_: Any) -> dict[str, Any] | None:
    """澄清轮 → 可序列化字典（[05 §3.2]）；无问项也无方向候选时 ``None``（不摆空壳）。

    问项逐条携 ``name`` / ``prompt`` / ``default`` / ``choices`` / ``skippable``——问句与取值域
    均取自后端（[05 §3.2]：一问一参、取值域取自该 ``ParameterSpec``），表现层只呈现、不杜撰。
    ``directions`` 是收敛失败时的方向候选（§3.2 的「3 个可能的方向」）。
    """
    if round_ is None:
        return None
    questions = [
        {
            "name": question.name,
            "prompt": question.prompt,
            "default": question.default,
            "choices": list(question.choices),
            "skippable": question.skippable,
        }
        for question in tuple(getattr(round_, "questions", ()) or ())
    ]
    directions = list(getattr(round_, "directions", ()) or ())
    if not questions and not directions:
        return None
    return {
        "questions": questions,
        "directions": directions,
        "deferred": list(getattr(round_, "deferred", ()) or ()),
    }


@dataclass(frozen=True)
class TurnResult:
    """一次「输入 → 理解 → 澄清」往返的产出（确认卡**未确认**，等用户动作）。"""

    session_id: str
    draft: IntentDraft | None
    clarification: Any  # ClarificationRound
    confirmation: Any  # IntentConfirmation | None（draft.intent is None 时不产卡）
    envelope: ResultEnvelope | None = None
    """理解阶段失败时的信封（此时 `draft` / `clarification` / `confirmation` 皆空）。

    **会话仍已创建、用户消息已落盘**——故 `session_id` 必须回给调用方，否则这一轮
    对话在下一次请求里无处归属（[T-L3-008] 的会话回填依赖它）。
    """

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
    ) -> TurnResult:
        """落一条 user 消息并收敛意图；未收敛时返回理解信封（`unavailable` / `empty`）。

        理解失败也返回 `TurnResult`（`envelope` 非空、其余为 `None`）——会话已创建、
        用户消息已落盘，标识必须回给调用方（[T-L3-008]）。
        """
        sid = session_id or self._sessions.create(now=self._now()).session_id
        self._sessions.append(sid, text, now=self._now())
        understood = self._intent.understand(text)
        if understood.status != "ok" or not isinstance(understood.data, IntentDraft):
            return TurnResult(sid, None, None, None, envelope=understood)
        draft = understood.data
        round_ = self._intent.clarify(draft)
        if draft.intent is None:  # 未收敛——给方向候选，不产卡（§3.2 不硬猜）
            self._pending.pop(sid, None)
            return TurnResult(sid, draft, round_, None)
        if self._intent.undispatchable(draft):
            # 可派发性守门（§3.2）：目标解析不出即不产确认卡，把失败挡在确认之前
            self._pending.pop(sid, None)
            return TurnResult(sid, draft, self._intent.withheld_round(draft), None)
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
            session_id=session_id,
        )
        if outcome.envelope.status == "ok":
            self._pending.pop(session_id, None)
        return outcome

    # ───────────────────── §1 会话历史（历史读面与助手轮落盘） ─────────────────────

    def record_reply(
        self,
        session_id: Any,
        *,
        text: str,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        """把**助手轮**写入会话（[05 §1](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

        `text` 是中性摘要（搜索 / Markdown 导出用），`payload` 是该轮经输出校验后的最终
        出站形态（历史回放用同一条渲染路径）。会话不存在或标识为空时返回 ``False``——
        不伪造一条落盘（同「失败显式化」取向）。
        """
        sid = str(session_id or "").strip()
        if not sid:
            return False
        try:
            self._sessions.append(
                sid, text, role="agent", now=self._now(), payload=payload,
            )
        except (SessionNotFoundError, SessionValidationError):
            return False
        return True

    def list_sessions(self) -> ResultEnvelope:
        """会话列表（[05 §1](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

        按 `updated_at` 倒序；逐个读盘取标题与末条摘要——**不建派生索引**（避免第二事实源，
        同 `SessionStore` 的取向）。单个损坏文件不拖垮整表：列入 `skipped` 并给原因，
        **不静默丢弃**。
        """
        entries: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for sid in self._sessions.sessions():
            try:
                session = self._sessions.load(sid)
            except (SessionNotFoundError, SessionValidationError) as exc:
                skipped.append({"session_id": sid, "reason": str(exc)})
                continue
            entries.append(self._session_summary(session, self._sessions.chain(session)))
        entries.sort(key=lambda item: item["updated_at"], reverse=True)
        return ResultEnvelope.ok(
            {"sessions": entries, "skipped": skipped}, as_of=self._now(),
        )

    def read_session(self, session_id: str) -> ResultEnvelope:
        """读一个会话的默认链路消息（[05 §1](../../docs/技术架构-v2/05-L3-对话主入口.md)）。"""
        sid = str(session_id or "").strip()
        if not sid:
            return ResultEnvelope.validation_failed(
                "缺少会话标识（需 ?id=<session_id>）",
            )
        try:
            session = self._sessions.load(sid)
        except SessionNotFoundError:
            return ResultEnvelope.validation_failed(f"会话不存在：{sid}")
        except SessionValidationError as exc:
            return ResultEnvelope.failed(f"会话记录损坏，无法解析：{exc}")
        chain = self._sessions.chain(session)
        data = self._session_summary(session, chain)
        data["messages"] = [
            {
                "message_id": message.message_id,
                "role": message.role,
                "text": message.text,
                "created_at": message.created_at.isoformat(),
                "payload": dict(message.payload) if message.payload is not None else None,
            }
            for message in chain
        ]
        return ResultEnvelope.ok(data, as_of=session.updated_at)

    @staticmethod
    def _session_summary(session: Any, chain: tuple[Any, ...]) -> dict[str, Any]:
        """列表 / 单会话共用的摘要字段（标题缺省取首条用户消息）。"""

        def brief(text: str, width: int) -> str:
            flat = " ".join(str(text).split())
            return flat if len(flat) <= width else f"{flat[:width]}…"

        first_user = next((m for m in chain if m.role == "user"), None)
        last = chain[-1] if chain else None
        title = (session.title or "").strip() or (
            brief(first_user.text, 40) if first_user is not None else "空会话"
        )
        return {
            "session_id": session.session_id,
            "title": title,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": len(chain),
            "preview": brief(last.text, 80) if last is not None else "",
        }

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
                        "description": None, "clarification": None, "traces": []}
            description = None
            traces: list[dict[str, Any]] = []
            if outcome.analysis is not None:  # analyze 去向：分歧图（06 §5）
                view = getattr(outcome.analysis, "view", None)
                described = describe_divergence_map(view, now=self._now())
                description = described.data if described.status == "ok" else None
                # 各视角链（[06 §5] 追问接口）：[`T-UI-016.3`] 起随响应**内联**，供结论卡的
                # 「推理链」入口展开（无会话态、按锚点对位，见 `_analysis_traces`）。
                traces = self._analysis_traces(outcome.analysis)
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
                    "needs_confirmation": False, "description": description,
                    "clarification": None, "traces": traces}
        # 默认：输入 → 理解 → 澄清 → 确认卡
        result = self.post(str(body.get("text") or ""), session_id=sid or None)
        if result.envelope is not None:  # 理解失败：会话已建，标识照样回给调用方
            return {"reply": result.envelope, "session_id": result.session_id,
                    "needs_confirmation": False, "description": None,
                    "clarification": None, "traces": []}
        clarification = _clarification_payload(result.clarification)
        if result.confirmation is None:  # 未收敛——回方向候选（clarification 的 empty 信封）
            return {"reply": result.clarification.envelope, "session_id": result.session_id,
                    "needs_confirmation": False, "description": None,
                    "clarification": clarification, "traces": []}
        return {"reply": result.confirmation.envelope, "session_id": result.session_id,
                "needs_confirmation": True, "description": None,
                "clarification": clarification, "traces": []}

    def _analysis_traces(self, analysis: Any) -> list[dict[str, Any]]:
        """各视角推理链 → 描述（[06 §5](../../docs/技术架构-v2/06-L4-多视角推理.md) 追问接口的取值面）。

        按 `LensOpinion.trace_id` 与 `analysis.traces` **对锚点**对位——编排返回的两串本就同序，
        但锚点对位不依赖该巧合（[`T-UI-016.3`] 起随 `/api/chat` 响应内联，供结论卡展开）。
        链取不到描述时 `description` 为 `None`（渲染面据此显式告知，**不伪造**一条链）。
        """
        result = getattr(analysis, "result", None)
        by_trace = {
            getattr(getattr(trace, "trace_id", None), "value", None): trace
            for trace in tuple(getattr(analysis, "traces", ()) or ())
        }
        out: list[dict[str, Any]] = []
        for opinion in tuple(getattr(result, "opinions", ()) or ()):
            trace_id = str(getattr(opinion, "trace_id", "") or "")
            trace = by_trace.get(trace_id)
            if trace is None:
                continue
            described = describe_trace(trace, now=self._now())
            out.append({
                "lens_id": str(getattr(opinion, "lens_id", "") or ""),
                "trace_id": trace_id,
                "description": described.data if described.status == "ok" else None,
            })
        return out
