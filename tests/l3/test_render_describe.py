"""T-L3-004.1 / .2 · 四型描述件（[05 §6–§7](../../docs/技术架构-v2/05-L3-对话主入口.md)；[01 §12](../../docs/技术架构-v2/01-平台共享契约.md)）。

锁三件事：**描述形状**（组件类型 / 必填槽 / 降级分支）、**槽的文本分栏**（哪些槽
标 ``generated``、哪些标 ``data``，以及两向的红-绿）、以及**本模块自有固定文案**
过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2。
"""

from __future__ import annotations

import pytest
from l3_helpers import NOW, STOCK_A, STOCK_B, node

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import PanelField
from st_agent.contracts.trace import ConclusionRef, Trace, TraceStep, digest_of
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.l2.memory import MemoryReader, MemoryWriter
from st_agent.l3.config.draft import ConfigDraft, OpenQuestion
from st_agent.l3.config.handling import PanelView
from st_agent.l3.config.registry import ChannelParam
from st_agent.l3.conflict.adjudication import (
    ACTION_LABELS,
    ADJUDICATION_HEADER,
    ADJUDICATION_QUESTION,
    STANCE_UNDECIDED_LABEL,
    AdjudicationAction,
    ConflictAdjudication,
    ConflictSide,
)
from st_agent.l3.home import build_context_card
from st_agent.l3.render import (
    DRAFT_TITLES,
    ORIGIN_LABELS,
    SIDE_EXISTING_LABEL,
    SIDE_PROPOSED_LABEL,
    TRACE_ABSENT_REASON,
    TRACE_EMPTY_REASON,
    describe_adjudication,
    describe_context_card,
    describe_draft,
    describe_trace,
)
from st_agent.ui.neutrality_gate import NeutralityGate

TRACE_ID = "tr_" + "1" * 20


def _step(step_type: str = "skill_run", *, note: str | None = None, degraded: bool = False):
    return TraceStep(
        step_type=step_type,
        ref="sk_run_0000000000000001",
        input_digest=digest_of({"a": 1}),
        output_digest=digest_of({"b": 2}),
        duration_ms=12,
        timestamp=NOW,
        degraded=degraded,
        note=note,
    )


def _trace(*steps: TraceStep, conclude: bool = True) -> Trace:
    trace = Trace(trace_id=TraceId.of(TRACE_ID))
    for step in steps:
        trace = trace.append_step(step)
    if conclude:
        trace = trace.conclude(ConclusionRef(kind="message", ref="msg_0000000000000001"))
    return trace


def _adjudication(**over) -> ConflictAdjudication:
    fields = {
        "conflict_id": "cf_00000000000000001",
        "kind": "value_conflict",
        "dimension": "thesis",
        "trace_id": TRACE_ID,
        "proposed": ConflictSide(
            node_id=None, dimension="thesis", fields={"view": "看好反转"}
        ),
        "existing": ConflictSide(
            node_id="mem_0000000000000001", dimension="thesis", fields={"view": "看空"}
        ),
        "header": ADJUDICATION_HEADER,
        "question": ADJUDICATION_QUESTION,
        "actions": (
            AdjudicationAction(decision="accept", label=ACTION_LABELS["accept"]),
            AdjudicationAction(decision="reject", label=ACTION_LABELS["reject"]),
        ),
        "directions": (),
        "stance_undecided": True,
    }
    fields.update(over)
    return ConflictAdjudication(**fields)


# ───────────────────────── .1 · trace_timeline（05 §7） ─────────────────────────


class TestGwt1TraceBecomesADescription:
    """GWT-1：一条 Trace → ``trace_timeline`` 描述，且裹在 ``ok`` 信封内。"""

    def test_component_type_and_envelope(self) -> None:
        envelope = describe_trace(_trace(_step()))
        assert envelope.status == "ok"
        description = envelope.data
        assert isinstance(description, UiDescription)
        assert description.component_type == "trace_timeline"
        assert description.description_id.startswith("desc_")

    def test_every_step_carries_the_eight_fields(self) -> None:
        """GWT-2：每一环可点开细看——输入快照 / 输出 / 耗时 / 依赖一项不缺。"""
        step = _step("memory_read", note="降级：读本地缓存", degraded=True)
        steps = describe_trace(_trace(step)).data.slots["steps"]
        assert len(steps) == 1
        assert set(steps[0]) == {
            "step_type", "ref", "input_digest", "output_digest",
            "duration_ms", "timestamp", "degraded", "note",
        }
        assert steps[0]["step_type"] == "memory_read"
        assert steps[0]["input_digest"] == step.input_digest
        assert steps[0]["output_digest"] == step.output_digest
        assert steps[0]["duration_ms"] == 12
        assert steps[0]["timestamp"] == NOW.isoformat()
        assert steps[0]["degraded"] is True
        assert steps[0]["note"] == "降级：读本地缓存"

    def test_step_order_is_preserved(self) -> None:
        steps = describe_trace(
            _trace(_step("data_fetch"), _step("skill_run"), _step("aggregation"))
        ).data.slots["steps"]
        assert [s["step_type"] for s in steps] == ["data_fetch", "skill_run", "aggregation"]

    def test_conclusion_is_carried(self) -> None:
        slots = describe_trace(_trace(_step())).data.slots
        assert slots["closed"] is True
        assert slots["conclusion_ref"] == {"kind": "message", "ref": "msg_0000000000000001"}

    def test_open_chain_has_no_conclusion(self) -> None:
        slots = describe_trace(_trace(_step(), conclude=False)).data.slots
        assert slots["closed"] is False
        assert slots["conclusion_ref"] is None


class TestGwt2TheSameRendererServesL4AndL6:
    """GWT-3：跨层复用——描述件只吃一条 Trace，不依赖任何 L3 会话态。"""

    def test_lens_opinion_steps_need_no_l3_specific_change(self) -> None:
        """L4 视角追问的链与 L3 的链走同一入口，逐字段同构。"""
        envelope = describe_trace(_trace(_step("lens_opinion"), _step("aggregation")))
        assert envelope.status == "ok"
        assert envelope.data.component_type == "trace_timeline"

    def test_describe_trace_is_a_pure_function(self) -> None:
        """同一输入两次调用得同形状（无隐藏状态 / 无落盘 / 无时钟依赖）。"""
        trace = _trace(_step())
        first, second = describe_trace(trace), describe_trace(trace)
        assert first.data.slots == second.data.slots
        assert first.data.component_type == second.data.component_type


class TestGwt3TraceDegradesExplicitly:
    """GWT-4：缺失 / 空链都**显式**呈现，不静默留空。"""

    def test_missing_trace_is_unavailable(self) -> None:
        envelope = describe_trace(None, now=NOW)
        assert envelope.status == "unavailable"
        assert envelope.reason == TRACE_ABSENT_REASON
        assert envelope.last_updated_at == NOW
        assert envelope.data is None

    def test_empty_chain_is_empty_not_unavailable(self) -> None:
        """链**存在但没有步骤**（未接入去向）与链**缺失**是两件事。"""
        envelope = describe_trace(_trace(conclude=False), now=NOW)
        assert envelope.status == "empty"
        assert envelope.reason == TRACE_EMPTY_REASON
        assert envelope.data is None


class TestTraceTextKinds:
    def test_steps_are_generated_and_anchors_are_data(self) -> None:
        description = describe_trace(_trace(_step())).data
        assert description.text_kinds == {
            "steps": "generated", "conclusion_ref": "data", "closed": "data",
        }

    def test_generated_slot_passes_the_gate_on_a_real_chain(self) -> None:
        verdict = NeutralityGate().check(describe_trace(_trace(_step())).data)
        assert verdict.passed is True, verdict.reason


# ───────────────────────── .2 · context_card（05 §2） ─────────────────────────


class TestContextCardDescription:
    """GWT-1：六段按各自 ``state`` 呈现，非 ok 段显原因，画像标签 ≤5。"""

    def _card(self, reader: MemoryReader, writer: MemoryWriter):
        writer.add_node(_attention())
        writer.add_node(_identity())
        return build_context_card(reader)

    def test_six_sections_in_fixed_order_with_labels(self, reader, writer) -> None:
        description = describe_context_card(self._card(reader, writer)).data
        assert description.component_type == "context_card"
        sections = description.slots["sections"]
        assert [s["key"] for s in sections] == [
            "profile", "holdings", "watchlist", "thesis", "unread_alerts", "hot_topics",
        ]
        labels = description.slots["labels"]
        assert set(labels) == {s["key"] for s in sections}
        assert labels["profile"]["title"] == "偏好画像"

    def test_unavailable_section_carries_reason_and_producer(self, reader, writer) -> None:
        """§2：数据来源未就绪的段以「不可用 + 原因」呈现，**不静默留空**。"""
        description = describe_context_card(self._card(reader, writer)).data
        sections = {s["key"]: s for s in description.slots["sections"]}
        labels = description.slots["labels"]
        assert sections["unread_alerts"]["state"] == "unavailable"
        assert labels["unread_alerts"]["reason"]
        assert labels["unread_alerts"]["producer"]
        assert "items" not in sections["unread_alerts"]

    def test_ok_sections_carry_no_reason(self, reader, writer) -> None:
        description = describe_context_card(self._card(reader, writer)).data
        labels = description.slots["labels"]
        ok_keys = [s["key"] for s in description.slots["sections"] if s["state"] == "ok"]
        assert ok_keys
        for key in ok_keys:
            assert "reason" not in labels[key]

    def test_profile_tags_are_capped_and_truncation_is_visible(self, reader, writer) -> None:
        writer.add_node(_attention(theme_interests=("AI", "算力", "半导体", "新能源")))
        description = describe_context_card(build_context_card(reader)).data
        profile = {s["key"]: s for s in description.slots["sections"]}["profile"]
        assert len(profile["tags"]) == 5
        assert profile["limit"] == 5
        assert profile["total"] == 6, "截断前的候选条数须如实给出"

    def test_empty_state_card_is_still_renderable(self, reader) -> None:
        """新用户空状态照样出卡（空状态本身是可渲染的合法态，不是降级）。"""
        envelope = describe_context_card(build_context_card(reader), now=NOW)
        assert envelope.status == "ok"
        description = envelope.data
        assert description.slots["is_empty"] is True
        assert description.slots["empty_hint"]
        assert description.slots["targets"]["onboarding"] is not None

    def test_targets_are_data_slots(self, reader, writer) -> None:
        """卡片携带的导航目标含用户话题（``query.topic``），属数据展示。"""
        description = describe_context_card(self._card(reader, writer)).data
        assert "targets" in description.slots
        assert description.text_kinds["targets"] == "data"


# ───────────────────────── .2 · config_draft_card（05 §5） ─────────────────────────


class TestDraftCardDescription:
    """GWT-2：草稿 / 面板视图都只渲染、不落值。"""

    def _draft(self) -> ConfigDraft:
        return ConfigDraft(
            target="stock-watch",
            parameter_draft={"keywords": "回购"},
            understanding_summary="标的选择：sh.600000（已确认）",
            open_questions=(
                OpenQuestion(
                    param="window", prompt="参数 window：回看窗口",
                    default=30, source="missing",
                ),
            ),
        )

    def test_draft_branch(self) -> None:
        description = describe_draft(self._draft(), now=NOW).data
        assert description.component_type == "config_draft_card"
        assert description.title == DRAFT_TITLES["draft"]
        assert description.slots["mode"] == "draft"
        assert description.slots["target"] == "stock-watch"
        assert description.slots["values"] == {"keywords": "回购"}
        assert description.slots["defaults"] == {"window": 30}
        question = description.slots["questions"][0]
        assert question["prompt"] == "参数 window：回看窗口"
        assert question["source_label"] == "待追问"
        assert description.slots["panels"] == []

    def test_panel_branch(self) -> None:
        view = PanelView(
            target="stock-watch",
            params=(
                ChannelParam(
                    name="keywords", origin="declared", chat_text="参数 keywords：关注关键词",
                    panel_field=PanelField(widget="text", label="keywords", help_text="关键词"),
                ),
            ),
            values={"keywords": "回购"},
        )
        description = describe_draft(view, now=NOW).data
        assert description.slots["mode"] == "panel"
        assert description.title == DRAFT_TITLES["panel"]
        panel = description.slots["panels"][0]
        assert panel["name"] == "keywords"
        assert panel["description"] == "参数 keywords：关注关键词"
        assert panel["origin_label"] == ORIGIN_LABELS["declared"]
        assert panel["field"]["widget"] == "text"

    def test_both_branches_fill_the_required_slots(self) -> None:
        """必填槽 ``target`` / ``panels`` 在两个分支都在——否则服务端会判缺槽。"""
        for description in (
            describe_draft(self._draft(), now=NOW).data,
            describe_draft(PanelView(target="stock-watch"), now=NOW).data,
        ):
            assert "target" in description.slots
            assert "panels" in description.slots

    def test_draft_summary_is_a_data_slot(self) -> None:
        """理解摘要由确认卡条目**派生**（含用户给出的值），不整串复检（D-053）。"""
        description = describe_draft(self._draft(), now=NOW).data
        assert description.text_kinds["summary"] == "data"
        assert description.text_kinds["values"] == "data"

    def test_unknown_payload_is_validation_failed(self) -> None:
        envelope = describe_draft(object(), now=NOW)
        assert envelope.status == "validation_failed"
        assert envelope.data is None


# ───────────────────────── .2 · conflict_adjudication_card（05 §9） ─────────────────────────


class TestAdjudicationCardDescription:
    """GWT-3：两方**并列**呈现、不合并；方向未判定时显式标注。"""

    def test_two_sides_are_parallel_and_labelled(self) -> None:
        description = describe_adjudication(_adjudication(), now=NOW).data
        assert description.component_type == "conflict_adjudication_card"
        sides = description.slots["sides"]
        assert [s["node_id"] for s in sides] == ["mem_0000000000000001", None]
        assert description.slots["side_labels"] == [SIDE_EXISTING_LABEL, SIDE_PROPOSED_LABEL]
        assert sides[0]["fields"] == {"view": "看空"}
        assert sides[1]["fields"] == {"view": "看好反转"}

    def test_side_labels_align_with_sides_when_there_is_no_existing_side(self) -> None:
        description = describe_adjudication(_adjudication(existing=None), now=NOW).data
        assert len(description.slots["sides"]) == len(description.slots["side_labels"]) == 1
        assert description.slots["side_labels"] == [SIDE_PROPOSED_LABEL]

    def test_undecided_stance_is_explicitly_marked(self) -> None:
        description = describe_adjudication(_adjudication(), now=NOW).data
        assert description.slots["stance_undecided"] is True
        assert description.slots["stance_label"] == STANCE_UNDECIDED_LABEL

    def test_decided_stance_carries_the_candidates(self) -> None:
        description = describe_adjudication(
            _adjudication(directions=("更保守",), stance_undecided=False), now=NOW
        ).data
        assert description.slots["stance_undecided"] is False
        assert description.slots["stance_label"] == ""
        assert description.slots["directions"] == ["更保守"]

    def test_actions_carry_decision_and_label(self) -> None:
        description = describe_adjudication(_adjudication(), now=NOW).data
        assert description.slots["actions"] == [
            {"decision": "accept", "label": ACTION_LABELS["accept"]},
            {"decision": "reject", "label": ACTION_LABELS["reject"]},
        ]

    def test_header_and_question_are_carried(self) -> None:
        description = describe_adjudication(_adjudication(), now=NOW).data
        assert description.title == ADJUDICATION_HEADER
        assert description.slots["question"] == ADJUDICATION_QUESTION


# ───────────────────────── 槽的文本分栏（D-064） ─────────────────────────


class TestNeutralitySplitBySlot:
    """``text_kinds`` 是**槽级**标签——分错会两向都出错（[D-064](../../项目管理/决策日志.md)）。"""

    def test_user_memory_in_the_data_slot_passes_the_gate(self, reader, writer) -> None:
        """红-绿向①：用户自己的原话（含第一人称）在数据槽里**放行**。"""
        writer.add_node(_attention())
        writer.add_node(_thesis(view="我认为该反转会持续"))
        description = describe_context_card(build_context_card(reader)).data
        verdict = NeutralityGate().check(description)
        assert verdict.passed is True, verdict.reason
        assert "我认为该反转会持续" in str(description.slots["sections"])

    def test_the_same_wording_in_a_generated_slot_is_blocked(self) -> None:
        """红-绿向②：同一措辞若出在**生成文案槽**，边界门阻断——分栏才有意义。

        若把 ``sections`` 标成 ``generated``，向①会误伤用户原话；若把 ``labels`` 标成
        ``data``，本向就会放行——两个方向各钉一条。
        """
        description = checked_description(
            description_id=new_description_id(),
            component_type="context_card",
            title="当前上下文",
            slots={
                "sections": [],
                "labels": {"profile": {"title": "我认为这是稳健的"}},
                "greeting": "输入关注标的或想跟踪的条件，即可开始。",
                "empty_hint": None,
                "is_empty": False,
                "targets": {"graph": {}, "onboarding": None},
            },
            text_kinds={
                "sections": "data", "labels": "generated",
                "greeting": "generated", "empty_hint": "generated",
                "is_empty": "data", "targets": "data",
            },
        )
        verdict = NeutralityGate().check(description)
        assert verdict.passed is False
        assert verdict.hits == ("slots.labels",)
        assert "我" not in verdict.reason  # 违规原文不随原因回显

    def test_adjudication_sides_are_data_and_its_own_prose_is_generated(self) -> None:
        description = describe_adjudication(_adjudication(), now=NOW).data
        assert description.text_kinds["sides"] == "data"
        for slot in ("side_labels", "question", "actions", "directions", "stance_label"):
            assert description.text_kinds[slot] == "generated"

    def test_draft_questions_are_generated_and_values_are_data(self) -> None:
        description = describe_draft(
            ConfigDraft(
                target="stock-watch",
                parameter_draft={"keywords": "我认为会涨"},
                understanding_summary="关键词：我认为会涨",
                open_questions=(
                    OpenQuestion(param="window", prompt="参数 window：回看窗口",
                                 default=30, source="missing"),
                ),
            ),
            now=NOW,
        ).data
        assert description.text_kinds["questions"] == "generated"
        assert description.text_kinds["values"] == "data"
        # 用户给的值（数据）与派生摘要（数据）都不整串复检 → 全卡过门
        assert NeutralityGate().check(description).passed is True


@pytest.mark.parametrize(
    "text",
    [
        *ORIGIN_LABELS.values(),
        SIDE_EXISTING_LABEL,
        SIDE_PROPOSED_LABEL,
        *DRAFT_TITLES.values(),
    ],
)
def test_own_fixed_texts_pass_the_neutrality_check(text: str) -> None:
    """本模块**自有**的固定文案逐条过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2。"""
    assert NeutralityGuard().check_output(text).passed is True


# ── 夹具 ─────────────────────────────────────────────────────────────────────


def _attention(**over):
    fields = {
        "holdings": (STOCK_A,),
        "watchlist": (STOCK_B,),
        "sector_preferences": ("银行", "券商"),
    }
    fields.update(over)
    return node("attention", **fields)


def _identity(**over):
    return node("identity", risk_preference="稳健", investing_years="5 年", **over)


def _thesis(**over):
    fields = {"subject": STOCK_A, "subject_kind": "stock", "view": "看好反转", "stated_at": NOW}
    fields.update(over)
    return node("thesis", **fields)
