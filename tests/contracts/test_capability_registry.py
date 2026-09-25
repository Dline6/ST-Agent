"""T-SC-001.4 测试：§2 SkillDescriptor / §3 LensOpinion / §7 配置元模型 / §9 版本化 / §10 权限。"""

import pytest
from pydantic import ValidationError

from st_agent.contracts import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    ContractViolation,
    LensOpinion,
    PanelField,
    ParameterSpec,
    Provenance,
    SemVer,
    SkillDescriptor,
    parse_permission,
    validate_permissions,
)


def param(**kw) -> ParameterSpec:
    base = dict(name="threshold", type="number", default=0.8,
                min_value=0.0, max_value=1.0, description="触发阈值")
    base.update(kw)
    return ParameterSpec(**base)


def descriptor(**kw) -> SkillDescriptor:
    base = dict(
        skill_id="sk_delisting_risk_scan_v1",
        name="退市风险扫描",
        description="识别退市高危信号的 Skill，每日名单同步后调用",
        input_schema={"type": "object", "properties": {"stock_id": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"risk_level": {"type": "string"}}},
        parameters=(param(),),
        dependencies=(),
        source="official",
        provenance=Provenance(),
        permissions=("local_read:<data/cache/**>",),
        offline_level="full",
        version_policy="follow-latest",
    )
    base.update(kw)
    return SkillDescriptor(**base)


# ───────────────────────── §2 SkillDescriptor ─────────────────────────


class TestSection2SkillDescriptor:
    """GWT-SC2：Skill 元数据必填字段齐全且枚举合法，name 过 §6 校验。"""

    def test_full_descriptor_constructs(self):
        d = descriptor()
        assert d.source == "official" and d.offline_level == "full"
        assert d.version_policy == "follow-latest"
        assert d.parameters[0].name == "threshold"

    def test_source_enum_enforced(self):
        with pytest.raises(ValidationError):
            descriptor(source="hacked")

    def test_offline_level_enum_enforced(self):
        with pytest.raises(ValidationError):
            descriptor(offline_level="sometimes")

    def test_imported_requires_full_provenance(self):
        """导入类必填分享者、时间、校验和（01 §2 → 09 §5）。"""
        with pytest.raises(ValidationError, match="provenance"):
            descriptor(source="imported")
        ok = descriptor(
            source="imported",
            provenance=Provenance(sharer="user-a", imported_at="2026-09-25T10:00:00+08:00",
                                  checksum="a" * 64, origin_chain=("user-a", "user-b")),
        )
        assert ok.provenance.origin_chain == ("user-a", "user-b")

    def test_checksum_format(self):
        with pytest.raises(ValidationError, match="checksum"):
            Provenance(checksum="tooshort")

    def test_parameter_spec_bounds(self):
        with pytest.raises(ValidationError, match="高于取值上限"):
            param(default=1.5, max_value=1.0)
        with pytest.raises(ValidationError, match="choices"):
            param(type="enum", choices=())
        with pytest.raises(ValidationError, match="default"):
            param(type="enum", choices=("a", "b"), default="c")


class TestNameNeutralityInjection:
    """name 过 §6 校验——通过注入函数实现，契约包不依赖具体词表。"""

    def test_neutral_validator(self):
        from st_agent.contracts import NeutralityGuard
        guard = NeutralityGuard()
        check = guard.check_name
        # 合法中性名
        d = descriptor(name="流动性视角", description="评估流动性维度的视角")
        assert check(d.name).passed
        # 违规名在构造前置校验中被拒
        assert not check("激进派").passed

    def test_contract_violation_helper(self):
        """make_skill_descriptor_validator 生成的校验器拒绝违规名。"""
        from st_agent.contracts import NeutralityGuard
        from st_agent.contracts.capability_types import make_skill_descriptor_validator
        v = make_skill_descriptor_validator(NeutralityGuard().check_name("x").passed.__class__
                                            and (lambda n: NeutralityGuard().check_name(n).passed))
        good = descriptor(name="机会视角")
        assert v(good) is good
        with pytest.raises(ContractViolation, match="中性化"):
            v(descriptor(name="投资大师"))


# ───────────────────────── §3 LensOpinion ─────────────────────────


class TestSection3LensOpinion:
    """GWT-SC3：stance/confidence 走枚举、key_reasons 陈述式、evidence_refs 合法引用。"""

    def opinion(self, **kw) -> LensOpinion:
        base = dict(
            lens_id="lens_liquidity",
            stance="negative",
            key_reasons=("换手率连续三日低于板块后 10% 分位", "大宗折价成交放大"),
            evidence_refs=("ann_0123456789abcdef0123", "snap_0123456789abcdef0123"),
            confidence="medium",
            skills_triggered=("run_0123456789abcdef0123",),
            trace_id="tr_0123456789abcdef012345",
        )
        base.update(kw)
        return LensOpinion(**base)

    def test_full_opinion_constructs(self):
        o = self.opinion()
        assert o.stance == "negative" and o.confidence == "medium"

    def test_stance_enum_enforced(self):
        with pytest.raises(ValidationError):
            self.opinion(stance="bullish")

    def test_insufficient_data_stance_legal(self):
        """失败隔离：stance=insufficient-data 是合法分支（06-L4 §2.2）。"""
        assert self.opinion(stance="insufficient-data").stance == "insufficient-data"

    def test_confidence_enum_enforced(self):
        with pytest.raises(ValidationError):
            self.opinion(confidence="very-high")

    def test_empty_reasons_rejected(self):
        """结构化观点须给出理由——禁止无理由 stance。"""
        with pytest.raises(ValidationError, match="key_reasons"):
            self.opinion(key_reasons=())

    def test_evidence_refs_shape(self):
        with pytest.raises(ValidationError):
            self.opinion(evidence_refs=("",))


# ───────────────────────── §7 配置元模型 ─────────────────────────


class TestSection7ConfigRegistry:
    """GWT-SC7：注册获得七字段。"""

    def entry(self, **kw) -> ConfigEntry:
        base = dict(
            config_id="cfg_attention_budget_weekend",
            display_name="周末注意力预算",
            value_schema={"type": "string"},
            default="routine",
            description_for_chat="周末允许的推送级别，可说『周末只收重要通知』",
            panel_form_spec=PanelField(widget="select", label="推送级别",
                                       help_text="周末时段允许的触达级别",
                                       choices=("emergency", "important", "routine")),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )
        base.update(kw)
        return ConfigEntry(**base)

    def test_seven_fields_registered(self):
        e = self.entry()
        assert e.config_id and e.display_name
        assert e.value_schema and e.default == "routine"
        assert e.description_for_chat and e.panel_form_spec.label
        assert e.scope == "global" and e.change_policy.requires_confirmation is True

    def test_default_matches_value_schema(self):
        with pytest.raises(ValidationError, match="boolean"):
            self.entry(value_schema={"type": "boolean"}, default="yes")
        with pytest.raises(ValidationError, match="number"):
            self.entry(value_schema={"type": "number"}, default="high")

    def test_change_policy_defaults_transparent(self):
        """record_history / rollback_enabled 默认开（透明 + 可回滚）。"""
        cp = ChangePolicy(requires_confirmation=False)
        assert cp.record_history is True and cp.rollback_enabled is True

    def test_change_record_shape(self):
        r = ChangeRecord(change_id="chg_0123456789abcdef0123",
                         config_id="cfg_x", old_value="routine", new_value="important",
                         applied_at="2026-09-25T10:00:00+08:00",
                         trace_ref="tr_0123456789abcdef012345")
        assert r.change_id.startswith("chg_")

    def test_panel_select_requires_choices(self):
        with pytest.raises(ValidationError, match="choices"):
            PanelField(widget="select", label="级别", help_text="选择级别")


# ───────────────────────── §9 版本化规范 ─────────────────────────


class TestSection9Versioning:
    """GWT-SC9：主版本变更 = 不兼容；次版本变更 = 兼容增强。"""

    def test_parse_and_str(self):
        v = SemVer.parse("1.4")
        assert (v.major, v.minor, v.patch) == (1, 4, 0)
        assert str(SemVer.parse("2.0.3")) == "2.0.3"

    def test_parse_invalid(self):
        with pytest.raises(ContractViolation):
            SemVer.parse("v1")
        with pytest.raises(ContractViolation):
            SemVer.parse("1.4.5.6")

    def test_major_bump_breaks_compat(self):
        v1_4 = SemVer.parse("1.4")
        v2_0 = v1_4.bump("major")
        assert not v2_0.is_compatible_upgrade_from(v1_4)   # 消费方需人工确认

    def test_minor_bump_keeps_compat(self):
        v1_4 = SemVer.parse("1.4")
        v1_5 = v1_4.bump("minor")
        assert v1_5.is_compatible_upgrade_from(v1_4)       # 兼容性增强


# ───────────────────────── §10 权限声明模型 ─────────────────────────


class TestSection10Permissions:
    """GWT-SC10：仅接受三项权限且 scope 语法合法。"""

    def test_parse_three_actions(self):
        assert parse_permission("local_read:<data/cache/**>") == ("local_read", "<data/cache/**>")
        assert parse_permission("net_access:<*.baostock.com>") == ("net_access", "<*.baostock.com>")
        assert parse_permission("exec_command") == ("exec_command", "*")

    def test_unknown_action_rejected(self):
        with pytest.raises(ContractViolation, match="语法非法"):
            parse_permission("admin:<everything>")
        with pytest.raises(ContractViolation):
            parse_permission("net_access:*.example.com")   # 缺尖括号 scope

    def test_exec_command_takes_no_scope(self):
        with pytest.raises(ContractViolation):
            parse_permission("exec_command:<rm -rf />")

    def test_validate_list_rejects_duplicates(self):
        validate_permissions(("exec_command", "local_read:<data/**>"))
        with pytest.raises(ContractViolation, match="重复"):
            validate_permissions(("local_read:<a/**>", "local_read:<a/**>"))
