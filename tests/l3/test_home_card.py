"""T-L3-001.2 · 上下文卡片（05 §2；GWT-1..6）。"""

from __future__ import annotations

import pytest
from l3_helpers import STOCK_A, STOCK_B, node

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l2.memory import MemoryReader, MemoryWriter
from st_agent.l3 import SessionValidationError
from st_agent.l3.home import (
    CARD_TITLE,
    EMPTY_CARD_HINT,
    HOME_GREETING,
    PROFILE_TAG_LIMIT,
    SECTION_KEYS,
    SECTION_TITLES,
    CardTag,
    build_context_card,
    checked_section,
)


class TestGwt1SectionsComeFromMemorySlices:
    """GWT-1：四段内容全部来自记忆切片，逐项可回溯到节点。"""

    def test_items_trace_back_to_seeded_nodes(
        self, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        attention = writer.add_node(node(
            "attention", holdings=(STOCK_A,), watchlist=(STOCK_B,),
            sector_preferences=("银行", "券商"),
        ))
        thesis = writer.add_node(node("thesis", view="看好反转"))

        card = build_context_card(reader)
        sections = {s.key: s for s in card.sections}

        assert card.is_empty is False
        assert card.envelope.status == "ok"
        assert sections["holdings"].items == (STOCK_A,)
        assert sections["watchlist"].items == (STOCK_B,)
        assert sections["thesis"].items == (f"{STOCK_A}：看好反转",)
        assert {t.label for t in sections["profile"].tags} == {"银行", "券商"}
        labeled = [t for t in sections["profile"].tags if t.label == "银行"][0]
        assert labeled.source_node_id == attention.memory_node_id
        assert thesis.memory_node_id  # 节点确已入库（切片来自它）

    def test_missing_dimension_is_empty_with_reason(
        self, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        writer.add_node(node("attention", holdings=(), watchlist=(STOCK_B,),
                             sector_preferences=("银行",)))

        sections = {s.key: s for s in build_context_card(reader).sections}
        assert sections["holdings"].state == "empty"
        assert sections["holdings"].reason
        assert sections["thesis"].state == "empty"
        assert sections["thesis"].reason


class TestGwt2ProfileTagLimit:
    """GWT-2：画像卡不超过 5 条，且截断可见。"""

    def test_tags_are_capped_and_truncation_is_visible(
        self, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        writer.add_node(node(
            "attention",
            holdings=(STOCK_A,),
            sector_preferences=("银行", "券商", "保险", "地产"),
            theme_interests=("AI", "算力", "半导体"),
        ))
        writer.add_node(node("identity", risk_preference="稳健", investing_years="5 年"))

        profile = {s.key: s for s in build_context_card(reader).sections}["profile"]
        assert len(profile.tags) == PROFILE_TAG_LIMIT
        assert profile.limit == PROFILE_TAG_LIMIT
        assert profile.total == 9, "截断前的候选条数须如实给出"
        assert profile.total > len(profile.tags)

    def test_over_limit_is_rejected_at_construction(self) -> None:
        too_many = tuple(
            CardTag(label=f"标签{i}", kind="sector", source_node_id="node_x")
            for i in range(PROFILE_TAG_LIMIT + 1)
        )
        with pytest.raises(SessionValidationError):
            checked_section(key="profile", title="偏好画像", state="ok", tags=too_many)

    def test_ok_section_without_content_and_empty_section_without_reason_are_rejected(
        self,
    ) -> None:
        with pytest.raises(SessionValidationError):
            checked_section(key="holdings", title="持仓摘要", state="ok")
        with pytest.raises(SessionValidationError):
            checked_section(key="holdings", title="持仓摘要", state="empty")


class TestGwt3EmptyState:
    """GWT-3：新用户无记忆 → 空状态 + 中性引导 + Onboarding 目标。"""

    def test_empty_graph_yields_guided_empty_card(self, reader: MemoryReader) -> None:
        card = build_context_card(reader)

        assert card.is_empty is True
        assert card.empty_hint == EMPTY_CARD_HINT
        assert card.onboarding_target is not None
        assert card.onboarding_target.kind == "onboarding"
        assert card.envelope.status == "empty"
        assert card.envelope.reason
        assert tuple(s.key for s in card.sections) == SECTION_KEYS

    def test_non_empty_card_carries_no_onboarding_target(
        self, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        writer.add_node(node("thesis", view="看好反转"))
        card = build_context_card(reader)
        assert card.onboarding_target is None and card.empty_hint is None


class TestGwt4UnavailableSlots:
    """GWT-4：生产方未就绪的两段显式不可用、带原因，且不随空状态被掩盖。"""

    @pytest.mark.parametrize("seed", [False, True])
    def test_slots_stay_unavailable_with_reason(
        self, reader: MemoryReader, writer: MemoryWriter, seed: bool
    ) -> None:
        if seed:
            writer.add_node(node("thesis", view="看好反转"))

        sections = {s.key: s for s in build_context_card(reader).sections}
        for key in ("unread_alerts", "hot_topics"):
            assert sections[key].state == "unavailable"
            assert sections[key].reason
            assert sections[key].producer
            assert sections[key].items == ()


class TestGwt5GraphTarget:
    """GWT-5：卡片携「进入完整图谱」的导航目标。"""

    def test_graph_target_points_at_the_graph_view(
        self, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        writer.add_node(node("thesis", view="看好反转"))
        target = build_context_card(reader).graph_target

        assert target.kind == "memory_graph"
        assert target.query is not None
        assert target.query.view == "graph"
        assert target.query.token_budget is None, "图谱视图要看全貌，不按对话预算截断"


class TestGwt6OwnCopyPassesNeutralityGuard:
    """GWT-6：卡片自有固定文案全部过 01 §6 校验（铁律 2；记忆本体内容不在其列）。"""

    def test_titles_greetings_and_reasons_pass(self) -> None:
        guard = NeutralityGuard()
        assert guard.check_name(CARD_TITLE).passed
        for text in (HOME_GREETING, EMPTY_CARD_HINT, *SECTION_TITLES.values()):
            verdict = guard.check_output(text)
            assert verdict.passed, (text, verdict.findings)

    def test_unavailable_reasons_pass(self, reader: MemoryReader) -> None:
        guard = NeutralityGuard()
        sections = {s.key: s for s in build_context_card(reader).sections}
        for key in ("unread_alerts", "hot_topics"):
            assert guard.check_output(sections[key].reason or "").passed

    def test_guard_rejects_the_storys_literal_wording(self) -> None:
        """反证：Story / §2 的示例措辞确实会被拦——故本模块取的是中性改述。"""
        guard = NeutralityGuard()
        assert not guard.check_output("你还没告诉我任何偏好，我们先聊两句？").passed
        assert not guard.check_output("副驾眼中的我").passed
