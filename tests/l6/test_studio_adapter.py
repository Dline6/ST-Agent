"""T-L6-004.1 测试：`SkillDraft`→`WorkflowDraft` 翻译与 Studio 交接受体。

GWT 对照（任务文件 5 条）：
- GWT-1 翻译形态对齐（四字段 + `workflow_draft` 子对象；身份字段不入）
- GWT-2 落画布交接（开画布会话并持住；形状非法 → `DraftShapeError`、画布不开）
- GWT-3 三态委托 L1（接受落 v1.0 + `change_id` / 微调返回同一句柄 / 拒绝不落盘）
- GWT-4 未注入即 fail-closed（点名，不伪造「已交 Studio」）
- GWT-5 文案中性（翻译不新造生成性文案）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from l6_helpers import NOW, PASS, SKILL_DRAFT, observation, observer
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.studio import (
    WORKFLOW_CHANGE_PREFIX,
    CanvasEditor,
    DraftIntake,
    DraftSessionError,
    DraftShapeError,
)
from st_agent.l6 import ProposalEngine, StudioHandoff, StudioHandoffError, to_workflow_draft

NEUTRAL = lambda _n: True                                      # noqa: E731


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    """注册草稿引用的 Skill（`SKILL_DRAFT` 的节点引用 `sk_valuation_check_v1.0`）。"""
    reg = SkillRegistry(store)
    reg.register(
        "sk_valuation_check", version="1.0", name="估值复核",
        description="对标的估值做复核并给出结论",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
    )
    return reg


@pytest.fixture()
def intake(store: Store, registry: SkillRegistry) -> DraftIntake:
    return DraftIntake(store, registry, name_check=NEUTRAL)


def _engine(store: Store, *items: dict) -> ProposalEngine:
    """检出提案的引擎（观察面注入确定性件；`registry=None` ⇒ 规则面不参与）。"""
    return ProposalEngine(store=store, observer=observer(*items), now=lambda: NOW)


def _one_proposal(store: Store, draft: object = None) -> tuple[ProposalEngine, str]:
    """检出恰好一条提案，返回（引擎, `proposal_id`）。"""
    extra = {} if draft is None else {"draft": draft}
    engine = _engine(store, observation(**extra))               # type: ignore[arg-type]
    run = engine.detect(now=NOW)
    assert run.enabled is True and len(run.proposals) == 1
    return engine, run.proposals[0].proposal_id


# ───────────────────────── GWT-1 翻译 ─────────────────────────


def test_gwt1_translation_shape(store: Store) -> None:
    engine, pid = _one_proposal(store)
    proposal = engine.get(pid)
    assert proposal is not None

    draft = to_workflow_draft(proposal)

    assert draft.target == proposal.draft.name
    assert draft.understanding_summary == proposal.reason        # 同源，不新造
    assert draft.parameter_draft == {}
    assert draft.open_questions == ()
    payload = draft.workflow_draft
    assert payload is not None
    assert set(payload) == {"name", "description", "nodes", "flow_name"}  # 空者不携
    assert payload["nodes"] == [dict(SKILL_DRAFT["nodes"][0])]
    # 身份字段**不入**（由 Studio「接受」派生）
    assert "flow_id" not in payload and "version" not in payload


def test_gwt1_translation_spreads_edges_groups_schedule(store: Store) -> None:
    draft_payload = {
        "name": "组合流程",
        "description": "含连线 / 分组 / 调度的工作流草稿",
        "nodes": ({"node_id": "a", "skill_id": "sk_x_v1.0"},
                  {"node_id": "b", "skill_id": "sk_y_v1.0"}),
        "edges": ({"edge_id": "e1", "from_node": "a", "to_node": "b"},),
        "groups": ({"group_id": "g1", "node_ids": ["a", "b"]},),
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "flow_name": "combined",
    }
    engine, pid = _one_proposal(store, draft_payload)
    proposal = engine.get(pid)
    assert proposal is not None

    payload = to_workflow_draft(proposal).workflow_draft
    assert set(payload) == {"name", "description", "nodes", "edges", "groups",
                            "schedule", "flow_name"}


# ───────────────────────── GWT-2 落画布交接 ─────────────────────────


def test_gwt2_handoff_opens_and_holds_session(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)

    view = handoff.handoff(pid)

    assert view.proposal_id == pid
    assert view.base == "wf_valuation_review"
    assert view.flow_id.endswith("_v0.0")                       # 画布期临时身份
    assert view.node_count == 1
    assert view.violations == ()                                # 引用已注册 Skill ⇒ 无语义违规
    assert handoff.session(pid) is not None and handoff.opened() == (pid,)


def test_gwt2_handoff_is_idempotent_for_open_session(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    handoff.handoff(pid)
    first = handoff.session(pid)
    handoff.handoff(pid)
    assert handoff.session(pid) is first                        # 同一会话，不重置


def test_gwt2_semantic_violations_do_not_block_canvas(store: Store) -> None:
    """Skill **未**注册 ⇒ 语义违规随视图带回，但画布**照开**（L1 两段口径）。"""
    empty = SkillRegistry(store)                                # 不注册任何 Skill
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(
        intake=DraftIntake(store, empty, name_check=NEUTRAL), proposals=engine,
    )
    view = handoff.handoff(pid)
    assert view.node_count == 1
    assert view.violations                                      # 非空：引用未注册的 Skill
    assert handoff.session(pid) is not None


def test_gwt2_shape_invalid_does_not_open_canvas(store: Store, intake: DraftIntake) -> None:
    """草稿节点缺 `skill_id` ⇒ 无法构造 `WorkflowDAG` ⇒ `DraftShapeError`、会话不登记。"""
    broken = {
        "name": "坏草稿",
        "description": "节点结构非法",
        "nodes": ({"node_id": "n1"},),                           # 缺 skill_id
        "flow_name": "broken",
    }
    engine, pid = _one_proposal(store, broken)
    handoff = StudioHandoff(intake=intake, proposals=engine)

    with pytest.raises(DraftShapeError):
        handoff.handoff(pid)
    assert handoff.session(pid) is None and handoff.opened() == ()


# ───────────────────────── GWT-3 三态委托 L1 ─────────────────────────


def test_gwt3_accept_lands_v1_with_change_id(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    handoff.handoff(pid)

    result = handoff.accept(pid)

    assert result.flow_id.endswith("_v1.0")
    assert result.change_id.startswith("chg_")
    assert f"{WORKFLOW_CHANGE_PREFIX}{result.change_id}.json" in store.list_files("execution_log")


def test_gwt3_tune_returns_same_editor(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    handoff.handoff(pid)

    editor = handoff.tune(pid)
    assert isinstance(editor, CanvasEditor)
    assert editor is handoff.session(pid).editor                 # 同一句柄，会话不分裂


def test_gwt3_reject_leaves_no_trace(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    handoff.handoff(pid)

    result = handoff.reject(pid, reason="暂不需要")

    assert result.rejected is True and result.reason == "暂不需要"
    assert handoff.opened() == ()                                # 会话已闭
    assert not [f for f in store.list_files("config") if f.startswith("workflow/")]


def test_gwt3_double_accept_is_explicitly_rejected(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    handoff.handoff(pid)
    handoff.accept(pid)

    with pytest.raises(DraftSessionError):
        handoff.accept(pid)                                      # 已闭会话，L1 显式拒


# ───────────────────────── GWT-4 fail-closed ─────────────────────────


def test_gwt4_missing_intake_is_fail_closed(store: Store) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(proposals=engine)                    # 未接接收面
    assert handoff.available is False
    with pytest.raises(StudioHandoffError):
        handoff.handoff(pid)


def test_gwt4_missing_proposals_is_fail_closed(intake: DraftIntake) -> None:
    handoff = StudioHandoff(intake=intake)                       # 未接提案读面
    with pytest.raises(StudioHandoffError):
        handoff.handoff("prop_x")


def test_gwt4_unknown_proposal_is_named(store: Store, intake: DraftIntake) -> None:
    engine, _pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    with pytest.raises(StudioHandoffError, match="提案不存在"):
        handoff.handoff("prop_missing")


def test_gwt4_decide_before_handoff_is_named(store: Store, intake: DraftIntake) -> None:
    engine, pid = _one_proposal(store)
    handoff = StudioHandoff(intake=intake, proposals=engine)
    with pytest.raises(StudioHandoffError, match="尚未交 Studio"):
        handoff.accept(pid)


# ───────────────────────── GWT-5 文案中性 ─────────────────────────


def test_gwt5_understanding_summary_falls_back_to_description(store: Store) -> None:
    """提案无 `reason`（观察面没给）⇒ 回落到草稿说明（同为已过 §6 的文案）。"""
    engine = ProposalEngine(
        store=store, observer=observer(observation(reason="")), now=lambda: NOW,
    )
    pid = engine.detect(now=NOW).proposals[0].proposal_id
    proposal = engine.get(pid)
    assert proposal is not None and proposal.reason == ""

    draft = to_workflow_draft(proposal)
    assert draft.understanding_summary == proposal.draft.description
