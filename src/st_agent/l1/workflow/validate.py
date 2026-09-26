"""WorkflowDAG 静态校验（T-L1-003.1；03 §3.1 + §4「校验」）。

判定件一律复用，不自造：
- 连线契约匹配 → ``contracts.schema_check.check_link``（01 §2 明令全平台唯一口径）
- 命名中性化 → ``contracts.neutrality.NeutralityGuard``（01 §6）
- skill_id 形态 / 存在性 → ``l1.skills`` 的 ``check_skill_id`` / ``SkillRegistry``

**依赖图口径**：节点间依赖 = ``edges[]`` 的连线 **并上** ``kind=ref`` 的参数绑定
（引用上游输出即是依赖，两者取并）。成环检测与拓扑序都跑在这张并图上——故
「A 引用 B 的输出、同时又有 B → A 的连线」这类绕开显式连线的环同样被拦下。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.schema_check import check_link
from st_agent.l1.skills.errors import SkillNotFoundError, SkillValidationError
from st_agent.l1.skills.ids import check_skill_id
from st_agent.l1.workflow.errors import WorkflowValidationError
from st_agent.l1.workflow.models import WorkflowDAG

__all__ = [
    "WorkflowIssue",
    "WorkflowValidation",
    "dependency_graph",
    "topological_order",
    "validate_dag",
]


class WorkflowIssue(BaseModel):
    """一条校验发现（含定位与中性措辞的人可读说明，供 Studio 回显）。"""

    model_config = ConfigDict(frozen=True)

    code: str
    """机器可读码（``cycle`` / ``link_mismatch`` / ``name_not_neutral`` …）。"""
    path: str
    """工作流内定位：``nodes[a].params.x`` / ``edges[e1]`` / ``name``。"""
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class WorkflowValidation(BaseModel):
    """``validate_dag`` 的判定结果（是否通过 + 全部发现）。"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    issues: tuple[WorkflowIssue, ...] = ()

    def codes(self) -> tuple[str, ...]:
        """本次全部发现码（去序保序）。"""
        return tuple(dict.fromkeys(i.code for i in self.issues))

    def of_code(self, code: str) -> tuple[WorkflowIssue, ...]:
        return tuple(i for i in self.issues if i.code == code)

    def describe(self) -> str:
        """人类可读汇总（发现以「；」连接）。"""
        if self.ok:
            return "工作流校验通过"
        return "；".join(str(i) for i in self.issues)


def _default_name_check(name: str) -> bool:
    return NeutralityGuard().check_name(name).passed


# ───────────────────────── 依赖图 ─────────────────────────


def dependency_graph(dag: WorkflowDAG) -> dict[str, set[str]]:
    """节点依赖图（``edges[]`` ∪ ``kind=ref`` 绑定；只含存在的节点）。"""
    adj: dict[str, set[str]] = {n.node_id: set() for n in dag.nodes}
    for e in dag.edges:
        if e.from_node in adj and e.to_node in adj and e.from_node != e.to_node:
            adj[e.from_node].add(e.to_node)
    for n in dag.nodes:
        for b in n.params.values():
            if b.kind == "ref" and b.from_node in adj:
                adj[b.from_node].add(n.node_id)
    return adj


def _find_cycle(adj: dict[str, set[str]]) -> tuple[str, ...] | None:
    """深度优先找第一个环 → 环上节点序列（含回到起点的一跳）；无环 → None。"""
    color = {k: 0 for k in adj}
    stack: list[str] = []

    def dfs(u: str) -> tuple[str, ...] | None:
        color[u] = 1
        stack.append(u)
        for v in sorted(adj[u]):
            if color.get(v) == 1:
                return tuple(stack[stack.index(v):] + [v])
            if color.get(v) == 0:
                found = dfs(v)
                if found is not None:
                    return found
        stack.pop()
        color[u] = 2
        return None

    for u in sorted(adj):
        if color[u] == 0:
            found = dfs(u)
            if found is not None:
                return found
    return None


def topological_order(dag: WorkflowDAG) -> tuple[str, ...]:
    """节点拓扑序（并图上的 Kahn 算法；同层按 ``node_id`` 排序以求确定性）。

    成环 → ``WorkflowValidationError``（本函数只服务「已通过校验」的图；
    校验未过时调用方应先看 ``validate_dag`` 的发现）。
    """
    adj = dependency_graph(dag)
    indeg = {u: 0 for u in adj}
    for u in adj:
        for v in adj[u]:
            indeg[v] += 1
    ready = sorted(u for u, d in indeg.items() if d == 0)
    out: list[str] = []
    while ready:
        u = ready.pop(0)
        out.append(u)
        for v in sorted(adj[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                ready.append(v)
        ready.sort()
    if len(out) != len(adj):
        cycle = _find_cycle(adj)
        raise WorkflowValidationError(
            f"工作流依赖成环，无法拓扑排序：{' → '.join(cycle or ())}")
    return tuple(out)


# ───────────────────────── 校验主体 ─────────────────────────


def validate_dag(
    dag: WorkflowDAG,
    registry: Any = None,
    *,
    name_check: Callable[[str], bool] | None = None,
) -> WorkflowValidation:
    """校验一条工作流（成环 / 连线契约 / 命名 / 参数绑定 / 分组）。

    :param registry: ``SkillRegistry``（给定时额外核 skill_id 存在性、连线契约、
        参数名与取值路径；``None`` 时只做「不依赖注册表」的那部分）
    :param name_check: 命名校验回调（默认接 01 §6 的 ``NeutralityGuard``）
    """
    issues: list[WorkflowIssue] = []
    name_ok = name_check or _default_name_check
    if not name_ok(dag.name):
        issues.append(WorkflowIssue(
            code="name_not_neutral", path="name",
            message=f"工作流命名未过中性化校验：{dag.name!r}（01 §6）"))

    by_id: dict[str, Any] = {}
    descs: dict[str, Any] = {}
    for n in dag.nodes:
        if n.node_id in by_id:
            issues.append(WorkflowIssue(
                code="duplicate_node", path=f"nodes[{n.node_id}]",
                message=f"节点标识重复：{n.node_id!r}"))
            continue
        by_id[n.node_id] = n

    for n in dag.nodes:
        try:
            check_skill_id(n.skill_id)
        except SkillValidationError as exc:
            issues.append(WorkflowIssue(
                code="bad_skill_id", path=f"nodes[{n.node_id}].skill_id",
                message=f"节点引用的 skill_id 形态非法：{exc}"))
            continue
        if registry is not None:
            try:
                descs[n.node_id] = registry.get(n.skill_id)
            except SkillNotFoundError:
                issues.append(WorkflowIssue(
                    code="unknown_skill", path=f"nodes[{n.node_id}].skill_id",
                    message=f"节点引用的 Skill 未注册：{n.skill_id!r}"))

    edge_ids: set[str] = set()
    for e in dag.edges:
        path = f"edges[{e.edge_id}]"
        if e.edge_id in edge_ids:
            issues.append(WorkflowIssue(
                code="duplicate_edge", path=path,
                message=f"边标识重复：{e.edge_id!r}"))
        edge_ids.add(e.edge_id)
        if e.from_node == e.to_node:
            issues.append(WorkflowIssue(
                code="self_loop", path=path,
                message=f"连线不允许自环：{e.from_node!r}"))
        for label, ref in (("起点", e.from_node), ("终点", e.to_node)):
            if ref not in by_id:
                issues.append(WorkflowIssue(
                    code="unknown_node", path=path,
                    message=f"连线{label}指向不存在的节点：{ref!r}"))

    cycle = _find_cycle(dependency_graph(dag))
    if cycle is not None:
        issues.append(WorkflowIssue(
            code="cycle", path="edges",
            message=f"依赖成环，拒绝保存：{' → '.join(cycle)}"))

    for e in dag.edges:
        up, down = descs.get(e.from_node), descs.get(e.to_node)
        if up is None or down is None:
            continue                      # 节点缺失已在上面报过，不重复堆噪音
        link = check_link(up.output_schema, down.input_schema)
        if not link.compatible:
            issues.append(WorkflowIssue(
                code="link_mismatch", path=f"edges[{e.edge_id}]",
                message=f"连线 {e.from_node} → {e.to_node} 契约不匹配："
                        f"{link.describe()}"))

    for n in dag.nodes:
        desc = descs.get(n.node_id)
        declared = ({p.name for p in desc.parameters} if desc is not None else None)
        for pname in sorted(n.params):
            b = n.params[pname]
            path = f"nodes[{n.node_id}].params.{pname}"
            if declared is not None and pname not in declared:
                issues.append(WorkflowIssue(
                    code="unknown_parameter", path=path,
                    message=f"节点 {n.node_id!r} 的 Skill 未声明参数 {pname!r}"))
            if b.kind != "ref":
                continue
            if b.from_node not in by_id:
                issues.append(WorkflowIssue(
                    code="unknown_node", path=path,
                    message=f"参数引用了不存在的节点：{b.from_node!r}"))
                continue
            up_desc = descs.get(b.from_node)
            if up_desc is None or not b.path:
                continue
            root = b.path.split(".", 1)[0]
            props = up_desc.output_schema.get("properties") or {}
            if props and root not in props:
                issues.append(WorkflowIssue(
                    code="binding_unknown_path", path=path,
                    message=f"取值路径 {b.path!r} 的根字段 {root!r} 未在 "
                            f"{b.from_node!r} 的输出契约中声明"))

    group_ids: set[str] = set()
    for g in dag.groups:
        path = f"groups[{g.group_id}]"
        if g.group_id in group_ids:
            issues.append(WorkflowIssue(
                code="duplicate_group", path=path,
                message=f"分组标识重复：{g.group_id!r}"))
        group_ids.add(g.group_id)
        for nid in g.node_ids:
            if nid not in by_id:
                issues.append(WorkflowIssue(
                    code="unknown_group_member", path=path,
                    message=f"分组含不存在的节点：{nid!r}"))

    return WorkflowValidation(ok=not issues, issues=tuple(issues))
