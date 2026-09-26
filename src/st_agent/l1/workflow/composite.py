"""复合 Skill：工作流 → 命名 Skill（T-L1-003.2；03 §3.2）。

用户把一条**校验通过**的工作流保存为复合 Skill（「命名的工作流」）：对外暴露
统一输入输出契约 + 参数，以 ``source=user-built`` 进 ``SkillRegistry``（复用
``config`` 分区 ``skill-registry/<skill_id>.json`` 布局，不新造存储面）。

落地口径（逐条对应 [03 §3.2] 的「落地口径」表）：

- **标识**：``skill_id`` 由工作流 base 派生——``wf_<名>_v<主>.<次>`` →
  ``sk_<名>_v<主>.<次>``（``composite_skill_id`` / ``source_flow_id`` 互为逆）。
  故「复合 Skill 回指来源工作流」不靠描述文本，而是**标识本身可逆推导**。
- **输入输出契约**：默认由**未连线端口**推导（``derive_io_contract``），
  ``properties`` 取节点限定点号键 ``<node_id>.<prop>``；可显式覆盖，键集与同名键
  的 ``type`` 必须与推导值一致（不一致即拒，不静默取其一）。
- **已连线判据**：与 §3.1 的粗粒度节点到节点边模型对称——入端口「已连」=
  该节点有入边 **或** 有同名绑定（``literal`` / ``ref``）；出端口「已连」=
  该节点有出边 **或** 被其他节点以 ``ref`` 引用。
- **参数暴露**：节点参数是内部绑定细节，不自动上浮；``exposed_params``
  显式声明（``<node_id>.<参数名>``）才进 ``SkillDescriptor.parameters``。
- **能力字段**：``offline_level`` 取所引用 Skill 的最差、``permissions`` 取并集。
- **依赖缺失不落半成品**：``missing_dependencies`` 给出逐条提示；
  ``save_as_composite_skill`` 遇缺失即抛 ``CompositeDependencyError``——用户在
  依赖未装期间不丢工作，因为工作流定义本身已由 ``WorkflowStore`` 承载。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.capability_types import (
    ParameterSpec,
    Provenance,
    SkillDescriptor,
)
from st_agent.l1.skills.errors import SkillNotFoundError, SkillValidationError
from st_agent.l1.skills.ids import base_of as skill_base_of
from st_agent.l1.skills.ids import check_skill_id, parse_skill_id, skill_id_for
from st_agent.l1.skills.registry import SkillRegistry, validate_param_values
from st_agent.l1.workflow.errors import WorkflowError, WorkflowValidationError
from st_agent.l1.workflow.ids import check_flow_id, flow_id_for, parse_flow_id
from st_agent.l1.workflow.models import WorkflowDAG
from st_agent.l1.workflow.validate import validate_dag

__all__ = [
    "CompositeDependencyError",
    "DependencyGap",
    "composite_skill_id",
    "derive_io_contract",
    "missing_dependencies",
    "save_as_composite_skill",
    "source_flow_id",
]

_FLOW_PREFIX = "wf_"
_SKILL_PREFIX = "sk_"

_OFFLINE_RANK = {"full": 2, "degraded": 1, "none": 0}
"""``offline_level`` 的能力序（取 min = 「最差」）。"""


class DependencyGap(BaseModel):
    """一条依赖缺失提示（03 §3.2「导入方走依赖提示流程」）。"""

    model_config = ConfigDict(frozen=True)

    node_id: str
    """引用方节点。"""
    skill_id: str
    """节点引用的 Skill（缺失者）。"""
    reason: str
    """中性措辞的缺失原因（未注册 / 该版本未注册）。"""


class CompositeDependencyError(WorkflowError):
    """复合 Skill 依赖缺失——拒绝物化（不落半成品）。``gaps`` 即依赖提示。"""

    def __init__(self, gaps: tuple[DependencyGap, ...]) -> None:
        self.gaps = tuple(gaps)
        detail = "；".join(
            f"{g.node_id} 引用 {g.skill_id}（{g.reason}）" for g in self.gaps)
        super().__init__(f"复合 Skill 依赖缺失，拒绝物化（不落半成品）：{detail}")


# ───────────────────────── 标识：复合 Skill ↔ 来源工作流 ─────────────────────────


def composite_skill_id(flow_id: str) -> str:
    """``wf_<名>_v<主>.<次>`` → ``sk_<名>_v<主>.<次>``（版本随工作流版本）。"""
    check_flow_id(flow_id)
    base, version = parse_flow_id(flow_id)
    return skill_id_for(_SKILL_PREFIX + base[len(_FLOW_PREFIX):], version)


def source_flow_id(skill_id: str) -> str:
    """``composite_skill_id`` 的逆：复合 Skill → 其来源工作流标识。

    任意 ``sk_`` 形态都能推出同名 ``wf_``——是否为**复合** Skill 由 Skill 库里
    该 ``skill_id`` 是否存在（及其 ``source``）判定，本函数只看标识形态。
    """
    check_skill_id(skill_id)
    base, version = parse_skill_id(skill_id)
    return flow_id_for(_FLOW_PREFIX + base[len(_SKILL_PREFIX):], version)


def _dependencies(dag: WorkflowDAG) -> tuple[str, ...]:
    """工作流所引用的全部 skill_id（去重排序，供 ``SkillDescriptor.dependencies``）。"""
    return tuple(sorted({n.skill_id for n in dag.nodes}))


# ───────────────────────── 依赖提示 ─────────────────────────


def missing_dependencies(
    dag: WorkflowDAG, registry: SkillRegistry,
) -> tuple[DependencyGap, ...]:
    """逐条列出未注册（或该版本未注册）的依赖（按 ``nodes`` 顺序，同节点多条并列）。

    纯查询、无副作用——供 Studio 保存前提示与导入侧（``T-ECO-002``）复用。
    """
    gaps: list[DependencyGap] = []
    for n in dag.nodes:
        try:
            registry.get(n.skill_id)
        except SkillNotFoundError:
            gaps.append(DependencyGap(
                node_id=n.node_id, skill_id=n.skill_id,
                reason=_missing_reason(n.skill_id, registry)))
    return tuple(gaps)


def _missing_reason(skill_id: str, registry: SkillRegistry) -> str:
    """区分「base 从未注册」与「base 在但该版本未注册」。"""
    latest = registry.get_latest(skill_base_of(skill_id))
    if latest is None:
        return f"Skill 未注册（base {skill_base_of(skill_id)!r} 无任何版本）"
    return f"该版本未注册（base 现有最高 {latest.skill_id}）"


# ───────────────────────── 契约推导 ─────────────────────────


def derive_io_contract(
    dag: WorkflowDAG, registry: SkillRegistry,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """由**未连线端口**推导 ``(input_schema, output_schema)``（03 §3.2）。

    端口 = 节点所引用 Skill 的 ``input_schema`` / ``output_schema`` 的
    ``properties``；键取 ``<node_id>.<prop>``。某节点有入边（出边）时，其入
    （出）端口整体视为已连——与 §3.1 的粗粒度节点到节点边模型对称。

    依赖缺失时由 ``registry.get`` 抛 ``SkillNotFoundError``；需要提示而非异常的
    调用方应先跑 ``missing_dependencies``。
    """
    incoming = {e.to_node for e in dag.edges}
    outgoing = {e.from_node for e in dag.edges}
    node_ids = {n.node_id for n in dag.nodes}
    consumed: dict[str, set[str]] = {}
    for n in dag.nodes:
        for b in n.params.values():
            if b.kind != "ref" or b.from_node not in node_ids:
                continue
            # 无 path 即取整个输出对象 → 该节点全部出端口被消费
            consumed.setdefault(b.from_node, set()).add(
                b.path.split(".", 1)[0] if b.path else "*")

    in_props: dict[str, Any] = {}
    out_props: dict[str, Any] = {}
    for n in dag.nodes:
        desc = registry.get(n.skill_id)
        if n.node_id not in incoming:
            for pname, pspec in (desc.input_schema.get("properties") or {}).items():
                if pname in n.params:
                    continue          # 同名绑定（literal / ref）→ 工作流内部已填
                in_props[f"{n.node_id}.{pname}"] = deepcopy(pspec)
        if n.node_id not in outgoing:
            got = consumed.get(n.node_id, set())
            for pname, pspec in (desc.output_schema.get("properties") or {}).items():
                if "*" in got or pname in got:
                    continue          # 被上游（下游）取用 → 不对外暴露
                out_props[f"{n.node_id}.{pname}"] = deepcopy(pspec)
    return ({"type": "object", "properties": in_props},
            {"type": "object", "properties": out_props})


def _resolve_override(
    label: str, derived: dict[str, Any], explicit: dict[str, Any] | None,
) -> dict[str, Any]:
    """显式覆盖的「不一致即拒」（键集相等 + 同名键 ``type`` 相等，其余随显式）。"""
    if explicit is None:
        return derived
    props = explicit.get("properties") or {}
    dprops = derived.get("properties") or {}
    if set(props) != set(dprops):
        raise WorkflowValidationError(
            f"显式 {label} 与推导的端口集不一致——多出 "
            f"{sorted(set(props) - set(dprops))}、缺少 {sorted(set(dprops) - set(props))}")
    for key in sorted(props):
        given, expect = _declared_type(props[key]), _declared_type(dprops[key])
        if given != expect:
            raise WorkflowValidationError(
                f"显式 {label} 的端口 {key!r} 类型 {given!r} 与推导 {expect!r} 不一致")
    return dict(explicit)


def _declared_type(schema: Any) -> Any:
    return schema.get("type") if isinstance(schema, dict) else None


# ───────────────────────── 参数暴露 ─────────────────────────


def _exposed_parameters(
    dag: WorkflowDAG, registry: SkillRegistry, exposed_params: tuple[str, ...],
) -> tuple[ParameterSpec, ...]:
    """``<node_id>.<参数名>`` 声明 → ``ParameterSpec``（默认值取该节点的字面量绑定）。

    默认值经 ``validate_param_values`` 复核（超范围即拒，与其余 Skill 同一口径）。
    ``kind=ref`` 的参数由上游喂入，再对外暴露会双源冲突，故拒绝。
    """
    out: list[ParameterSpec] = []
    seen: set[str] = set()
    for raw in exposed_params:
        key = str(raw)
        if key in seen:
            raise WorkflowValidationError(f"暴露参数重复声明：{key!r}")
        seen.add(key)
        node_id, _, pname = key.partition(".")
        if not pname:
            raise WorkflowValidationError(
                f"暴露参数须为 <node_id>.<参数名> 形态：{key!r}")
        node = dag.node(node_id)
        if node is None:
            raise WorkflowValidationError(
                f"暴露参数 {key!r} 指向不存在的节点：{node_id!r}")
        desc = registry.get(node.skill_id)
        spec = next((p for p in desc.parameters if p.name == pname), None)
        if spec is None:
            raise WorkflowValidationError(
                f"节点 {node_id!r} 的 Skill 未声明参数 {pname!r}，不得暴露")
        binding = node.params.get(pname)
        if binding is not None and binding.kind == "ref":
            raise WorkflowValidationError(
                f"参数 {key!r} 由上游节点 {binding.from_node!r} 喂入，不得再对外暴露")
        default = binding.value if binding is not None else spec.default
        merged = validate_param_values((spec,), {pname: default})[pname]
        out.append(spec.model_copy(update={"name": key, "default": merged}))
    return tuple(out)


# ───────────────────────── 保存 ─────────────────────────


def save_as_composite_skill(
    dag: WorkflowDAG,
    registry: SkillRegistry,
    *,
    name: str,
    description: str,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    exposed_params: tuple[str, ...] = (),
) -> SkillDescriptor:
    """把校验通过的工作流保存为复合 Skill（``source=user-built``）。

    失败面一律显式化：依赖缺失 → ``CompositeDependencyError``（携逐条 ``gaps``，
    且**不落任何文件**）；工作流校验未过 → ``WorkflowValidationError``；命名未过
    01 §6 或与既有 Skill 重名 → ``SkillValidationError``；``skill_id`` 已存在 →
    ``SkillExistsError``（更新走 ``publish_version``）。

    依赖门在校验之前——「依赖齐不齐」是能否物化的先决条件，且 GWT-4 要的是**逐条
    结构化提示**（节点 + skill_id + 原因），而 ``validate_dag`` 只会把未注册
    Skill 报成一条 ``unknown_skill`` 发现。
    """
    gaps = missing_dependencies(dag, registry)
    if gaps:
        raise CompositeDependencyError(gaps)

    result = validate_dag(dag, registry)
    if not result.ok:
        raise WorkflowValidationError(
            f"工作流校验未通过，不得保存为复合 Skill：{result.describe()}")

    skill_id = composite_skill_id(dag.flow_id)
    if skill_id in {n.skill_id for n in dag.nodes}:
        raise WorkflowValidationError(
            f"复合 Skill 标识 {skill_id!r} 与工作流所引用的 Skill 冲突——"
            "工作流 base 与 Skill 注册名不得同名")

    for other in registry.list_all():
        if other.name == name and other.skill_id != skill_id:
            raise SkillValidationError(
                f"Skill 命名与既有 Skill 重名：{name!r}（{other.skill_id}），请改中性化命名")

    derived_in, derived_out = derive_io_contract(dag, registry)
    descriptors = [registry.get(n.skill_id) for n in dag.nodes]
    return registry.register(
        skill_base_of(skill_id),
        version=dag.version,
        name=name,
        description=description,
        input_schema=_resolve_override("input_schema", derived_in, inputs),
        output_schema=_resolve_override("output_schema", derived_out, outputs),
        parameters=_exposed_parameters(dag, registry, exposed_params),
        dependencies=_dependencies(dag),
        source="user-built",
        provenance=Provenance(),
        permissions=tuple(sorted({p for d in descriptors for p in d.permissions})),
        offline_level=_worst_offline_level(descriptors),
    )


def _worst_offline_level(descriptors: list[SkillDescriptor]) -> str:
    """所引用 Skill 的最差 ``offline_level``（复合体只声明其部件支持的面）。

    无引用（空工作流）时取 ``full``——没有任何部件会限制它。
    """
    if not descriptors:
        return "full"
    return min((d.offline_level for d in descriptors),
               key=lambda lvl: _OFFLINE_RANK[lvl])
