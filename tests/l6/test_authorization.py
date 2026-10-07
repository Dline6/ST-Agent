"""演进授权档位与风险分级清单（[`authorization.py`](../../src/st_agent/l6/authorization.py)）的验收用例。

对齐任务文件 [`T-L6-003.1`](../项目管理/tasks/T-L6-003.1-演进授权档位与风险分级清单.md) 的 GWT-1..5——
三档语义、档位是可配条目且切换留痕、实验授权面的实体、风险分级三类且未分类保守、
读不到即 fail-closed。
"""

from __future__ import annotations

import pytest

from l6_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l1.registry import ConfigRegistryFacade
from st_agent.l6 import (
    DEFAULT_RISK_GRADING,
    DEFAULT_SCOPES,
    DEFAULT_TIER,
    GRADING_CONFIG_ID,
    RISK_CLASSES,
    TIERS,
    TIER_CONFIG_ID,
    AuthorizationError,
    EvolutionAuthorization,
    EvolutionRiskRule,
    build_l6,
    evolution_family,
)

LOW_RISK = DEFAULT_SCOPES[0]
OTHER_SCOPE = "routine-report"


def face(tmp_path, **kw) -> EvolutionAuthorization:
    """带真 ``Store`` 的授权面。"""
    store = kw.pop("store", None) or Store.create(tmp_path / "root", PASS)
    return EvolutionAuthorization(store=store, now=lambda: NOW, **kw)


def facade_with(tmp_path, auth: EvolutionAuthorization) -> ConfigRegistryFacade:
    store = Store.create(tmp_path / "facade", PASS)
    facade = ConfigRegistryFacade(store)
    facade.register_family(evolution_family(auth))
    return facade


class TestGwt1TierSemantics:
    """GWT-1：按当前档位给结论——手动只建议、协作需批准、自主仅对「可自主」类放行。"""

    def test_default_tier_is_collaborative(self, tmp_path):
        assert face(tmp_path).tier() == DEFAULT_TIER == "collaborative"

    def test_manual_and_collaborative_never_permit(self, tmp_path):
        auth = face(tmp_path)
        for tier in ("manual", "collaborative"):
            auth.set_tier(tier)
            decision = auth.permit_decision(LOW_RISK)
            assert decision.permitted is False
            assert tier in decision.reason

    def test_autonomous_permits_low_risk_scope_only(self, tmp_path):
        auth = face(tmp_path)
        auth.set_tier("autonomous")
        assert auth.permits(LOW_RISK) is True
        assert auth.permits(OTHER_SCOPE) is False
        assert OTHER_SCOPE in auth.permit_decision(OTHER_SCOPE).reason

    def test_unknown_tier_is_refused(self, tmp_path):
        with pytest.raises(AuthorizationError):
            face(tmp_path).set_tier("god-mode")


class TestGwt2TierIsARegisteredEntry:
    """GWT-2：档位是 01 §7 登记条目、缺省 collaborative、切换**留痕**。"""

    def test_entry_is_registered_and_readable(self, tmp_path):
        auth = face(tmp_path)
        facade = facade_with(tmp_path, auth)
        listed = {e.config_id for e in facade.list(scope="global")}
        assert {TIER_CONFIG_ID, GRADING_CONFIG_ID} <= listed
        entry = facade.entry(TIER_CONFIG_ID)
        assert entry.default == DEFAULT_TIER
        assert entry.value_schema["enum"] == list(TIERS)
        assert entry.change_policy.requires_confirmation is True

    def test_switching_the_tier_records_a_change(self, tmp_path):
        auth = face(tmp_path)
        change = auth.set_tier("autonomous")
        assert (change.old_value, change.new_value) == (DEFAULT_TIER, "autonomous")
        assert [c.change_id for c in auth.changes()] == [change.change_id]

    def test_unchanged_tier_leaves_no_trace(self, tmp_path):
        auth = face(tmp_path)
        assert auth.set_tier(DEFAULT_TIER) is None
        assert auth.changes() == ()

    def test_scalar_entry_is_writable_through_the_facade(self, tmp_path):
        auth = face(tmp_path)
        facade = facade_with(tmp_path, auth)
        change = facade.set(TIER_CONFIG_ID, "manual")
        assert change.new_value == "manual" and auth.tier() == "manual"

    def test_grading_entry_is_object_valued_so_fail_closed(self, tmp_path):
        from st_agent.l1.registry.errors import RegistryValidationError

        auth = face(tmp_path)
        facade = facade_with(tmp_path, auth)
        with pytest.raises(RegistryValidationError) as err:
            facade.set(GRADING_CONFIG_ID, {"rules": []})
        assert "set_grading" in str(err.value)          # 点名写面归属，不静默落一个半截值


class TestGwt3ExperimentAuthorizerFace:
    """GWT-3：本面就是 A/B 实验启用门的**实体**（鸭子面 `permits(scope)`）。"""

    def test_build_l6_defaults_the_authorizer_to_this_face(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        built = build_l6(store, now=lambda: NOW)
        assert isinstance(built.authorization, EvolutionAuthorization)
        assert built.experiments.start("假设文案", LOW_RISK).enabled is False   # 缺省档＝协作
        built.authorization.set_tier("autonomous")
        assert built.experiments.start("假设文案", LOW_RISK).enabled is True

    def test_explicit_authorizer_still_wins(self, tmp_path):
        from types import SimpleNamespace

        store = Store.create(tmp_path / "root", PASS)
        deny = SimpleNamespace(permits=lambda _scope: False)
        built = build_l6(store, authorizer=deny, now=lambda: NOW)
        built.authorization.set_tier("autonomous")
        assert built.experiments.start("假设文案", LOW_RISK).enabled is False

    def test_out_of_list_scope_is_not_permitted_even_in_autonomous(self, tmp_path):
        auth = face(tmp_path)
        auth.set_tier("autonomous")
        assert auth.permits(OTHER_SCOPE) is False


class TestGwt4RiskGrading:
    """GWT-4：三类语义可查；**未分类即保守**；红线类永不放行。"""

    def test_three_classes_are_present(self):
        classes = {rule.risk_class for rule in DEFAULT_RISK_GRADING}
        assert classes <= set(RISK_CLASSES)

    @pytest.mark.parametrize(
        "config_id,expected",
        [
            ("memory-policy/dynamics", "never-autonomous"),
            ("memory.policy", "never-autonomous"),
            ("daily-report.time", "autonomous-ok"),
            ("weekly-report.template", "autonomous-ok"),
            ("channel-delivery.quiet-hours", "autonomous-ok"),
            ("skill.demo.param", "collaborative-required"),
            ("attention-budget.max-per-slot", "collaborative-required"),
        ],
    )
    def test_classification(self, tmp_path, config_id, expected):
        assert face(tmp_path).classify(config_id) == expected

    def test_unlisted_prefix_is_conservative(self, tmp_path):
        assert face(tmp_path).classify("something.new") == "collaborative-required"

    def test_grading_can_be_replaced_and_validated(self, tmp_path):
        auth = face(tmp_path)
        change = auth.set_grading([
            EvolutionRiskRule(prefix="toy.", risk_class="autonomous-ok"),
        ])
        assert change is not None and auth.classify("toy.x") == "autonomous-ok"
        assert auth.classify("daily-report.time") == "collaborative-required"  # 旧清单不再生效

    @pytest.mark.parametrize(
        "rules",
        [
            [],
            [{"prefix": "a.", "risk_class": "whatever"}],
            [{"prefix": "a.", "risk_class": "autonomous-ok"},
             {"prefix": "a.", "risk_class": "collaborative-required"}],
        ],
    )
    def test_bad_grading_is_refused(self, tmp_path, rules):
        with pytest.raises(AuthorizationError):
            face(tmp_path).set_grading(rules)

    def test_grading_is_readable_and_reload_safe(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        auth = EvolutionAuthorization(store=store, now=lambda: NOW)
        auth.set_grading([EvolutionRiskRule(prefix="toy.", risk_class="autonomous-ok")])
        again = EvolutionAuthorization(store=store, now=lambda: NOW)     # 换构造器续读
        assert again.classify("toy.x") == "autonomous-ok"


class TestGwt5FailClosed:
    """GWT-5：档位读不到即 fail-closed（**不臆测为自主**）；无门面仍可用（纯内存态）。"""

    def test_corrupt_tier_fails_closed(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        auth = EvolutionAuthorization(store=store, now=lambda: NOW)
        store.put("config", "evolution/authorization.json", b"{not json")
        with pytest.raises(AuthorizationError):
            auth.tier()
        decision = auth.permit_decision(LOW_RISK)
        assert decision.permitted is False
        assert "fail-closed" in decision.reason

    def test_memory_red_line_has_no_autonomous_path(self, tmp_path):
        auth = face(tmp_path)
        auth.set_tier("autonomous")
        assert auth.classify("memory-policy/dynamics") == "never-autonomous"

    def test_without_a_registry_entries_still_read(self, tmp_path):
        auth = EvolutionAuthorization(now=lambda: NOW)          # 纯内存态
        assert auth.tier() == DEFAULT_TIER
        assert {e.config_id for e in auth.entries()} == {TIER_CONFIG_ID, GRADING_CONFIG_ID}

    def test_face_is_returned_by_build_l6_for_the_gate(self, tmp_path):
        built = build_l6(now=lambda: NOW)
        assert built.authorization is not None
        assert built.authorization.permits(LOW_RISK) is False
        assert built.change_flow is not None
        assert built.factory_reset is not None
