"""T-L1-003.4 测试：03 §4 编辑面（画布 mutation + 草稿落画布）。

GWT 对照（任务文件 5 条）：
- GWT-1 画布 mutation：增/删节点、连线/断线、分组/解组的结果可由 ``WorkflowDAG``
  读回，且每次操作后自动跑一次校验并随结果返回
- GWT-2 连线即时契约提示：上游输出与下游输入声明不匹配 → 返回「这里需要一个
  XX 类型的输入」式提示且**该连线不生效**（画布状态不变）
- GWT-3 草稿落画布：L3 草稿 → 可编辑的 ``WorkflowDAG`` 草稿（节点/连线/参数齐备）
  且先过一遍校验；**语义不合规的草稿同样可落**并带回违规清单
- GWT-4 接受 / 微调 / 拒绝：接受 → 落盘 v1.0 + ``change_id``；微调 → 编辑面句柄；
  拒绝 → 丢弃且可选记录原因
- GWT-5 成环即时阻止：会闭合环的连线被拒并**指出环路径**（编辑期即拦）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from st_agent.contracts.registry_types import SemVer
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.studio import (
    WORKFLOW_CHANGE_PREFIX,
    CanvasEditor,
    DraftAcceptError,
    DraftIntake,
    DraftSessionError,
    DraftShapeError,
    WorkflowDraft,
    slugify,
    workflow_base,
)
from st_agent.l1.workflow import (
    ParamBinding,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowGroup,
    WorkflowNode,
    checked_dag,
)

PASS = "correct horse battery staple"
NAME = "每日风险简报"
NEUTRAL = lambda _n: True                                      # noqa: E731
DESCRIPTION = "每日扫描退市风险并产出风险简报"


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
                                "properties": {"risk_level": {"type": "string"}}})
    reg.register("sk_alert", version="1.0", name="风险预警",
                 description="按风险等级产出预警条目",
                 input_schema={"type": "object", "required": ["risk_level"],
                               "properties": {"risk_level": {"type": "string"}}},
                 output_schema={"type": "object",
                                "properties": {"alerts": {"type": "array"}}},
                 parameters=(dict(name="risk_level", type="string", default="low",
                                  description="触发预警的风险等级"),))
    reg.register("sk_urgent", version="1.0", name="紧急性评估",
                 description="按紧急程度产出评估结论",
                 input_schema={"type": "object", "required": ["urgency"],
                               "properties": {"urgency": {"type": "string"}}},
                 output_schema={"type": "object",
                                "properties": {"score": {"type": "number"}}})
    return reg


def _dag(*nodes: WorkflowNode, edges: tuple[WorkflowEdge, ...] = (),
         groups: tuple[WorkflowGroup, ...] = (), version: str = "0.0") -> WorkflowDAG:
    """造一条草稿态工作流（画布期临时身份 ``v0.0``）。"""
    return checked_dag(
        flow_id=f"wf_demo_v{version}", name=NAME, description=DESCRIPTION,
        version=SemVer.parse(version), nodes=nodes, edges=edges, groups=groups)


def _node(node_id: str, skill_id: str, **params: ParamBinding) -> WorkflowNode:
    return WorkflowNode(node_id=node_id, skill_id=skill_id, params=params)


def _edge(edge_id: str, from_node: str, to_node: str) -> WorkflowEdge:
    return WorkflowEdge(edge_id=edge_id, from_node=from_node, to_node=to_node)


def _draft(*, payload: dict | None = None, **fields) -> WorkflowDraft:
    base = {
        "name": NAME,
        "description": DESCRIPTION,
        "flow_name": "demo",
        "nodes": [
            {"node_id": "scan", "skill_id": "sk_scan_v1.0"},
            {"node_id": "alert", "skill_id": "sk_alert_v1.0",
             "params": {"risk_level": {"kind": "ref", "from_node": "scan",
                                       "path": "risk_level"}}},
        ],
        "edges": [{"edge_id": "e1", "from_node": "scan", "to_node": "alert"}],
    }
    if payload is not None:
        base = payload
    return WorkflowDraft(
        target="工作流：每日风险简报",
        understanding_summary="我理解到 2 件事",
        workflow_draft=base,
        **fields)


def _editor(dag: WorkflowDAG, registry=None) -> CanvasEditor:
    return CanvasEditor(dag, registry, name_check=NEUTRAL)


def _intake(store: Store, registry) -> DraftIntake:
    return DraftIntake(store, registry, name_check=NEUTRAL)


# ───────────────────────── GWT-1 画布 mutation ─────────────────────────


def test_gwt1_add_node_round_trips_and_revalidates(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0")), registry)
    result = editor.add_node("alert", "sk_alert_v1.0")

    assert result.applied is True
    assert editor.dag.node("alert") is not None            # 结果可由 DAG 读回
    assert result.dag is editor.dag
    assert result.validation.ok is True                    # 操作后自动跑了一次校验
    assert result.blocked_by == ()


def test_gwt1_add_node_rejects_duplicate_and_bad_id(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0")), registry)
    before = editor.dag

    dup = editor.add_node("scan", "sk_alert_v1.0")
    assert dup.applied is False and editor.dag == before
    assert "已存在" in dup.message

    bad = editor.add_node("Scan!", "sk_alert_v1.0")
    assert bad.applied is False and editor.dag == before
    assert bad.message


def test_gwt1_remove_node_cascades_all_references(registry) -> None:
    """删节点连带移除**两类依赖**：连线与 ``ref`` 绑定，外加分组成员位。"""
    editor = _editor(_dag(
        _node("scan", "sk_scan_v1.0"),
        _node("alert", "sk_alert_v1.0",
              risk_level=ParamBinding(kind="ref", from_node="scan",
                                      path="risk_level")),
        _node("urgent", "sk_urgent_v1.0"),
        edges=(_edge("e1", "scan", "alert"), _edge("e2", "alert", "urgent")),
        groups=(WorkflowGroup(group_id="g1", name="扫描段",
                              node_ids=("scan", "alert")),),
    ), registry)

    result = editor.remove_node("scan")

    assert result.applied is True
    assert editor.dag.node("scan") is None
    assert [e.edge_id for e in editor.dag.edges] == ["e2"]        # 入射边一并移除
    alert = editor.dag.node("alert")
    assert alert is not None and alert.params == {}              # ref 绑定一并摘除
    assert editor.dag.groups[0].node_ids == ("alert",)           # 分组成员位一并移除
    # 依赖图不留悬空引用（无 unknown_node / cycle；alert→urgent 的契约缺口与本次删除无关）
    codes = result.validation.codes()
    assert "unknown_node" not in codes and "cycle" not in codes


def test_gwt1_remove_missing_node_is_noop(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0")), registry)
    result = editor.remove_node("nope")
    assert result.applied is False
    assert result.dag == editor.dag
    assert "不存在" in result.message


def test_gwt1_disconnect_group_ungroup(registry) -> None:
    editor = _editor(_dag(
        _node("scan", "sk_scan_v1.0"), _node("alert", "sk_alert_v1.0"),
        edges=(_edge("e1", "scan", "alert"),)), registry)

    grouped = editor.group("g1", "扫描段", ("scan", "alert"))
    assert grouped.applied is True
    assert editor.dag.groups[0].node_ids == ("scan", "alert")

    ungrouped = editor.ungroup("g1")
    assert ungrouped.applied is True and editor.dag.groups == ()

    cut = editor.disconnect("e1")
    assert cut.applied is True and editor.dag.edges == ()

    assert editor.disconnect("e1").applied is False       # 已不存在
    assert editor.ungroup("g1").applied is False


def test_gwt1_group_rejects_unknown_member(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0")), registry)
    result = editor.group("g1", "扫描段", ("scan", "ghost"))
    assert result.applied is False
    assert "ghost" in result.message and editor.dag.groups == ()


def test_gwt1_failed_operation_reports_current_validation(registry) -> None:
    """被拒操作也返回校验结论——只是结论属于**未变**的画布。"""
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0"),
                          _node("ghost", "sk_nowhere_v1.0")), registry)
    result = editor.add_node("scan", "sk_alert_v1.0")
    assert result.applied is False
    assert "unknown_skill" in result.validation.codes()     # 既有语义问题照常回显


# ───────────────────────── GWT-2 连线即时契约提示 ─────────────────────────


def test_gwt2_mismatched_link_is_refused_with_hint(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0"),
                          _node("urgent", "sk_urgent_v1.0")), registry)
    before = editor.dag

    result = editor.connect("e1", "scan", "urgent")

    assert result.applied is False
    assert editor.dag == before                            # 画布状态不变
    assert result.dag.edges == ()
    assert "这里需要一个 string 类型的输入" in result.message
    assert [i.code for i in result.blocked_by] == ["link_mismatch"]


def test_gwt2_matched_link_applies(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0"),
                          _node("alert", "sk_alert_v1.0")), registry)
    result = editor.connect("e1", "scan", "alert")
    assert result.applied is True
    assert [e.edge_id for e in editor.dag.edges] == ["e1"]
    assert result.validation.ok is True


def test_gwt2_unknown_node_is_refused(registry) -> None:
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0")), registry)
    result = editor.connect("e1", "scan", "ghost")
    assert result.applied is False and "ghost" in result.message


def test_gwt2_without_registry_link_check_is_skipped(registry) -> None:
    """无注册表 → 不判契约（无法判定即不臆测），连线照常生效。"""
    editor = _editor(_dag(_node("scan", "sk_scan_v1.0"),
                          _node("urgent", "sk_urgent_v1.0")))
    assert editor.connect("e1", "scan", "urgent").applied is True


# ───────────────────────── GWT-5 成环即时阻止 ─────────────────────────


def test_gwt5_cycle_is_refused_with_path(registry) -> None:
    editor = _editor(_dag(_node("a", "sk_scan_v1.0"),
                          _node("b", "sk_alert_v1.0"),
                          edges=(_edge("e1", "a", "b"),)), registry)
    before = editor.dag

    result = editor.connect("e2", "b", "a")

    assert result.applied is False and editor.dag == before
    assert "b → a → b" in result.message                    # 指出环路径
    assert [i.code for i in result.blocked_by] == ["cycle"]
    assert [i.path for i in result.blocked_by] == ["edges[e2]"]


def test_gwt5_self_loop_is_refused(registry) -> None:
    editor = _editor(_dag(_node("a", "sk_scan_v1.0")), registry)
    result = editor.connect("e1", "a", "a")
    assert result.applied is False
    assert "a → a" in result.message


def test_gwt5_cycle_through_ref_binding_is_refused(registry) -> None:
    """绕开显式连线的环同样被拦：``b`` 引用 ``a`` 的输出，再连 ``b → a``。"""
    editor = _editor(_dag(
        _node("a", "sk_scan_v1.0"),
        _node("b", "sk_alert_v1.0",
              risk_level=ParamBinding(kind="ref", from_node="a",
                                      path="risk_level")),
    ), registry)
    result = editor.connect("e1", "b", "a")
    assert result.applied is False
    assert "b → a → b" in result.message


def test_gwt5_preexisting_cycle_does_not_block_other_links(registry) -> None:
    """草稿既有的语义问题不阻断后续编辑（03 §4 非法草稿边界口径）。"""
    editor = _editor(_dag(_node("a", "sk_scan_v1.0"),
                          _node("b", "sk_alert_v1.0"),
                          _node("c", "sk_alert_v1.0"),
                          edges=(_edge("e1", "a", "b"),
                                 _edge("e2", "b", "a"))), registry)
    assert "cycle" in editor.validate_current().codes()

    result = editor.connect("e3", "a", "c")
    assert result.applied is True                           # 与既有环无关 → 放行
    assert "cycle" in result.validation.codes()             # 既有环仍在清单里


# ───────────────────────── GWT-3 草稿落画布 ─────────────────────────


def test_gwt3_receive_lands_draft_on_canvas(store, registry) -> None:
    session = _intake(store, registry).receive(_draft())

    assert session.status == "editing"
    assert {n.node_id for n in session.dag.nodes} == {"scan", "alert"}
    assert [e.edge_id for e in session.dag.edges] == ["e1"]
    assert session.dag.node("alert").params["risk_level"].kind == "ref"
    assert session.dag.flow_id == "wf_demo_v0.0"            # 画布期临时身份
    assert session.base == "wf_demo"
    assert session.validation.ok is True
    assert store.list_files("config").count("workflow/wf_demo_v0.0.json") == 0


def test_gwt3_noncompliant_draft_still_lands_with_issues(store, registry) -> None:
    """语义不合规（成环 / 未注册 / 命名不中性）照开画布，清单带回。"""
    payload = {
        "name": NAME,
        "description": DESCRIPTION,
        "flow_name": "demo",
        "nodes": [
            {"node_id": "a", "skill_id": "sk_scan_v1.0"},
            {"node_id": "b", "skill_id": "sk_nowhere_v1.0"},
        ],
        "edges": [{"edge_id": "e1", "from_node": "a", "to_node": "b"},
                  {"edge_id": "e2", "from_node": "b", "to_node": "a"}],
    }
    intake = DraftIntake(store, registry, name_check=lambda _n: False)
    session = intake.receive(_draft(payload=payload))

    codes = session.validation.codes()
    assert "cycle" in codes and "unknown_skill" in codes
    assert "name_not_neutral" in codes
    assert session.editor is not None                       # 画布已开、可继续编辑
    assert session.editor.connect("e3", "a", "b").action == "connect"


@pytest.mark.parametrize("payload, why", [
    ({"description": DESCRIPTION}, "缺 name"),
    ({"name": NAME}, "缺 description"),
    ({"name": NAME, "description": DESCRIPTION,
      "nodes": [{"node_id": "Bad!", "skill_id": "sk_scan_v1.0"}]}, "非法 node_id"),
    ({"name": NAME, "description": DESCRIPTION, "flow_id": "wf_x_v1.0"}, "带身份字段"),
    ({"name": NAME, "description": DESCRIPTION, "version": {"major": 1,
                                                            "minor": 0}},
     "带身份字段"),
    ({"name": NAME, "description": DESCRIPTION, "nodez": []}, "无法识别字段"),
    ({"name": "每日风险简报", "description": DESCRIPTION}, "无法派生注册名"),
])
def test_gwt3_shape_invalid_draft_is_refused(store, registry, payload, why) -> None:
    """形状非法 → 显式拒、画布不开。"""
    intake = _intake(store, registry)
    with pytest.raises(DraftShapeError):
        intake.receive(_draft(payload=payload))


def test_gwt3_receive_without_workflow_payload_is_refused(store, registry) -> None:
    with pytest.raises(DraftShapeError):
        _intake(store, registry).receive(_draft(payload={}))


def test_gwt3_explicit_flow_name_wins(store, registry) -> None:
    session = _intake(store, registry).receive(_draft(payload={
        "name": "每日风险简报", "description": DESCRIPTION,
        "flow_name": "daily_risk_brief",
        "nodes": [{"node_id": "scan", "skill_id": "sk_scan_v1.0"}]}))
    assert session.base == "wf_daily_risk_brief"


# ───────────────────────── GWT-4 接受 / 微调 / 拒绝 ─────────────────────────


def test_gwt4_accept_saves_v1_and_records_change(store, registry) -> None:
    intake = _intake(store, registry)
    session = intake.receive(_draft())

    result = intake.accept(session, trace_id="trace-demo")

    assert result.flow_id == "wf_demo_v1.0"
    assert result.dag.version.major == 1 and result.dag.version.minor == 0
    assert session.status == "accepted"
    # 落盘：工作流定义 + 变更留痕各一份
    assert "workflow/wf_demo_v1.0.json" in store.list_files("config")
    rel = f"{WORKFLOW_CHANGE_PREFIX}{result.change_id}.json"
    assert rel in store.list_files("execution_log")
    assert result.change.config_id == "wf_demo_v1.0"
    assert result.change.old_value is None
    assert result.change.trace_ref == "trace-demo"
    # 落盘内容可经 WorkflowStore 读回，且节点/连线齐备
    from st_agent.l1.workflow import WorkflowStore
    assert WorkflowStore(store, registry).get("wf_demo_v1.0").flow_id == "wf_demo_v1.0"


def test_gwt4_tune_returns_same_editor_and_edits_land(store, registry) -> None:
    intake = _intake(store, registry)
    session = intake.receive(_draft())

    handle = intake.tune(session)
    assert handle is session.editor                       # 同一个编辑面句柄，不分裂

    assert handle.add_node("urgent", "sk_urgent_v1.0").applied is True
    result = intake.accept(session)
    assert {n.node_id for n in result.dag.nodes} == {"scan", "alert", "urgent"}


def test_gwt4_reject_discards_without_persisting(store, registry) -> None:
    intake = _intake(store, registry)
    session = intake.receive(_draft())
    before = store.list_files("config")

    result = intake.reject(session, reason="节点划分不符合预期")

    assert result.rejected is True and result.reason == "节点划分不符合预期"
    assert session.status == "rejected"
    assert store.list_files("config") == before           # 不落盘
    assert all(not n.startswith("workflow/")
               for n in store.list_files("config"))


def test_gwt4_terminal_session_refuses_further_actions(store, registry) -> None:
    intake = _intake(store, registry)
    accepted = intake.receive(_draft())
    intake.accept(accepted)
    for call in (lambda: intake.accept(accepted),
                 lambda: intake.tune(accepted),
                 lambda: intake.reject(accepted)):
        with pytest.raises(DraftSessionError):
            call()

    rejected = intake.receive(_draft())
    intake.reject(rejected)
    with pytest.raises(DraftSessionError):
        intake.accept(rejected)


def test_gwt4_accept_refuses_existing_base(store, registry) -> None:
    intake = _intake(store, registry)
    intake.accept(intake.receive(_draft()))

    with pytest.raises(DraftAcceptError, match="已存在"):
        intake.accept(intake.receive(_draft()))
    assert "workflow/wf_demo_v1.1.json" not in store.list_files("config")


def test_gwt4_accept_refuses_semantically_invalid_canvas(store, registry) -> None:
    intake = _intake(store, registry)
    session = intake.receive(_draft(payload={
        "name": NAME, "description": DESCRIPTION, "flow_name": "demo",
        "nodes": [{"node_id": "a", "skill_id": "sk_nowhere_v1.0"}]}))
    assert session.status == "editing"                    # 不合规也能落画布
    with pytest.raises(DraftAcceptError, match="校验未通过"):
        intake.accept(session)
    assert store.list_files("config").count("workflow/wf_demo_v1.0.json") == 0


# ───────────────────────── 注册名派生 ─────────────────────────


def test_base_derivation_rules() -> None:
    assert workflow_base("每日 ST 简报") == "wf_st"       # 只留 ASCII 主干
    assert workflow_base("Daily Brief") == "wf_daily_brief"
    assert workflow_base("每日简报", "daily_brief") == "wf_daily_brief"
    assert slugify("  A  B  ") == "a_b"
    assert len(workflow_base("x" * 200)) == len("wf_") + 61      # 截到注册名上限
    assert re.match(r"^wf_[a-z0-9][a-z0-9_]{0,60}$", workflow_base("x" * 200))


def test_base_derivation_rejects_unsluggable_name() -> None:
    with pytest.raises(DraftAcceptError, match="无法派生注册名"):
        workflow_base("每日风险简报")
    with pytest.raises(DraftAcceptError, match="无法派生注册名"):
        workflow_base("多因子---回测")                     # 分隔符收拢后为空
    with pytest.raises(DraftAcceptError, match="flow_name"):
        workflow_base("每日风险简报", "Daily-Brief!")      # 非法 flow_name 形态
