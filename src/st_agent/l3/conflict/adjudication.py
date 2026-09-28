"""L3 · 冲突裁决（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 第一条 + [04 §4](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)）。

L3 **承接** L2 的 ``MemoryConflictDetected`` 事件（[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md)），
在对话中发起裁决，再把用户的裁决结果**交回** L2。三段分工：

1. **承接**——:meth:`ConflictAdjudicator.card_for` / :meth:`~ConflictAdjudicator.from_event`
   把一条待裁决提案（或一条事件）转成一张**裁决卡**：冲突两方内容 + 中性问句 +
   可选方向候选 + ``accept`` / ``reject`` 两个动作。
2. **裁决**——:meth:`ConflictAdjudicator.submit` 经 L2 ``ConflictQueue.resolve`` 落裁决。
3. **呈现**——卡片的**渲染**归 [05 §6](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的
   骨架任务与 [`T-L3-004`](../../../项目管理/tasks/T-L3-004-GenerativeUI推理链可视化.md)，
   本模块只产结构化卡。

**只新增、不改写**（假设 A4）：L3 **不自行改写 L2 图谱、不绕过 ``resolve``、不另存
一份裁决记录**——落盘归 L2 的 ``execution_log`` 分区（[`conflict.py`](../../../l2/memory/conflict.py)）。

**方向判定归 L3**（假设 A2；解 [L2 册 `B1`](../../../项目管理/遗留问题/L2-遗留问题.md)）：
[04 §1](../../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的 ``thesis`` 只有自由文本 ``view``
（无方向字段），「方向相反」在结构上不可判——故判定由**注入的判定端口**承担
（鸭子类型 ``compare(existing, proposed) -> Sequence[str]``，返回方向候选文案）；
**缺省不注入即不判定**——卡上不给方向候选并显式标注
:data:`STANCE_UNDECIDED_LABEL`。L2 节点模型**不增字段**。

**中性视角（铁律 2 / [D-053](../../../项目管理/决策日志.md)）**：冲突**两方内容**
是记忆本体，属**数据展示**、**不过** [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)；
而**本层自有**的生成文案（抬头 / 问句 / 动作标签 / 方向候选）逐条过 §6 执行点 2
``check_output``，**命中即阻断**（:class:`~st_agent.l3.errors.ConflictValidationError`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l2.memory.conflict import MemoryConflictProposal
from st_agent.l2.memory.errors import MemoryNotFoundError, MemoryValidationError
from st_agent.l3.errors import ConflictValidationError

__all__ = [
    "ADJUDICATION_HEADER",
    "ADJUDICATION_QUESTION",
    "ACTION_LABELS",
    "QUEUE_ABSENT_REASON",
    "STANCE_UNDECIDED_LABEL",
    "AdjudicationAction",
    "ConflictAdjudication",
    "ConflictAdjudicator",
    "ConflictSide",
]

QUEUE_ABSENT_REASON = (
    "未注入冲突裁决面（L2 ConflictQueue），无法发起冲突裁决（05 §9）"
)

STANCE_UNDECIDED_LABEL = "方向未判定"
"""方向判定端口未注入时的显式标注（**不静默留空、不拿无关内容充数**）。"""

ADJUDICATION_HEADER = "记忆冲突待裁决"
"""裁决卡抬头（生成文案，过 §6）。"""

ADJUDICATION_QUESTION = (
    "既有记录与新的推断不一致：采用新版本（新增一条并保留演化链），或保持原状？"
)
"""裁决问句（生成文案，过 §6）。§4 原文的示例措辞含第一人称与对话体，按铁律 2 中性改述。"""

ACTION_LABELS: dict[str, str] = {
    "accept": "采用新版本",
    "reject": "保持原状",
}
"""两个动作的展示标签（生成文案，逐条过 §6）。键与 L2 ``resolve`` 的 ``decision`` 同构。"""

Decision = Literal["accept", "reject"]


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class ConflictSide(BaseModel):
    """冲突的**一方**（记忆节点内容）。

    ``fields`` 是节点载荷的**原样**承载——记忆本体属数据展示，
    **不过** [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)（[D-053](../../../项目管理/决策日志.md)）。
    """

    model_config = ConfigDict(frozen=True)

    node_id: str | None = None
    """该方的 ``memory_node_id``；提案方尚无 id（接受时才落新节点），故为 ``None``。"""
    dimension: str
    """节点类型（[04 §1](../../../../docs/技术架构-v2/04-L2-记忆图谱.md) 六类之一）。"""
    fields: dict[str, Any] = Field(default_factory=dict)


class AdjudicationAction(BaseModel):
    """裁决卡上的一个动作（``decision`` + 展示标签）。"""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    label: str
    """生成文案（过 §6）。"""


class ConflictAdjudication(BaseModel):
    """一张裁决卡（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的机器可读形态）。

    动作与 [04 §4](../../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的裁决结果**一一对应**
    （``accept`` → 新版本 + ``evolves_from``；``reject`` → 丢弃并记录）。
    """

    model_config = ConfigDict(frozen=True)

    conflict_id: str
    kind: str
    """冲突类型（[`ConflictKind`](../../../l2/memory/conflict.py)，L2 检测产出）。"""
    dimension: str
    trace_id: str
    """推断依据的推理链锚点（[04 §1](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)：推断类节点必带）。"""
    proposed: ConflictSide
    existing: ConflictSide | None = None
    """既有方；``dimension_requires_consent`` 类冲突无对象时为 ``None``。"""
    header: str = ""
    question: str = ""
    actions: tuple[AdjudicationAction, ...] = ()
    directions: tuple[str, ...] = ()
    """方向候选（注入判定端口产出；空 = 未判定）。"""
    stance_undecided: bool = True
    """方向是否未判定（判定端口未注入或缺既有方时为 ``True``）。"""

    def question_line(self) -> str:
        """问句行（抬头 + 问句；均为生成文案）。"""
        return f"{self.header}：{self.question}"

    def label_for(self, decision: str) -> str | None:
        """取某动作的展示标签（不存在即 ``None``）。"""
        for action in self.actions:
            if action.decision == decision:
                return action.label
        return None


class ConflictAdjudicator:
    """冲突裁决的编排面（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param queue: 注入的 L2 裁决面（鸭子类型 ``get`` / ``pending`` / ``resolve``，
        如 [`ConflictQueue`](../../../l2/memory/conflict.py)）；缺省 ``None`` →
        承接 fail-closed（``unavailable`` + 点名），**不伪造裁决**
    :param graph: 取「既有方」内容的节点面（鸭子类型 ``has_node`` / ``get_node``，
        如 [`MemoryGraph`](../../../l2/memory/graph.py)）；缺省不注入则卡上无既有方
    :param stance_judge: 注入的**方向判定端口**（鸭子类型
        ``compare(existing, proposed) -> Sequence[str]``）；缺省 ``None`` 即
        **不判定**（卡上标注 :data:`STANCE_UNDECIDED_LABEL`），**不硬猜**
    :param guard: 中性规则库守卫（缺省用默认规则库；**无**关闭开关）
    """

    def __init__(
        self,
        *,
        queue: Any = None,
        graph: Any = None,
        stance_judge: Any = None,
        guard: NeutralityGuard | None = None,
    ) -> None:
        self._queue = queue
        self._graph = graph
        self._stance_judge = stance_judge
        self._guard = guard if guard is not None else NeutralityGuard()

    # ───────────────────────── 承接 ─────────────────────────

    def from_event(self, event: PlatformEvent) -> ResultEnvelope:
        """承接一条 ``MemoryConflictDetected`` 事件（[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md)）。

        载荷的 ``conflict_id`` 即寻址键——事件只通知「有冲突待裁决」，
        提案本体由 L2 的裁决面另取（**不在事件里搬运节点内容**）。
        """
        if event.event != "MemoryConflictDetected":
            return ResultEnvelope.validation_failed(
                f"事件 {event.event!r} 不是冲突裁决的承接对象（01 §11）"
            )
        conflict_id = str(event.payload.get("conflict_id") or "").strip()
        if not conflict_id:
            return ResultEnvelope.validation_failed(
                "MemoryConflictDetected 事件未携带 conflict_id，无法寻址提案"
            )
        return self.card_for(conflict_id)

    def card_for(self, conflict_id: str) -> ResultEnvelope:
        """承接一条待裁决提案，产出裁决卡（载荷为 :class:`ConflictAdjudication`）。"""
        if self._queue is None:
            return ResultEnvelope.unavailable(QUEUE_ABSENT_REASON, last_updated_at=_now())
        try:
            proposal = self._queue.get(conflict_id)
        except MemoryNotFoundError as exc:
            return ResultEnvelope.empty(f"查无此冲突提案：{exc}")
        except MemoryValidationError as exc:
            return ResultEnvelope.validation_failed(f"冲突提案记录损坏，无法裁决：{exc}")
        except Exception as exc:  # 注入端口的实现缺陷 → 显式失败，不静默降级
            return ResultEnvelope.dependency_failed(f"冲突裁决面异常：{exc}")
        return ResultEnvelope.ok(self._build(proposal), as_of=_now())

    def cards(self) -> ResultEnvelope:
        """全部**待裁决**提案的裁决卡（按 ``conflict_id`` 升序；空则 ``empty``）。"""
        if self._queue is None:
            return ResultEnvelope.unavailable(QUEUE_ABSENT_REASON, last_updated_at=_now())
        try:
            pending = tuple(self._queue.pending())
        except Exception as exc:
            return ResultEnvelope.dependency_failed(f"冲突裁决面异常：{exc}")
        if not pending:
            return ResultEnvelope.empty("当前没有待裁决的记忆冲突", as_of=_now())
        return ResultEnvelope.ok(
            tuple(self._build(p) for p in pending), as_of=_now()
        )

    # ───────────────────────── 裁决 ─────────────────────────

    def submit(
        self,
        card: ConflictAdjudication,
        *,
        decision: Decision,
        reason: str | None = None,
        confirmed_by: str = "user",
        now: datetime | None = None,
    ) -> ResultEnvelope:
        """把用户对裁决卡的选择交回 L2（[04 §4](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)）。

        **只经 L2 的 ``resolve``**——本层不改写图谱、不另存裁决记录。``confirmed_by``
        恒须为 ``"user"``（04 §3.2 红线）；非 ``pending`` 的提案不得重复裁决，
        一律显式失败而非吞错。
        """
        if self._queue is None:
            return ResultEnvelope.unavailable(QUEUE_ABSENT_REASON, last_updated_at=_now())
        if confirmed_by != "user":
            return ResultEnvelope.validation_failed(
                f"冲突裁决必须经用户确认（04 §3.2 红线），得到 confirmed_by={confirmed_by!r}"
            )
        try:
            resolution = self._queue.resolve(
                card.conflict_id, decision=decision, confirmed_by="user",
                reason=reason, now=now,
            )
        except MemoryValidationError as exc:
            return ResultEnvelope.validation_failed(f"裁决被 L2 拒绝：{exc}")
        except MemoryNotFoundError as exc:
            return ResultEnvelope.empty(f"裁决对象已不存在：{exc}")
        except Exception as exc:
            return ResultEnvelope.dependency_failed(f"冲突裁决面异常：{exc}")
        return ResultEnvelope.ok(resolution, as_of=now if now is not None else _now())

    # ───────────────────────── 内部 ─────────────────────────

    def _build(self, proposal: MemoryConflictProposal) -> ConflictAdjudication:
        """提案 → 裁决卡（生成文案在此逐条过 §6）。"""
        existing = self._existing_side(proposal)
        proposed = ConflictSide(
            node_id=None, dimension=proposal.dimension, fields=dict(proposal.proposed)
        )
        directions, undecided = self._directions(existing, proposed)
        header, question = self._checked_texts(
            (ADJUDICATION_HEADER, ADJUDICATION_QUESTION)
        )
        actions = tuple(
            AdjudicationAction(decision=d, label=self._checked_texts((ACTION_LABELS[d],))[0])
            for d in ("accept", "reject")
        )
        return ConflictAdjudication(
            conflict_id=proposal.conflict_id, kind=proposal.kind,
            dimension=proposal.dimension, trace_id=proposal.trace_id,
            proposed=proposed, existing=existing,
            header=header, question=question, actions=actions,
            directions=directions, stance_undecided=undecided,
        )

    def _existing_side(self, proposal: MemoryConflictProposal) -> ConflictSide | None:
        """既有方（按 ``target_node_id`` 取节点内容；取不到即无既有方，不臆造）。"""
        target = proposal.target_node_id
        if not target or self._graph is None:
            return None
        try:
            if not self._graph.has_node(target):
                return None
            node = self._graph.get_node(target)
        except Exception:
            return None
        return ConflictSide(
            node_id=target, dimension=proposal.dimension,
            fields=node.model_dump(mode="json"),
        )

    def _directions(
        self, existing: ConflictSide | None, proposed: ConflictSide
    ) -> tuple[tuple[str, ...], bool]:
        """方向候选（注入端口产出；未注入或缺既有方即**不判定**）。"""
        if self._stance_judge is None or existing is None:
            return (), True
        try:
            raw = self._stance_judge.compare(existing, proposed)
        except Exception as exc:
            raise ConflictValidationError(f"方向判定端口异常：{exc}") from exc
        candidates = tuple(str(d) for d in (raw or ()))
        if not candidates:
            return (), True
        return self._checked_texts(candidates), False

    def _checked_texts(self, texts: tuple[str, ...]) -> tuple[str, ...]:
        """逐条过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2；命中即阻断。

        红-绿已验：把本方法改为直通时，「含第一人称的生成文案被阻断」用例 FAIL。
        """
        for text in texts:
            verdict = self._guard.check_output(text)
            if not verdict.passed:
                hits = "、".join(f"{f.kind}:{f.matched}" for f in verdict.findings)
                raise ConflictValidationError(
                    f"生成文案未过中性校验（{hits}）：{text!r}",
                    findings=tuple(hits.split("、")),
                )
        return texts
