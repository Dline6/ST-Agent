"""T-L1-013.3 · 中立性规则库经官方 Pack 生效（01 §13 / §6；GWT-3 / GWT-4）。"""

from __future__ import annotations

import pytest

from st_agent.contracts.neutrality import (
    RULEPACK_KIND,
    NeutralityGuard,
    RulePack,
    default_rulepack,
    official_rulepack,
    set_official_rulepack,
)

CUSTOM_TOKEN = "嗷呜"


def _pack_with_first_person(*extra: str) -> RulePack:
    """内置规则库 + 追加的「第一人称」词（供命中测试）。"""
    base = default_rulepack()
    return RulePack(
        person_names=base.person_names,
        person_name_pattern=base.person_name_pattern,
        first_person=base.first_person + tuple(extra),
        emotion=base.emotion,
        dialogue=base.dialogue,
        personality_tags=base.personality_tags,
        anthro_suffixes=base.anthro_suffixes,
        functional_hint=base.functional_hint,
    )


@pytest.fixture(autouse=True)
def _reset_official_rulepack():
    """每例前后清空官方规则库——全局设置不跨例泄漏。"""
    set_official_rulepack(None)
    yield
    set_official_rulepack(None)


def test_rulepack_kind_is_registered() -> None:
    assert RULEPACK_KIND == "rulepack"


def test_official_rulepack_applies_to_guards_without_explicit_rules() -> None:
    set_official_rulepack(_pack_with_first_person(CUSTOM_TOKEN))
    guard = NeutralityGuard()                       # 未显式传规则库
    assert guard.check_output(CUSTOM_TOKEN).passed is False


def test_lazy_resolution_covers_guards_built_before_set() -> None:
    """import 期即构造的守卫（如 L4 模块级常量）也吃官方规则库——取用时解析。"""
    guard = NeutralityGuard()                       # 先构造（此刻官方未设）
    assert guard.check_output(CUSTOM_TOKEN).passed is True
    set_official_rulepack(_pack_with_first_person(CUSTOM_TOKEN))
    assert guard.check_output(CUSTOM_TOKEN).passed is False


def test_explicit_rulepack_wins_over_official() -> None:
    guard = NeutralityGuard(default_rulepack())    # 显式传内置规则库
    set_official_rulepack(_pack_with_first_person(CUSTOM_TOKEN))
    assert guard.check_output(CUSTOM_TOKEN).passed is True   # 显式者优先


def test_reset_restores_builtin() -> None:
    set_official_rulepack(_pack_with_first_person(CUSTOM_TOKEN))
    set_official_rulepack(None)
    assert official_rulepack() is None
    assert NeutralityGuard().check_output(CUSTOM_TOKEN).passed is True
