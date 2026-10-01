"""T-L1-013.3 · 官方资源包在组合根装配（01 §13；GWT-1 / GWT-3 / GWT-4）。

验证 `build_m1_runtime` 把官方 Pack 的 `skill` / `command` / `rulepack` 三类
经容器分发到各消费方；无 Pack 时的内置缺省不受影响（由各层单测覆盖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m1 import M1Rig, seeded_m1

from st_agent.contracts.neutrality import NeutralityGuard, official_rulepack, set_official_rulepack
from st_agent.l2.memory import DEFAULT_ONBOARDING_QUESTIONS

STORIES_FIVE = ("盯盘", "回测", "压测", "今日看板", "反思")


@pytest.fixture()
def rig(tmp_path: Path) -> M1Rig:
    yield_wrapper = seeded_m1(tmp_path / ROOT_NAME)
    yield yield_wrapper
    set_official_rulepack(None)          # 组合根设了全局规则库——用后清理


def test_official_commands_come_from_pack(rig: M1Rig) -> None:
    """GWT-1：装载后官方指令集＝Pack 的 `command` kind（5 条预置）。"""
    official = rig.m1.commands.list(source="official")
    assert tuple(c.name for c in official) == tuple(sorted(STORIES_FIVE))


def test_official_rulepack_is_installed_by_root(rig: M1Rig) -> None:
    """GWT-3：组合根经 Pack 注入官方规则库（全局生效面已设置）。"""
    _ = rig
    assert official_rulepack() is not None


def test_guard_resolves_official_rulepack_after_assembly(rig: M1Rig) -> None:
    """装配后未显式传规则库的守卫按官方 Pack 解析（此处官方＝内置，行为不变）。"""
    _ = rig
    guard = NeutralityGuard()
    assert guard.check_output("这只标的我认为会涨").passed is False   # 第一人称仍被拦


def test_onboarding_default_comes_through_pack(rig: M1Rig) -> None:
    """GWT-1：`onboarding_questions` kind 经容器分发，缺省＝Pack 清单（引用内置）。"""
    assert rig.m1.onboarding.questions() == DEFAULT_ONBOARDING_QUESTIONS
