"""T-L1-012.1 测试：配置注册表门面本体与命名规则（01 §7 + 决策 D-067）。

GWT 对照（任务文件 5 条）：

- GWT-1 命名与派生：``config_id`` 点分语义 → 落盘路径确定性派生；非法 id 即拒
- GWT-2 物化（不落盘）：无 override 时按描述体 ``ParameterSpec`` 合成规范条目
- GWT-3 override 往返：落值 + 产生 ``ChangeRecord``；同 base 跨版本共用
- GWT-4 值未变不留痕
- GWT-5 按 ``scope`` 列举 + 未接入描述体时 fail-closed（不臆测）

另覆盖假设 A1（配置键取 base）、A2（作用域段不含分隔符，反解失败即拒）、
A3（物化恒回条目、``origin`` 因此恒为 ``registry``）、A4（留痕前缀本族自有）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.l0.storage import Store
from st_agent.l1.registry import (
    CHANGE_PARTITION,
    CONFIG_PARTITION,
    ConfigRegistryFacade,
    ParamFamily,
    RegistryFamilyConflict,
    RegistryValidationError,
    check_config_id,
    config_path,
    family_of,
    panel_field_for,
    param_config_id,
    parse_param_config_id,
)

PASS = "correct horse battery staple"

BASE = "sk_risk_alert"
SKILL_V10 = "sk_risk_alert_v1.0"
SKILL_V11 = "sk_risk_alert_v1.1"

PARAMS = (
    ParameterSpec(name="window", type="integer", default=30, min_value=1, max_value=250,
                  description="回看窗口（天）"),
    ParameterSpec(name="threshold", type="number", default=0.8, min_value=0.0, max_value=1.0,
                  description="触发阈值"),
    ParameterSpec(name="mode", type="enum", default="all", choices=("all", "watch"),
                  description="扫描范围"),
)


class _Descriptor:
    """最小描述体替身（门面只读 ``parameters``）。"""

    def __init__(self, params: tuple[ParameterSpec, ...]) -> None:
        self.parameters = params


class _Descriptors:
    """最小描述体取数口替身（鸭子类型 ``get_latest(base) -> 描述体 | None``）。"""

    def __init__(self, by_base: dict[str, _Descriptor]) -> None:
        self._by_base = by_base

    def get_latest(self, base: str) -> _Descriptor | None:
        return self._by_base.get(base)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def skills() -> _Descriptors:
    return _Descriptors({BASE: _Descriptor(PARAMS)})


@pytest.fixture()
def facade(store: Store, skills: _Descriptors) -> ConfigRegistryFacade:
    return ConfigRegistryFacade(store, resolve=skills.get_latest)


# ───────────────────────── GWT-1 命名与派生 ─────────────────────────


def test_config_path_is_deterministically_derived() -> None:
    assert config_path("skill.sk_risk_alert.window") == "skill/sk_risk_alert/window.json"
    assert config_path("retention.chat_history_days") == "retention/chat_history_days.json"
    # 存量路径前缀式 id 派生结果与其既有落盘路径一致（D-067 不改名）
    assert config_path("scheduler-policy/offline-catch-up") == "scheduler-policy/offline-catch-up.json"
    assert config_path("mcp-hub/reconnect-max-retries") == "mcp-hub/reconnect-max-retries.json"


def test_family_and_param_config_id() -> None:
    assert family_of("skill.sk_risk_alert.window") == "skill"
    assert family_of("mcp-hub/reconnect-max-retries") == "mcp-hub"
    assert param_config_id("skill", BASE, "window") == "skill.sk_risk_alert.window"
    assert parse_param_config_id("skill.sk_risk_alert.window") == ("skill", BASE, "window")


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "skill.two words", "skill..window", "skill.", ".skill",
     "skill.a/b.window", "a\\b", "x" * 129],
)
def test_bad_config_id_rejected(bad: str) -> None:
    with pytest.raises(RegistryValidationError):
        check_config_id(bad)


@pytest.mark.parametrize("bad", ["skill.sk_a.b.window", "skill.sk_a", "other.sk_a.window"])
def test_ambiguous_or_foreign_param_id_maps_to_none(bad: str) -> None:
    assert parse_param_config_id(bad) is None


def test_param_segments_must_be_single() -> None:
    """A2：作用域族的 base / param 不得含段分隔符（否则点分形态歧义）。"""
    with pytest.raises(RegistryValidationError):
        param_config_id("skill", "sk_a.b", "window")
    with pytest.raises(RegistryValidationError):
        param_config_id("skill", "sk_a", "a.b")
    with pytest.raises(RegistryValidationError):
        param_config_id("skill", "sk_a/slash", "window")


# ───────────────────────── GWT-2 物化（不落盘） ─────────────────────────


def test_materialize_declared_param_without_persisting(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    entry = facade.entry_for(SKILL_V10, "window")
    assert entry is not None
    assert entry.config_id == "skill.sk_risk_alert.window"
    assert entry.default == 30
    assert entry.description_for_chat == "回看窗口（天）"
    assert entry.panel_form_spec.widget == "number"
    assert entry.scope == "skill"
    assert entry.value_schema == {"type": "integer", "minimum": 1, "maximum": 250}
    # 物化**不落盘**
    assert store.list_files(CONFIG_PARTITION) == ()


def test_materialize_unknown_param_is_none(facade: ConfigRegistryFacade) -> None:
    assert facade.entry_for(SKILL_V10, "no_such_param") is None
    assert facade.entry_for("unknown-target", "window") is None
    assert facade.entry_for("wf_other_v1.0", "window") is None


# ───────────────────────── GWT-3 override 往返 ─────────────────────────


def test_apply_persists_value_and_change_record(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    records = facade.apply(SKILL_V10, {"window": 60})
    assert len(records) == 1
    change = records[0]
    assert change.config_id == "skill.sk_risk_alert.window"
    assert (change.old_value, change.new_value) == (30, 60)
    assert change.change_id.startswith("chg_")
    assert store.list_files(CONFIG_PARTITION) == ("skill/sk_risk_alert/window.json",)
    assert store.list_files(CHANGE_PARTITION) == (
        f"skill-config-change/{change.change_id}.json",
    )
    # 读回：取值取自 override，文案仍取声明（同一条条目两段来源）
    read = facade.entry_for(SKILL_V10, "window")
    assert read is not None
    assert read.default == 60
    assert read.description_for_chat == "回看窗口（天）"


def test_override_is_shared_across_versions(facade: ConfigRegistryFacade) -> None:
    """A1：配置键取 base——同 base 的版本升级沿用同一份配置。"""
    facade.apply(SKILL_V10, {"window": 60})
    later = facade.entry_for(SKILL_V11, "window")
    assert later is not None
    assert later.default == 60


def test_apply_is_idempotent_at_declared_default(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    """落值等于**声明默认**时也算「值未变」——不留痕、不落盘。"""
    assert facade.apply(SKILL_V10, {"window": 30}) == ()
    assert store.list_files(CONFIG_PARTITION) == ()


# ───────────────────────── GWT-4 值未变不留痕 ─────────────────────────


def test_same_value_twice_leaves_no_trace(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    facade.apply(SKILL_V10, {"window": 60})
    before = store.list_files(CHANGE_PARTITION)
    assert facade.apply(SKILL_V10, {"window": 60}) == ()
    assert store.list_files(CHANGE_PARTITION) == before


def test_partial_change_returns_only_changed(
    facade: ConfigRegistryFacade
) -> None:
    facade.apply(SKILL_V10, {"window": 60, "mode": "watch"})
    again = facade.apply(SKILL_V10, {"window": 60, "mode": "all"})
    assert len(again) == 1
    assert again[0].config_id == "skill.sk_risk_alert.mode"
    assert (again[0].old_value, again[0].new_value) == ("watch", "all")


def test_value_outside_declaration_is_rejected(facade: ConfigRegistryFacade) -> None:
    with pytest.raises(RegistryValidationError):
        facade.apply(SKILL_V10, {"window": 999})          # 高于上限
    with pytest.raises(RegistryValidationError):
        facade.apply(SKILL_V10, {"mode": "nope"})         # enum 外
    with pytest.raises(RegistryValidationError):
        facade.apply(SKILL_V10, {"window": "sixty"})      # 类型不符
    with pytest.raises(RegistryValidationError):
        facade.apply(SKILL_V10, {"no_such_param": 1})     # 未知参数
    with pytest.raises(RegistryValidationError):
        facade.apply("unknown-target", {"window": 1})     # 目标形态不认得


# ───────────────────────── GWT-5 列举与 fail-closed ─────────────────────────


def test_list_by_scope(facade: ConfigRegistryFacade) -> None:
    assert facade.list("skill") == ()
    assert facade.list() == ()
    facade.apply(SKILL_V10, {"window": 60, "mode": "watch"})
    listed = facade.list("skill")
    assert [e.config_id for e in listed] == [
        "skill.sk_risk_alert.mode",
        "skill.sk_risk_alert.window",
    ]
    # 其它 scope 为空（未接入 global 族）
    assert facade.list("global") == ()


def test_without_descriptors_only_stored_entries_are_visible(store: Store) -> None:
    """A3 的对偶：未接入描述体时**不物化**——有 override 则回存盘形态，
    无 override 则 ``None``（消费方回落声明面），不编造。"""
    bare = ConfigRegistryFacade(store)
    assert bare.entry_for(SKILL_V10, "window") is None
    assert bare.entry_for(SKILL_V10, "no_such_param") is None


def test_family_prefix_conflict_is_rejected(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    with pytest.raises(RegistryFamilyConflict):
        facade.register_family(ParamFamily(store, scope="skill"))


def test_panel_mapping_has_a_single_source() -> None:
    spec = ParameterSpec(name="m", type="enum", default="a", choices=("a", "b"),
                         description="说明")
    field = panel_field_for(spec)
    assert (field.widget, field.choices, field.help_text) == ("select", ("a", "b"), "说明")
    # L3 侧由 L1 再导出（不各造一套）
    from st_agent.l3.config import panel_field_for as l3_mapping

    assert l3_mapping(spec) == field
