"""运行期授权档位与共享清单（[`runtime_authorization.py`](../../src/st_agent/l6/runtime_authorization.py)）的验收用例。

对齐任务文件 [`T-AGT-005.1`](../项目管理/tasks/T-AGT-005.1-运行期授权档位与共享清单.md) 的 GWT-1..5——
**共享清单只此一份**（红线单点）、**档位是两条彼此独立的条目**、动作标识可判且未命中即保守、
缺省与损坏都不放行；另附档位切换留痕落 `agent-change/`、族适配器与 `build_l6` 装配。

执行面一律**真** `Store` + 真 [`EvolutionAuthorization`](../../src/st_agent/l6/authorization.py)
（清单本体不注入替身——「同一张清单」这件事只有拿真面才验得出来）。
"""

from __future__ import annotations

import pytest

from l6_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l1.registry import ConfigRegistryFacade
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l6 import (
    ACTION_VERDICTS,
    AGENT_CHANGE_PREFIX,
    AGENT_TIER_CONFIG_ID,
    DEFAULT_TIER,
    GRADING_CONFIG_ID,
    MEMORY_WRITE_ACTION,
    NET_EGRESS_ACTION,
    TIER_CONFIG_ID,
    AuthorizationError,
    EvolutionAuthorization,
    EvolutionRiskRule,
    RuntimeAuthorization,
    agent_family,
    build_l6,
    evolution_family,
    skill_action_id,
)

ACTION = skill_action_id("sk_stock_watch")
"""默认清单下「须协作」的技能动作标识（`skill.` 前缀，08 §5）。"""

APPROVED = skill_action_id("sk_approved_tool")
"""一份**自定义**清单里被标为可自主的技能动作标识（缺省清单下没有可自主的动作，见下）。"""

AUTONOMOUS_ONLY_RULES = (
    EvolutionRiskRule(prefix=APPROVED, risk_class="autonomous-ok"),
    EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
)
"""把 APPROVED 从 `skill.` 一族里单独摘出来标为可自主——同时验**首个命中前缀**。"""


def shared(tmp_path, *, rules=None):
    """带真 ``Store`` 的清单面（演进授权面即清单 owner）+ 运行期授权面。"""
    store = Store.create(tmp_path / "root", PASS)
    authz = EvolutionAuthorization(store=store, now=lambda: NOW)
    if rules is not None:
        authz.set_grading(rules)
    return store, authz, RuntimeAuthorization(
        store=store, grading=authz, now=lambda: NOW,
    )


# ───────────────────────── GWT-1 · 单一清单 ─────────────────────────


class TestGwt1SingleChecklist:
    """运行期侧**不登记第二张清单**——两处读的是同一 `config_id`、同一取值。"""

    def test_runtime_registers_only_the_runtime_tier(self, tmp_path):
        _store, _authz, runtime = shared(tmp_path)
        assert [entry.config_id for entry in runtime.entries()] == [AGENT_TIER_CONFIG_ID]
        assert AGENT_TIER_CONFIG_ID != GRADING_CONFIG_ID

    def test_grading_source_is_the_evolution_face(self, tmp_path):
        _store, authz, runtime = shared(tmp_path)
        assert runtime.grading_source is authz          # 同一实例，非副本

    def test_both_families_expose_exactly_one_grading_entry(self, tmp_path):
        store, authz, runtime = shared(tmp_path)
        facade = ConfigRegistryFacade(store)
        facade.register_family(evolution_family(authz))
        facade.register_family(agent_family(runtime))
        ids = [entry.config_id for entry in facade.list()]
        assert ids.count(GRADING_CONFIG_ID) == 1
        assert ids.count(AGENT_TIER_CONFIG_ID) == 1

    def test_replacing_the_shared_list_moves_both_readers(self, tmp_path):
        _store, authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        runtime.set_tier("autonomous")
        assert runtime.decide(APPROVED).verdict == "autonomous-ok"
        assert authz.classify(APPROVED) == "autonomous-ok"

        authz.set_grading((EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),))
        assert authz.classify(APPROVED) == "collaborative-required"
        assert runtime.decide(APPROVED).verdict == "needs-confirmation"   # 一处改，两处同变


# ───────────────────────── GWT-2 · 红线单点 ─────────────────────────


class TestGwt2RedLineIsSinglePointed:
    """`memory.*` 红线**只登记一处**即对两处生效（08 §7）。"""

    def test_memory_write_is_never_autonomous_on_both_sides(self, tmp_path):
        _store, authz, runtime = shared(tmp_path)
        runtime.set_tier("autonomous")                  # 即便运行期档已最宽
        assert authz.classify(MEMORY_WRITE_ACTION) == "never-autonomous"
        decision = runtime.decide(MEMORY_WRITE_ACTION)
        assert decision.risk_class == "never-autonomous"
        assert decision.verdict == "denied"
        assert decision.permitted is False

    def test_memory_rules_follow_the_shared_list(self, tmp_path):
        """把 memory 规则从清单里拿掉 ⇒ **两处同时**不再判红线（证明不是各标一处）。"""
        _store, authz, runtime = shared(tmp_path, rules=(
            EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
        ))
        runtime.set_tier("autonomous")
        assert authz.classify(MEMORY_WRITE_ACTION) == "collaborative-required"
        assert runtime.decide(MEMORY_WRITE_ACTION).verdict == "needs-confirmation"


# ───────────────────────── GWT-3 · 动作标识可判 ─────────────────────────


class TestGwt3ActionIdsAreJudgeable:
    """动作标识按**首个命中前缀**判；未命中任何规则即保守（不静默放行）。"""

    def test_skill_base_becomes_the_action_id(self):
        assert skill_action_id("sk_stock_watch") == "skill.sk_stock_watch"
        with pytest.raises(AuthorizationError):
            skill_action_id("  ")

    def test_default_list_puts_every_skill_under_collaborative(self, tmp_path):
        _store, _authz, runtime = shared(tmp_path)
        decision = runtime.decide(ACTION)
        assert decision.risk_class == "collaborative-required"
        assert decision.verdict == "needs-confirmation"

    def test_first_matching_prefix_wins(self, tmp_path):
        _store, _authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        runtime.set_tier("autonomous")
        assert runtime.decide(APPROVED).risk_class == "autonomous-ok"      # 更具体的前缀先命中
        assert runtime.decide(ACTION).risk_class == "collaborative-required"

    def test_unmatched_action_is_conservative(self, tmp_path):
        """`net.egress` 未登记任何规则 ⇒ 保守（白名单语义，01 §7）。"""
        _store, _authz, runtime = shared(tmp_path)
        runtime.set_tier("autonomous")
        decision = runtime.decide(NET_EGRESS_ACTION)
        assert decision.risk_class == "collaborative-required"
        assert decision.verdict == "needs-confirmation"
        assert decision.permitted is False


# ───────────────────────── GWT-4 · 档位彼此独立 ─────────────────────────


class TestGwt4TiersAreIndependent:
    """演进档与运行期档**互不顶替**——工具调用听运行期档的。"""

    def test_evolution_autonomous_does_not_open_the_runtime_gate(self, tmp_path):
        store, authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        authz.set_tier("autonomous")                    # 演进档最宽
        assert authz.tier() == "autonomous"
        assert runtime.tier() == DEFAULT_TIER == "collaborative"
        assert runtime.decide(APPROVED).verdict == "needs-confirmation"

    def test_runtime_autonomous_opens_only_the_runtime_gate(self, tmp_path):
        _store, authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        runtime.set_tier("autonomous")
        assert runtime.decide(APPROVED).verdict == "autonomous-ok"
        assert authz.tier() == DEFAULT_TIER               # 演进档**未被动过**
        assert authz.permits("delivery-strategy") is False

    def test_later_evolution_change_does_not_move_the_runtime_tier(self, tmp_path):
        _store, authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        runtime.set_tier("autonomous")
        authz.set_tier("manual")
        assert runtime.tier() == "autonomous"
        assert runtime.decide(APPROVED).verdict == "autonomous-ok"


# ───────────────────────── GWT-5 · 缺省与损坏都不放行 ─────────────────────────


class TestGwt5AbsentOrCorruptFailsClosed:
    """条目未落值 ⇒ `collaborative`；条目 / 清单**损坏 ⇒ 一律不放行**并给出成因。"""

    def test_default_tier_keeps_even_autonomous_class_actions_back(self, tmp_path):
        _store, _authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        decision = runtime.decide(APPROVED)
        assert decision.tier == "collaborative"
        assert decision.verdict == "needs-confirmation"
        assert decision.permitted is False

    def test_missing_entry_reads_as_collaborative_without_writing(self, tmp_path):
        store, _authz, runtime = shared(tmp_path)
        assert runtime.tier() == "collaborative"
        assert store.list_files("config") == ()           # 读面不落值

    def test_corrupt_tier_entry_is_refused_and_fails_closed(self, tmp_path):
        store, _authz, runtime = shared(tmp_path, rules=AUTONOMOUS_ONLY_RULES)
        store.put("config", "agent/authorization.json", b"{ this is not json")
        with pytest.raises(AuthorizationError):
            runtime.tier()
        decision = runtime.decide(APPROVED)
        assert decision.verdict == "denied"
        assert "fail-closed" in decision.reason
        assert decision.tier is None                      # 读不出即不猜

    def test_corrupt_checklist_fails_closed_too(self, tmp_path):
        store, _authz, runtime = shared(tmp_path)
        store.put("config", "evolution/risk-grading.json", b"{ broken")
        decision = runtime.decide(ACTION)
        assert decision.verdict == "denied"
        assert "fail-closed" in decision.reason
        assert decision.risk_class is None

    def test_without_a_checklist_face_nothing_is_judged(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        runtime = RuntimeAuthorization(store=store, now=lambda: NOW)
        decision = runtime.decide(ACTION)
        assert decision.verdict == "denied"
        with pytest.raises(AuthorizationError):
            runtime.classify(ACTION)

    def test_empty_action_id_is_a_caller_bug(self, tmp_path):
        _store, _authz, runtime = shared(tmp_path)
        with pytest.raises(AuthorizationError):
            runtime.decide("   ")


# ───────────────────────── 档位切换的留痕落点 ─────────────────────────


class TestTierChangeTrail:
    """档位**配置切换**照 01 §7 留痕，但落 `agent-change/`——不混进演进变更历史。"""

    def test_switch_leaves_a_change_under_the_agent_directory(self, tmp_path):
        store, _authz, runtime = shared(tmp_path)
        change = runtime.set_tier("autonomous", trace_ref="tr_agent")
        assert change is not None and change.config_id == AGENT_TIER_CONFIG_ID
        assert runtime.tier() == "autonomous"
        files = store.list_files("execution_log")
        assert files == (f"{AGENT_CHANGE_PREFIX}{change.change_id}.json",)
        assert not any(name.startswith("evolution-change/") for name in files)

    def test_unchanged_value_leaves_no_trail(self, tmp_path):
        _store, _authz, runtime = shared(tmp_path)
        assert runtime.set_tier("collaborative") is None       # ＝缺省，取值未变
        assert runtime.changes() == ()

    def test_cross_family_reader_still_sees_the_agent_change(self, tmp_path):
        """`facade.changes()` 按 `*-change` 目录跨族读取 ⇒ 运行期档位切换**可追溯不丢**。"""
        store, _authz, runtime = shared(tmp_path)
        runtime.set_tier("autonomous")
        facade = ConfigRegistryFacade(store)
        facade.register_family(agent_family(runtime))
        records = facade.changes()
        assert [record.config_id for record in records] == [AGENT_TIER_CONFIG_ID]


# ───────────────────────── 族适配器与装配 ─────────────────────────


class TestFamilyAndWiring:
    """`agent.*` 族的门面适配器与 `build_l6` 的接线。"""

    def _facade(self, tmp_path):
        store, _authz, runtime = shared(tmp_path)
        facade = ConfigRegistryFacade(store)
        facade.register_family(agent_family(runtime))
        return facade, runtime

    def test_apply_delegates_to_the_tier_owner(self, tmp_path):
        facade, runtime = self._facade(tmp_path)
        record = facade.set(AGENT_TIER_CONFIG_ID, "autonomous")
        assert record is not None and record.new_value == "autonomous"
        assert runtime.tier() == "autonomous"

    def test_unknown_agent_entry_and_foreign_entry_are_refused(self, tmp_path):
        facade, _runtime = self._facade(tmp_path)
        with pytest.raises(RegistryValidationError):
            facade.set("agent.nope", "x")
        with pytest.raises(RegistryValidationError):
            facade.set(GRADING_CONFIG_ID, {})          # 清单另一处，写面不归本族

    def test_illegal_tier_is_refused_by_the_family(self, tmp_path):
        facade, _runtime = self._facade(tmp_path)
        with pytest.raises(RegistryValidationError):
            facade.set(AGENT_TIER_CONFIG_ID, "god-mode")

    def test_build_l6_wires_the_runtime_face_to_the_shared_checklist(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        facade = ConfigRegistryFacade(store)
        stack = build_l6(store, registry=facade, now=lambda: NOW)
        runtime = stack.agent_authorization
        assert runtime is not None
        assert runtime.grading_source is stack.authorization        # 清单只此一份
        assert runtime.tier() == "collaborative"                    # 档位独立、缺省最保守
        assert facade.entry(AGENT_TIER_CONFIG_ID) is not None
        assert facade.entry(TIER_CONFIG_ID) is not None
        assert facade.entry(AGENT_TIER_CONFIG_ID).config_id != TIER_CONFIG_ID


def test_verdict_vocabulary_is_three_states():
    """三态结论词表（与 L3 的 `GATE_VERDICTS` 相乘由 `tests/l3/test_agent_gate.py` 跨层钉住）。"""
    assert ACTION_VERDICTS == ("autonomous-ok", "needs-confirmation", "denied")
