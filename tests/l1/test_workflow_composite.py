"""T-L1-003.2 测试：03 §3.2 复合 Skill（工作流 → 命名 Skill + 依赖提示）。

GWT 对照（任务文件 5 条）：
- GWT-1 保存为复合 Skill：``SkillDescriptor``（``source=user-built``）注册进 Skill
  库、``list_all()`` 可见、可按 ``skill_id`` 寻址；能力字段由所引用 Skill 派生
- GWT-2 统一输入输出契约：未连线端口推导（节点限定点号键）+ 显式覆盖
  （键集与同名键 ``type`` 一致放行、不一致即拒）
- GWT-3 参数暴露：``exposed_params`` → ``SkillDescriptor.parameters``（双通道可改）
- GWT-4 依赖缺失提示：逐条 ``DependencyGap``（含 ``node_id`` / ``skill_id``）、
  拒绝物化且不落任何文件
- GWT-5 命名中性化与重名：拒绝并说明理由（复用 01 §6，不另立口径）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.capability_types import Provenance
from st_agent.contracts.registry_types import SemVer
from st_agent.l0.storage import Store
from st_agent.l1.skills import (
    SkillExistsError,
    SkillRegistry,
    SkillValidationError,
)
from st_agent.l1.workflow import (
    CompositeDependencyError,
    ParamBinding,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowNode,
    WorkflowValidationError,
    composite_skill_id,
    derive_io_contract,
    missing_dependencies,
    save_as_composite_skill,
    source_flow_id,
)

PASS = "correct horse battery staple"
FLOW = "wf_demo_v1.0"
COMPOSITE = "sk_demo_v1.0"
DESC = "组合扫描与预警产出每日简报"


# ───────────────────────── 夹具与构件 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    reg.register("sk_scan", version="1.0", name="退市风险扫描",
                 description="识别退市高危信号并给出触发原因",
                 input_schema={"type": "object",
                               "properties": {"symbol": {"type": "string"}}},
                 output_schema={"type": "object",
                                "properties": {"risk_level": {"type": "string"}}},
                 parameters=(dict(name="threshold", type="number", default=0.8,
                                  min_value=0.0, max_value=1.0,
                                  description="触发阈值"),
                             dict(name="symbol", type="string", default="",
                                  description="待扫描的证券代码")),
                 permissions=("local_read:<data/cache/**>",),
                 offline_level="full")
    reg.register("sk_alert", version="1.0", name="风险预警",
                 description="按风险等级产出预警条目",
                 input_schema={"type": "object", "required": ["risk_level"],
                               "properties": {"risk_level": {"type": "string"}}},
                 output_schema={"type": "object",
                                "properties": {"alerts": {"type": "array"}}},
                 parameters=(dict(name="risk_level", type="string", default="low",
                                  description="触发预警的风险等级"),),
                 permissions=("net_access:<*.example.invalid>",),
                 offline_level="degraded")
    reg.register("sk_pack", version="1.0", name="导出打包",
                 description="把结果打包为可分享文件",
                 input_schema={"type": "object", "properties": {}},
                 output_schema={"type": "object",
                                "properties": {"bundle": {"type": "string"}}},
                 permissions=("exec_command",),
                 offline_level="none")
    return reg


def node(nid: str, skill_id: str, **params: ParamBinding) -> WorkflowNode:
    return WorkflowNode(node_id=nid, skill_id=skill_id, params=params)


def lit(value: object) -> ParamBinding:
    return ParamBinding(kind="literal", value=value)


def ref(from_node: str, path: str | None = None) -> ParamBinding:
    return ParamBinding(kind="ref", from_node=from_node, path=path)


def edge(eid: str, src: str, dst: str) -> WorkflowEdge:
    return WorkflowEdge(edge_id=eid, from_node=src, to_node=dst)


def make_dag(flow_id: str = FLOW, *, nodes, edges=(), name="每日ST板块简报") -> WorkflowDAG:
    return WorkflowDAG(
        flow_id=flow_id,
        name=name,
        description="面向演示的工作流定义",
        version=SemVer.parse(flow_id.rsplit("_v", 1)[1]),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def scan_then_alert() -> WorkflowDAG:
    """扫描 → 预警的连线工作流（a 的入端口未连，b 的出端口未被消费）。"""
    return make_dag(nodes=[node("a", "sk_scan_v1.0"), node("b", "sk_alert_v1.0")],
                    edges=[edge("e1", "a", "b")])


# ───────────────────────── 标识：复合 Skill ↔ 来源工作流 ─────────────────────────


class TestIdMapping:
    def test_composite_skill_id_derives_from_flow_base(self):
        assert composite_skill_id(FLOW) == COMPOSITE
        assert composite_skill_id("wf_daily_brief_v2.3") == "sk_daily_brief_v2.3"

    def test_source_flow_id_is_inverse(self):
        assert source_flow_id(COMPOSITE) == FLOW
        assert source_flow_id(composite_skill_id("wf_daily_brief_v2.3")) == \
            "wf_daily_brief_v2.3"


# ───────────────────────── GWT-1 保存为复合 Skill ─────────────────────────


class TestGwt1SaveAsComposite:
    def test_saves_user_built_descriptor_into_skill_library(self, registry, store):
        desc = save_as_composite_skill(scan_then_alert(), registry,
                                       name="每日退市简报", description=DESC)
        assert desc.skill_id == COMPOSITE
        assert desc.source == "user-built"
        assert desc.provenance == Provenance()
        assert desc.dependencies == ("sk_alert_v1.0", "sk_scan_v1.0")
        assert COMPOSITE in {d.skill_id for d in registry.list_all()}
        assert registry.get(COMPOSITE) == desc
        assert f"skill-registry/{COMPOSITE}.json" in store.list_files("config")

    def test_capability_fields_derived_from_referenced_skills(self, registry):
        """offline_level 取最差、permissions 取并集（复合体只声明部件支持的面）。"""
        desc = save_as_composite_skill(scan_then_alert(), registry,
                                       name="每日退市简报", description=DESC)
        assert desc.offline_level == "degraded"          # full + degraded → 最差
        assert desc.permissions == ("local_read:<data/cache/**>",
                                    "net_access:<*.example.invalid>")

    def test_worst_offline_level_propagates_none(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0"), node("c", "sk_pack_v1.0")],
                       edges=[edge("e1", "a", "c")])
        desc = save_as_composite_skill(dag, registry,
                                       name="每日退市简报", description=DESC)
        assert desc.offline_level == "none"              # 任一 none → none

    def test_resave_of_same_version_is_rejected_not_overwritten(self, registry):
        save_as_composite_skill(scan_then_alert(), registry,
                                name="每日退市简报", description=DESC)
        with pytest.raises(SkillExistsError):
            save_as_composite_skill(scan_then_alert(), registry,
                                    name="每日退市简报", description=DESC)

    def test_flow_base_colliding_with_referenced_skill_rejected(self, registry):
        dag = make_dag("wf_scan_v1.0", nodes=[node("a", "sk_scan_v1.0")])
        with pytest.raises(WorkflowValidationError, match="与工作流所引用的 Skill 冲突"):
            save_as_composite_skill(dag, registry, name="每日退市简报", description=DESC)

    def test_invalid_workflow_not_saved(self, registry, store):
        """未过校验的工作流不得物化（此处：依赖齐全但连线契约不匹配）。"""
        before = store.list_files("config")
        dag = make_dag(nodes=[node("a", "sk_pack_v1.0"), node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        with pytest.raises(WorkflowValidationError, match="校验未通过"):
            save_as_composite_skill(dag, registry, name="每日退市简报", description=DESC)
        assert store.list_files("config") == before


# ───────────────────────── GWT-2 统一输入输出契约 ─────────────────────────


class TestGwt2IoContract:
    def test_derived_from_unconnected_ports(self, registry):
        ins, outs = derive_io_contract(scan_then_alert(), registry)
        assert ins == {"type": "object",
                       "properties": {"a.symbol": {"type": "string"}}}
        assert outs == {"type": "object",
                        "properties": {"b.alerts": {"type": "array"}}}

    def test_incoming_edge_covers_input_ports_wholesale(self, registry):
        ins, _ = derive_io_contract(scan_then_alert(), registry)
        assert "b.risk_level" not in ins["properties"]    # b 有入边 → 入端口整体已连

    def test_outgoing_edge_covers_output_ports_wholesale(self, registry):
        _, outs = derive_io_contract(scan_then_alert(), registry)
        assert "a.risk_level" not in outs["properties"]   # a 有出边 → 出端口整体已连

    def test_ref_binding_counts_as_connected(self, registry):
        dag = make_dag(nodes=[
            node("a", "sk_scan_v1.0"),
            node("b", "sk_alert_v1.0", risk_level=ref("a", "risk_level"))])
        ins, outs = derive_io_contract(dag, registry)
        assert set(ins["properties"]) == {"a.symbol"}
        assert set(outs["properties"]) == {"b.alerts"}    # a 的出端口被 ref 消费

    def test_literal_binding_counts_as_filled(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0", symbol=lit("600000")),
                              node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        ins, _ = derive_io_contract(dag, registry)
        assert ins["properties"] == {}                    # 字面量已填 → 不对外暴露

    def test_explicit_override_consistent_accepted(self, registry):
        explicit = {"type": "object", "required": ["a.symbol"],
                    "properties": {"a.symbol": {"type": "string",
                                                "description": "证券代码"}}}
        desc = save_as_composite_skill(scan_then_alert(), registry,
                                       name="每日退市简报", description=DESC,
                                       inputs=explicit)
        assert desc.input_schema == explicit              # 其余关键字随显式

    @pytest.mark.parametrize("bad", [
        {"type": "object", "properties": {"a.symbol": {"type": "string"},
                                          "a.extra": {"type": "string"}}},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {"a.symbol": {"type": "integer"}}},
    ], ids=["多出端口", "缺少端口", "type不符"])
    def test_explicit_override_inconsistent_rejected(self, registry, store, bad):
        before = store.list_files("config")
        with pytest.raises(WorkflowValidationError, match="不一致"):
            save_as_composite_skill(scan_then_alert(), registry,
                                    name="每日退市简报", description=DESC,
                                    inputs=bad)
        assert store.list_files("config") == before


# ───────────────────────── GWT-3 参数暴露 ─────────────────────────


class TestGwt3ExposedParams:
    def test_exposed_literal_param_enters_parameters_and_is_editable(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0", threshold=lit(0.5)),
                              node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        desc = save_as_composite_skill(dag, registry, name="每日退市简报",
                                       description=DESC,
                                       exposed_params=("a.threshold",))
        assert [p.name for p in desc.parameters] == ["a.threshold"]
        assert desc.parameters[0].type == "number"
        assert desc.parameters[0].default == 0.5          # 默认值取该节点的字面量
        updated = registry.set_parameters(desc.skill_id, {"a.threshold": 0.9})
        assert updated.parameters[0].default == 0.9       # 双通道可改

    def test_unexposed_params_stay_internal(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0", threshold=lit(0.5)),
                              node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        desc = save_as_composite_skill(dag, registry, name="每日退市简报",
                                       description=DESC)
        assert desc.parameters == ()                      # A3：不自动上浮

    def test_exposed_param_without_binding_takes_declared_default(self, registry):
        desc = save_as_composite_skill(scan_then_alert(), registry,
                                       name="每日退市简报", description=DESC,
                                       exposed_params=("a.threshold",))
        assert desc.parameters[0].default == 0.8          # 未绑定时取 Skill 自身默认值

    def test_out_of_range_literal_rejected(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0", threshold=lit(5.0)),
                              node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        with pytest.raises(SkillValidationError, match="高于上限"):
            save_as_composite_skill(dag, registry, name="每日退市简报",
                                    description=DESC,
                                    exposed_params=("a.threshold",))

    def test_ref_bound_param_not_exposable(self, registry):
        dag = make_dag(nodes=[
            node("a", "sk_scan_v1.0"),
            node("b", "sk_alert_v1.0", risk_level=ref("a", "risk_level"))])
        with pytest.raises(WorkflowValidationError, match="不得再对外暴露"):
            save_as_composite_skill(dag, registry, name="每日退市简报",
                                    description=DESC,
                                    exposed_params=("b.risk_level",))

    def test_unknown_param_or_node_rejected(self, registry):
        with pytest.raises(WorkflowValidationError, match="未声明参数"):
            save_as_composite_skill(scan_then_alert(), registry,
                                    name="每日退市简报", description=DESC,
                                    exposed_params=("a.nope",))
        with pytest.raises(WorkflowValidationError, match="指向不存在的节点"):
            save_as_composite_skill(scan_then_alert(), registry,
                                    name="每日退市简报", description=DESC,
                                    exposed_params=("zz.threshold",))


# ───────────────────────── GWT-4 依赖缺失提示 ─────────────────────────


class TestGwt4MissingDependencies:
    def test_gaps_listed_per_node(self, registry):
        dag = make_dag(nodes=[node("a", "sk_ghost_v1.0"),
                              node("b", "sk_alert_v1.0")])
        gaps = missing_dependencies(dag, registry)
        assert [(g.node_id, g.skill_id) for g in gaps] == [("a", "sk_ghost_v1.0")]
        assert "未注册" in gaps[0].reason

    def test_missing_version_reason_names_existing_latest(self, registry):
        gaps = missing_dependencies(make_dag(nodes=[node("a", "sk_scan_v9.9")]),
                                    registry)
        assert "该版本未注册" in gaps[0].reason
        assert "sk_scan_v1.0" in gaps[0].reason

    def test_save_with_missing_dep_raises_and_writes_nothing(self, registry, store):
        before = store.list_files("config")
        with pytest.raises(CompositeDependencyError) as excinfo:
            save_as_composite_skill(make_dag(nodes=[node("a", "sk_ghost_v1.0")]),
                                    registry, name="每日退市简报",
                                    description="引用未注册依赖")
        assert [g.skill_id for g in excinfo.value.gaps] == ["sk_ghost_v1.0"]
        assert store.list_files("config") == before        # 不落半成品


# ───────────────────────── GWT-5 命名中性化与重名 ─────────────────────────


class TestGwt5Naming:
    @pytest.mark.parametrize("bad", ["激进派简报", "老张盯盘流程", "分析师简报"])
    def test_non_neutral_name_rejected(self, registry, store, bad):
        before = store.list_files("config")
        with pytest.raises(SkillValidationError, match="中性化"):
            save_as_composite_skill(scan_then_alert(), registry,
                                    name=bad, description=DESC)
        assert store.list_files("config") == before

    def test_name_duplicating_existing_skill_rejected(self, registry):
        with pytest.raises(SkillValidationError, match="重名"):
            save_as_composite_skill(scan_then_alert(), registry,
                                    name="退市风险扫描", description=DESC)
