"""T-L3-003 测试：对话即配置 Config Draft（[05 §5](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

叶覆盖：`.1` 契约与生成 + `configure` 去向接线（GWT-1..3）· `.2` 双通道登记面与
处置三态（GWT-1..4）· `.3` 工作流草稿交接（GWT-1..4）。

装配口径与 [`test_dispatch_bus.py`](test_dispatch_bus.py) 一致——`descriptors` 用**真 L1**
`SkillRegistry`（经真 `Store`），工作流分支用**真 L1** `DraftIntake` + `WorkflowStore`；
登记面（仓库尚无统一门面）用最小 **Fake** 端口，只满足
[`ConfigRegistryPort`](../../src/st_agent/l3/config/registry.py) 的语义。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.identifiers import ChangeId
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.studio import DraftIntake, DraftShapeError, WorkflowDraft
from st_agent.l3.config import (
    CONFIG_DRAFT_ABSENT_REASON,
    REGISTRY_ABSENT_REASON,
    WORKFLOW_BRANCH_ABSENT_REASON,
    ConfigDraft,
    ConfigDraftHandling,
    ConfigDraftProtocol,
    WorkflowDraftBuilder,
    dual_channel_view,
    handoff,
    is_workflow_draft,
)
from st_agent.l3.dispatch import ROUTE_BY_INTENT, DispatchBus
from st_agent.l3.errors import ConfigValidationError
from st_agent.l3.intent import ConfirmationItem, checked_confirmation

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=CST)

BASE = "sk_probe_aggregate"
SKILL = f"{BASE}_v1.0"
NEUTRAL = lambda name: NeutralityGuard().check_name(name).passed  # noqa: E731

PARAMS = (
    ParameterSpec(name="window_days", type="integer", default=30,
                  description="统计窗口天数"),
    ParameterSpec(name="board", type="enum", default="沪深主板",
                  choices=("沪深主板", "创业板"), description="板块范围"),
    ParameterSpec(name="keyword", type="string",
                  description="盯盘关键词"),
)


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    registry = SkillRegistry(store)
    registry.register(
        BASE, version="1.0", name="数据聚合探针", description="按范围聚合数据并出报表",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        parameters=PARAMS, dependencies=(), source="user-built",
    )
    return registry


def _card(items=(), *, values=None, target=SKILL, confirmed=True, intent="configure"):
    """一张意图确认卡（默认已确认；条目缺省为第 ① 项「意图」）。"""
    rows = items or (ConfirmationItem(
        text="意图 configure", value=target or "未指定", source="intent"),)
    return checked_confirmation(
        envelope=ResultEnvelope.ok(list(rows)),
        intent=intent, target=target, items=tuple(rows),
        values=dict(values or {}), confirmed=confirmed,
    )


def _entry(config_id: str, *, scope: str = "skill") -> ConfigEntry:
    """一条 01 §7 登记项（Fake 端口用）。"""
    return ConfigEntry(
        config_id=config_id, display_name="统计窗口天数",
        value_schema={"type": "integer"}, default=7,
        description_for_chat="统计窗口的天然语言描述（对话通道）",
        panel_form_spec=PanelField(widget="number", label="统计窗口",
                                   help_text="面板上的说明"),
        scope=scope, change_policy=ChangePolicy(requires_confirmation=False),
    )


class FakeRegistry:
    """最小登记面端口（只满足 :class:`ConfigRegistryPort` 的语义）。"""

    def __init__(self, entries=()) -> None:
        self._entries = {(e.config_id): e for e in entries}
        self.applied: list[tuple[str, dict, str | None]] = []

    def entry_for(self, target: str, param: str):
        return self._entries.get(f"{target}.{param}")

    def apply(self, target: str, values, *, trace_id=None):
        self.applied.append((target, dict(values), trace_id))
        return tuple(
            ChangeRecord(
                change_id=ChangeId.generate().value,
                config_id=f"{target}.{name}", old_value=None, new_value=value,
                applied_at=NOW.isoformat(), trace_ref=trace_id,
            )
            for name, value in values.items()
        )


# ───────────────────── .1 GWT-1 四字段生成 ─────────────────────


class TestGwt1DraftGeneration:
    def test_four_fields_come_from_the_confirmation_card(self, skills) -> None:
        card = _card(
            items=(
                ConfirmationItem(text="意图 configure", value=SKILL, source="intent"),
                ConfirmationItem(param="window_days", text="参数 window_days · 用户给出",
                                 value="60", source="stated"),
            ),
            values={"window_days": 60},
        )
        env = ConfigDraftProtocol(descriptors=skills).generate(card)
        assert env.status == "ok"
        draft = env.data
        assert draft.target == SKILL
        assert draft.parameter_draft == {"window_days": 60}
        # understanding_summary 与确认卡条目**同源**（逐条 line 拼装）
        assert draft.understanding_summary == "\n".join(i.line() for i in card.items)
        assert "参数 window_days · 用户给出：60" in draft.understanding_summary

    def test_unconfirmed_card_is_refused(self, skills) -> None:
        env = ConfigDraftProtocol(descriptors=skills).generate(
            _card(confirmed=False))
        assert env.status == "validation_failed"

    def test_missing_target_is_refused(self, skills) -> None:
        card = _card(target=None)
        env = ConfigDraftProtocol(descriptors=skills).generate(card)
        assert env.status == "validation_failed"

    def test_undeclared_parameter_is_refused(self, skills) -> None:
        card = _card(values={"nope": 1})
        env = ConfigDraftProtocol(descriptors=skills).generate(card)
        assert env.status == "validation_failed"
        assert "nope" in env.reason

    def test_carries_no_workflow_subobject(self, skills) -> None:
        """参数分支的草稿是四字段本身——工作流子对象由载体现，不在此另立平行模型。"""
        draft = ConfigDraftProtocol(descriptors=skills).generate(_card()).data
        assert isinstance(draft, ConfigDraft)
        assert not hasattr(draft, "workflow_draft")


# ───────────────────── .1 GWT-2 未澄清项来源标注 ─────────────────────


class TestGwt2OpenQuestions:
    def test_defaulted_and_missing_are_not_conflated(self, skills) -> None:
        card = _card(
            items=(
                ConfirmationItem(text="意图 configure", value=SKILL, source="intent"),
                ConfirmationItem(param="window_days", text="参数 window_days · 默认值",
                                 value="30", source="default"),
            ),
            values={"window_days": 30},
        )
        draft = ConfigDraftProtocol(descriptors=skills).generate(card).data
        by_name = {q.param: q for q in draft.open_questions}
        # board 声明有默认值但未进 values → 仍是待追问（不问「是否填过」，只看事实）
        assert by_name["board"].source == "missing"
        assert by_name["board"].default == "沪深主板"
        assert by_name["keyword"].source == "missing"
        assert by_name["keyword"].default is None
        # window_days 已按默认值填充 → 单独一类，且值确在 parameter_draft 里
        assert by_name["window_days"].source == "defaulted"
        assert draft.parameter_draft["window_days"] == 30

    def test_stated_parameter_gets_no_open_question(self, skills) -> None:
        card = _card(
            items=(
                ConfirmationItem(text="意图 configure", value=SKILL, source="intent"),
                ConfirmationItem(param="keyword", text="参数 keyword · 用户给出",
                                 value="退市风险", source="stated"),
            ),
            values={"keyword": "退市风险"},
        )
        draft = ConfigDraftProtocol(descriptors=skills).generate(card).data
        assert "keyword" not in {q.param for q in draft.open_questions}

    def test_absent_descriptors_produce_no_guesses(self) -> None:
        draft = ConfigDraftProtocol().generate(_card()).data
        assert draft.open_questions == ()
        assert draft.parameter_draft == {}


# ───────────────────── .1 GWT-3 configure 去向接线 ─────────────────────


class TestGwt3ConfigureRoute:
    def test_route_is_wired_and_yields_a_draft(self, skills) -> None:
        assert ROUTE_BY_INTENT["configure"].wired is True
        bus = DispatchBus(configs=ConfigDraftProtocol(descriptors=skills))
        outcome = bus.dispatch(_card(values={"keyword": "退市风险"}))
        assert outcome.envelope.status == "ok"
        assert outcome.wired is True and outcome.owner is None
        assert isinstance(outcome.draft, ConfigDraft)
        assert outcome.draft.target == SKILL

    def test_absent_generator_fails_closed(self) -> None:
        outcome = DispatchBus().dispatch(_card())
        assert outcome.envelope.status == "unavailable"
        assert CONFIG_DRAFT_ABSENT_REASON in outcome.envelope.reason
        assert outcome.draft is None

    def test_generator_failure_passes_through_unchanged(self, skills) -> None:
        """生成面的失败信封原样透出——不重包、不改 status、不吞 reason。"""
        bus = DispatchBus(configs=ConfigDraftProtocol(descriptors=skills))
        outcome = bus.dispatch(_card(target=None))
        assert outcome.envelope.status == "validation_failed"
        assert "目标" in outcome.envelope.reason

    def test_workflow_branch_is_unavailable_without_its_generator(self, skills) -> None:
        bus = DispatchBus(configs=ConfigDraftProtocol(descriptors=skills))
        outcome = bus.dispatch(_card(), structure={"name": "每日风险简报"})
        assert outcome.envelope.status == "unavailable"
        assert WORKFLOW_BRANCH_ABSENT_REASON in outcome.envelope.reason


# ───────────────────── .2 GWT-1 双通道同源 ─────────────────────


class TestGwt1DualChannel:
    def test_registered_param_uses_the_same_entry_for_both_channels(self, skills) -> None:
        entry = _entry(f"{SKILL}.window_days")
        env = dual_channel_view(SKILL, descriptors=skills, registry=FakeRegistry([entry]))
        assert env.status == "ok"
        param = {p.name: p for p in env.data.params}["window_days"]
        assert param.origin == "registry"
        assert param.chat_text == entry.description_for_chat          # 对话通道
        assert param.panel_field == entry.panel_form_spec            # 面板通道
        assert param.default == entry.default and param.config_id == entry.config_id

    def test_unregistered_param_falls_back_in_both_channels_together(self, skills) -> None:
        env = dual_channel_view(SKILL, descriptors=skills, registry=FakeRegistry())
        param = {p.name: p for p in env.data.params}["board"]
        assert param.origin == "declared"
        assert param.chat_text == "板块范围"                          # 声明面的 description
        assert param.panel_field.widget == "select"                   # enum → select
        assert param.panel_field.choices == ("沪深主板", "创业板")
        assert param.default == "沪深主板"

    def test_without_descriptors_both_channels_are_unavailable(self) -> None:
        env = dual_channel_view(SKILL, registry=FakeRegistry())
        assert env.status == "unavailable"
        assert "描述体" in env.reason


# ───────────────────── .2 GWT-2/3/4 处置三态 ─────────────────────


class TestGwt2Accept:
    def test_accept_applies_values_and_yields_change_ids(self, skills) -> None:
        port = FakeRegistry()
        draft = ConfigDraft(
            target=SKILL, parameter_draft={"window_days": 60},
            understanding_summary="已理解 1 项：参数 window_days：60")
        env = ConfigDraftHandling(descriptors=skills, registry=port).accept(
            draft, trace_id="tr_" + "0" * 20)
        assert env.status == "ok"
        assert port.applied == [(SKILL, {"window_days": 60}, "tr_" + "0" * 20)]
        assert len(env.data.change_ids) == 1

    def test_accept_without_registry_fails_closed(self, skills) -> None:
        draft = ConfigDraft(
            target=SKILL, parameter_draft={"window_days": 60},
            understanding_summary="已理解 1 项")
        env = ConfigDraftHandling(descriptors=skills).accept(draft)
        assert env.status == "unavailable"
        assert REGISTRY_ABSENT_REASON in env.reason
        assert not hasattr(env, "data") or env.data is None

    def test_accept_without_values_is_empty(self, skills) -> None:
        draft = ConfigDraft(target=SKILL, understanding_summary="已理解 1 项")
        env = ConfigDraftHandling(descriptors=skills, registry=FakeRegistry()).accept(draft)
        assert env.status == "empty"


class TestGwt3Tune:
    def test_tune_returns_the_panel_view_of_the_same_entry(self, skills) -> None:
        port = FakeRegistry([_entry(f"{SKILL}.window_days")])
        draft = ConfigDraft(
            target=SKILL, parameter_draft={"window_days": 60},
            understanding_summary="已理解 1 项")
        env = ConfigDraftHandling(descriptors=skills, registry=port).tune(draft)
        assert env.status == "ok"
        assert env.data.values == {"window_days": 60}       # 草稿当前值（未落盘）
        assert env.data.panel_field("window_days") == _entry(
            f"{SKILL}.window_days").panel_form_spec
        assert not port.applied                             # 微调不落值


class TestGwt4Reject:
    def test_reject_discards_and_names_the_feedback_owner(self, skills) -> None:
        draft = ConfigDraft(
            target=SKILL, parameter_draft={"window_days": 60},
            understanding_summary="已理解 1 项")
        port = FakeRegistry()
        env = ConfigDraftHandling(descriptors=skills, registry=port).reject(
            draft, reason="不是我想要的口径")
        assert env.status == "ok"
        assert env.data.rejected is True
        assert env.data.reason == "不是我想要的口径"
        assert env.data.feedback_owner == "T-L6-001"
        assert not port.applied                             # 拒绝不落盘


# ───────────────────── .3 工作流草稿交接 ─────────────────────


WORKFLOW_STRUCTURE = {
    "name": "每日风险简报",
    "description": "扫描持仓风险并生成简报",
    "flow_name": "daily_risk",
    "nodes": [{"node_id": "scan", "skill_id": "sk_scan_v1.0"}],
    "edges": [],
    "schedule": {"mode": "cron", "cron": "0 8 * * *"},
}


def _workflow_card(**over):
    card = _card(**over)
    return card


@pytest.fixture()
def wf_skills(store: Store, skills: SkillRegistry) -> SkillRegistry:
    """工作流草稿里被引用的两个 Skill（「接受」的保存期校验要求它们已注册）。"""
    for base, name in (("sk_scan", "风险扫描"), ("sk_alert", "风险提示")):
        skills.register(
            base, version="1.0", name=name, description=f"{name}能力",
            input_schema={"type": "object", "properties": {"risk_level": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"risk_level": {"type": "string"}}},
            parameters=(), dependencies=(), source="user-built",
        )
    return skills


class TestGwt1WorkflowDraft:
    def test_builds_the_draft_carrier_without_identity_fields(self) -> None:
        builder = WorkflowDraftBuilder()
        env = builder.build(_workflow_card(), WORKFLOW_STRUCTURE)
        assert env.status == "ok"
        draft = env.data
        assert isinstance(draft, WorkflowDraft)
        assert draft.workflow_draft["flow_name"] == "daily_risk"
        assert "flow_id" not in draft.workflow_draft
        assert "version" not in draft.workflow_draft

    def test_identity_fields_in_the_payload_are_refused_not_dropped(self) -> None:
        payload = {**WORKFLOW_STRUCTURE, "flow_id": "wf_daily_risk_v1.0"}
        env = WorkflowDraftBuilder().build(_workflow_card(), payload)
        assert env.status == "validation_failed"
        assert "flow_id" in env.reason

    def test_judgement_lives_in_one_place(self) -> None:
        assert is_workflow_draft(WORKFLOW_STRUCTURE) is True
        assert is_workflow_draft(None) is False

    def test_unconfirmed_card_is_refused(self) -> None:
        env = WorkflowDraftBuilder().build(
            _workflow_card(confirmed=False), WORKFLOW_STRUCTURE)
        assert env.status == "validation_failed"


class TestGwt2CanvasHandoff:
    def test_draft_lands_on_the_canvas(self, store: Store, skills: SkillRegistry) -> None:
        builder = WorkflowDraftBuilder()
        draft = builder.build(_workflow_card(), WORKFLOW_STRUCTURE).data
        session = handoff(DraftIntake(store, skills, name_check=NEUTRAL), draft)
        assert session.status == "editing"
        assert session.base == "wf_daily_risk"
        assert session.dag.flow_id == "wf_daily_risk_v0.0"      # 画布期临时身份

    def test_shape_illegal_draft_keeps_the_canvas_closed(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        smuggled = WorkflowDraft(
            target="工作流：每日风险简报", understanding_summary="已理解 1 项",
            workflow_draft={**WORKFLOW_STRUCTURE, "version": "1.0"})
        with pytest.raises(DraftShapeError):
            handoff(DraftIntake(store, skills, name_check=NEUTRAL), smuggled)

    def test_non_workflow_draft_does_not_enter_the_channel(
        self, store: Store, skills: SkillRegistry
    ) -> None:
        plain = WorkflowDraft(target=SKILL, understanding_summary="已理解 1 项")
        with pytest.raises(ConfigValidationError):
            handoff(DraftIntake(store, skills, name_check=NEUTRAL), plain)


class TestGwt3DelegatedThreeStates:
    def test_accept_tune_reject_are_delegated_to_l1(
        self, store: Store, wf_skills: SkillRegistry
    ) -> None:
        intake = DraftIntake(store, wf_skills, name_check=NEUTRAL)
        draft = WorkflowDraftBuilder().build(_workflow_card(), WORKFLOW_STRUCTURE).data
        session = handoff(intake, draft)
        assert intake.tune(session) is session.editor          # 微调：同一个编辑面句柄
        accepted = intake.accept(session)
        assert accepted.flow_id == "wf_daily_risk_v1.0"
        assert accepted.change_id.startswith("chg_")           # 01 §7 变更留痕

    def test_reject_discards_without_persisting(
        self, store: Store, wf_skills: SkillRegistry
    ) -> None:
        intake = DraftIntake(store, wf_skills, name_check=NEUTRAL)
        draft = WorkflowDraftBuilder().build(_workflow_card(), WORKFLOW_STRUCTURE).data
        session = handoff(intake, draft)
        result = intake.reject(session, reason="再想想")
        assert result.reason == "再想想"
        assert session.status == "rejected"
        # 草稿态不落盘：`config` 分区里没有工作流定义
        assert not [n for n in store.list_files("config") if n.startswith("workflow/")]
