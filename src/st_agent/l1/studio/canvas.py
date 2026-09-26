"""画布编辑面（T-L1-003.4；03 §4 双通道的可视化通道）。

``CanvasEditor`` 是**内存编辑会话**——增量操作（增/删节点、连线/断线、分组/
解组），草稿态**不落盘**；「接受」才落盘（见 ``draft``）。落地口径逐条对应
[03 §4] 的「编辑面落地口径」表：

- **校验复用**：编辑期与保存期是**同一判定件的两次调用**，不另造第二套——
  连线契约走 ``contracts.schema_check.check_link``（01 §2 唯一口径）、全图走
  ``workflow.validate.validate_dag``、成环走 ``workflow.validate.find_cycle``
- **连线拦截范围**：``connect`` 只拦**本连线造成**的问题（该边闭合了环 /
  ``check_link`` 不匹配）；草稿**既有**的语义问题不阻断后续编辑，只在
  ``EditResult.validation`` 中回显
- **删节点连带**：删除一个节点即移除它的**全部引用**——入射/出射连线、
  ``ref`` 参数绑定、分组内的成员位。理由是 ``dependency_graph`` 把「边」与
  「``ref`` 绑定」视为**同一类依赖**（两者取并），删除后依赖图不留悬空引用
- **全函数**：用户级编辑冲突一律以 ``EditResult.applied=False`` 表达，不抛异常

画布渲染与交互 UI 归 L3（仓库无前端），本模块只交付后端编辑面。
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

from st_agent.contracts.schema_check import check_link
from st_agent.l1.skills.errors import SkillNotFoundError
from st_agent.l1.workflow.models import (
    ParamBinding,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowGroup,
    WorkflowNode,
    checked_dag,
)
from st_agent.l1.workflow.validate import (
    WorkflowIssue,
    WorkflowValidation,
    dependency_graph,
    validate_dag,
)

__all__ = ["CanvasEditor", "EditResult"]


class EditResult(BaseModel):
    """一次画布操作的结果（03 §4 编辑面：新 DAG 视图 + 校验结论 + 人可读提示）。"""

    model_config = ConfigDict(frozen=True)

    action: str
    """操作名（``add_node`` / ``remove_node`` / ``connect`` / ``disconnect`` /
    ``group`` / ``ungroup``）。"""
    applied: bool
    """本次操作是否生效；``False`` 即画布状态未变（``dag`` 为操作前状态）。"""
    dag: WorkflowDAG
    """操作后的画布状态（``applied=False`` 时与操作前逐字段相同）。"""
    validation: WorkflowValidation
    """对 ``dag`` 的校验结论——每次操作后自动跑一遍（03 §4 编辑面）。"""
    message: str
    """人可读提示：生效说明，或被拦原因（契约不匹配时为「这里需要一个 XX
    类型的输入」式措辞，直接取 ``check_link`` 的说明）。"""
    blocked_by: tuple[WorkflowIssue, ...] = ()
    """拦下本次操作的原因（``applied=True`` 时为空）。"""


class CanvasEditor:
    """画布编辑会话（草稿态不落盘；由 ``DraftIntake.receive`` 或直接构造）。"""

    def __init__(self, dag: WorkflowDAG, registry=None, *,
                 name_check=None) -> None:
        """
        :param registry: ``SkillRegistry``（给定时连线即时契约校验与全图校验
            可核 Skill 存在性 / 输出输入契约；``None`` 时只做不依赖注册表的部分）
        :param name_check: 命名校验回调（缺省接 01 §6 ``NeutralityGuard``）
        """
        self._dag = dag
        self._registry = registry
        self._name_ok = name_check

    # ───────────────────────── 读 ─────────────────────────

    @property
    def dag(self) -> WorkflowDAG:
        """当前画布状态。"""
        return self._dag

    def validate_current(self) -> WorkflowValidation:
        """对当前画布跑一遍全图校验（保存期同一判定件）。"""
        return self._validate(self._dag)

    # ───────────────────────── 写：节点 ─────────────────────────

    def add_node(self, node_id: str, skill_id: str,
                 params: Mapping[str, ParamBinding] | None = None) -> EditResult:
        """添加节点（标识重复或形态非法即不生效）。"""
        if self._dag.node(node_id) is not None:
            return self._blocked("add_node", f"节点标识已存在：{node_id!r}")
        try:
            node = WorkflowNode(node_id=node_id, skill_id=skill_id,
                                params=dict(params or {}))
        except ValidationError as exc:
            # 构造期非法（如 node_id 形态）——pydantic 把模型校验器抛的
            # WorkflowValidationError（ValueError 子类）包成 ValidationError
            return self._blocked("add_node", str(exc))
        return self._commit("add_node", f"已添加节点 {node_id!r}",
                            nodes=(*self._dag.nodes, node))

    def remove_node(self, node_id: str) -> EditResult:
        """删除节点，并**连带移除其全部引用**（连线 / ``ref`` 绑定 / 分组成员位）。"""
        if self._dag.node(node_id) is None:
            return self._blocked("remove_node", f"节点不存在：{node_id!r}")
        survivors = tuple(n for n in self._dag.nodes if n.node_id != node_id)
        return self._commit(
            "remove_node", f"已删除节点 {node_id!r}（连带其全部引用）",
            nodes=tuple(self._strip_refs(n, node_id) for n in survivors),
            edges=tuple(e for e in self._dag.edges
                        if node_id not in (e.from_node, e.to_node)),
            groups=tuple(
                g.model_copy(update={"node_ids": tuple(
                    nid for nid in g.node_ids if nid != node_id)})
                for g in self._dag.groups),
        )

    # ───────────────────────── 写：连线 ─────────────────────────

    def connect(self, edge_id: str, from_node: str, to_node: str) -> EditResult:
        """建立连线（即时校验）：闭合环或契约不匹配即**不生效**，画布状态不变。"""
        for label, ref in (("起点", from_node), ("终点", to_node)):
            if self._dag.node(ref) is None:
                return self._blocked(
                    "connect", f"连线{label}指向不存在的节点：{ref!r}")
        if any(e.edge_id == edge_id for e in self._dag.edges):
            return self._blocked("connect", f"边标识已存在：{edge_id!r}")
        try:
            edge = WorkflowEdge(edge_id=edge_id, from_node=from_node,
                                to_node=to_node)
        except ValidationError as exc:
            return self._blocked("connect", str(exc))

        cycle = self._cycle_closed_by(edge)
        if cycle is not None:
            path = " → ".join(cycle)
            return self._blocked(
                "connect",
                f"连线 {from_node} → {to_node} 会闭合依赖环，已拒绝：{path}",
                WorkflowIssue(code="cycle", path=f"edges[{edge_id}]",
                              message=f"依赖成环：{path}"))
        mismatch = self._link_issue(edge)
        if mismatch is not None:
            return self._blocked("connect", mismatch.message, mismatch)
        return self._commit("connect", f"已连线 {from_node} → {to_node}",
                            edges=(*self._dag.edges, edge))

    def disconnect(self, edge_id: str) -> EditResult:
        """断开连线（不存在即不生效）。"""
        if not any(e.edge_id == edge_id for e in self._dag.edges):
            return self._blocked("disconnect", f"连线不存在：{edge_id!r}")
        return self._commit("disconnect", f"已断开连线 {edge_id!r}",
                            edges=tuple(e for e in self._dag.edges
                                        if e.edge_id != edge_id))

    # ───────────────────────── 写：分组 ─────────────────────────

    def group(self, group_id: str, name: str,
              node_ids: tuple[str, ...] = ()) -> EditResult:
        """建立分组（只动 ``groups[]``；标识重复或含未知节点即不生效）。"""
        if any(g.group_id == group_id for g in self._dag.groups):
            return self._blocked("group", f"分组标识已存在：{group_id!r}")
        unknown = [nid for nid in node_ids if self._dag.node(nid) is None]
        if unknown:
            return self._blocked(
                "group",
                f"分组含不存在的节点：{'、'.join(repr(n) for n in unknown)}")
        try:
            fresh = WorkflowGroup(group_id=group_id, name=name,
                                  node_ids=tuple(node_ids))
        except ValidationError as exc:
            return self._blocked("group", str(exc))
        return self._commit("group", f"已建立分组 {group_id!r}",
                            groups=(*self._dag.groups, fresh))

    def ungroup(self, group_id: str) -> EditResult:
        """解散分组（只动 ``groups[]``；节点与连线不动）。"""
        if not any(g.group_id == group_id for g in self._dag.groups):
            return self._blocked("ungroup", f"分组不存在：{group_id!r}")
        return self._commit("ungroup", f"已解散分组 {group_id!r}",
                            groups=tuple(g for g in self._dag.groups
                                         if g.group_id != group_id))

    # ───────────────────────── 内部 ─────────────────────────

    def _commit(self, action: str, message: str, **changes) -> EditResult:
        """提交一次变更并以**新状态**跑校验。"""
        self._dag = checked_dag(**{**self._dag.model_dump(), **changes})
        return self._result(action, applied=True, message=message)

    def _blocked(self, action: str, message: str,
                 *issues: WorkflowIssue) -> EditResult:
        """拦下一次变更（画布不动）并以**原状态**跑校验。"""
        return self._result(action, applied=False, message=message,
                            blocked_by=issues)

    def _result(self, action: str, *, applied: bool, message: str,
                blocked_by: tuple[WorkflowIssue, ...] = ()) -> EditResult:
        return EditResult(
            action=action, applied=applied, dag=self._dag,
            validation=self.validate_current(), message=message,
            blocked_by=blocked_by)

    def _validate(self, dag: WorkflowDAG) -> WorkflowValidation:
        return validate_dag(dag, self._registry, name_check=self._name_ok)

    def _cycle_closed_by(self, edge: WorkflowEdge) -> tuple[str, ...] | None:
        """这条连线是否闭合了环 → 环路径（含该边）；否则 ``None``。

        判据：**不含本边**的依赖图上 ``to_node`` 能否走回 ``from_node``——
        能走回即「本边闭合了一个环」，走不回即本边与既有环无关（草稿既有环
        不阻断后续编辑，见 03 §4 编辑面落地口径）。依赖图含 ``ref`` 绑定，
        故「引用上游输出 + 反向连线」这类绕开显式连线的环同样被拦。
        """
        if edge.from_node == edge.to_node:
            return (edge.from_node, edge.from_node)
        back = _path_to(dependency_graph(self._dag), edge.to_node,
                        edge.from_node)
        return None if back is None else (edge.from_node, *back)

    def _link_issue(self, edge: WorkflowEdge) -> WorkflowIssue | None:
        """这条连线的契约不匹配发现（兼容 / 无法判定 → ``None``）。"""
        if self._registry is None:
            return None
        up = self._descriptor(edge.from_node)
        down = self._descriptor(edge.to_node)
        if up is None or down is None:
            return None              # 未注册属语义问题，不阻断连线
        link = check_link(up.output_schema, down.input_schema)
        if link.compatible:
            return None
        return WorkflowIssue(
            code="link_mismatch", path=f"edges[{edge.edge_id}]",
            message=f"连线 {edge.from_node} → {edge.to_node} 契约不匹配："
                    f"{link.describe()}")

    def _descriptor(self, node_id: str):
        node = self._dag.node(node_id)
        if node is None:
            return None
        try:
            return self._registry.get(node.skill_id)
        except SkillNotFoundError:
            return None

    @staticmethod
    def _strip_refs(node: WorkflowNode, removed: str) -> WorkflowNode:
        """摘掉该节点指向 ``removed`` 的 ``ref`` 绑定（无命中则原样返回）。"""
        kept = {name: b for name, b in node.params.items()
                if not (b.kind == "ref" and b.from_node == removed)}
        if len(kept) == len(node.params):
            return node
        return node.model_copy(update={"params": kept})


def _path_to(adj: dict[str, set[str]], src: str, dst: str) -> tuple[str, ...] | None:
    """依赖图上 ``src → … → dst`` 的一条路径（含两端；不可达 → ``None``）。

    本函数只做**定位查询**（这条边闭合了哪条环）；成环的**判定件**仍是
    ``workflow.validate.find_cycle``，两者不重复。
    """
    stack: list[tuple[str, tuple[str, ...]]] = [(src, (src,))]
    seen = {src}
    while stack:
        node, path = stack.pop()
        if node == dst:
            return path
        for nxt in sorted(adj.get(node, ()), reverse=True):
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, (*path, nxt)))
    return None
