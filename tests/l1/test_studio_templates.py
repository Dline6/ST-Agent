"""T-L1-003.5 测试：03 §4 模板库与空状态。

GWT 对照（任务文件 4 条）：
- GWT-1 模板库浏览：至少含「每日 ST 简报」「退市风险扫描」「策略回测流水线」，
  每条含名称 / 说明 / 节点数 / 引用 Skill 清单
- GWT-2 fork：生成用户自有副本（新 ``flow_id``），模板本身**逐字段不变**
- GWT-3 加载即走依赖提示：引用未注册时，浏览 / fork / 加载**三条路径都**列出
  缺失 ``skill_id``，不静默通过
- GWT-4 空状态：无任何工作流时返回「模板库 + 一句话入口」的结构化对象，
  不外抛异常、不返回空对象

假设复核对照：A1 纯数据种子 / A2 官方 Pack 播种后可解析 / A3 去重加序号 /
A4 判据取 list_all 为空 / A5 模板名过 ≠6 中性化 / A6 template_id 不落盘 /
A7 装载期校验 + 深拷贝
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l1.studio import (
    OFFICIAL_TEMPLATES,
    STUDIO_ONE_LINE_ENTRY,
    CanvasEditor,
    StudioHome,
    TemplateError,
    TemplateLibrary,
    TemplateSummary,
    studio_empty_state,
)
from st_agent.l1.workflow import WorkflowStore
from st_agent.l1.workflow.composite import (
    CompositeDependencyError,
    save_as_composite_skill,
)
from st_agent.l1.workflow.validate import validate_dag

PASS = "correct horse battery staple"

_NAMED = {
    "daily_st_brief": "每日 ST 简报",
    "delisting_risk_scan": "退市风险扫描",
    "strategy_backtest_pipeline": "策略回测流水线",
}


# ───────────────────────── 夹具与构件 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def empty_registry(store: Store) -> SkillRegistry:
    """**未播种**任何 Skill 的注册表（GWT-3 的缺失面）。"""
    return SkillRegistry(store)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    """播种官方 Pack 后的注册表（A2：模板引用可解析）。"""
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


def _pure_data(value) -> bool:
    """种子项是否为**纯数据**（A1：无对象 / 无回调，可随包发布）。"""
    if isinstance(value, dict):
        return all(isinstance(k, str) and _pure_data(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_pure_data(v) for v in value)
    return isinstance(value, (str, int, float, bool)) or value is None


# ───────────────────────── GWT-1 浏览 ─────────────────────────


def test_gwt1_lists_three_named_official_templates(registry: SkillRegistry,
                                                   store: Store) -> None:
    """GWT-1：官方预置模板至少含题面点名的三条，且名称逐字对齐。"""
    summaries = TemplateLibrary(store, registry).list()
    by_id = {s.template_id: s for s in summaries}
    for template_id, name in _NAMED.items():
        assert template_id in by_id, f"缺模板 {template_id}"
        assert by_id[template_id].name == name


def test_gwt1_summary_carries_name_description_node_count_and_skills(
        registry: SkillRegistry, store: Store) -> None:
    """GWT-1：每条摘要含 名称 / 说明 / 节点数 / 引用 Skill 清单。"""
    library = TemplateLibrary(store, registry)
    for summary in library.list():
        assert isinstance(summary, TemplateSummary)
        assert summary.name and summary.description
        assert summary.node_count >= 1
        assert summary.skill_ids, f"{summary.template_id} 未列出引用 Skill"
        # 节点数与真身一致（摘要不是另算的一套口径）
        assert summary.node_count == len(library.load(summary.template_id).nodes)


def test_gwt1_seed_names_and_graphs_pass_dag_validation(
        registry: SkillRegistry, store: Store) -> None:
    """A2 / A5：官方 Pack 播种后，每条模板**整体**过 ``validate_dag``（含中性化）。"""
    library = TemplateLibrary(store, registry)
    for summary in library.list():
        dag = library.load(summary.template_id)
        result = validate_dag(dag, registry)
        assert result.ok, f"{summary.template_id} 未过校验：{result.describe()}"
        assert summary.missing_skill_ids == ()


def test_a1_seeds_are_pure_data() -> None:
    """A1：种子是纯数据（可随包发布、不依赖网络 / 不携带对象）。"""
    assert OFFICIAL_TEMPLATES
    assert all(_pure_data(seed) for seed in OFFICIAL_TEMPLATES)


# ───────────────────────── GWT-2 fork ─────────────────────────


def test_gwt2_fork_yields_user_owned_copy_with_new_identity(
        registry: SkillRegistry, store: Store) -> None:
    """GWT-2：fork 产**新 ``flow_id``** 的自有副本，节点连线参数齐备。"""
    library = TemplateLibrary(store, registry)
    forked = library.fork("daily_st_brief")
    assert forked.flow_id == "wf_daily_st_brief_v1.0"
    preview = library.load("daily_st_brief")
    assert forked.flow_id != preview.flow_id          # 身份是新的
    assert forked.name == preview.name                # 正文逐字段取自模板
    assert (len(forked.nodes), len(forked.edges)) == (len(preview.nodes),
                                                      len(preview.edges))
    assert forked.node("brief").params["format"].value == "brief"


def test_gwt2_template_itself_is_field_by_field_unchanged(
        registry: SkillRegistry, store: Store) -> None:
    """GWT-2：fork 之后模板**逐字段不变**（正文与种子两侧都核）。"""
    library = TemplateLibrary(store, registry)
    before = library.load("daily_st_brief")
    snapshot = tuple(dict(seed) for seed in OFFICIAL_TEMPLATES)
    library.fork("daily_st_brief")
    library.fork("daily_st_brief")
    assert library.load("daily_st_brief") == before
    assert tuple(dict(seed) for seed in OFFICIAL_TEMPLATES) == snapshot


def test_gwt2_fork_twice_does_not_clobber_the_first(
        registry: SkillRegistry, store: Store) -> None:
    """A3：base 已存在即加序号；先落的副本不被后 fork 的冲掉。"""
    library = TemplateLibrary(store, registry)
    workflows = WorkflowStore(store, registry)
    first = library.fork("daily_st_brief")
    workflows.save(first)                              # 模拟「接受」落盘
    second = library.fork("daily_st_brief")
    workflows.save(second)
    assert second.flow_id == "wf_daily_st_brief_2_v1.0"
    assert workflows.get("wf_daily_st_brief_v1.0") == first
    third = library.fork("daily_st_brief")
    assert third.flow_id == "wf_daily_st_brief_3_v1.0"


def test_gwt2_fork_copy_does_not_share_mutable_state_with_template(
        registry: SkillRegistry, store: Store) -> None:
    """A7：fork 与模板**不共享**可变状态（正文深拷贝）——用自定义种子验证。"""
    seed = dict(
        template_id="custom", flow_name="custom_flow", name="自定义流程",
        description="用于验证深拷贝的模板",
        nodes=(dict(node_id="n1", skill_id="sk_st_list_sync_v1.0",
                    params={"exchange": {"kind": "literal",
                                         "value": {"scope": "all"}}}),),
        edges=(), groups=(), schedule={"mode": "manual"},
    )
    library = TemplateLibrary(store, registry, seeds=(seed,))
    first = library.fork("custom")
    first.node("n1").params["exchange"].value["scope"] = "sh"   # 就地改副本
    assert library.fork("custom").node("n1").params["exchange"].value == {
        "scope": "all"}
    assert library.load("custom").node("n1").params["exchange"].value == {
        "scope": "all"}


def test_gwt2_forked_copy_is_editable_in_canvas(registry: SkillRegistry,
                                                store: Store) -> None:
    """GWT-2「可编辑」：fork 产物可直接进 ``CanvasEditor`` 继续改。"""
    forked = TemplateLibrary(store, registry).fork("delisting_risk_scan")
    editor = CanvasEditor(forked, registry)
    result = editor.add_node("extra", "sk_sector_heatmap_v1.0")
    assert result.applied
    assert editor.dag.node("extra") is not None
    assert len(editor.dag.nodes) == 5


def test_gwt2_forked_copy_can_be_saved_as_composite_skill(
        registry: SkillRegistry, store: Store) -> None:
    """GWT-2「可保存为复合 Skill」：fork 产物可直接物化为命名 Skill（§3.2）。"""
    forked = TemplateLibrary(store, registry).fork("daily_st_brief")
    descriptor = save_as_composite_skill(
        forked, registry, name="每日 ST 简报流程",
        description="把每日 ST 简报模板固化为命名工作流")
    assert descriptor.skill_id == "sk_daily_st_brief_v1.0"
    assert descriptor.source == "user-built"


# ───────────────────────── GWT-3 依赖提示 ─────────────────────────


def test_gwt3_browse_reports_missing_skills(empty_registry: SkillRegistry,
                                            store: Store) -> None:
    """GWT-3（浏览面）：未注册的 Skill 逐条列出，不静默。"""
    for summary in TemplateLibrary(store, empty_registry).list():
        assert summary.missing_skill_ids == summary.skill_ids


def test_gwt3_fork_and_load_refuse_with_gaps(empty_registry: SkillRegistry,
                                             store: Store) -> None:
    """GWT-3（fork / 加载面）：缺失即**显式失败**并携逐条缺口，不静默通过。"""
    library = TemplateLibrary(store, empty_registry)
    for action in (library.fork, library.load):
        with pytest.raises(CompositeDependencyError) as excinfo:
            action("daily_st_brief")
        gaps = excinfo.value.gaps
        assert [g.skill_id for g in gaps] == [
            "sk_st_list_sync_v1.0", "sk_delisting_risk_scan_v1.0",
            "sk_sector_heatmap_v1.0", "sk_data_aggregate_v1.0"]
        assert all(g.node_id and g.reason for g in gaps)


def test_gwt3_gaps_disappear_once_pack_is_seeded(empty_registry: SkillRegistry,
                                                 store: Store) -> None:
    """GWT-3 对照组：播种官方 Pack 后同一模板缺口清空（A2）。"""
    library = TemplateLibrary(store, empty_registry)
    with pytest.raises(CompositeDependencyError):
        library.load("strategy_backtest_pipeline")
    ensure_official_pack(empty_registry)
    assert library.get("strategy_backtest_pipeline").missing_skill_ids == ()
    assert library.load("strategy_backtest_pipeline").flow_id.endswith("_v0.0")


def test_templates_depend_on_official_pack_only(registry: SkillRegistry,
                                               store: Store) -> None:
    """A2：模板只引用官方 Pack 的 skill_id——播种后无一缺失。"""
    official = {s.skill_id for s in registry.list_all()}
    for summary in TemplateLibrary(store, registry).list():
        assert set(summary.skill_ids) <= official


# ───────────────────────── GWT-4 空状态 ─────────────────────────


def test_gwt4_empty_state_returns_templates_and_one_line_entry(
        registry: SkillRegistry, store: Store) -> None:
    """GWT-4：无任何工作流时返回结构化空状态（模板库 + 一句话入口）。"""
    home = studio_empty_state(store, registry)
    assert isinstance(home, StudioHome)
    assert home.is_empty is True
    assert len(home.templates) == len(OFFICIAL_TEMPLATES)
    assert home.one_line_entry == STUDIO_ONE_LINE_ENTRY
    assert home.one_line_entry                       # 非空串，非 None


def test_gwt4_templates_are_returned_even_when_not_empty(
        registry: SkillRegistry, store: Store) -> None:
    """A4：判据是「未创建任何工作流」；非空时清单**恒返回**（复用同一数据源）。"""
    library = TemplateLibrary(store, registry)
    WorkflowStore(store, registry).save(library.fork("daily_st_brief"))
    home = studio_empty_state(store, registry)
    assert home.is_empty is False
    assert len(home.templates) == len(OFFICIAL_TEMPLATES)


def test_a6_template_id_never_reaches_storage(registry: SkillRegistry,
                                              store: Store) -> None:
    """A6 + fork 不落盘：浏览 / 加载 / fork 三面都不写存储，``template_id`` 不派生身份。"""
    library = TemplateLibrary(store, registry)
    before = store.list_files("config")
    library.list()
    library.load("daily_st_brief")
    library.fork("daily_st_brief")
    studio_empty_state(store, registry)
    assert store.list_files("config") == before
    # template_id 只是进程内寻址键——身份由 flow_name 派生，故两者刻意取不同值
    seed = dict(template_id="tpl_alias", flow_name="real_flow", name="别名流程",
                description="验证 template_id 不作为身份来源",
                nodes=(dict(node_id="n1", skill_id="sk_st_list_sync_v1.0"),),
                edges=(), groups=(), schedule={"mode": "manual"})
    aliased = TemplateLibrary(store, registry, seeds=(seed,))
    assert aliased.fork("tpl_alias").flow_id == "wf_real_flow_v1.0"
    assert aliased.load("tpl_alias").flow_id == "wf_real_flow_v0.0"


# ───────────────────────── 装载期校验（A5 / A7） ─────────────────────────


def test_a7_bad_seed_fails_at_load_not_at_runtime(registry: SkillRegistry,
                                                 store: Store) -> None:
    """A7 / A5：种子有错在**装载期**即失败（三种坏种子各一）。"""
    bad_cases = {
        "非中性命名": dict(template_id="x", flow_name="x", name="激进派大师",
                          description="d", nodes=(
                              dict(node_id="n1", skill_id="sk_st_list_sync_v1.0"),),
                          edges=(), groups=(), schedule={"mode": "manual"}),
        "含无法识别字段": dict(template_id="x", flow_name="x", name="流程",
                            description="d", mystery=1, nodes=(
                                dict(node_id="n1", skill_id="sk_st_list_sync_v1.0"),),
                            edges=(), groups=(), schedule={"mode": "manual"}),
        "成环": dict(template_id="x", flow_name="x", name="流程", description="d",
                    nodes=(dict(node_id="n1", skill_id="sk_st_list_sync_v1.0"),
                           dict(node_id="n2", skill_id="sk_st_list_sync_v1.0")),
                    edges=(dict(edge_id="e1", from_node="n1", to_node="n2"),
                           dict(edge_id="e2", from_node="n2", to_node="n1")),
                    groups=(), schedule={"mode": "manual"}),
    }
    for label, seed in bad_cases.items():
        with pytest.raises(TemplateError):
            TemplateLibrary(store, registry, seeds=(seed,))


def test_a7_empty_seed_tuple_is_rejected(registry: SkillRegistry,
                                         store: Store) -> None:
    """A7：「可增不可缺」——空种子集不是合法的官方模板库。"""
    with pytest.raises(TemplateError):
        TemplateLibrary(store, registry, seeds=())


def test_unknown_template_id_is_an_explicit_error(registry: SkillRegistry,
                                                  store: Store) -> None:
    """标识不存在 → ``TemplateError``（不返回 ``None``、不静默）。"""
    library = TemplateLibrary(store, registry)
    for action in (library.load, library.fork, library.get):
        with pytest.raises(TemplateError):
            action("no_such_template")
