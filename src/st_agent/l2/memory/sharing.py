"""记忆片段导出（04 §8 片段分享）。

[§8](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 与
[09 §2](../../../docs/技术架构-v2/09-生态与分享.md) 给的是同一条**强制三步**：
过滤 ``private`` / ``sensitive`` 节点 → 展示「本次导出包含的公开信息」清单 →
用户确认后才生成。本模块是这三步在 L2 的落点，交付**片段 payload**（公开节点与
相接边的可移植形态）。

**边界**：``.stmem`` 的**文件容器**（``header`` / ``payload`` / ``manifest`` 段、
格式版本、校验和、来源追溯链）归 [09 §1](../../../docs/技术架构-v2/09-生态与分享.md)
的 [`T-ECO-001`](../../../项目管理/tasks/T-ECO-001-分享物类型格式导出流程来源追溯链.md)
——四类分享物同构的容器须待四类 payload 齐备才定稿。本模块只回答「哪些记忆可以
离开本机、以什么形态离开、离开前给用户看什么」。

三条不变量：

- **只放 ``public``**：另两级一条不留；手工构造的载荷由导入侧（
  [`importer`](importer.py)）同口径拦回
- **无悬空边**（[§2](../../../docs/技术架构-v2/04-L2-记忆图谱.md)）：一端被过滤掉的
  边一并剔除；``refers_to`` 的外端若指向本图谱节点，同样要求在片段内
- **只承载当前值**：payload 里的节点清空 ``revision_history``——历史快照可能含早先的
  私有取值，节点级过滤管不到它（见 [D-052](../../../项目管理/决策日志.md)）
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    INTERNAL_EDGE_TYPES,
    NODE_TYPES,
    AnyNode,
    MemoryEdge,
    check_node_id,
)

__all__ = [
    "EXPORTABLE_PRIVACY_LEVELS",
    "SHARE_CONFIRMATION",
    "FragmentPayload",
    "FragmentPlan",
    "MemoryShare",
]

SHARE_CONFIRMATION = "user"
"""导出确认门的合法取值（§8「用户确认后生成」，与 §3.2 的用户确认门同款）。"""

EXPORTABLE_PRIVACY_LEVELS: tuple[str, ...] = ("public",)
"""可离开本机的隐私分级（§8：强制过滤 ``private`` / ``sensitive``）。"""


class FragmentPayload(BaseModel):
    """一次片段分享的载荷（**无文件容器**——容器归 `T-ECO-001`）。

    ``nodes`` 用 §1 的判别联合 :data:`AnyNode`：基类类型会在序列化时丢掉子类专属
    字段（pydantic v2 按声明类型序列化），故此处不能用 ``MemoryNode``。
    """

    model_config = ConfigDict(frozen=True)

    nodes: tuple[AnyNode, ...] = ()
    edges: tuple[MemoryEdge, ...] = ()

    def node_ids(self) -> frozenset[str]:
        """片段内全部节点 id（导入侧复核用）。"""
        return frozenset(n.memory_node_id for n in self.nodes)


class FragmentPlan(BaseModel):
    """一次导出的计划：载荷 + 给用户看的「本次导出包含的公开信息」清单。"""

    model_config = ConfigDict(frozen=True)

    payload: FragmentPayload
    included_summary: tuple[str, ...]
    """中性措辞的包含清单（不含节点原文——回显用户原话属渲染面）。"""
    excluded_nodes: int = Field(ge=0)
    """被隐私过滤剔除的节点数（只报条数，不报内容）。"""


class MemoryShare:
    """记忆片段的导出面（04 §8；只读图谱，不落盘）。

    :param graph: :class:`MemoryGraph`（取材面）
    """

    def __init__(self, graph: MemoryGraph) -> None:
        self._graph = graph

    # ───────────────────────── 计划 ─────────────────────────

    def plan(self) -> FragmentPlan:
        """算出这次导出会包含什么（§8 第二步的清理**与**清单同一次算出）。

        节点级过滤 + 相接边回收 + 清空修正历史，三步在同一次读图上完成——避免
        「先算清单、后取载荷」之间图谱被改动而两者不一致。
        """
        all_nodes = self._graph.nodes()
        public = tuple(n for n in all_nodes if n.privacy_level in EXPORTABLE_PRIVACY_LEVELS)
        allowed = frozenset(n.memory_node_id for n in public)
        edges = tuple(e for e in self._graph.edges() if self._edge_in_scope(e, allowed))
        payload = FragmentPayload(
            nodes=tuple(_shareable(n) for n in public),
            edges=edges,
        )
        return FragmentPlan(
            payload=payload,
            included_summary=_summary(payload, excluded=len(all_nodes) - len(public)),
            excluded_nodes=len(all_nodes) - len(public),
        )

    # ───────────────────────── 产出（确认门） ─────────────────────────

    def export(self, *, confirmed_by: str) -> FragmentPayload:
        """用户确认后产出片段载荷（§8 第三步）。

        :param confirmed_by: **只接受 ``"user"``** —— 调用方须显式声明本次导出已获
            用户确认（§8「展示清单 → 用户确认」；与 ``MemoryWriter.edit_node`` /
            ``MemoryDeleter.delete`` 同款门）。其余取值即拒。
        """
        if confirmed_by != SHARE_CONFIRMATION:
            raise MemoryValidationError(
                f"导出片段的 confirmed_by 只接受 {SHARE_CONFIRMATION!r}，得到 "
                f"{confirmed_by!r}——04 §8 要求展示清单并经用户确认后才生成"
            )
        return self.plan().payload

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _edge_in_scope(edge: MemoryEdge, allowed: frozenset[str]) -> bool:
        """边是否留在片段内（内端须在片段里；``refers_to`` 只约束内端与「本图谱节点」外端）。"""
        if edge.source_id not in allowed:
            return False
        if edge.edge_type in INTERNAL_EDGE_TYPES:
            return edge.target_id in allowed
        return not _is_node_id(edge.target_id) or edge.target_id in allowed


def _is_node_id(raw: str) -> bool:
    """该字符串是否是 ``memory_node_id`` 形态（``refers_to`` 外端判据）。"""
    try:
        check_node_id(raw)
    except MemoryValidationError:
        return False
    return True


def _shareable(node: Any) -> Any:
    """片段里的节点形态：清空 ``revision_history``（旧私有快照不外泄，D-052）。

    ``provenance`` **保留**——[§1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 要求
    ``source=inferred`` 必带溯源，清掉会让片段里的推断类节点成为非法节点；其中的
    ``trace_id`` 是不过名的本机锚点，导入侧会以 ``imported`` 覆盖。
    """
    return node.model_copy(update={"revision_history": ()})


def _summary(payload: FragmentPayload, *, excluded: int) -> tuple[str, ...]:
    """「本次导出包含的公开信息」清单（中性措辞：无第一人称、无情感、无对话体）。"""
    lines: list[str] = [f"公开记忆节点 {len(payload.nodes)} 条"]
    counts = {t: sum(1 for n in payload.nodes if n.type == t) for t in NODE_TYPES}
    lines += [f"{t} 节点 {counts[t]} 条" for t in NODE_TYPES if counts[t]]
    lines.append(f"相接边 {len(payload.edges)} 条")
    if excluded:
        lines.append(f"已剔除私有 / 敏感节点 {excluded} 条")
    return tuple(lines)
