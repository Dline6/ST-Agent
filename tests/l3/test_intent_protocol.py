"""T-L3-002.1 测试：意图理解与澄清协议（[05 §3](../../docs/技术架构-v2/05-L3-对话主入口.md)；GWT-1..5）。

Fake 说明：理解端口与 L1 描述体取数口按**鸭子类型**装配（真 `SkillRegistry` 供参数
面），LLM 实现的两条用例用**真 `LlmClient`**（本地端点 + 注入 transport）跑。
"""

from __future__ import annotations

import json

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm.client import LlmClient
from st_agent.l0.llm.models import StreamEvent
from st_agent.l0.llm.registry import EndpointRegistry
from st_agent.l0.secrets.vault import CredentialVault
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l3.commands import INTENT_KINDS, CommandRegistry
from st_agent.l3.errors import IntentValidationError
from st_agent.l3.intent import (
    CLARIFICATION_BUDGET,
    CONFIRMATION_HEADER,
    DIRECTION_LIMIT,
    INTENT_PARAM_SPECS,
    INTENT_TARGETS,
    UNSPECIFIED_LABEL,
    IntentDraft,
    IntentProtocol,
    LlmIntentUnderstander,
    checked_draft,
    parse_understanding,
)

ENDPOINT = "local-intent"

#: 五参 Skill：`window_days` 有默认值、`channel` 无——覆盖「跳过取默认值」与「未指定」两态。
PARAMS = (
    {"name": "window_days", "type": "integer", "default": 30, "description": "跟踪窗口天数"},
    {"name": "channel", "type": "string", "description": "推送渠道"},
    {"name": "risk_kinds", "type": "string", "default": "退市", "description": "风险情形"},
    {"name": "session", "type": "string", "default": "全天", "description": "推送时段"},
    {"name": "quiet", "type": "boolean", "default": False, "description": "是否静默"},
)


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    return SkillRegistry(store)


@pytest.fixture()
def seeded(skills: SkillRegistry) -> str:
    """注册一个五参探针 Skill，返回 ``skill_id``。"""
    return skills.register(
        "sk_probe_watch", version="1.0",
        name="标的跟踪探针", description="按条件跟踪标的并在命中时告知",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        parameters=PARAMS, dependencies=(), source="user-built",
    ).skill_id


class _Understander:
    """理解端口探针：原样回一个信封（或按需抛异常）。"""

    def __init__(self, result) -> None:
        self._result = result
        self.seen: list[str] = []

    def understand(self, text: str) -> ResultEnvelope:
        self.seen.append(text)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _protocol(skills: SkillRegistry, *, draft: IntentDraft | None = None, **kw) -> IntentProtocol:
    return IntentProtocol(
        understander=_Understander(ResultEnvelope.ok(draft)) if draft is not None else None,
        descriptors=skills, **kw,
    )


# ───────────────────────── GWT-1 意图分类 ─────────────────────────


class TestGwt1IntentClassification:
    def test_vocabulary_matches_05_3_1(self) -> None:
        assert tuple(INTENT_TARGETS) == INTENT_KINDS

    def test_understander_draft_passes_through(self, skills: SkillRegistry) -> None:
        draft = IntentDraft(intent="query", target="sk_probe_watch", understood={"a": 1})
        result = _protocol(skills, draft=draft).understand("看看今天的行情")
        assert result.status == "ok"
        assert result.data.intent == "query"
        assert result.data.understood == {"a": 1}

    def test_open_questions_filled_from_descriptor(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        """理解端口只给 target 时，未澄清参数由描述体补齐（假设 A2 的落点）。"""
        draft = IntentDraft(intent="query", target=seeded, understood={"window_days": 7})
        result = _protocol(skills, draft=draft).understand("盯一下")
        assert "window_days" not in result.data.open_questions
        assert set(result.data.open_questions) == {"channel", "risk_kinds", "session", "quiet"}

    def test_absent_port_is_fail_closed(self, skills: SkillRegistry) -> None:
        result = IntentProtocol(descriptors=skills).understand("随便说点什么")
        assert result.status == "unavailable"
        assert result.reason

    def test_empty_input_is_rejected(self, skills: SkillRegistry) -> None:
        assert IntentProtocol().understand("   ").status == "validation_failed"

    def test_port_exception_becomes_dependency_failed(self, skills: SkillRegistry) -> None:
        proto = IntentProtocol(understander=_Understander(RuntimeError("端点炸了")),
                               descriptors=skills)
        result = proto.understand("盯一下")
        assert result.status == "dependency_failed"
        assert "端点炸了" in result.reason

    def test_port_non_ok_envelope_passes_through(self, skills: SkillRegistry) -> None:
        proto = IntentProtocol(
            understander=_Understander(ResultEnvelope.validation_failed("解析不出意图")),
            descriptors=skills,
        )
        assert proto.understand("盯一下").status == "validation_failed"


# ───────────────────────── GWT-2 快捷指令短路 ─────────────────────────


class TestGwt2Shortcut:
    def test_shortcut_carries_template_and_prefilled_params(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        class _Matcher:
            def match(self, query: str):
                return skills.get(seeded)

        proto = IntentProtocol(
            commands=CommandRegistry(), matcher=_Matcher(), descriptors=skills,
        )
        result = proto.understand("/盯盘 600000 退市风险")
        assert result.status == "ok"
        assert result.data.intent == "configure"
        assert result.data.target == seeded
        assert result.data.via_shortcut == "盯盘"
        assert result.data.understood == {"window_days": 30}

    def test_prefilled_params_are_not_asked_again(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        class _Matcher:
            def match(self, query: str):
                return skills.get(seeded)

        proto = IntentProtocol(
            commands=CommandRegistry(), matcher=_Matcher(), descriptors=skills,
        )
        draft = proto.understand("/盯盘").data
        assert "window_days" not in draft.open_questions
        assert "channel" in draft.open_questions

    def test_unknown_command_envelope_passes_through(self, skills: SkillRegistry) -> None:
        proto = IntentProtocol(commands=CommandRegistry(), descriptors=skills)
        result = proto.understand("/并不存在的指令")
        assert result.status == "empty"
        assert "盯盘" in result.reason  # 带可用指令清单（CommandRegistry 的既有口径）


# ───────────────────────── GWT-3 澄清预算与跳过 ─────────────────────────


class TestGwt3ClarificationBudget:
    def test_questions_capped_at_budget(self, skills: SkillRegistry, seeded: str) -> None:
        draft = checked_draft(
            intent="configure", target=seeded,
            open_questions=("channel", "risk_kinds", "session", "quiet", "window_days"),
        )
        round_ = _protocol(skills).clarify(draft)
        assert len(round_.questions) == CLARIFICATION_BUDGET
        assert round_.deferred == ("quiet", "window_days")

    def test_every_question_is_skippable(self, skills: SkillRegistry, seeded: str) -> None:
        draft = checked_draft(intent="configure", target=seeded,
                              open_questions=("channel", "risk_kinds"))
        for question in _protocol(skills).clarify(draft).questions:
            assert question.skippable is True
            assert question.prompt

    def test_skip_fills_default_and_labels_it(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        draft = checked_draft(intent="configure", target=seeded,
                              open_questions=("risk_kinds", "session"))
        card = _protocol(skills).confirm(draft, answers={})  # 全部跳过
        assert card.values == {"risk_kinds": "退市", "session": "全天"}
        labels = {i.param: i.source for i in card.items if i.param}
        assert labels == {"risk_kinds": "default", "session": "default"}

    def test_over_budget_param_filled_by_default(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        draft = checked_draft(
            intent="configure", target=seeded,
            open_questions=("channel", "risk_kinds", "session", "quiet"),
        )
        card = _protocol(skills).confirm(draft)
        assert card.values["quiet"] is False  # 第 4 项超预算 → 默认值

    def test_answer_overrides_default(self, skills: SkillRegistry, seeded: str) -> None:
        draft = checked_draft(intent="configure", target=seeded, open_questions=("channel",))
        card = _protocol(skills).confirm(draft, answers={"channel": "邮件"})
        assert card.values["channel"] == "邮件"
        assert [i.source for i in card.items if i.param == "channel"] == ["stated"]

    def test_no_default_means_unspecified_and_not_in_values(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        draft = checked_draft(intent="configure", target=seeded, open_questions=("channel",))
        card = _protocol(skills).confirm(draft)
        assert "channel" not in card.values
        item = next(i for i in card.items if i.param == "channel")
        assert item.value == UNSPECIFIED_LABEL
        assert item.source == "default"

    def test_no_open_question_yields_empty_round(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        draft = checked_draft(intent="query", target=seeded)
        round_ = _protocol(skills).clarify(draft)
        assert round_.questions == ()
        assert round_.envelope.status == "empty"


# ───────────────────────── GWT-4 无法收敛给方向 ─────────────────────────


class TestGwt4Directions:
    def test_unclear_draft_has_directions_not_questions(self, skills: SkillRegistry) -> None:
        draft = IntentDraft(intent=None, directions=("查询某标的的行情", "对组合做压力测试"))
        plan = _protocol(skills).clarify(draft)
        assert plan.questions == ()
        assert plan.envelope.status == "empty"
        assert plan.directions == ("查询某标的的行情", "对组合做压力测试")

    def test_directions_capped_at_limit(self, skills: SkillRegistry) -> None:
        draft = IntentDraft(
            intent=None,
            directions=tuple(f"方向{i}" for i in range(DIRECTION_LIMIT + 3)),
        )
        assert len(_protocol(skills).clarify(draft).directions) == DIRECTION_LIMIT

    def test_draft_without_intent_or_directions_is_rejected(self) -> None:
        """构造面经 `checked_draft`（pydantic 会把校验期异常再包一层，工厂还原回来）。"""
        with pytest.raises(IntentValidationError):
            checked_draft(intent=None)

    def test_unclear_draft_cannot_be_confirmed(self, skills: SkillRegistry) -> None:
        draft = IntentDraft(intent=None, directions=("方向一",))
        with pytest.raises(IntentValidationError):
            _protocol(skills).confirm(draft)


# ───────────────────────── GWT-5 意图确认卡 ─────────────────────────


class TestGwt5ConfirmationCard:
    def test_card_always_has_the_intent_item(self, skills: SkillRegistry, seeded: str) -> None:
        card = _protocol(skills).confirm(checked_draft(intent="query", target=seeded))
        assert len(card.items) >= 1
        assert card.items[0].source == "intent"
        assert card.header().startswith("已理解 1 项")

    def test_card_is_unconfirmed_until_marked(self, skills: SkillRegistry, seeded: str) -> None:
        card = _protocol(skills).confirm(checked_draft(intent="query", target=seeded))
        assert card.confirmed is False
        assert card.mark_confirmed().confirmed is True
        assert card.confirmed is False  # 不可变

    def test_card_items_are_rendered_neutrally(
        self, skills: SkillRegistry, seeded: str
    ) -> None:
        draft = checked_draft(intent="configure", target=seeded, open_questions=("channel",))
        card = _protocol(skills).confirm(draft, answers={"channel": "邮件"})
        assert all(i.line() for i in card.items)

    def test_generated_text_hitting_neutrality_is_blocked(
        self, skills: SkillRegistry
    ) -> None:
        """生成文案含第一人称 → 阻断（01 §6 执行点 2）。"""
        draft = IntentDraft(intent=None, directions=("我需要知道你想要什么",))
        with pytest.raises(IntentValidationError):
            _protocol(skills).clarify(draft)


# ───────────────────────── LLM 理解实现（假设 A1） ─────────────────────────


def _llm(store: Store, transport=None) -> LlmClient:
    registry = EndpointRegistry(store)
    registry.register(ENDPOINT, "local", "local-llama",
                      {"max_context_tokens": 8192, "supports_structured_output": True})
    return LlmClient(store, registry, CredentialVault(store), transport)


class TestLlmUnderstander:
    def test_parses_structured_reply(self, store: Store) -> None:
        payload = json.dumps({
            "intent": "query", "target": "sk_probe_watch",
            "understood": {"window_days": 7}, "open_questions": [], "directions": [],
        })

        def transport(endpoint, prompt, key, timeout_ms, tools=None):
            assert "intent" in prompt  # 提示词里的契约说明
            return [payload]

        result = LlmIntentUnderstander(_llm(store, transport), ENDPOINT).understand("看看行情")
        assert result.status == "ok"
        assert result.data.intent == "query"
        assert result.data.understood == {"window_days": 7}

    def test_endpoint_without_transport_degrades_explicitly(self, store: Store) -> None:
        """端点不可用 → `unavailable`（显式降级告知的源头，05 §4）。"""
        result = LlmIntentUnderstander(_llm(store), ENDPOINT).understand("看看行情")
        assert result.status == "unavailable"
        assert result.reason

    def test_unparsable_reply_is_dependency_failed(self) -> None:
        assert parse_understanding("这不是 JSON").status == "dependency_failed"

    def test_unknown_intent_is_dependency_failed(self) -> None:
        result = parse_understanding(json.dumps({"intent": "chat"}))
        assert result.status == "dependency_failed"
        assert "chat" in result.reason

    def test_code_fence_is_tolerated(self) -> None:
        body = '```json\n{"intent": "explain", "directions": []}\n```'
        assert parse_understanding(body).data.intent == "explain"


# ─────────── T-INT-003：无 target Skill 的意图取「意图级参数声明」（05 §3.2） ───────────


class TestIntentLevelParams:
    """`analyze` 的派发目标是整条 L4 流程、**没有**被调用的 Skill，故它问不出
    `SkillDescriptor.parameters`——「模式询问」（[06 §2.1]）由 [`INTENT_PARAM_SPECS`]
    承载，规则与 Skill 侧逐条相同。"""

    def test_declared_intents_are_real_intents(self) -> None:
        assert set(INTENT_PARAM_SPECS) <= set(INTENT_KINDS)

    def _protocol(self, draft: IntentDraft, skills: SkillRegistry, **kw) -> IntentProtocol:
        return IntentProtocol(
            understander=_Understander(ResultEnvelope.ok(draft)),
            descriptors=skills, **kw,
        )

    def test_analyze_gets_a_mode_question(self, skills: SkillRegistry) -> None:
        protocol = self._protocol(
            checked_draft(intent="analyze", target=None, understood={"topic": "sh.600000"}),
            skills,
        )
        understood = protocol.understand("分析下 sh.600000")
        assert understood.status == "ok"
        draft = understood.data
        assert draft.open_questions == ("mode",), "无 target 也须补出意图级问项"

        round_ = protocol.clarify(draft)
        assert [q.name for q in round_.questions] == ["mode"]
        assert round_.questions[0].choices == ("quick", "deep")
        assert round_.questions[0].default == "deep"
        assert round_.questions[0].skippable is True

    def test_mode_answer_lands_in_values_and_skip_takes_the_default(
        self, skills: SkillRegistry
    ) -> None:
        protocol = self._protocol(
            checked_draft(intent="analyze", target=None, understood={"topic": "sh.600000"}),
            skills,
        )
        draft = protocol.understand("分析下 sh.600000").data
        assert protocol.confirm(draft).values == {"topic": "sh.600000", "mode": "deep"}
        answered = protocol.confirm(draft, {"mode": "quick"})
        assert answered.values == {"topic": "sh.600000", "mode": "quick"}
        assert any(item.value == "quick" for item in answered.items)

    def test_mode_prefilled_by_the_understander_is_not_asked_again(
        self, skills: SkillRegistry
    ) -> None:
        protocol = self._protocol(
            checked_draft(
                intent="analyze", target=None,
                understood={"topic": "x", "mode": "quick"},
            ),
            skills,
        )
        draft = protocol.understand("分析下 x").data
        assert draft.open_questions == ()
        assert protocol.clarify(draft).questions == ()

    def test_shortcut_to_analyze_also_asks_the_mode(self, skills: SkillRegistry) -> None:
        """§8 快捷指令（`/回测` `压测`）落 `analyze`——同样受意图级声明覆盖。"""
        protocol = self._protocol(
            checked_draft(intent="query", target=None), skills, commands=CommandRegistry(),
        )
        resolved = protocol.understand("/回测 sh.600000")
        assert resolved.data.intent == "analyze"
        assert resolved.data.open_questions == ("mode",)

    def test_other_targetless_intents_gain_no_phantom_questions(
        self, skills: SkillRegistry
    ) -> None:
        """未声明的意图（如无 target 的 `query`）行为**逐字节不变**：不凭空产问项。"""
        protocol = self._protocol(checked_draft(intent="query", target=None), skills)
        draft = protocol.understand("看看").data
        assert draft.open_questions == ()
        round_ = protocol.clarify(draft)
        assert round_.questions == () and round_.envelope.status == "empty"


# ─────────── T-AGT-006：第七类意图 investigate 的确认卡（05 §3.2） ───────────


class TestInvestigateConfirmation:
    """T-AGT-006 GWT-2：`investigate` **刻意无**意图级参数声明（其循环上界是系统预算、
    非用户可调项，[05 §3.2]），故确认卡**只有「意图」一条目**、抬头计数正确——**不因
    无参数而构造失败**（`_intent_item` 直取 `INTENT_TARGETS[draft.intent]`）。"""

    def test_vocabulary_includes_the_seventh_intent(self) -> None:
        assert "investigate" in INTENT_KINDS
        assert "investigate" in INTENT_TARGETS
        assert "investigate" not in INTENT_PARAM_SPECS  # 刻意无参数声明

    def test_card_has_exactly_the_intent_item(self, skills: SkillRegistry) -> None:
        protocol = IntentProtocol(
            understander=_Understander(ResultEnvelope.ok(checked_draft(
                intent="investigate", target=None,
            ))),
            descriptors=skills,
        )
        draft = protocol.understand("查一下这家公司的上下游关联").data
        card = protocol.confirm(draft)
        assert len(card.items) == 1
        assert card.items[0].source == "intent"
        assert card.items[0].param is None
        assert card.header() == CONFIRMATION_HEADER.format(n=1)
        assert card.values == {}

    def test_no_questions_are_asked_for_investigate(self, skills: SkillRegistry) -> None:
        """`investigate` 不产任何澄清问项（无参数可问）——clarify 回空，不臆造问项。"""
        protocol = IntentProtocol(
            understander=_Understander(ResultEnvelope.ok(checked_draft(
                intent="investigate", target=None,
            ))),
            descriptors=skills,
        )
        draft = protocol.understand("查一下上游供应商").data
        assert draft.open_questions == ()
        round_ = protocol.clarify(draft)
        assert round_.questions == () and round_.envelope.status == "empty"
