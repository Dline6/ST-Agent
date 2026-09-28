"""记忆删除与审计（04 §6）。

§6 两条在此落成一条通道：

1. **级联删除**——删除任一节点即删其**派生推理链**（``derived_from`` 下游）
2. **审计**——``execution_log`` 分区中的原始记录**保留**：审计与反思依据不因
   记忆删除而失真（本模块只**增**一条删除记录，绝不改写既有留痕）

三处取向：

- **级联方向**（见任务 `A1`）：[04 §2](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)
  示例写作「pattern 节点 ← 聚合的 history 节点」，且 `conflict.resolve` 的
  ``evolves_from`` 先例为「新 → 旧」（新＝派生方）。故边的存储方向为
  **派生方 → 依据方**，下游＝``edge_type == 'derived_from'`` 且
  ``target_id == 当前节点`` 的那些边的 ``source_id``。
- **先边后节点**（见任务 `A2`）：只删节点会留下指向空气的边，破坏
  :meth:`~st_agent.l2.memory.graph.MemoryGraph.add_edge` 的无悬空边不变量。
  按「删边 → 删节点」次序，中断只可能留下「节点在而边少」（合法态）。
- **只能删除、不能改写**（同 §3.2 红线）：入口要求调用方**显式声明**用户确认
  （``confirmed_by="user"``），与 ``edit_node`` / ``resolve`` 同款形状——副驾不得
  自主删除既有记忆。

**删除记录**落 ``execution_log`` 分区 ``memory-delete/<delete_id>.json``：被删节点
快照（含它们在 ``memory`` 分区内的原样内容）+ 相接边快照 + 时刻 + 缘由，使「某条
记忆何时被删、连带删了什么」可复核。记录在**节点与边都删完之后**写——记录不谎报
未发生的删除；代价是中断可能留下「已删而无记录」，反之不会发生。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.contracts.identifiers import digest_id
from st_agent.l2.memory.errors import (
    MemoryNotFoundError,
    MemoryValidationError,
)
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    MemoryEdge,
    MemoryNode,
    _require_tz,
    check_node_id,
)

__all__ = [
    "DELETE_PREFIX",
    "DeletionOutcome",
    "DeletionRecord",
    "MemoryDeleter",
    "checked_record",
]

DELETE_PREFIX = "memory-delete/"
"""``execution_log`` 分区内删除记录的目录前缀（见任务 `A4`）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class DeletionRecord(BaseModel):
    """一条删除的审计记录（落 ``execution_log`` 分区；只增不改）。

    节点与边以**快照**留存（``model_dump(mode="json")``）——删除后图谱中已无原件，
    快照是这条记忆「曾被记录过什么」的唯一载体；其**原始推理依据**另在
    ``execution_log`` 的既有留痕里（§6：原始记录保留），不受本次删除影响。
    """

    model_config = ConfigDict(frozen=True)

    delete_id: str
    """**业务键的确定性摘要**（``dl_<20 位十六进制>``）——同一删除根重复计算得同一
    ID；**不新增契约 ID 类**（沿用 `T-SC-002` / D-034 取向）。"""
    root_node_id: str
    """用户点名的那个节点（级联链的起点）。"""
    nodes: tuple[dict[str, Any], ...]
    """被删节点的快照（按 ``memory_node_id`` 升序，含根与级联到的下游）。"""
    edges: tuple[dict[str, Any], ...]
    """被删边的快照（按边键升序）。"""
    deleted_at: datetime
    reason: str | None = None
    """删除缘由（可选；面向人的中性措辞）。"""

    @property
    def node_ids(self) -> tuple[str, ...]:
        """本次删掉的节点 id（按记录内次序）。"""
        return tuple(n["memory_node_id"] for n in self.nodes)

    def contains(self, node_id: str) -> bool:
        """该节点是否在本次删除范围内（作根或作级联成员）。"""
        return node_id == self.root_node_id or node_id in set(self.node_ids)

    @model_validator(mode="after")
    def _shape(self) -> "DeletionRecord":
        _require_tz(self.deleted_at, "deleted_at")
        check_node_id(self.root_node_id)
        if not self.nodes:
            raise MemoryValidationError("删除记录至少要有一条被删节点快照")
        return self


class DeletionOutcome(BaseModel):
    """一次删除的产出（记录 + 被删的节点与边本体）。"""

    model_config = ConfigDict(frozen=True)

    record: DeletionRecord
    nodes: tuple[MemoryNode, ...]
    edges: tuple[MemoryEdge, ...]


def checked_record(**fields: Any) -> DeletionRecord:
    """构造一条删除记录（非法 → ``MemoryValidationError``，不抛裸 pydantic 异常）。"""
    try:
        return DeletionRecord(**fields)
    except ValidationError as exc:
        raise MemoryValidationError(f"删除记录非法：{exc}") from exc


class MemoryDeleter:
    """记忆删除面（04 §6；只经 :class:`MemoryGraph` 读写）。

    :param graph: :class:`MemoryGraph`（读既有节点 / 边，删其一；``store`` 亦由它取得）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, graph: MemoryGraph, *, now: Callable[[], datetime] | None = None) -> None:
        self._graph = graph
        self._now = _now if now is None else now

    # ───────────────────────── 删除 ─────────────────────────

    def delete(
        self,
        node_id: str,
        *,
        confirmed_by: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> DeletionOutcome:
        """删除一条记忆及其派生推理链（§6；用户确认后调用）。

        :param node_id: 删除根（级联链的起点）
        :param confirmed_by: **只接受 ``"user"``** —— 调用方须显式声明删除已获用户
            确认；其余取值即拒（副驾不得自主删除既有记忆）
        :param reason: 删除缘由（可选，进记录）
        :param now: 删除时刻（默认当前本地时刻；传入须带时区）

        级联范围＝**根节点 + ``derived_from`` 下游的传递闭包**（递归，菱形共享派生
        只删一次）；相接的边（以被删节点为任一端者）一并移除，先边后节点，故不会
        留下悬空边。目标不存在 / 声明不当 / 快照读取失败一律**显式失败且图谱零变更**。
        """
        if confirmed_by != "user":
            raise MemoryValidationError(
                f"delete 的 confirmed_by 只接受 'user'，得到 {confirmed_by!r}——"
                "删除既有 Memory 节点必须经用户确认（04 §6 / §3.2 红线）"
            )
        check_node_id(node_id)
        if not self._graph.has_node(node_id):
            raise MemoryNotFoundError(f"memory 分区无节点 {node_id!r}，无可删除")
        moment = now if now is not None else self._now()
        _require_tz(moment, "now")

        every_edge = self._graph.edges()
        closing = self._closure(node_id, every_edge)
        doomed = set(closing)
        # 边：以被删节点为任一端的一律回收（含幸存节点指向被删节点者——否则悬空）
        victims = tuple(sorted(
            (e for e in every_edge if e.source_id in doomed or e.target_id in doomed),
            key=lambda e: (e.source_id, e.edge_type, e.target_id),
        ))
        # 快照先取全，再动手——任何读失败都发生在图谱被改之前
        nodes = tuple(self._graph.get_node(nid) for nid in closing)
        record = checked_record(
            delete_id=digest_id("dl", node_id, moment.isoformat()),
            root_node_id=node_id,
            nodes=tuple(n.model_dump(mode="json") for n in nodes),
            edges=tuple(e.model_dump(mode="json") for e in victims),
            deleted_at=moment,
            reason=reason,
        )
        for e in victims:
            self._graph.delete_edge(e)
        for n in nodes:
            self._graph.delete_node(n.memory_node_id)
        self._write(record)
        return DeletionOutcome(record=record, nodes=nodes, edges=victims)

    # ───────────────────────── 审计面 ─────────────────────────

    def records(self) -> tuple[DeletionRecord, ...]:
        """全部删除记录（按 ``delete_id`` 升序）。"""
        return tuple(sorted((self._parse(self._graph.store.get("execution_log", p))
                             for p in self._files()), key=lambda r: r.delete_id))

    def record_for(self, node_id: str) -> tuple[DeletionRecord, ...]:
        """该节点出现在哪些删除记录里（作根或作级联成员；按 ``delete_id`` 升序）。"""
        check_node_id(node_id)
        return tuple(r for r in self.records() if r.contains(node_id))

    # ───────────────────────── 内部工具 ─────────────────────────

    def _closure(self, root: str, every_edge: tuple[MemoryEdge, ...]) -> tuple[str, ...]:
        """派生推理链的传递闭包（根在前；同层按首次遇到次序）。

        下游判据见模块文档：``derived_from`` 边的**派生方（``source_id``）**指向
        **依据方（``target_id``）**，故由依据方出发取其 ``source_id``。
        """
        downstream: dict[str, list[str]] = {}
        for e in every_edge:
            if e.edge_type == "derived_from":
                downstream.setdefault(e.target_id, []).append(e.source_id)
        chain: list[str] = [root]
        seen = {root}
        cursor = 0
        while cursor < len(chain):
            for nxt in sorted(downstream.get(chain[cursor], ())):
                if nxt not in seen:
                    seen.add(nxt)
                    chain.append(nxt)
            cursor += 1
        return tuple(chain)

    def _files(self) -> tuple[str, ...]:
        """``execution_log`` 分区内本模块的文件（清单口径，且只认本前缀）。"""
        return tuple(p for p in self._graph.store.list_files("execution_log")
                     if p.startswith(DELETE_PREFIX) and p.endswith(".json"))

    @staticmethod
    def _path(delete_id: str) -> str:
        return f"{DELETE_PREFIX}{delete_id}.json"

    def _write(self, record: DeletionRecord) -> None:
        self._graph.store.put("execution_log", self._path(record.delete_id),
                              record.model_dump_json().encode("utf-8"))

    @staticmethod
    def _parse(raw: bytes) -> DeletionRecord:
        try:
            return DeletionRecord.model_validate_json(raw)
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise MemoryValidationError(f"删除记录损坏无法解析：{exc}") from exc
