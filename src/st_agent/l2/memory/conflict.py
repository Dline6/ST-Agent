"""冲突检测 · 提案队列与裁决落盘（04 §4 后三条）。

§4 的三条规则在此落成一条通道：

1. **冲突检测**——新信息与既有节点冲突 → 产生 ``MemoryConflictDetected`` 事件
   （[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)），由 L3 发起裁决对话
2. **裁决结果**——用户确认变更 → 写入新版本 + ``evolves_from`` 边（保留演化链）；
   确认不变 → 丢弃提案并记录
3. **不存在任何静默覆盖**

**检测是结构化的**（见任务 `A1`）：L2 无 LLM 出口（``LlmClient`` 归 L1），
[§1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的 ``thesis`` 亦只有自由文本
``view``（无方向字段）。故判据取**结构可比对**者：同 ``subject`` 的新 thesis、
同类节点的单值字段取值分歧。**语义方向判定与询问对话归 L3**（§4 原文即
「L3 发起裁决对话」）。

**只有副驾推断面进本门**（见任务 `A6`）：``source=user_stated`` 按 §3.2 直写——
且 [Story 3 验收段](../../../docs/PRD-v2-Agent/story-03-memory-graph.md) 明写用户
自陈 thesis 是「**自动写入**」，再问一遍与产品行为相抵。

**裁决只能新增、不能改写**：接受路径经 :meth:`MemoryWriter.add_node` 落一个**新
节点**（新 ``memory_node_id``）+ ``evolves_from`` 边指向旧节点，旧节点内容一字不改
——与 §3.2 红线同向（见任务 `A3`）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, Field

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l2.memory.errors import (
    MemoryNotFoundError,
    MemoryValidationError,
)
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    EvolutionPoint,
    MemoryEdge,
    MemoryNode,
    checked_edge,
    checked_node,
    new_node_id,
)
from st_agent.l2.memory.write_policy import WritePolicy
from st_agent.l2.memory.writer import MemoryWriter

__all__ = [
    "CONFLICT_PREFIX",
    "ConflictFinding",
    "ConflictKind",
    "ConflictQueue",
    "ConflictResolution",
    "MemoryConflictProposal",
]

CONFLICT_PREFIX = "memory-conflict/"
"""``execution_log`` 分区内冲突提案与裁决记录的目录前缀（见任务 `A4`）。"""

ConflictKind = Literal[
    "dimension_requires_consent",
    "same_subject_thesis",
    "preference_divergence",
]
"""冲突类型：

- ``dimension_requires_consent``——该维度不在自主写入白名单，须先问
- ``same_subject_thesis``——已有一条针对同一 ``subject`` 的 thesis
- ``preference_divergence``——同类节点的单值字段与既有节点取值分歧
"""

_STAMP_KEYS: tuple[str, ...] = ("memory_node_id", "created_at", "updated_at", "revision_history")
"""提案载荷**不带**的字段：ID 与时间戳于裁决接受时现取，修正历史由写入面维护。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _present(value: Any) -> bool:
    """字段是否「有值」（与 §1 专属字段的非空判据同口径）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, dict, set)):
        return len(value) > 0
    return True


def _normalized(value: Any) -> Any:
    """分歧比对的归一口径：序列看**集合**（顺序无关），演变点只看其 ``value``。"""
    if isinstance(value, (tuple, list, set, frozenset)):
        out: set[str] = set()
        for item in value:
            out.add(item.value if isinstance(item, EvolutionPoint) else str(item))
        return frozenset(out)
    return value.strip() if isinstance(value, str) else value


class ConflictFinding(BaseModel):
    """一次结构化检测的结论（不落盘；落盘形态见 :class:`MemoryConflictProposal`）。"""

    model_config = ConfigDict(frozen=True)

    kind: ConflictKind
    target_node_id: str | None = None
    """冲突对象（既有节点）；``dimension_requires_consent`` 无对象时为 ``None``。"""


class MemoryConflictProposal(BaseModel):
    """一条待裁决的冲突提案（落 ``execution_log`` 分区；幂等，同一冲突只一条）。"""

    model_config = ConfigDict(frozen=True)

    conflict_id: str
    """**业务键的确定性摘要**（``cf_<20 位十六进制>``）——同一冲突重复检测得同一 ID，
    故天然幂等；**不新增契约 ID 类**（沿用 `T-SC-002` / D-034 的取向）。"""
    kind: ConflictKind
    dimension: str
    """节点类型（[04 §1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 六类之一）。"""
    target_node_id: str | None = None
    proposed: dict[str, Any] = Field(default_factory=dict)
    """待写节点载荷（``model_dump(mode="json")``，去掉 :data:`_STAMP_KEYS`）。"""
    trace_id: str
    """推断依据的推理链锚点（§1：推断类节点必带）。"""
    created_at: datetime
    status: Literal["pending", "accepted", "rejected"] = "pending"
    resolved_at: datetime | None = None
    reason: str | None = None
    """裁决缘由（可选；面向人的中性措辞）。"""


class ConflictResolution(BaseModel):
    """一次裁决的产出（接受 → 新节点与新边；否决 → 两者皆空）。"""

    model_config = ConfigDict(frozen=True)

    proposal: MemoryConflictProposal
    node: MemoryNode | None = None
    edge: MemoryEdge | None = None


class ConflictQueue:
    """冲突提案队列（04 §4；检测 + 提案 + 裁决，落 ``execution_log`` 分区）。

    :param graph: :class:`MemoryGraph`（读既有节点；``store`` 亦由它取得）
    :param writer: :class:`MemoryWriter`（接受路径**只**经 ``add_node`` / ``add_edge``）
    :param policy: :class:`WritePolicy`（自主写入白名单门）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    :param events: 上行事件的发布端口（鸭子类型 ``publish(event)``，[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md)
        的投递口径）；缺省 ``None`` ⇒ **不发送**（事件仍可由 ``event_for`` 另取，
        调用方自行递送——本参数只补「谁发」这一环，不改既有行为）
    """

    def __init__(
        self,
        graph: MemoryGraph,
        writer: MemoryWriter,
        policy: WritePolicy,
        *,
        now: Callable[[], datetime] | None = None,
        events: Any = None,
    ) -> None:
        self._graph = graph
        self._writer = writer
        self._policy = policy
        self._now = _now if now is None else now
        self._events = events

    # ───────────────────────── 检测（不落盘） ─────────────────────────

    def detect(self, node: MemoryNode) -> ConflictFinding | None:
        """结构化冲突检测：该节点是否与既有节点冲突（不落盘）。

        只检 **副驾推断面**——用户显式自陈按 §3.2 直写（见模块文档）。
        """
        if node.source != "inferred":
            return None
        found = self._divergence(node)
        if found is not None:
            return found
        if self._policy.decide(node) == "propose":
            return ConflictFinding(kind="dimension_requires_consent")
        return None

    # ───────────────────────── 提案（幂等落盘） ─────────────────────────

    def propose(self, node: MemoryNode, *, trace_id: str) -> MemoryConflictProposal | None:
        """一条推断类新信息的**唯一**入口：产出提案，或 ``None`` 表示可直写。

        门 = 冲突检测 **或** 白名单（§4 两条规则并列，任一命中即须询问）。
        """
        finding = self.detect(node)
        if finding is None:
            return None
        proposal = self._build(node, finding, trace_id)
        path = self._path(proposal.conflict_id)
        if path in self._files():
            return self._parse(self._graph.store.get("execution_log", path))  # 幂等：返回既有
        self._write(proposal)
        self._publish(proposal)
        return proposal

    def _publish(self, proposal: MemoryConflictProposal) -> None:
        """把提案的上行通知发出去（01 §11 的投递口径）。

        **注入发布面才发**；未注入时静默不发也**不假装送达**——`event_for` 仍可取事件，
        调用方自行递送（与「幂等重放不发」一致：只在新提案落盘这一跳发一次）。
        """
        if self._events is None:
            return
        self._events.publish(self.event_for(proposal))

    def pending(self) -> tuple[MemoryConflictProposal, ...]:
        """尚未裁决的提案（按 ``conflict_id`` 升序）。"""
        return tuple(p for p in self.all() if p.status == "pending")

    def all(self) -> tuple[MemoryConflictProposal, ...]:
        """全部提案与裁决记录（含已裁决；按 ``conflict_id`` 升序）。"""
        return tuple(self._parse(self._graph.store.get("execution_log", p))
                     for p in self._files())

    # ───────────────────────── 裁决 ─────────────────────────

    def resolve(
        self,
        conflict_id: str,
        *,
        decision: Literal["accept", "reject"],
        confirmed_by: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> ConflictResolution:
        """裁决一条提案（04 §4：接受 → 新版本 + ``evolves_from``；否决 → 丢弃并记录）。

        :param decision: ``accept`` 采纳变更 / ``reject`` 保持原状
        :param confirmed_by: **只接受 ``"user"``** —— 与 ``edit_node`` 同款（§3.2 红线）
        :param reason: 裁决缘由（可选，进记录）
        :param now: 裁决时刻（默认当前本地时刻；传入须带时区）

        接受路径**只新增**：新节点（新 ``memory_node_id``）+ ``evolves_from`` 边
        （新 → 旧）；既有节点内容不变（§4「不存在任何静默覆盖」）。写入次序为
        「先落节点与边、后标记提案」——中断只会留下「节点已写而提案仍 pending」，
        不丢用户的裁决。
        """
        if confirmed_by != "user":
            raise MemoryValidationError(
                f"resolve 的 confirmed_by 只接受 'user'，得到 {confirmed_by!r}——"
                "冲突裁决必须经用户确认（04 §3.2 红线）"
            )
        proposal = self.get(conflict_id)
        if proposal.status != "pending":
            raise MemoryValidationError(
                f"提案 {conflict_id!r} 已裁决（{proposal.status}），不得重复裁决"
            )
        target = proposal.target_node_id
        if target is not None and not self._graph.has_node(target):
            raise MemoryNotFoundError(
                f"冲突对象 {target!r} 已不存在，无法建立演化链——"
                "请先核对图谱状态再裁决（不做「悄悄退化为无演化链」的降级）"
            )

        moment = now if now is not None else self._now()
        node: MemoryNode | None = None
        edge: MemoryEdge | None = None
        if decision == "accept":
            node = self._writer.add_node(checked_node(
                **proposal.proposed,
                memory_node_id=new_node_id(),
                created_at=moment,
                updated_at=moment,
            ))
            if target is not None:
                edge = self._writer.add_edge(checked_edge(
                    edge_type="evolves_from",
                    source_id=node.memory_node_id,
                    target_id=target,
                    created_at=moment,
                ))

        resolved = proposal.model_copy(update={
            "status": "accepted" if decision == "accept" else "rejected",
            "resolved_at": moment,
            "reason": reason,
        })
        self._write(resolved)
        return ConflictResolution(proposal=resolved, node=node, edge=edge)

    def get(self, conflict_id: str) -> MemoryConflictProposal:
        """按 id 取提案（不存在 → ``MemoryNotFoundError``；损坏 → 校验错）。"""
        path = self._path(conflict_id)
        if path not in self._files():
            raise MemoryNotFoundError(f"无冲突提案 {conflict_id!r}")
        return self._parse(self._graph.store.get("execution_log", path))

    def event_for(self, proposal: MemoryConflictProposal) -> PlatformEvent:
        """提案对应的上行通知（01 §11：``MemoryConflictDetected`` 须带 ``trace_id``）。"""
        return PlatformEvent(
            event="MemoryConflictDetected",
            payload={
                "conflict_id": proposal.conflict_id,
                "kind": proposal.kind,
                "dimension": proposal.dimension,
                "target_node_id": proposal.target_node_id,
            },
            trace_id=proposal.trace_id,
            occurred_at=proposal.created_at,
        )

    # ───────────────────────── 内部工具 ─────────────────────────

    def _divergence(self, node: MemoryNode) -> ConflictFinding | None:
        """同类节点的取值分歧（同 subject 的 thesis / 单值字段不一致）。"""
        for other in self._graph.nodes():
            if other.type != node.type:
                continue
            if node.type == "thesis":
                if node.subject and node.subject == other.subject:
                    return ConflictFinding(kind="same_subject_thesis",
                                           target_node_id=other.memory_node_id)
                continue
            if self._fields_diverge(other, node):
                return ConflictFinding(kind="preference_divergence",
                                       target_node_id=other.memory_node_id)
        return None

    @staticmethod
    def _fields_diverge(other: MemoryNode, node: MemoryNode) -> bool:
        """两节点是否有「双方都有值且不相等」的专属字段。"""
        for name in type(node)._own_fields:
            a = getattr(other, name, None)
            b = getattr(node, name, None)
            if not (_present(a) and _present(b)):
                continue
            if _normalized(a) != _normalized(b):
                return True
        return False

    def _build(
        self, node: MemoryNode, finding: ConflictFinding, trace_id: str
    ) -> MemoryConflictProposal:
        """把节点与检测结论组装成提案（``conflict_id`` 为业务键的确定性摘要）。"""
        payload = node.model_dump(mode="json")
        proposed = {k: v for k, v in payload.items() if k not in _STAMP_KEYS}
        canonical = json.dumps(proposed, sort_keys=True, ensure_ascii=False)
        return MemoryConflictProposal(
            conflict_id=digest_id("cf", finding.kind, finding.target_node_id or "",
                                  node.type, canonical),
            kind=finding.kind,
            dimension=node.type,
            target_node_id=finding.target_node_id,
            proposed=proposed,
            trace_id=trace_id,
            created_at=self._now(),
        )

    def _files(self) -> tuple[str, ...]:
        """``execution_log`` 分区内本队列的文件（清单口径，且只认本前缀）。"""
        return tuple(p for p in self._graph.store.list_files("execution_log")
                     if p.startswith(CONFLICT_PREFIX) and p.endswith(".json"))

    @staticmethod
    def _path(conflict_id: str) -> str:
        return f"{CONFLICT_PREFIX}{conflict_id}.json"

    def _write(self, proposal: MemoryConflictProposal) -> None:
        self._graph.store.put("execution_log", self._path(proposal.conflict_id),
                              proposal.model_dump_json().encode("utf-8"))

    @staticmethod
    def _parse(raw: bytes) -> MemoryConflictProposal:
        try:
            return MemoryConflictProposal.model_validate_json(raw)
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise MemoryValidationError(f"冲突提案记录损坏无法解析：{exc}") from exc
