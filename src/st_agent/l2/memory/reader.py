"""MemoryReader 上下文切片查询（04 §3.1）。

按（当前任务类型、对话主题、Token 预算）取**相关节点切片**，供 L3 上下文卡片、
Skill 个性化参数、L4 视角注入、L5 文案个性化消费。三种浏览视图（图谱 / 列表 /
时间线）**共享同一查询层**——:meth:`MemoryReader.query` 是唯一取数入口，``view``
只改排序，不改候选集；故同一查询参数下三种视图返回**同一批节点**，只是组织不同。

切片携带 ``as_of`` 与置信度（§3.1 明列）：消费方据此区分「这是用户说的还是推断的」。

**相关性判据是结构性的、无外部依赖**（见任务 `A1`）——类型亲和 → 主题命中 →
置信度 → 时近度加权；Token 预算按字符数近似。语义检索不在此面。

无相关记忆 → 返回 ``empty`` 信封并带原因（01 §5「合法的空结果必须携带原因，
不得返回裸空」），不编造内容。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import NODE_TYPES, MemoryEdge, MemoryNode, node_text

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "TASK_TYPE_AFFINITY",
    "MemoryReader",
    "MemorySlice",
    "MemorySliceResult",
    "SliceQuery",
]

DEFAULT_TOKEN_BUDGET = 800
"""默认 Token 预算（字符数近似；§3.1 未定默认值）。"""

TASK_TYPE_AFFINITY: dict[str, tuple[str, ...]] = {
    "all": NODE_TYPES,
    "chat": NODE_TYPES,
    "decision": ("thesis", "attention", "identity", "history"),
    "deliberation": ("thesis", "attention", "identity"),
    "onboarding": ("identity", "attention"),
    "delivery": ("attention", "thesis"),
    "reflection": ("history", "pattern", "evolution"),
}
"""任务类型 → 相关节点类型（§3.1 未定口径，此表为初版；未登记的 task_type 取全类型）。"""

_TOPIC_WEIGHT = 2.0
_CONFIDENCE_WEIGHT = 1.0
_RECENCY_WEIGHT = 1.0
_RECENCY_HORIZON_DAYS = 365.0
"""时近度线性衰减视界（超过一年记 0）。"""

_TOPIC_SEPARATORS = re.compile(r"[\s,，。;；:：/|、!！?？\-—]+")

_NO_MEMORY_REASON = "该维度尚无记忆（04 §7 空状态：引导对话入口，不阻断其他功能）"
_BUDGET_REASON = "给定 Token 预算内无可用记忆切片"


class SliceQuery(BaseModel):
    """一次切片查询的输入（§3.1 的三个输入项 + 视图）。"""

    model_config = ConfigDict(frozen=True)

    task_type: str = "chat"
    """当前任务类型（决定节点类型的相关性，见 :data:`TASK_TYPE_AFFINITY`）。"""
    topic: str = ""
    """对话主题（关键词命中判据；空串即不按主题加权）。"""
    token_budget: int | None = Field(default=DEFAULT_TOKEN_BUDGET, ge=1)
    """Token 预算（字符数近似）；``None`` = 不截断（浏览视图用）。"""
    view: Literal["graph", "list", "timeline"] = "list"
    """浏览视图——只改排序，不改候选集（§3.1 三视图共享查询层）。"""


class MemorySlice(BaseModel):
    """一条记忆切片（§3.1：必须携带 ``as_of`` 与置信度）。"""

    model_config = ConfigDict(frozen=True)

    node: MemoryNode
    as_of: datetime
    """该切片的时间锚点（取节点 ``updated_at``；见任务 `A2`）。"""
    confidence: float
    source: str
    """``user_stated`` / ``inferred``——消费方据此区分用户自述与推断。"""
    edges: tuple[MemoryEdge, ...] = ()
    """与该节点相接的边（图谱视图的取材面）。"""


class MemorySliceResult(BaseModel):
    """一次切片查询的产出（§5 信封 + 强类型切片）。"""

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    """``ok``（有切片）/ ``empty``（无相关记忆或预算内无切片），均带原因语义。"""
    slices: tuple[MemorySlice, ...] = ()
    as_of: datetime | None = None
    """本结果的整体时间锚点（切片中最新者；无切片为 None）。"""


class MemoryReader:
    """记忆图谱的读取面（04 §3.1；只读，不落盘）。"""

    def __init__(self, graph: MemoryGraph, *, now: datetime | None = None) -> None:
        self._graph = graph
        self._fixed_now = now

    # ───────────────────────── 查询（唯一取数入口） ─────────────────────────

    def query(self, spec: SliceQuery) -> MemorySliceResult:
        """按查询规格产出切片结果（三种视图同经本入口）。"""
        moment = self._fixed_now if self._fixed_now is not None else _now()
        candidates = [n for n in self._graph.nodes() if _affinity(spec.task_type, n.type)]
        if not candidates:
            return MemorySliceResult(envelope=ResultEnvelope.empty(_NO_MEMORY_REASON))

        tokens = _tokens(spec.topic)
        ranked = sorted(
            (_to_slice(n, self._graph, moment) for n in candidates),
            key=lambda s: (-_score(s, tokens, moment), s.node.memory_node_id),
        )
        ordered = _order(ranked, spec.view)
        kept = _truncate(ordered, spec.token_budget)
        if not kept:
            return MemorySliceResult(envelope=ResultEnvelope.empty(_BUDGET_REASON))

        as_of = max(s.as_of for s in kept)
        return MemorySliceResult(
            envelope=ResultEnvelope.ok(list(kept), as_of=as_of),
            slices=kept,
            as_of=as_of,
        )


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _affinity(task_type: str, node_type: str) -> bool:
    """该节点类型是否与任务类型相关（未登记的 task_type 取全类型）。"""
    return node_type in TASK_TYPE_AFFINITY.get(task_type, NODE_TYPES)


def _tokens(topic: str) -> tuple[str, ...]:
    """主题关键词（按空白与常见标点切分；中文无空格时隔「的/和」不做细分）。"""
    return tuple(t for t in _TOPIC_SEPARATORS.split(topic.strip()) if t)


def _topic_hit(slice_: MemorySlice, tokens: tuple[str, ...]) -> float:
    """主题命中率（命中关键词数 / 关键词总数）；无关键词记 0。"""
    if not tokens:
        return 0.0
    haystack = node_text(slice_.node).lower()
    return sum(1 for t in tokens if t.lower() in haystack) / len(tokens)


def _recency(updated_at: datetime, now: datetime) -> float:
    """时近度（线性衰减至一年视界；未来时间戳记满值）。"""
    age_days = (now - updated_at).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return max(0.0, 1.0 - age_days / _RECENCY_HORIZON_DAYS)


def _score(slice_: MemorySlice, tokens: tuple[str, ...], now: datetime) -> float:
    return (
        _TOPIC_WEIGHT * _topic_hit(slice_, tokens)
        + _CONFIDENCE_WEIGHT * slice_.confidence
        + _RECENCY_WEIGHT * _recency(slice_.as_of, now)
    )


def _to_slice(node: MemoryNode, graph: MemoryGraph, moment: datetime) -> MemorySlice:
    return MemorySlice(
        node=node,
        as_of=node.updated_at,
        confidence=node.confidence,
        source=node.source,
        edges=graph.edges_of(node.memory_node_id),
    )


def _order(slices: list[MemorySlice], view: str) -> list[MemorySlice]:
    """按视图排序（**只改序不改集**，故三视图共享同一候选面）。"""
    if view == "timeline":
        return sorted(slices, key=lambda s: (-s.as_of.timestamp(), s.node.memory_node_id))
    if view == "graph":
        return sorted(slices, key=lambda s: (-len(s.edges), -s.confidence, s.node.memory_node_id))
    return slices                      # list：保持相关性序（上游已按分数排好）


def _truncate(slices: list[MemorySlice], budget: int | None) -> tuple[MemorySlice, ...]:
    """按预算截断（字符数近似）。超预算即**停**，不跳过大的去凑小的——保住优先序。"""
    if budget is None:
        return tuple(slices)
    kept: list[MemorySlice] = []
    used = 0
    for s in slices:
        size = len(s.model_dump_json())
        if used + size > budget:
            break
        kept.append(s)
        used += size
    return tuple(kept)
