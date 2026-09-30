"""T-L1-012.3 · 统一配置注册表门面接入组合根的端到端（跨层装配）。

只测**装配关系与跨层数据流**，不重复 `.1` / `.2` 的单测：

- GWT-1 双通道升真：生产组合根装配后，每个已声明参数取到**真登记项**（非声明面回落）
- GWT-2 接受真落值：`ConfigDraftHandling.accept` 经门面落值并产生真 `change_id`、可读回
- GWT-3 未装 L2 的运行时仍可装配：`memory-policy` 族缺席由「不在列」显式体现
- GWT-4 缺省行为不变：不传 `registry=` 时端口语义与门面交付前**逐字节一致**
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m1 import M1Rig, seeded_m1

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, build_l1_runtime
from st_agent.l3.config import ConfigDraft, ConfigDraftHandling, dual_channel_view

PASS = "correct horse battery staple"
SKILL = "sk_risk_alert_v1.0"
PARAM = "lookahead_days"


class _FakeMarketQuery:
    """恒空取数源（官方 Pack 装配只要求「有取数面」）。"""

    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        return ResultEnvelope.empty("测试数据面为空")


@pytest.fixture()
def rig(tmp_path: Path) -> M1Rig:
    return seeded_m1(tmp_path / ROOT_NAME)


@pytest.fixture()
def l1_only(tmp_path: Path) -> L1Runtime:
    store = Store.create(tmp_path / "l1-root", PASS)
    return build_l1_runtime(store, market_query=_FakeMarketQuery())


# ─────────────────────────── GWT-1 双通道升真 ───────────────────────────


def test_dual_channel_rises_from_declared_to_registry(rig: M1Rig) -> None:
    facade = rig.m1.runtime.config_registry
    view = dual_channel_view(SKILL, descriptors=rig.m1.runtime.skills, registry=facade)
    assert view.status == "ok"
    assert view.data.params
    # 已声明参数全部取到**真登记项**
    assert all(p.origin == "registry" for p in view.data.params)
    # 两通道取自**同一条**条目（同一 config_id / scope）
    for param in view.data.params:
        assert param.config_id == f"skill.sk_risk_alert.{param.name}"
        assert param.scope == "skill"


def test_global_families_are_reachable_from_the_assembled_root(rig: M1Rig) -> None:
    ids = {e.config_id for e in rig.m1.runtime.config_registry.list()}
    # L0 孤儿（纳注册）+ L1 调度策略
    assert {
        "retention.chat_history_days",
        "retention.execution_log_days",
        "scheduler-policy/offline-catch-up",
    } <= ids
    # L2 族由 app 根注入（L1 不 import L2）
    assert any(i.startswith("memory-policy/") for i in ids)


# ─────────────────────────── GWT-2 接受真落值 ───────────────────────────


def test_accept_persists_through_the_facade(rig: M1Rig) -> None:
    draft = ConfigDraft(
        target=SKILL, parameter_draft={PARAM: 60},
        understanding_summary=f"参数 {PARAM}：60 天",
    )
    accepted = rig.m1.handling.accept(draft)
    assert accepted.status == "ok", accepted.reason
    assert accepted.data.change_ids
    assert all(cid.startswith("chg_") for cid in accepted.data.change_ids)

    entry = rig.m1.runtime.config_registry.entry_for(SKILL, PARAM)
    assert entry is not None
    assert entry.default == 60


def test_accept_rejects_value_outside_declaration(rig: M1Rig) -> None:
    draft = ConfigDraft(
        target=SKILL, parameter_draft={PARAM: 9_999},
        understanding_summary=f"参数 {PARAM}：越界",
    )
    rejected = rig.m1.handling.accept(draft)
    assert rejected.status == "dependency_failed", rejected.reason


# ─────────────────────── GWT-3 未装 L2 仍可装配 ───────────────────────


def test_l1_only_runtime_assembles_and_omits_l2_family(l1_only: L1Runtime) -> None:
    ids = {e.config_id for e in l1_only.config_registry.list()}
    assert not any(i.startswith("memory-policy/") for i in ids)   # L2 不在场
    assert "retention.chat_history_days" in ids                   # L0 族仍在场
    # L1 侧的作用域参数族照常可用
    entry = l1_only.config_registry.entry_for(SKILL, PARAM)
    assert entry is not None and entry.default == 30


# ─────────────────────── GWT-4 缺省行为逐字节不变 ───────────────────────


def test_absent_registry_keeps_the_declared_fallback(rig: M1Rig) -> None:
    view = dual_channel_view(SKILL, descriptors=rig.m1.runtime.skills)
    assert view.status == "ok"
    assert all(p.origin == "declared" for p in view.data.params)

    bare = ConfigDraftHandling(descriptors=rig.m1.runtime.skills)
    draft = ConfigDraft(
        target=SKILL, parameter_draft={PARAM: 60},
        understanding_summary=f"参数 {PARAM}：60 天",
    )
    assert bare.accept(draft).status == "unavailable"
