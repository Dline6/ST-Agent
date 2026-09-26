"""T-L1-001.1 测试：03 §1.1 注册与发现 + §1.4 版本管理。

GWT 对照（任务文件 4 条）：
- GWT-1 Pack 自动可用：官方 12 Skill 种子幂等可用
- GWT-5 更新可选：变更日志 + 影响范围 + 更新/跳过/锁定 + 主版本待检查
- GWT-7 参数拦截：超范围拦截不保存（注册默认值 + 改参 + 调用传参三处）
- GWT-R 注册不变量：skill_id 拼接规则 + 全局唯一 + 重复拒绝
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.skills import (
    OFFICIAL_PACK,
    SKILL_PREFIX,
    UPDATE_PREFIX,
    SkillExistsError,
    SkillNotFoundError,
    SkillRegistry,
    SkillValidationError,
    base_of,
    check_skill_id,
    ensure_official_pack,
    parse_skill_id,
    skill_id_for,
)
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"


@pytest.fixture()
def registry(tmp_path: Path) -> SkillRegistry:
    return SkillRegistry(Store.create(tmp_path / "root", PASS))


def root_of(registry: SkillRegistry) -> Path:
    return registry._store._root


def minimal(base="sk_demo_thing", **kw) -> dict:
    fields = dict(
        name="演示功能",
        description="演示用 Skill 描述",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permissions=("local_read:<data/cache/**>",),
    )
    fields.update(kw)
    return dict(base=base, **fields)


# ───────────────────────── GWT-R 注册不变量（A1） ─────────────────────────


class TestGwtRSkillIdRule:
    def test_skill_id_shape(self):
        assert skill_id_for("sk_unhat_eligibility_check", "1.0") == \
            "sk_unhat_eligibility_check_v1.0"
        base, ver = parse_skill_id("sk_unhat_eligibility_check_v1.0")
        assert base == "sk_unhat_eligibility_check"
        assert (ver.major, ver.minor) == (1, 0)

    def test_bad_shape_rejected(self):
        for bad in ("sk_xxx_v2", "unhat_v1.0", "sk_UPPER_v1.0", "sk_x_v1.0.0", ""):
            with pytest.raises(SkillValidationError):
                check_skill_id(bad)

    def test_register_assigns_versioned_id(self, registry):
        d = registry.register(version="1.2", **minimal())
        assert d.skill_id == "sk_demo_thing_v1.2"

    def test_duplicate_rejected_not_silent(self, registry):
        registry.register(**minimal())
        with pytest.raises(SkillExistsError):
            registry.register(**minimal())

    def test_bad_dependency_id_rejected(self, registry):
        with pytest.raises(SkillValidationError):
            registry.register(dependencies=("not-a-skill-id",), **minimal(base="sk_dep_bad"))

    def test_unknown_skill_get(self, registry):
        with pytest.raises(SkillNotFoundError):
            registry.get("sk_missing_thing_v1.0")


# ───────────────────────── GWT-1 Pack 自动可用 ─────────────────────────


class TestGwt1OfficialPack:
    def test_pack_is_twelve(self):
        assert len(OFFICIAL_PACK) == 12

    def test_ensure_idempotent(self, registry):
        first = ensure_official_pack(registry)
        assert len(first) == 12
        second = ensure_official_pack(registry)
        assert second == ()
        assert len(registry.list_all()) == 12

    def test_both_bundles_present(self, registry):
        ensure_official_pack(registry)
        bases = {base_of(d.skill_id) for d in registry.list_all()}
        assert "sk_unhat_eligibility_check" in bases  # GWT-2 的匹配目标
        assert "sk_stock_watch" in bases  # GWT-3 的改参目标


# ───────────────────────── GWT-5 更新可选 ─────────────────────────


class TestGwt5VersionManagement:
    def test_minor_publish_no_pending(self, registry):
        registry.register(**minimal())
        d2 = registry.publish_version("sk_demo_thing", version="1.1")
        assert d2.skill_id == "sk_demo_thing_v1.1"
        assert registry.pending_updates() == ()

    def test_major_publish_requires_changelog(self, registry):
        registry.register(**minimal())
        with pytest.raises(SkillValidationError, match="changelog"):
            registry.publish_version("sk_demo_thing", version="2.0")
        # 回滚：2.0 文件不得残留
        with pytest.raises(SkillNotFoundError):
            registry.get("sk_demo_thing_v2.0")

    def test_major_publish_marks_pending(self, registry):
        registry.register(**minimal())
        registry.publish_version("sk_demo_thing", version="2.0",
                                 changelog="输入契约变更", impact="引用方需确认")
        pending = registry.pending_updates()
        assert len(pending) == 1
        assert pending[0].base == "sk_demo_thing"
        assert pending[0].changelog == "输入契约变更"
        assert pending[0].impact == "引用方需确认"

    def test_confirm_clears_and_old_kept(self, registry):
        registry.register(**minimal())
        registry.publish_version("sk_demo_thing", version="2.0",
                                 changelog="变更", impact="影响")
        info = registry.confirm_update("sk_demo_thing")
        assert info.to_version == "2.0"
        assert registry.pending_updates() == ()
        # 锁定引用方：老版本仍可读
        assert registry.get("sk_demo_thing_v1.0").skill_id == "sk_demo_thing_v1.0"
        assert registry.get_latest("sk_demo_thing").skill_id == "sk_demo_thing_v2.0"

    def test_skip_keeps_old_usable(self, registry):
        registry.register(**minimal())
        registry.publish_version("sk_demo_thing", version="2.0",
                                 changelog="变更", impact="影响")
        registry.skip_update("sk_demo_thing")
        assert registry.pending_updates() == ()
        assert registry.get("sk_demo_thing_v1.0") is not None

    def test_version_must_increase(self, registry):
        registry.register(**minimal())
        with pytest.raises(SkillValidationError):
            registry.publish_version("sk_demo_thing", version="1.0")

    def test_list_versions_ordered(self, registry):
        registry.register(**minimal())
        registry.publish_version("sk_demo_thing", version="1.1")
        versions = [d.skill_id for d in registry.list_versions("sk_demo_thing")]
        assert versions == ["sk_demo_thing_v1.0", "sk_demo_thing_v1.1"]


# ───────────────────────── GWT-7 参数拦截 ─────────────────────────


class TestGwt7ParamGuard:
    RANGE = dict(
        parameters=(dict(name="threshold", type="number", default=0.8,
                         min_value=0.0, max_value=1.0,
                         description="触发阈值"),),
    )

    def test_register_default_out_of_range(self, registry):
        bad = dict(self.RANGE)
        bad["parameters"] = (dict(name="threshold", type="number", default=1.5,
                                  min_value=0.0, max_value=1.0,
                                  description="触发阈值"),)
        with pytest.raises(SkillValidationError):
            registry.register(**minimal(base="sk_range_bad"), **bad)

    def test_set_parameters_out_of_range_not_saved(self, registry):
        registry.register(**minimal(base="sk_range_thing"), **self.RANGE)
        with pytest.raises(SkillValidationError, match="上限"):
            registry.set_parameters("sk_range_thing_v1.0", {"threshold": 9.0})
        stored = registry.get("sk_range_thing_v1.0")
        assert stored.parameters[0].default == 0.8  # 未被写脏

    def test_set_parameters_ok_applies_immediately(self, registry):
        """GWT-3：Studio 改参立即生效（下次读取即新值）。"""
        registry.register(**minimal(base="sk_range_ok"), **self.RANGE)
        updated = registry.set_parameters("sk_range_ok_v1.0", {"threshold": 0.5})
        assert updated.parameters[0].default == 0.5
        assert registry.get("sk_range_ok_v1.0").parameters[0].default == 0.5

    def test_call_params_unknown_name(self, registry):
        registry.register(**minimal(base="sk_call_thing"), **self.RANGE)
        with pytest.raises(SkillValidationError, match="未知参数"):
            registry.validate_call_params("sk_call_thing_v1.0", {"nope": 1})

    def test_call_params_merges_defaults(self, registry):
        """GWT-2：参数确认卡数据 = 完整合并值（含默认值）。"""
        registry.register(**minimal(base="sk_card_thing"), **self.RANGE)
        merged = registry.validate_call_params("sk_card_thing_v1.0", {})
        assert merged == {"threshold": 0.8}

    def test_enum_choices_enforced(self, registry):
        registry.register(
            base="sk_enum_thing", name="枚举功能", description="枚举参数",
            parameters=(dict(name="format", type="enum", default="brief",
                             choices=("table", "trend", "brief"),
                             description="输出形态"),),
        )
        with pytest.raises(SkillValidationError):
            registry.set_parameters("sk_enum_thing_v1.0", {"format": "video"})


# ───────────────────────── A2 存储布局 / A3 校验链 ─────────────────────────


class TestStorageLayoutAndGuards:
    def test_files_under_config_prefixes(self, registry):
        registry.register(**minimal())
        files = registry._store.list_files("config")
        assert f"{SKILL_PREFIX}sk_demo_thing_v1.0.json" in files

    def test_pending_marker_under_update_prefix(self, registry):
        registry.register(**minimal())
        registry.publish_version("sk_demo_thing", version="2.0",
                                 changelog="变更", impact="影响")
        files = registry._store.list_files("config")
        assert f"{UPDATE_PREFIX}sk_demo_thing.json" in files

    def test_encrypted_at_rest(self, registry):
        registry.register(**minimal())
        raw = (root_of(registry) / "config" / SKILL_PREFIX
               / "sk_demo_thing_v1.0.json").read_bytes()
        assert "演示功能".encode("utf-8") not in raw

    def test_neutrality_rejected(self, registry):
        fields = minimal(base="sk_bad_name")
        fields["name"] = "投资大师"
        with pytest.raises(SkillValidationError, match="中性化"):
            registry.register(**fields)

    def test_bad_permission_rejected(self, registry):
        fields = minimal(base="sk_bad_perm")
        fields["permissions"] = ("admin:<everything>",)
        with pytest.raises(SkillValidationError, match="权限"):
            registry.register(**fields)

    def test_semver_two_levels(self):
        assert str(SemVer.parse("2.0")) == "2.0.0"
        assert not SemVer.parse("2.0").is_compatible_upgrade_from(SemVer.parse("1.9"))


# ───────────────────────── T-L1-007 反注册（回收面） ─────────────────────────


class TestUnregister:
    """T-L1-007：反注册 base 的**全部版本** + 待检查标记（MCP 派生 Skill 回收用）。"""

    def test_removes_all_versions_and_marker(self, registry):
        registry.register(**minimal())
        registry.publish_version("sk_demo_thing", version="1.1")
        registry.publish_version("sk_demo_thing", version="2.0",
                                 changelog="契约不兼容", impact="引用方须确认")
        assert len(registry.list_versions("sk_demo_thing")) == 3
        assert registry.pending_updates() != ()
        removed = registry.unregister("sk_demo_thing")
        assert set(removed) == {
            "sk_demo_thing_v1.0", "sk_demo_thing_v1.1", "sk_demo_thing_v2.0",
        }
        assert registry.list_versions("sk_demo_thing") == ()
        assert registry.pending_updates() == ()
        files = registry._store.list_files("config")
        assert [f for f in files if f.startswith(SKILL_PREFIX) and "sk_demo_thing" in f] == []
        assert f"{UPDATE_PREFIX}sk_demo_thing.json" not in files

    def test_unknown_base_raises(self, registry):
        with pytest.raises(SkillNotFoundError):
            registry.unregister("sk_nope_nothing")

    def test_bad_base_shape_rejected(self, registry):
        with pytest.raises(SkillValidationError):
            registry.unregister("demo_thing")

    def test_other_bases_and_audit_untouched(self, registry):
        """只动本 base；`execution_log` 是 append-only 审计（02 §6），不注销。"""
        registry.register(**minimal())
        registry.register(**minimal(base="sk_other_thing"))
        registry._store.put("execution_log", "audit/x.json", b"{}")
        registry.unregister("sk_demo_thing")
        assert registry.get_latest("sk_other_thing") is not None
        with pytest.raises(SkillNotFoundError):
            registry.get("sk_demo_thing_v1.0")
        assert "audit/x.json" in registry._store.list_files("execution_log")
