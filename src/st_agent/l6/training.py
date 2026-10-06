"""L6 训练对话协议（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

用户明确修正（「这条不对，因为…」）或发起「我想训练你」时，本面按 [08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md)
的三步走：

1. **概念性理解**——经**注入的理解端口**产出**结构化复述**（不是简单加权）；
   端口缺省不注入即 **fail-closed**（`unavailable` + 原因），**不硬猜**
   （先例＝[05 §3.2](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的意图理解端口、
   [05 §9](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的方向判定端口，[D-062](../../../项目管理/决策日志.md)）。
2. **确认门**——:meth:`TrainingProtocol.record` **只接受已确认的会话**：未经用户确认一律
   **拒**（:class:`TrainingError`），**不写记忆、不产提案、不留回访**。
3. **落三件**——经注入的记忆写入面**新增**一个 `pattern` 节点（`source: user_stated`，
   **只增不改**：本面**不调** ``edit_node``，[04 §3.2](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 红线）·
   产出一条 **Skill 参数调整的提案候选**（复用 [`.2`](weekly.py) 的
   :class:`ProposalCandidate`，**无 `change_id`、不生效**——生效 / 回滚走
   [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)，[§7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线）·
   落**回访留痕**（``reflection/training/<training_id>.json``，读面供「下次对话主动提及」取材）。

四条口径：

- **复述 / 模式陈述 / 建议理由 / 回访文案是生成性文案**，过
  [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2，命中即抛；**用户原话
  （`correction`）是用户数据**、原样承载、**不过 §6**（[D-053](../../../项目管理/决策日志.md)）——
  故本面的模型把「生成文案」与「用户数据」**分字段**承载，不拼成一串（拼接会让用户自己
  说的「我觉得…」把整条文案判违规，同 [T-L6-001.2](weekly.py) 用 :class:`ReportLine.generated`
  钉住的那条边界）。
- **同一反馈的重复训练 = 同一次会话**——`training_id` 取 ``digest_id("train", feedback_id 或 原话)``，
  落盘键由它确定性派生 ⇒ **幂等覆盖**（先例＝[`.1` 池子](pool.py) 的 `feedback_id` 口径）；
  本层**不新增** [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 的 ID 类。
- **「在对话中提及」不在此处**——本面只交留痕与读面（:meth:`TrainingProtocol.callbacks`）。
  在对话里真正把回访渲染出来是**表现层**的事，跨层接线归里程碑集成关卡
  （[工作流](../../../项目管理/工作流.md)「跨层装配与端到端验证不按此拆分」）。
- **无理解端口即无会话**——缺端口 / 端口给出 `None` / 端口返回非法结构，三者分别以
  `unavailable`（前两者）与 :class:`TrainingError`（后者，**显式失败**）呈现，**都不臆造复述**。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l2.memory.models import checked_node, new_node_id
from st_agent.l6.errors import TrainingError
from st_agent.l6.training_store import TrainingStore
from st_agent.l6.weekly import ProposalCandidate

__all__ = [
    "CALLBACK_NOTICE",
    "MEMORY_ABSENT_NOTE",
    "NO_SUGGESTION_NOTE",
    "TRAINING_CONFIDENCE",
    "TRAINING_PRIVACY",
    "UNDERSTANDER_ABSENT_REASON",
    "CallbackNote",
    "SuggestionDraft",
    "TrainingDraft",
    "TrainingOutcome",
    "TrainingProtocol",
    "TrainingSession",
    "TrainingTurn",
]

UNDERSTANDER_ABSENT_REASON = "未注入理解端口，无法做概念性理解（08 §3）"

MEMORY_ABSENT_NOTE = "未注入记忆写入面，本次修正未写入记忆"
"""未接记忆面时的说明——**显式缺席**，不假装写了（[04 §3.2](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的写入面归 L2）。"""

NO_SUGGESTION_NOTE = "本次修正未给出配置建议"
"""理解端口未给出建议时的说明——**不臆造**一条建议。"""

CALLBACK_NOTICE = "上次修正已确认，待下次对话回访"
"""回访留痕的**生成性**骨架文案（过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）。

用户原话（`correction`）与复述（`restatement`）**分字段**随行，由渲染侧按
[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md) 的 `text_kinds` 各自处置——不在此拼接。
"""

TRAINING_CONFIDENCE = 0.9
"""用户自陈节点的置信度基线（同 [04 §5](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 置信度模型的 `user_stated` 基线）。"""

TRAINING_PRIVACY = "private"
"""训练对话写入的节点隐私级别（个人行为模式，不属对外分享面）。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class SuggestionDraft(BaseModel):
    """理解端口给出的**配置建议**草案（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md) 第 2 步的「对应 Skill 参数调整」）。"""

    model_config = ConfigDict(frozen=True)

    config_id: str
    """建议调整的条目（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的点分形态）。"""
    current: Any = None
    suggested: Any = None
    reason: str
    """中性陈述式理由（**生成性文案**，过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""


class TrainingDraft(BaseModel):
    """一次**概念性理解**的产物（理解端口 → 本面）。

    三步齐备才算成型：**结构化复述**（请用户确认的那份）+ **模式陈述**（写进
    `pattern` 节点的内容）+ **可选的配置建议**（缺席即「本次无建议」，不臆造）。
    """

    model_config = ConfigDict(frozen=True)

    restatement: str
    """结构化复述用户的修正逻辑（**生成性文案**，过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    pattern: str
    """提炼出的模式陈述（写 `pattern` 节点；**生成性文案**，过 §6）。"""
    suggestion: SuggestionDraft | None = None


class TrainingSession(BaseModel):
    """一次训练会话（确认前后同一份；:meth:`TrainingProtocol.confirm` 置 `confirmed`）。"""

    model_config = ConfigDict(frozen=True)

    training_id: str
    correction: str
    """用户给出的修正原话（**用户数据**，原样承载、不过 §6）。"""
    restatement: str
    """概念性理解的复述（**生成性文案**）。"""
    pattern: str
    """拟写入记忆的模式陈述（**生成性文案**）。"""
    feedback_id: str = ""
    """被修正的反馈标识（若该修正由某条反馈触发；空即用户直接发起）。"""
    target_ref: str = ""
    """被修正对象的 [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 锚点（推送 / 结论 / 建议的 ref）。"""
    suggestion: ProposalCandidate | None = None
    """**提案候选**（无 `change_id`、不生效；缺省即本次无建议）。"""
    confirmed: bool = False
    """用户是否已确认这份理解——**未确认不得落账**。"""
    created_at: datetime


class CallbackNote(BaseModel):
    """待回访项（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md) 第 3 步的取材面）。"""

    model_config = ConfigDict(frozen=True)

    training_id: str
    text: str
    """**生成性**骨架文案（过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    correction: str
    """用户原话（**用户数据**，原样承载、不过 §6）。"""
    restatement: str
    """复述（**生成性文案**）。"""
    created_at: datetime
    acknowledged: bool = False
    """是否已在一次对话里被提及过（由调用方经 :meth:`TrainingProtocol.acknowledge` 置位）。"""


class TrainingOutcome(BaseModel):
    """一次**已落账**的训练（三件的实际落地情况——缺席项逐条写明，不静默）。"""

    model_config = ConfigDict(frozen=True)

    session: TrainingSession
    pattern_node_id: str = ""
    """写入的 `pattern` 节点标识（空＋`memory_note` 非空即**未写入**）。"""
    memory_note: str = ""
    suggestion_note: str = ""
    callback: CallbackNote


class TrainingTurn(BaseModel):
    """一次 `train` 去向派发的载荷（**给 L3 总线**的鸭子面；总线只认 ``envelope``）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    envelope: ResultEnvelope
    session: TrainingSession | None = None


class TrainingProtocol:
    """训练对话协议面（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（协议照常，但不落盘）
    :param understander: **理解端口**鸭子面（``understand(correction, *, target="") ->
        TrainingDraft | Mapping | None``，如组合根注入的真实 LLM 实现）；
        缺省 ``None`` ⇒ **fail-closed**（`unavailable` + 点名），**不硬猜**；
        返回 ``None`` 同样走 `unavailable`（端口明确表示「理解不出」）
    :param memory_writer: L2 ``MemoryWriter`` 鸭子面（``add_node(node)``）；
        缺省 ``None`` ⇒ **未写入记忆**（显式说明，不假装写了）。本面**只调** ``add_node``——
        既有节点一字不改（[04 §3.2](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 红线）
    :param pool: [`.1` 反思数据池](pool.py) 鸭子面（`get(feedback_id)`）——
        由 `feedback_id` 触发时取回该条反馈的原话与对象锚点
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        understander: Any = None,
        memory_writer: Any = None,
        pool: Any = None,
        guard: NeutralityGuard | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = TrainingStore(store, now=now) if store is not None else None
        self._understander = understander
        self._writer = memory_writer
        self._pool = pool
        self._guard = guard if guard is not None else NeutralityGuard()
        self._now = _system_now if now is None else now
        self._memory: dict[str, dict[str, Any]] = {}     # 纯内存态（无 Store 时）

    # ───────────────────────── 第一步：概念性理解 ─────────────────────────

    def understand(
        self,
        *,
        correction: str = "",
        feedback_id: str = "",
        target_ref: str = "",
        now: datetime | None = None,
    ) -> ResultEnvelope:
        """做一次概念性理解（**不落账**——落账在 :meth:`record`）。

        :param correction: 用户给出的修正原话；缺省时由 `feedback_id` 经池子回取
        :param feedback_id: 由某条反馈触发的修正（取该条反馈的 `reason` 原话与对象锚点）
        :return: `ok` 时 ``data`` 为**未确认**的 :class:`TrainingSession`；
            理解面缺席 / 端口给不出复述 ⇒ `unavailable` + 原因
        """
        moment = self._now() if now is None else now
        text, ref = self._resolve_input(correction, feedback_id, target_ref)
        if not text.strip():
            raise TrainingError("训练对话须给出修正原话，或以 feedback_id 指一条带原因的反饋")
        if self._understander is None:
            return ResultEnvelope.unavailable(UNDERSTANDER_ABSENT_REASON, last_updated_at=moment)
        draft = _to_draft(self._understander.understand(text, target=ref))
        if draft is None:                     # 端口明确表示「理解不出」——不臆造复述
            return ResultEnvelope.unavailable(
                "理解端口未能给出复述（08 §3）", last_updated_at=moment,
            )
        self._require_neutral(draft.restatement, "训练对话复述")
        self._require_neutral(draft.pattern, "训练对话模式陈述")
        suggestion: ProposalCandidate | None = None
        if draft.suggestion is not None:
            self._require_neutral(draft.suggestion.reason, "训练对话建议理由")
            suggestion = _to_candidate(draft.suggestion)
        session = TrainingSession(
            training_id=digest_id("train", feedback_id or text),
            correction=text, restatement=draft.restatement, pattern=draft.pattern,
            feedback_id=feedback_id, target_ref=ref, suggestion=suggestion,
            created_at=moment,
        )
        return ResultEnvelope.ok(session)

    def confirm(self, session: TrainingSession) -> TrainingSession:
        """用户确认这份理解（**置位**；落账另见 :meth:`record`）。"""
        return session.model_copy(update={"confirmed": True})

    # ───────────────────────── 第二步：确认后落三件 ─────────────────────────

    def record(self, session: TrainingSession, *, now: datetime | None = None) -> TrainingOutcome:
        """确认后落三件：写记忆 `pattern` 节点 · 承载提案候选 · 落回访留痕。

        :raises TrainingError: 会话**未经确认**（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md)
            第 2 步的确认门）——拒且**不写记忆、不产提案、不留回访**
        """
        if not session.confirmed:
            raise TrainingError(
                "训练会话未经用户确认，拒绝落账（08 §3 第 2 步：确认后才更新记忆与生成提案）"
            )
        moment = self._now() if now is None else now
        node_id, memory_note = self._write_pattern(session, moment)
        suggestion_note = "" if session.suggestion is not None else NO_SUGGESTION_NOTE
        callback = CallbackNote(
            training_id=session.training_id, text=CALLBACK_NOTICE,
            correction=session.correction, restatement=session.restatement,
            created_at=moment,
        )
        self._require_neutral(callback.text, "训练对话回访文案")
        outcome = TrainingOutcome(
            session=session, pattern_node_id=node_id, memory_note=memory_note,
            suggestion_note=suggestion_note, callback=callback,
        )
        self._persist(session.training_id, outcome)
        return outcome

    # ───────────────────────── `train` 去向的注入面（L3 鸭子面） ─────────────────────────

    def start(
        self, confirmation: Any, *, values: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> TrainingTurn:
        """承接 [05 §4](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的 `train` 去向。

        只做**第一步**（概念性理解）——用户对复述的确认是**下一轮对话**的事，
        由调用方拿到 ``session`` 后经 :meth:`confirm` + :meth:`record` 落账。
        本层**不 import L3**：只读确认卡上的鸭子字段（先例＝`analyze` 去向）。
        """
        merged = dict(confirmation.values) if values is None else dict(values)
        correction = str(merged.get("correction") or "")
        feedback_id = str(merged.get("feedback_id") or "")
        target_ref = str(getattr(confirmation, "target", "") or "")
        envelope = self.understand(
            correction=correction, feedback_id=feedback_id, target_ref=target_ref, now=now,
        )
        session = envelope.data if envelope.status == "ok" else None
        return TrainingTurn(envelope=envelope, session=session)

    # ───────────────────────── 读面 ─────────────────────────

    def outcome(self, training_id: str) -> TrainingOutcome | None:
        """取一次已落账的训练（从未训练过 → ``None``；落盘损坏 → :class:`TrainingError`）。"""
        raw = self._store.get(training_id) if self._store is not None else self._memory.get(training_id)
        if raw is None:
            return None
        try:
            return TrainingOutcome(**raw)
        except ValidationError as exc:
            raise TrainingError(f"训练会话形态损坏（{training_id}）：{exc}") from exc

    def all(self) -> tuple[TrainingOutcome, ...]:
        """全部已落账的训练（按会话标识升序；无 → 空集，**不报错**）。"""
        raws = self._store.all() if self._store is not None else tuple(self._memory.values())
        out: list[TrainingOutcome] = []
        for raw in raws:
            try:
                out.append(TrainingOutcome(**raw))
            except ValidationError as exc:
                raise TrainingError(f"训练会话形态损坏：{exc}") from exc
        return tuple(sorted(out, key=lambda o: o.session.training_id))

    def callbacks(self, *, pending_only: bool = True) -> tuple[CallbackNote, ...]:
        """待回访项（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md) 第 3 步的取材面）。

        :param pending_only: 只给**尚未被提及**的（缺省）；``False`` 给全部（含已提及的）
        """
        notes = [o.callback for o in self.all()]
        if pending_only:
            notes = [n for n in notes if not n.acknowledged]
        return tuple(sorted(notes, key=lambda n: (n.created_at, n.training_id)))

    def acknowledge(self, training_id: str) -> CallbackNote:
        """把某次训练的回访标记为**已在对话里提及过**（幂等）。

        :raises TrainingError: 该次训练不存在
        """
        outcome = self.outcome(training_id)
        if outcome is None:
            raise TrainingError(f"无此训练会话：{training_id!r}")
        node = outcome.callback
        if node.acknowledged:
            return node
        updated = outcome.model_copy(
            update={"callback": node.model_copy(update={"acknowledged": True})}
        )
        self._persist(training_id, updated)
        return updated.callback

    # ───────────────────────── 内部 ─────────────────────────

    def _resolve_input(
        self, correction: str, feedback_id: str, target_ref: str
    ) -> tuple[str, str]:
        """修正原话与对象锚点——显式给出的优先，缺失时经池子按 `feedback_id` 回取。"""
        text, ref = correction, target_ref
        if feedback_id and (not text.strip() or not ref):
            entry = None if self._pool is None else self._pool.get(feedback_id)
            event = getattr(entry, "event", None)
            if event is not None:
                text = text or str(getattr(event, "reason", "") or "")
                ref = ref or str(getattr(getattr(event, "target", None), "ref", "") or "")
        return text, ref

    def _write_pattern(self, session: TrainingSession, moment: datetime) -> tuple[str, str]:
        """经注入的写入面**新增**一个 `pattern` 节点；未接写入面 → 显式说明。"""
        if self._writer is None:
            return "", MEMORY_ABSENT_NOTE
        try:
            node = checked_node(
                type="pattern", memory_node_id=new_node_id(),
                confidence=TRAINING_CONFIDENCE, source="user_stated",
                privacy_level=TRAINING_PRIVACY, created_at=moment, updated_at=moment,
                pattern=session.pattern,
            )
            return str(self._writer.add_node(node).memory_node_id), ""
        except Exception as exc:              # 含写入面拒收 / 撞 id（只增不改，不重试改写）
            raise TrainingError(f"记忆写入面不可用（{exc}）") from exc

    def _persist(self, training_id: str, outcome: TrainingOutcome) -> None:
        payload = outcome.model_dump(mode="json")
        if self._store is None:
            self._memory[training_id] = payload
            return
        self._store.put(training_id, payload)

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise TrainingError(
                f"{where} 未过 01 §6 中性化校验（{hits}）：文案须中性（铁律 2）"
            )


# ───────────────────────── 端口载荷的形态归一 ─────────────────────────


def _to_draft(raw: Any) -> TrainingDraft | None:
    """端口返回 → :class:`TrainingDraft`（``None`` ＝端口明确表示「理解不出」）。"""
    if raw is None:
        return None
    if isinstance(raw, TrainingDraft):
        return raw
    if not isinstance(raw, Mapping):
        raise TrainingError(
            f"理解端口返回非法结构 {type(raw).__name__}"
            "（须为 TrainingDraft / 映射 / None）"
        )
    payload = dict(raw)
    suggestion = payload.get("suggestion")
    if isinstance(suggestion, Mapping):
        payload["suggestion"] = SuggestionDraft(**dict(suggestion))
    try:
        return TrainingDraft(**payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise TrainingError(f"理解端口返回的结构不合 08 §3 形态：{exc}") from exc


def _to_candidate(suggestion: SuggestionDraft) -> ProposalCandidate:
    """建议草案 → **提案候选**（复用 [`.2`](weekly.py) 的载体；**不产 `change_id`**）。"""
    if "." not in suggestion.config_id:
        raise TrainingError(
            f"建议的 config_id {suggestion.config_id!r} 不合 01 §7 点分形态（<族>.<名>）"
        )
    return ProposalCandidate(
        config_id=suggestion.config_id, current=suggestion.current,
        suggested=suggestion.suggested, reason=suggestion.reason,
    )
