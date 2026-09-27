"""记忆图谱门面（04 §1–§2 本体的存取层；只经 ``Store`` 读写的 ``memory`` 分区）。

落盘布局（04 §1/§2 无物理约定，本节定，理由见任务 `A1`/`A2`）::

    memory/
      node/<memory_node_id>.json     # 一节点一文件（`AnyNode` 的 JSON）
      edge/<edge_key>.json           # 一边一文件（`edge_key` = 组合键）

逐实体落盘的取舍：单用户本地图谱（量级百至数千实体），逐实体落盘使节点级增删
与将来的级联删除（`T-L2-003`）不必重写整图；代价是列举需按清单扫描。

图不变量（`:meth:`MemoryGraph.add_edge` 强制）:

- **无悬空边**：边的**内端**（``memory_node_id`` 一侧）必须已存在——不存在的
  端点即显式失败，不产生指向空气的边
- **边幂等**：同一条边（同 ``edge_key``）重复写入不产生第二条，返回既有记录

本类只做**存取与图结构**；写入路径的语义（两条写入路径、红线、修正历史）在
:class:`~st_agent.l2.memory.writer.MemoryWriter`。``replace_node`` 是**存储层
原语**，语义入口只有 writer 的用户显式编辑路径。
"""

from __future__ import annotations

from st_agent.l2.memory.errors import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryValidationError,
)
from st_agent.l2.memory.models import (
    EDGE_PREFIX,
    INTERNAL_EDGE_TYPES,
    NODE_PREFIX,
    MemoryEdge,
    MemoryNode,
    check_node_id,
    edge_key,
    parse_node,
)

__all__ = ["MemoryGraph"]


class MemoryGraph:
    """记忆图谱的存取门面（节点 / 边的增删读；只经 ``Store``）。"""

    NODE_PREFIX = NODE_PREFIX
    EDGE_PREFIX = EDGE_PREFIX

    def __init__(self, store) -> None:
        self._store = store

    # ───────────────────────── 路径 ─────────────────────────

    @staticmethod
    def node_path(node_id: str) -> str:
        """节点在 ``memory`` 分区内的相对路径（先校验 id 形态）。"""
        return f"{NODE_PREFIX}{check_node_id(node_id)}.json"

    @staticmethod
    def edge_path(edge: MemoryEdge) -> str:
        """边在 ``memory`` 分区内的相对路径（键为主干）。"""
        return f"{EDGE_PREFIX}{edge_key(edge)}.json"

    # ───────────────────────── 节点 ─────────────────────────

    def put_node(self, node: MemoryNode) -> MemoryNode:
        """新增一个节点；同一 ``memory_node_id`` 已存在即冲突（**不得覆盖**）。

        冲突即错而非覆盖：覆盖既有节点是「自主修改既有 Memory 节点内容」的
        形态之一（04 §3.2 红线 / §4「不存在任何静默覆盖」），故存储层直接把
        「新增路径撞既有 id」堵死。
        """
        path = self.node_path(node.memory_node_id)
        if path in self._files(NODE_PREFIX):
            raise MemoryConflictError(
                f"memory_node_id {node.memory_node_id!r} 已存在，不得经新增路径覆盖；"
                "修改既有节点须走用户显式编辑（MemoryWriter.edit_node）"
            )
        self._store.put("memory", path, node.model_dump_json().encode("utf-8"))
        return node

    def replace_node(self, node: MemoryNode) -> MemoryNode:
        """整体替换既有节点的内容（**存储层原语**，供用户显式编辑路径调用）。

        只对**已存在**的节点放行——不存在即错，避免把「编辑」悄悄变成「新增」。
        语义入口见 ``MemoryWriter.edit_node``（唯一调用方）。
        """
        path = self.node_path(node.memory_node_id)
        if path not in self._files(NODE_PREFIX):
            raise MemoryNotFoundError(
                f"memory_node_id {node.memory_node_id!r} 不存在，不得经替换路径新增"
            )
        self._store.put("memory", path, node.model_dump_json().encode("utf-8"))
        return node

    def get_node(self, node_id: str) -> MemoryNode:
        """按 id 取节点（不存在 → ``MemoryNotFoundError``；记录损坏 → 校验错）。"""
        try:
            raw = self._store.get("memory", self.node_path(node_id))
        except KeyError as exc:
            raise MemoryNotFoundError(f"memory 分区无节点 {node_id!r}") from exc
        return parse_node(raw)

    def has_node(self, node_id: str) -> bool:
        """该 id 是否有节点（不解析内容，只查清单）。"""
        return self.node_path(node_id) in self._files(NODE_PREFIX)

    def nodes(self) -> tuple[MemoryNode, ...]:
        """全部节点（按 id 升序，确定序便于比对与渲染）。"""
        return tuple(self.get_node(p[len(NODE_PREFIX):-len(".json")])
                     for p in self._files(NODE_PREFIX))

    # ───────────────────────── 边 ─────────────────────────

    def add_edge(self, edge: MemoryEdge) -> MemoryEdge:
        """加一条边（内端须存在；同一条边幂等）。

        内端 = ``memory_node_id`` 一侧：``refers_to`` 只有 ``source_id`` 是内端
        （外端是外部 ID，不为外部实体建影子节点）。
        """
        if not self.has_node(edge.source_id):
            raise MemoryNotFoundError(
                f"边 {edge.edge_type} 的源节点 {edge.source_id!r} 不存在（不产生悬空边）"
            )
        if edge.edge_type in INTERNAL_EDGE_TYPES and not self.has_node(edge.target_id):
            raise MemoryNotFoundError(
                f"边 {edge.edge_type} 的目标节点 {edge.target_id!r} 不存在（不产生悬空边）"
            )
        path = self.edge_path(edge)
        if path in self._files(EDGE_PREFIX):
            return self._read_edge(path)         # 同一条边：幂等返回既有记录
        self._store.put("memory", path, edge.model_dump_json().encode("utf-8"))
        return edge

    def edges(self) -> tuple[MemoryEdge, ...]:
        """全部边（按键升序）。"""
        return tuple(self._read_edge(p) for p in self._files(EDGE_PREFIX))

    def edges_of(self, node_id: str) -> tuple[MemoryEdge, ...]:
        """与该节点相接的全部边（两个方向都算——图谱视图的取材面）。"""
        check_node_id(node_id)
        return tuple(e for e in self.edges()
                     if e.source_id == node_id or e.target_id == node_id)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _files(self, prefix: str) -> tuple[str, ...]:
        """``memory`` 分区内某前缀下的文件（清单口径，且只认该前缀）。"""
        return tuple(p for p in self._store.list_files("memory") if p.startswith(prefix))

    def _read_edge(self, path: str) -> MemoryEdge:
        try:
            return MemoryEdge.model_validate_json(self._store.get("memory", path))
        except ValueError as exc:
            raise MemoryValidationError(f"记忆边记录损坏无法解析（{path}）：{exc}") from exc
