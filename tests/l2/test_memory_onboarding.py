"""T-L2-004.1 · Onboarding 协议与空状态（04 §7；GWT-1..5）。"""

from __future__ import annotations

import pytest
from memory_helpers import NOW

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    DEFAULT_ONBOARDING_QUESTIONS,
    DEFAULT_STATED_CONFIDENCE,
    EMPTY_STATE_HINT,
    MAX_ONBOARDING_QUESTIONS,
    NODE_TYPES,
    ONBOARDING_CONFIG_ID,
    REQUIRED_DIMENSIONS,
    MemoryGraph,
    MemoryReader,
    MemoryValidationError,
    OnboardingProtocol,
    OnboardingQuestion,
    SliceQuery,
    checked_questions,
)

QUESTIONS_PATH = "memory-policy/onboarding-questions.json"

#: §7 三个必需维度的答案（覆盖 identity 两项 + attention 一项）。
REQUIRED_ANSWERS = {
    "risk_preference": "稳健",
    "investing_years": "5 年",
    "sector_preferences": "银行",
}


class TestGwt1QuestionListIsConfigRegistryEntry:
    """GWT-1：问题清单是 01 §7 条目（七字段齐备）。"""

    def test_entry_carries_all_seven_fields(self, onboarding: OnboardingProtocol) -> None:
        entry = onboarding.question_entry()
        assert entry.config_id == ONBOARDING_CONFIG_ID
        assert entry.display_name
        assert entry.value_schema["maxItems"] == MAX_ONBOARDING_QUESTIONS
        assert entry.value_schema["items"]["properties"]["node_type"]["enum"] == list(NODE_TYPES)
        assert entry.default == [q.model_dump(mode="json")
                                 for q in DEFAULT_ONBOARDING_QUESTIONS]
        assert entry.description_for_chat
        assert entry.panel_form_spec.choices == NODE_TYPES
        assert entry.scope == "global"
        assert entry.change_policy.requires_confirmation is True

    def test_updates_land_in_config_partition(
        self, store: Store, onboarding: OnboardingProtocol
    ) -> None:
        assert QUESTIONS_PATH not in store.list_files("config")
        onboarding.set_questions(DEFAULT_ONBOARDING_QUESTIONS[1:])
        assert QUESTIONS_PATH in store.list_files("config")

    def test_missing_entry_falls_back_to_defaults(
        self, store: Store, onboarding: OnboardingProtocol
    ) -> None:
        assert onboarding.questions() == DEFAULT_ONBOARDING_QUESTIONS


class TestGwt2QuestionListCapAndCoverage:
    """GWT-2：清单 ≤10 问且覆盖 §7 点名的三个必需维度。"""

    def test_default_list_within_cap_and_covers_required_dimensions(
        self, onboarding: OnboardingProtocol
    ) -> None:
        questions = onboarding.questions()
        assert len(questions) <= MAX_ONBOARDING_QUESTIONS
        fields = {q.field for q in questions}
        assert set(REQUIRED_DIMENSIONS) <= fields

    def test_eleven_questions_are_rejected(self, onboarding: OnboardingProtocol) -> None:
        base = DEFAULT_ONBOARDING_QUESTIONS[0]
        too_many = tuple(
            base.model_copy(update={"question_id": f"q{i}"})
            for i in range(MAX_ONBOARDING_QUESTIONS + 1)
        )
        with pytest.raises(MemoryValidationError, match="不得超过"):
            onboarding.set_questions(too_many)

    def test_unknown_field_on_type_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="不在 identity 节点上"):
            checked_questions([{"question_id": "bogus", "prompt": "?",
                                "node_type": "identity", "field": "not_a_field"}])

    def test_unknown_node_type_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError):
            checked_questions([{"question_id": "bogus", "prompt": "?",
                                "node_type": "nope", "field": "whatever"}])

    def test_duplicate_question_id_is_rejected(self) -> None:
        q = DEFAULT_ONBOARDING_QUESTIONS[0]
        with pytest.raises(MemoryValidationError, match="重复"):
            checked_questions([q.model_dump(mode="json"), q.model_dump(mode="json")])

    def test_corrupted_entry_falls_back_without_stalling(
        self, store: Store, onboarding: OnboardingProtocol
    ) -> None:
        store.put("config", QUESTIONS_PATH, b"{not json")
        assert onboarding.questions() == DEFAULT_ONBOARDING_QUESTIONS


class TestGwt3AnswersBuildInitialProfile:
    """GWT-3：答完建立初始画像（三个必需维度 → identity + attention 两类节点）。"""

    def test_required_answers_produce_two_nodes(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        created = onboarding.build_initial_nodes(REQUIRED_ANSWERS)

        assert [n.type for n in created] == ["identity", "attention"]
        by_type = {n.type: n for n in created}
        assert by_type["identity"].risk_preference == "稳健"
        assert by_type["identity"].investing_years == "5 年"
        assert by_type["attention"].sector_preferences == ("银行",)
        # 同一节点类型的多个维度答案合建一个节点（§1：「某维度无记忆」＝ 没有该字段）
        assert len(created) == 2

    def test_nodes_are_user_stated_and_directly_written(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        created = onboarding.build_initial_nodes(REQUIRED_ANSWERS)
        assert all(n.source == "user_stated" for n in created)
        assert all(n.provenance is None for n in created)
        assert all(n.confidence == DEFAULT_STATED_CONFIDENCE for n in created)
        assert all(n.privacy_level == "private" for n in created)
        # 直接写入（§3.2）：落盘即可读回，且时间戳为注入时钟
        assert {n.memory_node_id for n in graph.nodes()} == {n.memory_node_id for n in created}
        assert all(n.created_at == NOW for n in created)

    def test_blank_answers_are_skipped_not_written_as_empty(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        created = onboarding.build_initial_nodes(
            {"risk_preference": "   ", "investing_years": "5 年"}
        )
        assert [n.type for n in created] == ["identity"]
        assert created[0].investing_years == "5 年"
        assert created[0].risk_preference is None

    def test_all_blank_produces_nothing_and_does_not_raise(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        assert onboarding.build_initial_nodes({"risk_preference": ""}) == ()
        assert graph.nodes() == ()

    def test_collection_field_takes_single_value_or_a_sequence(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        one = onboarding.build_initial_nodes({"sector_preferences": "银行"})
        assert one[0].sector_preferences == ("银行",)

        multi = onboarding.build_initial_nodes({"theme_interests": ["AI", " 算力 "]})
        assert multi[0].theme_interests == ("AI", "算力")

    def test_unknown_question_id_is_rejected(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        with pytest.raises(MemoryValidationError, match="未知的 Onboarding 问题 id"):
            onboarding.build_initial_nodes({"nope": "x"})
        assert graph.nodes() == ()


class TestGwt4SkippedDimensionEntersEmptyStateWithoutBlocking:
    """GWT-4：跳过维度进空状态，且不阻断其他功能。"""

    def test_answered_dimensions_are_not_reported_as_empty(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        onboarding.build_initial_nodes(REQUIRED_ANSWERS)
        empty = onboarding.empty_dimensions()
        assert not set(REQUIRED_DIMENSIONS) & set(empty)

    def test_only_answered_dimension_leaves_others_empty(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        onboarding.build_initial_nodes({"risk_preference": "稳健"})
        empty = onboarding.empty_dimensions()
        assert "risk_preference" not in empty
        assert {"investing_years", "sector_preferences"} <= set(empty)

    def test_empty_state_is_a_query_not_a_block(
        self, graph: MemoryGraph, reader: MemoryReader, onboarding: OnboardingProtocol
    ) -> None:
        # 空图谱：全部维度为空、无副作用
        assert set(onboarding.empty_dimensions()) == {
            q.question_id for q in DEFAULT_ONBOARDING_QUESTIONS
        }
        assert set(onboarding.empty_state().values()) == {EMPTY_STATE_HINT}
        # 其他功能照常（切片查询不因空维度失败、也不被阻断）
        result = reader.query(SliceQuery(task_type="chat", topic="银行"))
        assert result.slices == ()

    def test_filled_dimension_disappears_from_empty_state(
        self, graph: MemoryGraph, onboarding: OnboardingProtocol
    ) -> None:
        onboarding.build_initial_nodes(REQUIRED_ANSWERS)
        assert "risk_preference" not in onboarding.empty_dimensions()
        assert "sector_preferences" not in onboarding.empty_dimensions()
        # 未答的维度仍在
        assert "capital_scale" in onboarding.empty_dimensions()


class TestGwt5UpdatingTheListTakesEffectAndLeavesATrace:
    """GWT-5：改清单即生效且留痕；值未变不留痕。"""

    def test_update_writes_entry_and_change_record(
        self, store: Store, onboarding: OnboardingProtocol
    ) -> None:
        shortened = DEFAULT_ONBOARDING_QUESTIONS[:2]
        change = onboarding.set_questions(shortened)
        assert change is not None
        assert change.config_id == ONBOARDING_CONFIG_ID
        assert change.old_value == [q.model_dump(mode="json")
                                    for q in DEFAULT_ONBOARDING_QUESTIONS]
        assert change.new_value == [q.model_dump(mode="json") for q in shortened]
        assert onboarding.questions() == shortened
        assert onboarding.changes() == (change,)
        assert QUESTIONS_PATH in store.list_files("config")

    def test_unchanged_value_leaves_no_trace(
        self, onboarding: OnboardingProtocol
    ) -> None:
        assert onboarding.set_questions(DEFAULT_ONBOARDING_QUESTIONS) is None
        assert onboarding.changes() == ()

    def test_timestamps_come_from_the_injected_clock(
        self, onboarding: OnboardingProtocol
    ) -> None:
        change = onboarding.set_questions(DEFAULT_ONBOARDING_QUESTIONS[:1])
        assert change is not None
        assert change.applied_at == NOW.isoformat()
