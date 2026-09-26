"""T-L1-003.1 测试：03 §3.1 WorkflowDAG 模型 + 校验 + 持久化与版本。

GWT 对照（任务文件 5 条）：
- GWT-1 成环阻止：环被指出且不落盘
- GWT-2 连线契约匹配：不匹配给「这里需要一个 XX 类型的输入」，匹配放行
- GWT-3 中性化命名：拟人化名称被拒（01 §6）
- GWT-4 版本共存 / 回滚：多版本共存 + 激活指针回滚 + diff
- GWT-5 引用方「待检查」：主版本发布标待检查，跟随后自动消失
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from st_agent.contracts.registry_types import SemVer
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.workflow import (
    ParamBinding,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowExistsError,
    WorkflowGroup,
    WorkflowNode,
    WorkflowNotFoundError,
    WorkflowStore,
    WorkflowValidationError,
    base_of,
    check_flow_id,
    flow_id_for,
    parse_flow_id,
    topological_order,
    validate_dag,
)

PASS = "correct horse battery staple"
FLOW = "wf_demo_v1.0"


# ───────────────────────── 夹具与构件 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    reg.register("sk_scan", version="1.0", name="退市风险扫描",
                 description="识别退市高危信号并给出触发原因",
                 input_schema={"type": "object", "properties": {}},
                 output_schema={"type": "object",
                                "properties": {"risk_level": {"type": "string"}}},
                 parameters=(dict(name="threshold", type="number", default=0.8,
                                  description="触发阈值"),),
                 permissions=())
    reg.register("sk_alert", version="1.0", name="风险预警",
                 description="按风险等级产出预警条目",
                 input_schema={"type": "object", "required": ["risk_level"],
                               "properties": {"risk_level": {"type": "string"}}},
                 output_schema={"type": "object",
                                "properties": {"alerts": {"type": "array"}}},
                 parameters=(dict(name="risk_level", type="string", default="low",
                                  description="触发预警的风险等级"),),
                 permissions=())
    reg.register("sk_other", version="1.0", name="板块热力图",
                 description="输出板块热度数据",
                 input_schema={"type": "object", "properties": {}},
                 output_schema={"type": "object",
                                "properties": {"sector": {"type": "string"}}},
                 permissions=())
    return reg


def node(nid: str, skill_id: str, **params: ParamBinding) -> WorkflowNode:
    return WorkflowNode(node_id=nid, skill_id=skill_id, params=params)


def lit(value: object) -> ParamBinding:
    return ParamBinding(kind="literal", value=value)


def ref(from_node: str, path: str | None = None) -> ParamBinding:
    return ParamBinding(kind="ref", from_node=from_node, path=path)


def edge(eid: str, src: str, dst: str) -> WorkflowEdge:
    return WorkflowEdge(edge_id=eid, from_node=src, to_node=dst)


def make_dag(flow_id: str = FLOW, *, nodes, edges=(), groups=(), name="每日ST板块简报"):
    return WorkflowDAG(
        flow_id=flow_id,
        name=name,
        description="面向演示的工作流定义",
        version=SemVer.parse(flow_id.rsplit("_v", 1)[1]),
        nodes=tuple(nodes),
        edges=tuple(edges),
        groups=tuple(groups),
    )


def wstore(store: Store, registry: SkillRegistry) -> WorkflowStore:
    return WorkflowStore(store, registry)


# ───────────────────────── GWT-1 成环阻止 ─────────────────────────


class TestGwt1Cycle:
    def test_cycle_reported_and_not_persisted(self, store, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0"), node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b"), edge("e2", "b", "a")])
        result = validate_dag(dag, registry)
        assert not result.ok
        assert "cycle" in result.codes()
        assert "a → b → a" in result.of_code("cycle")[0].message

        w = wstore(store, registry)
        with pytest.raises(WorkflowValidationError, match="成环"):
            w.save(dag)
        assert w.list_all() == ()          # 拒绝即不落盘

    def test_cycle_through_binding_only(self, registry):
        """环也能只经 ``kind=ref`` 绑定形成（不靠显式连线绕开）。"""
        dag = make_dag(nodes=[
            node("a", "sk_alert_v1.0", risk_level=ref("b", "risk_level")),
            node("b", "sk_scan_v1.0", x=ref("a", "alerts")),
        ])
        assert "cycle" in validate_dag(dag, registry).codes()

    def test_topological_order_of_acyclic_dag(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0"), node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        assert validate_dag(dag, registry).ok
        assert topological_order(dag) == ("a", "b")


# ───────────────────────── GWT-2 连线契约匹配 ─────────────────────────


class TestGwt2LinkContract:
    def test_matching_link_passes(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0"), node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        assert validate_dag(dag, registry).ok

    def test_mismatching_link_reports_expected_input(self, registry):
        dag = make_dag(nodes=[node("a", "sk_other_v1.0"), node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        result = validate_dag(dag, registry)
        assert "link_mismatch" in result.codes()
        msg = result.of_code("link_mismatch")[0].message
        assert "这里需要一个 string 类型的输入" in msg
        assert "edges[e1]" == result.of_code("link_mismatch")[0].path


# ───────────────────────── GWT-3 中性化命名 ─────────────────────────


class TestGwt3NameNeutrality:
    @pytest.mark.parametrize("bad", ["激进派简报", "老张盯盘流程", "分析师简报"])
    def test_anthropomorphic_name_rejected(self, registry, bad):
        result = validate_dag(make_dag(nodes=[node("a", "sk_scan_v1.0")], name=bad),
                              registry)
        assert "name_not_neutral" in result.codes()
        assert result.of_code("name_not_neutral")[0].path == "name"

    def test_functional_name_passes(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0")], name="每日ST板块简报")
        assert validate_dag(dag, registry).ok


# ───────────────────────── GWT-4 版本共存 / 回滚 ─────────────────────────


class TestGwt4Versions:
    def test_versions_coexist_rollback_and_diff(self, store, registry):
        w = wstore(store, registry)
        v1 = make_dag(nodes=[node("a", "sk_scan_v1.0")])
        w.save(v1)

        v11 = make_dag("wf_demo_v1.1",
                       nodes=[node("a", "sk_scan_v1.0"), node("b", "sk_alert_v1.0")],
                       edges=[edge("e1", "a", "b")])
        w.publish_version("wf_demo", SemVer(major=1, minor=1), v11)

        assert w.active("wf_demo").flow_id == "wf_demo_v1.1"      # 默认取最高版本
        assert len(w.list_versions("wf_demo")) == 2

        record = w.rollback("wf_demo", "wf_demo_v1.0")
        assert record.config_id == "wf_demo"
        assert (record.old_value, record.new_value) == ("wf_demo_v1.1", "wf_demo_v1.0")
        assert w.active("wf_demo") == v1                          # 逐字段还原
        assert len(w.list_versions("wf_demo")) == 2               # v1.1 仍在历史中

        diff = w.diff("wf_demo_v1.0", "wf_demo_v1.1")
        assert diff.added_nodes == ("b",)
        assert diff.added_edges == ("e1",)
        assert not diff.is_empty()
        assert w.diff("wf_demo_v1.0", "wf_demo_v1.0").is_empty()

    def test_same_version_not_overwritten(self, store, registry):
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        with pytest.raises(WorkflowExistsError):
            w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))

    def test_publish_requires_increasing_version(self, store, registry):
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        with pytest.raises(WorkflowValidationError, match="须高于"):
            w.publish_version("wf_demo", SemVer(major=1, minor=0))

    def test_major_publish_requires_changelog(self, store, registry):
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        with pytest.raises(WorkflowValidationError, match="changelog"):
            w.publish_version("wf_demo", SemVer(major=2, minor=0))

    def test_activate_rejects_cross_flow(self, store, registry):
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        w.save(make_dag("wf_other_v1.0", nodes=[node("a", "sk_scan_v1.0")]))
        with pytest.raises(WorkflowValidationError, match="不属于"):
            w.activate("wf_demo", "wf_other_v1.0")

    def test_missing_flow_raises(self, store, registry):
        with pytest.raises(WorkflowNotFoundError):
            wstore(store, registry).get("wf_nope_v1.0")


# ───────────────────────── GWT-5 引用方「待检查」 ─────────────────────────


class TestGwt5PendingChecks:
    def test_major_publish_marks_then_follow_latest_clears(self, store, registry):
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        assert w.pending_checks() == ()

        registry.publish_version("sk_scan", version="2.0",
                                 changelog="输出新增 verdict 字段",
                                 impact="引用方需人工确认后升级")
        checks = w.pending_checks()
        assert len(checks) == 1
        c = checks[0]
        assert (c.flow_id, c.base, c.node_id) == ("wf_demo_v1.0", "wf_demo", "a")
        assert (c.skill_id, c.to_version) == ("sk_scan_v1.0", "2.0")

        followed = w.follow_latest("wf_demo_v1.0", changelog="跟进主版本 2.0",
                                   impact="仅引用版本变化")
        assert followed.flow_id == "wf_demo_v1.1"           # 未自动破坏：落为新版本
        assert followed.nodes[0].skill_id == "sk_scan_v2.0"
        assert w.get("wf_demo_v1.0").nodes[0].skill_id == "sk_scan_v1.0"
        assert w.pending_checks() == ()

    def test_follow_latest_without_stale_refs(self, store, registry):
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        with pytest.raises(WorkflowValidationError, match="无落后"):
            w.follow_latest("wf_demo_v1.0")

    def test_derived_over_active_version_only(self, store, registry):
        """按生效版本派生：历史版本不误报，显式回滚后旧版本才被标。"""
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        w.publish_version("wf_demo", SemVer(major=1, minor=1))
        registry.publish_version("sk_scan", version="2.0",
                                 changelog="输出新增 verdict 字段",
                                 impact="引用方需人工确认后升级")
        assert [c.flow_id for c in w.pending_checks()] == ["wf_demo_v1.1"]

        w.rollback("wf_demo", "wf_demo_v1.0")
        assert [c.flow_id for c in w.pending_checks()] == ["wf_demo_v1.0"]


# ───────────────────────── 模型与校验的边界 ─────────────────────────


class TestModelAndValidationEdges:
    def test_flow_id_version_must_match(self):
        with pytest.raises(ValidationError, match="不一致"):
            WorkflowDAG(flow_id="wf_demo_v2.0", name="每日ST板块简报",
                        description="面向演示的工作流定义", version=SemVer(major=1, minor=0),
                        nodes=(node("a", "sk_scan_v1.0"),))

    def test_duplicate_node_rejected(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0"), node("a", "sk_alert_v1.0")])
        assert "duplicate_node" in validate_dag(dag, registry).codes()

    def test_unknown_skill_and_bad_form(self, registry):
        dag = make_dag(nodes=[node("a", "sk_nope_v1.0"), node("b", "not-a-skill")])
        codes = validate_dag(dag, registry).codes()
        assert "unknown_skill" in codes and "bad_skill_id" in codes

    def test_self_loop_unknown_node_and_group_member(self, registry):
        dag = make_dag(nodes=[node("a", "sk_scan_v1.0")],
                       edges=[edge("e1", "a", "a"), edge("e2", "a", "ghost")],
                       groups=[WorkflowGroup(group_id="g1", name="子流程",
                                             node_ids=("ghost",))])
        codes = validate_dag(dag, registry).codes()
        assert {"self_loop", "unknown_node", "unknown_group_member"} <= set(codes)

    def test_parameter_and_binding_path_checks(self, registry):
        reg_dag = make_dag(nodes=[
            node("a", "sk_scan_v1.0"),
            node("b", "sk_alert_v1.0", nope=lit(1), risk_level=ref("a", "nope_field")),
        ], edges=[edge("e1", "a", "b")])
        codes = validate_dag(reg_dag, registry).codes()
        assert "unknown_parameter" in codes
        assert "binding_unknown_path" in codes
        assert "link_mismatch" not in codes          # 两条边契约本身匹配

    def test_binding_ref_to_unknown_node(self, registry):
        dag = make_dag(nodes=[
            node("b", "sk_alert_v1.0", risk_level=ref("ghost", "risk_level"))])
        assert "unknown_node" in validate_dag(dag, registry).codes()

    def test_param_binding_shape(self):
        with pytest.raises(ValidationError, match="literal"):
            ParamBinding(kind="literal")
        with pytest.raises(ValidationError, match="literal"):
            ParamBinding(kind="literal", value=1, path="x")
        with pytest.raises(ValidationError, match="from_node"):
            ParamBinding(kind="ref")
        assert ParamBinding(kind="literal", value=None).value is None


# ───────────────────────── flow_id 拼接规则 ─────────────────────────


class TestFlowIdRule:
    def test_parse_and_for(self):
        base, ver = parse_flow_id("wf_daily_brief_v2.3")
        assert (base, str(ver)) == ("wf_daily_brief", "2.3.0")
        assert base_of("wf_daily_brief_v2.3") == "wf_daily_brief"
        assert flow_id_for("wf_daily_brief", SemVer(major=2, minor=3)) == "wf_daily_brief_v2.3"

    @pytest.mark.parametrize("bad", ["wf_demo", "demo_v1.0", "sk_demo_v1.0", "wf__v1.0"])
    def test_bad_forms_rejected(self, bad):
        with pytest.raises(WorkflowValidationError):
            check_flow_id(bad)

    def test_bad_base_rejected(self):
        with pytest.raises(WorkflowValidationError, match="wf_ 前缀"):
            flow_id_for("demo", SemVer(major=1, minor=0))


# ───────────────────────── 落盘布局 ─────────────────────────


class TestPersistenceLayout:
    def test_dag_roundtrip_with_bindings(self, store, registry):
        """两种绑定（literal / ref）经加密落盘后逐字段还原。"""
        w = wstore(store, registry)
        dag = make_dag(nodes=[
            node("a", "sk_scan_v1.0", threshold=lit(0.8)),
            node("b", "sk_alert_v1.0", risk_level=ref("a", "risk_level")),
        ], edges=[edge("e1", "a", "b")])
        w.save(dag)
        assert w.get(FLOW) == dag
        loaded = w.get(FLOW).node("b").params["risk_level"]
        assert (loaded.kind, loaded.from_node, loaded.path) == (
            "ref", "a", "risk_level")

    def test_partition_prefixes_do_not_overlap(self, store, registry):
        from st_agent.l1.workflow import ACTIVE_PREFIX, WORKFLOW_PREFIX
        w = wstore(store, registry)
        w.save(make_dag(nodes=[node("a", "sk_scan_v1.0")]))
        w.activate("wf_demo", FLOW)
        names = set(store.list_files("config"))
        assert f"{WORKFLOW_PREFIX}{FLOW}.json" in names
        assert f"{ACTIVE_PREFIX}wf_demo.json" in names
        # 激活指针不被工作流扫描串入（前缀差一个字符）
        assert not ACTIVE_PREFIX.startswith(WORKFLOW_PREFIX)
        assert len(w.list_all()) == 1
