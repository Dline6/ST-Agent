"""T-L4-001 测试：06 §1 视角模型 Lens + 常设阵容（用户可增删）。

GWT 对照（任务文件 5 条）：
- GWT-1 官方阵容 7 内置视角、字段齐、命名过 §6、skill_bundle 真实
- GWT-2 自定义视角命名/描述中性化拒绝（人名 / 性格标签 / 第一人称）
- GWT-3 合规自定义视角创建、落盘、进阵容、lens_id 合法
- GWT-4 自定义可删、内置不可删只能停用（A2）
- GWT-5 无自定义时空态判定（has_custom）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from st_agent.contracts.identifiers import LensId
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l4.builtin import BUILTIN_LENSES, builtin_lens_ids
from st_agent.l4.errors import BuiltinLensError, LensNotFoundError, LensValidationError
from st_agent.l4.lens import (
    BUILTIN_LENSES_COUNT,
    ConfidencePolicy,
    JudgingCriteria,
    Lens,
)
from st_agent.l4.roster import LENS_PREFIX, LensRoster

PASS = "correct horse battery staple"
NAMES = ("机会视角", "风险视角", "基本面视角", "情绪视角", "流动性视角", "宏观视角", "合规视角")


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def roster(store: Store, skills: SkillRegistry) -> LensRoster:
    return LensRoster(store, skills=skills)


# ───────────────────────── GWT-1 · 官方常设阵容 ─────────────────────────

class TestBuiltinRoster:
    def test_seed_yields_seven_builtin(self, roster: LensRoster):
        """GWT-1：播种后恰有 7 内置视角，kind=builtin、默认启用。"""
        assert roster.seed_builtin() == BUILTIN_LENSES_COUNT
        all_lens = roster.list_all()
        assert len(all_lens) == 7
        assert all(l.kind == "builtin" for l in all_lens)
        assert all(l.enabled for l in all_lens)
        assert {l.name for l in all_lens} == set(NAMES)

    def test_seed_is_idempotent(self, roster: LensRoster):
        assert roster.seed_builtin() == 7
        assert roster.seed_builtin() == 0          # 二次播种不新增
        assert len(roster.list_all()) == 7

    def test_fields_complete_and_names_neutral(self, roster: LensRoster):
        """每条内置视角六字段齐、命名过 §6、lens_id 合法。"""
        guard = NeutralityGuard()
        roster.seed_builtin()
        for l in roster.list_all():
            assert l.name and l.description and l.skill_bundle
            assert isinstance(l.judging_criteria, JudgingCriteria)
            assert isinstance(l.confidence_policy, ConfidencePolicy)
            assert LensId.of(l.lens_id).value == l.lens_id
            assert guard.check_name(l.name).passed, l.name

    def test_skill_bundle_resolves_in_registry(self, roster: LensRoster, skills: SkillRegistry):
        """A1 验证：每条内置 skill_bundle 的 skill_id 都能在 SkillRegistry 取到。"""
        roster.seed_builtin()
        for l in roster.list_all():
            for sid in l.skill_bundle:
                skills.get(sid)  # 不存在即抛 SkillNotFoundError

    def test_builtin_lens_ids_stable(self):
        assert builtin_lens_ids() == tuple(l.lens_id for l in BUILTIN_LENSES)
        assert len(set(builtin_lens_ids())) == BUILTIN_LENSES_COUNT  # 确定性、无重复


# ───────────────────────── GWT-2 · 中性化门 ─────────────────────────

class TestNeutralityGate:
    @pytest.mark.parametrize("bad_name", ["老张视角", "激进派", "李四"])
    def test_rejects_person_or_personality_name(self, roster: LensRoster, bad_name: str):
        """GWT-2：人名 / 性格标签命名 → 拒绝、不落盘。"""
        with pytest.raises(LensValidationError, match="中性化"):
            roster.add_custom(name=bad_name, description="中性功能描述",
                              skill_bundle=(), judging_criteria={"natural": "准则"})
        assert len(roster.list_all()) == 0

    def test_rejects_first_person_description(self, roster: LensRoster):
        """GWT-2：命名合规但描述含第一人称 → 仍拒绝。"""
        with pytest.raises(LensValidationError, match="中性化"):
            roster.add_custom(name="动量视角", description="我认为这个标的不该错过",
                              skill_bundle=(), judging_criteria={"natural": "准则"})

    def test_rejected_lens_not_persisted(self, roster: LensRoster):
        with pytest.raises(LensValidationError):
            roster.add_custom(name="老王视角", description="中性",
                              skill_bundle=(), judging_criteria={"natural": "x"})
        assert not any(n.startswith(LENS_PREFIX) for n in roster._store.list_files("config"))


# ───────────────────────── GWT-3 · 创建自定义视角 ─────────────────────────

class TestAddCustom:
    def test_creates_custom_in_roster(self, roster: LensRoster):
        """GWT-3：合规命名/描述 → kind=custom 落盘、进阵容、lens_id 合法。"""
        roster.seed_builtin()
        lens = roster.add_custom(name="短线动量视角", description="以换手率与量能刻画短期动量",
                                 skill_bundle=("sk_data_aggregate_v1.0",),
                                 judging_criteria={"natural": "以量能为主"})
        assert lens.kind == "custom"
        assert lens.enabled is True
        assert LensId.of(lens.lens_id).value == lens.lens_id
        assert len(roster.list_all()) == 8
        # 重新构造一个只读门面（模拟进程重启），验证确实落盘
        reopened = LensRoster(roster._store, skills=roster._skills)
        assert reopened.get(lens.lens_id).name == "短线动量视角"

    def test_accepts_natural_or_rule_criteria(self, roster: LensRoster):
        """06 §1 预留两入口：rule 形态亦可建（不止 natural）。"""
        lens = roster.add_custom(name="估值视角", description="以安全边际与财务健康为准",
                                 skill_bundle=("sk_fundamental_screening_v1.0",),
                                 judging_criteria={"rule": {"op": "gt", "field": "margin"}})
        assert lens.judging_criteria.rule  # 规则入口被接受

    def test_rejects_unregistered_skill_in_bundle(self, roster: LensRoster):
        with pytest.raises(LensValidationError, match="未注册"):
            roster.add_custom(name="坏引用视角", description="中性描述",
                              skill_bundle=("sk_does_not_exist_v1.0",),
                              judging_criteria={"natural": "x"})


# ───────────────────────── GWT-4 · 删除与停用语义（A2）─────────────────────────

class TestRemoveAndDisable:
    def test_custom_can_be_removed(self, roster: LensRoster):
        roster.seed_builtin()
        lens = roster.add_custom(name="临时视角", description="中性描述",
                                 skill_bundle=("sk_data_aggregate_v1.0",),
                                 judging_criteria={"natural": "x"})
        roster.remove(lens.lens_id)
        with pytest.raises(LensNotFoundError):
            roster.get(lens.lens_id)
        assert len(roster.list_all()) == 7

    def test_builtin_cannot_be_removed(self, roster: LensRoster):
        """A2：内置视角删除被拒（BuiltinLensError），盘上仍在。"""
        roster.seed_builtin()
        bid = builtin_lens_ids()[0]
        with pytest.raises(BuiltinLensError, match="停用"):
            roster.remove(bid)
        assert roster.get(bid).kind == "builtin"  # 未被删

    def test_builtin_can_be_disabled(self, roster: LensRoster):
        """内置可停用：enabled=False，读面仍可见、list_enabled 排除。"""
        roster.seed_builtin()
        bid = builtin_lens_ids()[0]
        updated = roster.set_enabled(bid, False)
        assert updated.enabled is False
        assert any(l.lens_id == bid for l in roster.list_all())        # 仍在 all
        assert all(l.lens_id != bid for l in roster.list_enabled())    # 不在 enabled
        assert len(roster.list_enabled()) == BUILTIN_LENSES_COUNT - 1
        # 重新启用
        assert roster.set_enabled(bid, True).enabled is True
        assert len(roster.list_enabled()) == BUILTIN_LENSES_COUNT

    def test_remove_unknown_raises(self, roster: LensRoster):
        with pytest.raises(LensNotFoundError):
            roster.remove(LensId.generate().value)


# ───────────────────────── GWT-5 · 空状态 ─────────────────────────

class TestEmptyState:
    def test_no_custom_before_creation(self, roster: LensRoster):
        """新用户无自定义视角 → has_custom=False，仅 7 内置。"""
        roster.seed_builtin()
        assert roster.has_custom() is False
        assert len([l for l in roster.list_all() if l.kind == "builtin"]) == 7

    def test_has_custom_true_after_creation(self, roster: LensRoster):
        roster.seed_builtin()
        roster.add_custom(name="自定义视角", description="中性描述",
                          skill_bundle=("sk_data_aggregate_v1.0",),
                          judging_criteria={"natural": "x"})
        assert roster.has_custom() is True

    def test_empty_roster_before_seed(self, roster: LensRoster):
        """未播种时阵容为空（空态入口由此上层渲染，非本任务职责）。"""
        assert roster.list_all() == ()
        assert roster.has_custom() is False


# ───────────────────────── Lens 模型构造期不变量 ─────────────────────────

class TestLensModelInvariants:
    def test_rejects_malformed_skill_id(self):
        with pytest.raises(ValidationError) as excinfo:
            Lens(lens_id=LensId.generate().value, name="机会视角", description="中性",
                 skill_bundle=("not_a_skill_id",),
                 judging_criteria=JudgingCriteria(natural="x"), kind="custom")
        assert "skill_bundle" in str(excinfo.value) or "非法" in str(excinfo.value)

    def test_judging_criteria_requires_one_entry(self):
        with pytest.raises(ValidationError):
            JudgingCriteria(natural="   ", rule={})  # 两入口皆空

    def test_confidence_policy_monotonic(self):
        with pytest.raises(ValidationError):
            ConfidencePolicy(high_at=0.3, medium_at=0.7)  # 非单调

    def test_facade_wraps_model_errors_as_lens_validation(self, roster: LensRoster):
        """门面把 pydantic 的构造错统一还原成层内 LensValidationError（同 l1._checked_descriptor）。"""
        with pytest.raises(LensValidationError):
            roster.add_custom(name="坏准则视角", description="中性",
                              skill_bundle=(), judging_criteria={"natural": "   ", "rule": {}})
        with pytest.raises(LensValidationError):
            roster.add_custom(name="坏信心视角", description="中性", skill_bundle=(),
                              judging_criteria={"natural": "x"},
                              confidence_policy={"high_at": 0.3, "medium_at": 0.7})

    def test_confidence_evaluate_buckets(self):
        p = ConfidencePolicy(high_at=0.8, medium_at=0.5)
        assert p.evaluate(0.9) == "high"
        assert p.evaluate(0.6) == "medium"
        assert p.evaluate(0.2) == "low"

    def test_empty_skill_bundle_allowed(self):
        """A1 兜底：skill_bundle 允许为空（内置视角若暂无 skill 可占位）。"""
        lens = Lens(lens_id=LensId.generate().value, name="占位视角", description="中性",
                    skill_bundle=(), judging_criteria=JudgingCriteria(natural="x"), kind="custom")
        assert lens.skill_bundle == ()


# ───────────────────────── 导入安装面（T-ECO-002.1） ─────────────────────────

def _shared_lens(**over) -> Lens:
    fields = dict(
        lens_id=LensId.generate().value, name="导入视角", description="导入校验流水线的安装面",
        skill_bundle=("sk_st_list_sync_v1.0",),
        judging_criteria=JudgingCriteria(natural="按数据充分度给出中性评判"),
        kind="custom", enabled=True,
    )
    fields.update(over)
    return Lens(**fields)


class TestInstallShared:
    """[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 的安装段：保留分享方标识，只增不改既有行为。"""

    def test_preserves_identity_and_lands_in_roster(self, roster: LensRoster):
        lens = _shared_lens()
        installed = roster.install_shared(lens)
        assert installed.lens_id == lens.lens_id          # 身份不被本机改写
        assert roster.get(lens.lens_id).name == lens.name

    def test_builtin_kind_is_forced_to_custom(self, roster: LensRoster):
        """导入物不是官方预置；`builtin` 只能停用不可删，而导入物应当可删。"""
        installed = roster.install_shared(_shared_lens(kind="builtin"))
        assert installed.kind == "custom"
        roster.remove(installed.lens_id)                  # 可删（内置不可删）

    def test_duplicate_is_not_silently_overwritten(self, roster: LensRoster):
        lens = _shared_lens()
        roster.install_shared(lens)
        with pytest.raises(LensValidationError, match="已存在"):
            roster.install_shared(lens)

    def test_persona_name_is_refused(self, roster: LensRoster):
        with pytest.raises(LensValidationError, match="中性化"):
            roster.install_shared(_shared_lens(name="激进派"))

    def test_unknown_skill_in_bundle_is_refused(self, roster: LensRoster):
        with pytest.raises(LensValidationError, match="未注册"):
            roster.install_shared(_shared_lens(skill_bundle=("sk_absent_v1.0",)))

    def test_mapping_form_is_accepted(self, roster: LensRoster):
        lens = _shared_lens()
        installed = roster.install_shared(lens.model_dump(mode="python"))
        assert installed.lens_id == lens.lens_id
