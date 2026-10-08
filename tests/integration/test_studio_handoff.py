"""`T-L6-004.2` · 主动提案落 Studio 草稿接收面的**跨层装配**用例。

只测**装配关系与跨层数据流**（翻译与会话三态的行为已由 `tests/l6/test_studio_adapter.py`
覆盖）：主动提案 → 组合根翻译 → `DraftIntake.receive` 落画布 → 接受落 v1.0，全程经
**生产组合根** `build_m4_runtime`（真 `Store` / 真 `SkillRegistry` / 真 `DraftIntake`）。

锚点：[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 的「衔接 story-06」
（[story-09](../../docs/PRD-v2-Agent/story-09-reflection-loop.md) 的「要不要创建一个专门 Skill？」+ 草稿）。
全部离线（出网面由 rig 替身接管）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from rig import ROOT_NAME
from rig_m4 import NOW, M4Rig, observation, seeded_m4

from st_agent.app import ReflectionFacade


@pytest.fixture()
def rig(tmp_path: Path) -> M4Rig:
    """真装配 + 一条必然过阈值的模式观察（`count=6 > 缺省 5`）。"""
    return seeded_m4(tmp_path / ROOT_NAME, observations=(observation(),))


def _detect_one(rig: M4Rig) -> str:
    """跑一次模式识别并返回唯一那条提案的 `proposal_id`。"""
    rig.m4.l6.proposals.detect(now=NOW)
    proposals = rig.m4.l6.proposals.all()
    assert len(proposals) == 1
    return proposals[0].proposal_id


# ───────────────────────── GWT-4 真装配端到端 ─────────────────────────


def test_gwt4_proposal_handoff_then_accept_lands_workflow(rig: M4Rig) -> None:
    pid = _detect_one(rig)
    face = rig.m4.reflection
    assert isinstance(face, ReflectionFacade)

    view = face.studio_handoff(pid)

    assert view["available"] is True
    assert view["base"] == "wf_same_kind_helper"
    assert view["flow_id"].endswith("_v0.0")                 # 画布期临时身份
    assert view["node_count"] == 1
    assert view["violations"] == []                          # 引用的官方 Skill 已注册

    accepted = face.studio_decide(proposal_id=pid, action="accept")

    assert accepted["applied"] is True
    assert accepted["flow_id"].endswith("_v1.0")             # 接受落首版
    assert accepted["change_id"].startswith("chg_")
    assert f"workflow/{accepted['flow_id']}.json" in rig.m4.store.list_files("config")
    assert (
        f"workflow-change/{accepted['change_id']}.json"
        in rig.m4.store.list_files("execution_log")
    )


def test_gwt4_reject_leaves_config_untouched(rig: M4Rig) -> None:
    pid = _detect_one(rig)
    face = rig.m4.reflection
    face.studio_handoff(pid)

    result = face.studio_decide(proposal_id=pid, action="reject")

    assert result["rejected"] is True
    assert not [f for f in rig.m4.store.list_files("config") if f.startswith("workflow/")]


def test_gwt4_decide_before_handoff_is_input_failure(rig: M4Rig) -> None:
    """未交 Studio 就处置 ⇒ 组合根转 `ValueError`（表现层据此回 `validation_failed`）。"""
    pid = _detect_one(rig)
    with pytest.raises(ValueError, match="尚未交 Studio"):
        rig.m4.reflection.studio_decide(proposal_id=pid, action="accept")


# ───────────────────────── GWT-1 适配面 fail-closed ─────────────────────────


def test_gwt1_studio_subface_absent_is_unavailable_not_faked() -> None:
    """`L6Stack.studio is None` ⇒ `available: false`（**不**伪造「已交 Studio」）。"""
    face = ReflectionFacade(l6=SimpleNamespace(studio=None), feedback=lambda *a, **k: None)
    assert face.studio_handoff("prp_x")["available"] is False
    assert face.studio_decide(proposal_id="prp_x", action="accept")["available"] is False


def test_gwt1_unknown_action_is_input_failure(rig: M4Rig) -> None:
    """`defer` 不在 Studio 两动作内（微调需画布页）⇒ `ValueError`。"""
    with pytest.raises(ValueError, match="accept/reject"):
        rig.m4.reflection.studio_decide(proposal_id="prp_x", action="defer")
