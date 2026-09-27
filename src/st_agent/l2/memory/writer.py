"""MemoryWriter 写入接口（04 §3.2）。

两条写入路径：**用户显式**（对话表达偏好 / Onboarding / 手动编辑）→
``source: user_stated``；**系统推断**（Skill 结果 / 反馈 / 模式识别）→
``source: inferred``。两者的差别落在节点自带的 ``source`` / ``provenance`` 上
（由 §1 的不变量强制），本类不为它们分设入口——**新增路径是同一条**。

**红线**（§3.2）：副驾不得自主修改既有 Memory 节点内容。本类以 **API 形状**
强制，不加可绕行开关（决策日志 D-031 / `T-L1-009.3` 取向）：

- 推断面**只有** ``add_node``——而 ``add_node`` 撞既有 id 即冲突（:class:`MemoryGraph`），
  故推断类写入无法「借新增之名改既有节点」
- 修改既有节点**只有一个入口** ``edit_node``，它要求调用方显式声明
  ``confirmed_by="user"``；取值非 ``"user"`` 即拒（运行时校验，不只是类型标注）
- 修改必留修正历史（§1 ``revision_history``），无静默覆盖

策略层（自主写入白名单 / 冲突检测 / 裁决，§4）不在本类，归 `T-L2-002`——本类只交付
写入**原语**与红线形状。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    MemoryEdge,
    MemoryNode,
    RevisionEntry,
    checked_node,
)

__all__ = ["MemoryWriter"]

_IMMUTABLE_KEYS: tuple[str, ...] = (
    "memory_node_id",   # 01 §1：ID 生成后不可变
    "type",             # 改类型即换实体，不是「编辑」
    "created_at",       # 创建时刻不可改写
    "updated_at",       # 由本类按编辑时刻写入，不由调用方给
    "revision_history", # 由本类维护（防篡改历史）
)
"""不得经编辑变更的字段（携带这些键的 ``changes`` 一律拒绝）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class MemoryWriter:
    """记忆图谱的写入面（04 §3.2；持久化只经 :class:`MemoryGraph`）。"""

    def __init__(self, graph: MemoryGraph) -> None:
        self._graph = graph

    # ───────────────────────── 新增（两条写入路径共用） ─────────────────────────

    def add_node(self, node: MemoryNode) -> MemoryNode:
        """新增一条记忆节点（``user_stated`` / ``inferred`` 都走这里；只增不改）。

        撞既有 id → ``MemoryConflictError``（见类文档「红线」）。
        """
        return self._graph.put_node(node)

    def add_edge(self, edge: MemoryEdge) -> MemoryEdge:
        """加一条边（内端须存在、重复幂等；不变量在 :class:`MemoryGraph`）。"""
        return self._graph.add_edge(edge)

    # ───────────────────────── 编辑（用户显式，唯一修改入口） ─────────────────────────

    def edit_node(
        self,
        node_id: str,
        *,
        changes: Mapping[str, Any],
        confirmed_by: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> MemoryNode:
        """用户显式修改一条既有节点（§3.2：编辑走修正历史）。

        :param changes: 要改的字段（不含 ID / type / 时间戳 / 修正历史，见
            :data:`_IMMUTABLE_KEYS`；未知名段一律拒绝，不静默丢弃）
        :param confirmed_by: **只接受 ``"user"``** —— 调用方须显式声明修改已获用户
            确认；其余取值即拒（§3.2 红线：副驾不得自主修改既有节点内容）
        :param reason: 修正缘由（可选，进修正历史）
        :param now: 编辑时刻（默认当前本地时刻；传入须带时区）

        旧值以**整节点快照**存入 ``revision_history``，与修正时刻、缘由一同保留
        供审计（§1）。
        """
        if confirmed_by != "user":
            raise MemoryValidationError(
                f"edit_node 的 confirmed_by 只接受 'user'，得到 {confirmed_by!r}——"
                "修改既有 Memory 节点内容必须经用户确认（04 §3.2 红线）"
            )
        current = self._graph.get_node(node_id)
        frozen = sorted(k for k in changes if k in _IMMUTABLE_KEYS)
        if frozen:
            raise MemoryValidationError(f"以下字段不可经编辑变更：{frozen}")
        unknown = sorted(set(changes) - set(type(current).model_fields))
        if unknown:
            raise MemoryValidationError(f"未知字段不可写入：{unknown}")

        moment = now if now is not None else _now()
        payload = current.model_dump(mode="python")
        payload.update(changes)
        payload["revision_history"] = (
            *current.revision_history,
            RevisionEntry(replaced_at=moment, previous=current.model_dump(mode="json"),
                          reason=reason),
        )
        payload["updated_at"] = moment
        return self._graph.replace_node(checked_node(**payload))
