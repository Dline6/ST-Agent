"""T-L1-013.4 · Onboarding 问题清单经官方 Pack 注入（01 §13 / 04 §7；GWT-1..4）。"""

from __future__ import annotations

import pytest

from st_agent.l2.memory import (
    DEFAULT_ONBOARDING_QUESTIONS,
    ONBOARDING_KIND,
    MAX_ONBOARDING_QUESTIONS,
    MemoryValidationError,
    OnboardingProtocol,
    OnboardingQuestion,
    official_onboarding_entry,
)

CUSTOM = (
    OnboardingQuestion(question_id="risk_preference", node_type="identity",
                       field="risk_preference", prompt="风险偏好属于哪一类？"),
    OnboardingQuestion(question_id="sector_preferences", node_type="attention",
                       field="sector_preferences", prompt="当前关注哪些板块？"),
)


def test_official_onboarding_entry_shape() -> None:
    entry = official_onboarding_entry()
    assert entry.kind == ONBOARDING_KIND == "onboarding_questions"
    assert entry.payload is DEFAULT_ONBOARDING_QUESTIONS     # 引用（非复制）


def test_without_pack_falls_back_to_builtin(onboarding: OnboardingProtocol) -> None:
    """GWT-2：未注入 → 内置缺省，行为与交付前一致。"""
    assert onboarding.questions() == DEFAULT_ONBOARDING_QUESTIONS


def test_injected_default_is_used_and_surfaces_in_entry(
    onboarding: OnboardingProtocol,
) -> None:
    """GWT-1：注入后缺省＝Pack 清单，配置条目的 default 亦随之为 Pack 清单。"""
    onboarding.set_official_default(CUSTOM)
    assert onboarding.questions() == CUSTOM
    assert onboarding.question_entry().default == [q.model_dump(mode="json") for q in CUSTOM]


def test_config_entry_stays_writable_after_injection(
    onboarding: OnboardingProtocol,
) -> None:
    """GWT-3：来源改变不降级——仍可改一版并留痕（01 §7 三能力不变）。"""
    onboarding.set_official_default(CUSTOM)
    record = onboarding.set_questions(CUSTOM[:1])
    assert record is not None                                # 取值已变 → 留痕
    assert onboarding.questions() == CUSTOM[:1]


def test_invalid_injection_is_rejected(onboarding: OnboardingProtocol) -> None:
    """GWT-4：超上限 / id 重复一律显式拒（不静默截断）。"""
    too_many = tuple(
        OnboardingQuestion(question_id=f"q{i}", node_type="identity",
                           field="risk_preference", prompt="x")
        for i in range(MAX_ONBOARDING_QUESTIONS + 1)
    )
    with pytest.raises(MemoryValidationError):
        onboarding.set_official_default(too_many)

    duplicated = CUSTOM + (CUSTOM[0],)
    with pytest.raises(MemoryValidationError):
        onboarding.set_official_default(duplicated)
