"""T-AGT-003 测试：工具目录读面（01 §2 工具暴露面 + 03 §1 读面落点）。

GWT 对照（任务文件 6 条）：
- GWT-1 目录即注册表：注册表能力的条数 = 目录条数，名集不遗漏不臆造
- GWT-2 工具名是 base：同 base 两版本得同一工具名（且折叠为一条）
- GWT-3 参数面从描述体派生：可读名 / 类型 / 默认值 / 取值域，约束（min / max / choices）不丢
- GWT-4 MCP 映射来源同路：与官方来源同形产出（无特例分支、条目不带 source）
- GWT-5 不吞失败：描述体缺失 / 形态坏 / 参数面冲突 → 显式失败携违规点，不留半个条目
- GWT-6 纯投影、无副作用：注册表逐字节不变 + 同输入两次结果一致

另有一条**跨层字母表守卫**（[D-095](../../项目管理/决策日志.md) ④）：`sk_` base 全集
⊆ L0 `TOOL_NAME_PATTERN`——兑现 [D-093](../../项目管理/决策日志.md) 的 A4 留档。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.capability_types import ParameterSpec, Provenance, SkillDescriptor
from st_agent.l0.llm import TOOL_NAME_PATTERN, ToolSpec, check_tool_name
from st_agent.l0.storage import Store
from st_agent.l1.skills import (
    OFFICIAL_PACK,
    SkillError,
    SkillNotFoundError,
    SkillRegistry,
    SkillValidationError,
    ToolEntry,
    base_of,
    ensure_official_pack,
    project_catalog,
    project_tool_entry,
)

PASS = "correct horse battery staple"

MCP_MAPPED = "mcp-mapped"
USER_BUILT = "user-built"


@pytest.fixture()
def registry(tmp_path: Path) -> SkillRegistry:
    return SkillRegistry(Store.create(tmp_path / "root", PASS))


def _register(registry: SkillRegistry, base: str, *, source: str = USER_BUILT, **kw):
    fields = dict(
        name="演示功能",
        description="演示用 Skill",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        source=source,
    )
    fields.update(kw)
    return registry.register(base, **fields)


def _descriptor(**kw) -> SkillDescriptor:
    fields = dict(
        skill_id="sk_demo_thing_v1.0",
        name="演示功能",
        description="演示用 Skill",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        source="official",
        provenance=Provenance(),
        offline_level="full",
        version_policy="follow-latest",
    )
    fields.update(kw)
    return SkillDescriptor(**fields)


def _snapshot(registry: SkillRegistry) -> dict[str, bytes]:
    """``config`` 分区全量字节快照（GWT-6 用）。"""
    names = list(registry._store.list_files("config"))
    return {n: registry._store.get("config", n) for n in names}


# ───────────────────────── GWT-1 · 目录即注册表 ─────────────────────────


class TestGwt1CatalogIsRegistry:
    def test_entry_count_equals_capability_count(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        _register(registry, "sk_my_thing")
        _register(registry, "sk_mapped_tool", source=MCP_MAPPED)
        catalog = registry.tool_catalog()
        expected = {base_of(d.skill_id) for d in registry.list_all()}
        assert len(catalog) == len(expected) == len(OFFICIAL_PACK) + 2
        assert [e.name for e in catalog] == sorted(expected)

    def test_each_entry_points_back_to_its_base(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        for entry in registry.tool_catalog():
            assert base_of(entry.skill_id) == entry.name
            assert registry.get(entry.skill_id).description == entry.description

    def test_names_are_unique(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        _register(registry, "sk_my_thing")
        names = [e.name for e in registry.tool_catalog()]
        assert len(names) == len(set(names))

    def test_empty_registry_yields_empty_catalog(self, registry: SkillRegistry) -> None:
        assert registry.tool_catalog() == ()

    def test_official_pack_covers_all_twelve_bases(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        bases = tuple(seed["base"] for seed in OFFICIAL_PACK)
        assert tuple(e.name for e in registry.tool_catalog()) == tuple(sorted(bases))


# ───────────────────────── GWT-2 · 工具名是 base ─────────────────────────


class TestGwt2ToolNameIsBase:
    def test_two_versions_share_one_entry_and_one_name(self, registry: SkillRegistry) -> None:
        old = _register(registry, "sk_versioned", version="1.0")
        registry.publish_version("sk_versioned", version="1.1")
        catalog = registry.tool_catalog()
        assert len(catalog) == 1
        assert catalog[0].name == "sk_versioned"
        # 执行目标取最高版本；两个描述体各自投影都得同一工具名
        assert catalog[0].skill_id == "sk_versioned_v1.1"
        assert project_tool_entry(old).name == catalog[0].name

    def test_major_version_upgrade_keeps_tool_name(self, registry: SkillRegistry) -> None:
        _register(registry, "sk_versioned", version="1.0")
        registry.publish_version("sk_versioned", version="2.0",
                                 changelog="不兼容变更", impact="引用方需确认")
        names = [e.name for e in registry.tool_catalog()]
        assert names == ["sk_versioned"]

    def test_fold_picks_highest_version_regardless_of_input_order(self) -> None:
        v10, v11 = _descriptor(skill_id="sk_x_v1.0"), _descriptor(skill_id="sk_x_v1.1")
        forward = project_catalog([v10, v11])
        backward = project_catalog([v11, v10])
        assert forward == backward
        assert forward[0].skill_id == "sk_x_v1.1"

    def test_catalog_sorted_by_base(self, registry: SkillRegistry) -> None:
        _register(registry, "sk_zzz")
        _register(registry, "sk_aaa")
        assert [e.name for e in registry.tool_catalog()] == ["sk_aaa", "sk_zzz"]


# ───────────────────────── GWT-3 · 参数面从描述体派生 ─────────────────────────


class TestGwt3ParameterDerivation:
    @staticmethod
    def _entry() -> ToolEntry:
        desc = _descriptor(
            parameters=(
                ParameterSpec(name="threshold", type="number", default=0.8,
                              min_value=0.0, max_value=1.0, description="触发阈值"),
                ParameterSpec(name="window", type="integer", default=20,
                              min_value=5, max_value=120, description="统计窗口天数"),
                ParameterSpec(name="exchange", type="enum", default="all",
                              choices=("all", "sh", "sz"), description="交易所范围"),
                ParameterSpec(name="keywords", type="string", default="",
                              description="关键词表"),
                ParameterSpec(name="strict", type="boolean", default=False,
                              description="严格模式"),
            ),
            input_schema={
                "type": "object",
                "properties": {"stock_id": {"type": "string"}},
                "required": ["stock_id"],
            },
        )
        return project_tool_entry(desc)

    def test_object_rooted_schema(self) -> None:
        schema = self._entry().parameters
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)

    def test_readable_name_type_default_description(self) -> None:
        props = self._entry().parameters["properties"]
        assert props["threshold"]["type"] == "number"
        assert props["threshold"]["default"] == 0.8
        assert props["threshold"]["description"] == "触发阈值"
        assert props["window"]["type"] == "integer"
        assert props["strict"]["type"] == "boolean"

    def test_numeric_bounds_survive(self) -> None:
        props = self._entry().parameters["properties"]
        assert (props["threshold"]["minimum"], props["threshold"]["maximum"]) == (0.0, 1.0)
        assert (props["window"]["minimum"], props["window"]["maximum"]) == (5, 120)

    def test_enum_becomes_string_with_choices(self) -> None:
        props = self._entry().parameters["properties"]
        assert props["exchange"]["type"] == "string"
        assert props["exchange"]["enum"] == ["all", "sh", "sz"]

    def test_input_schema_properties_and_required_kept(self) -> None:
        schema = self._entry().parameters
        assert schema["properties"]["stock_id"] == {"type": "string"}
        assert schema["required"] == ["stock_id"]

    def test_scalar_params_are_not_required(self) -> None:
        # ParameterSpec 是带默认值的可调标量，非必填——required 只取 input_schema
        schema = self._entry().parameters
        assert "threshold" not in schema["required"]
        assert "strict" not in schema["required"]

    def test_output_schema_not_projected(self) -> None:
        schema = self._entry().parameters
        assert "output_schema" not in schema
        assert set(schema["properties"]) == {
            "threshold", "window", "exchange", "keywords", "strict", "stock_id",
        }

    def test_schema_is_feedable_to_l0_tool_spec(self) -> None:
        entry = self._entry()
        spec = ToolSpec(name=entry.name, description=entry.description,
                        parameters=entry.parameters)
        assert spec.parameters["properties"]["window"]["minimum"] == 5

    def test_no_default_key_when_default_is_none(self) -> None:
        desc = _descriptor(parameters=(
            ParameterSpec(name="note", type="string", description="备注"),
        ))
        props = project_tool_entry(desc).parameters["properties"]
        assert "default" not in props["note"]

    def test_falsy_defaults_are_kept(self) -> None:
        props = self._entry().parameters["properties"]
        assert props["strict"]["default"] is False
        assert props["keywords"]["default"] == ""


# ───────────────────────── GWT-4 · MCP 映射来源同路 ─────────────────────────


class TestGwt4SourceAgnostic:
    def test_same_shape_for_every_source(self) -> None:
        shapes = []
        for source in ("official", USER_BUILT, MCP_MAPPED, "imported"):
            desc = _descriptor(source=source, provenance=Provenance(
                sharer="share_1", imported_at="2026-10-07T10:00+08:00",
                checksum="a" * 64,
            ))
            shapes.append(project_tool_entry(desc).model_dump_json())
        assert len(set(shapes)) == 1

    def test_entry_carries_no_source_field(self) -> None:
        assert "source" not in ToolEntry.model_fields

    def test_catalog_mixes_sources_without_special_casing(self, registry: SkillRegistry) -> None:
        _register(registry, "sk_official_thing", source="official")
        _register(registry, "sk_mapped_thing", source=MCP_MAPPED)
        catalog = registry.tool_catalog()
        assert [type(e) for e in catalog] == [ToolEntry, ToolEntry]
        assert [e.name for e in catalog] == ["sk_mapped_thing", "sk_official_thing"]


# ───────────────────────── GWT-5 · 不吞失败 ─────────────────────────


class TestGwt5NoSilentFailure:
    def test_corrupt_descriptor_fails_loudly(self, registry: SkillRegistry) -> None:
        _register(registry, "sk_good")
        _register(registry, "sk_broken")
        registry._store.put("config", "skill-registry/sk_broken_v1.0.json", b"{ not json")
        with pytest.raises(SkillNotFoundError) as err:
            registry.tool_catalog()
        assert "sk_broken_v1.0" in str(err.value)

    def test_shape_bad_descriptor_fails_loudly(self, registry: SkillRegistry) -> None:
        # 描述体缺必填字段：注册表读取面把它与「读不出」同归为显式失败
        # （_load 的既有口径「记录损坏无法解析」），本面原样逸出、点明 skill_id。
        _register(registry, "sk_good")
        registry._store.put(
            "config", "skill-registry/sk_ghost_v1.0.json",
            b'{"skill_id": "sk_ghost_v1.0", "name": "ghost"}',
        )
        with pytest.raises(SkillError) as err:
            registry.tool_catalog()
        assert "sk_ghost_v1.0" in str(err.value)

    def test_param_name_collision_is_rejected(self) -> None:
        desc = _descriptor(
            parameters=(ParameterSpec(name="threshold", type="number",
                                      default=1, description="阈值"),),
            input_schema={"type": "object", "properties": {"threshold": {"type": "number"}}},
        )
        with pytest.raises(SkillValidationError) as err:
            project_tool_entry(desc)
        assert "threshold" in str(err.value) and "同名冲突" in str(err.value)

    def test_non_object_input_schema_root_is_rejected(self) -> None:
        desc = _descriptor(input_schema={"type": "array"})
        with pytest.raises(SkillValidationError) as err:
            project_tool_entry(desc)
        assert "'object'" in str(err.value)

    def test_non_object_properties_is_rejected(self) -> None:
        desc = _descriptor(input_schema={"type": "object", "properties": ["stock_id"]})
        with pytest.raises(SkillValidationError) as err:
            project_tool_entry(desc)
        assert "properties" in str(err.value)

    def test_non_list_required_is_rejected(self) -> None:
        desc = _descriptor(input_schema={"type": "object", "required": "stock_id"})
        with pytest.raises(SkillValidationError) as err:
            project_tool_entry(desc)
        assert "required" in str(err.value)

    def test_illegal_skill_id_is_rejected(self) -> None:
        desc = _descriptor(skill_id="sk_no_version_suffix")
        with pytest.raises(SkillValidationError) as err:
            project_tool_entry(desc)
        assert "sk_no_version_suffix" in str(err.value)

    def test_enum_with_numeric_bounds_is_rejected(self) -> None:
        # enum 的数值上下界无法进 JSON Schema——丢掉即丢一条已声明的约束，故显式拒
        desc = _descriptor(parameters=(
            ParameterSpec(name="exchange", type="enum", default="all",
                          choices=("all", "sh"), min_value=1.0, max_value=2.0,
                          description="交易所范围"),
        ))
        with pytest.raises(SkillValidationError) as err:
            project_tool_entry(desc)
        assert "exchange" in str(err.value) and "enum" in str(err.value)


# ───────────────────────── GWT-6 · 纯投影、无副作用 ─────────────────────────


class TestGwt6PureProjection:
    def test_registry_is_byte_identical_after_read(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        _register(registry, "sk_my_thing")
        before = _snapshot(registry)
        registry.tool_catalog()
        assert _snapshot(registry) == before

    def test_repeated_reads_are_identical(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        first = registry.tool_catalog()
        second = registry.tool_catalog()
        assert first == second
        assert [e.model_dump_json() for e in first] == [e.model_dump_json() for e in second]

    def test_projection_does_not_mutate_descriptor(self) -> None:
        desc = _descriptor(
            parameters=(ParameterSpec(name="threshold", type="number", default=0.8,
                                      min_value=0.0, max_value=1.0, description="阈值"),),
            input_schema={"type": "object", "properties": {"stock_id": {"type": "string"}}},
        )
        before = desc.model_dump_json()
        entry = project_tool_entry(desc)
        entry.parameters["properties"]["stock_id"]["type"] = "mutated"
        assert desc.model_dump_json() == before
        assert desc.input_schema["properties"]["stock_id"] == {"type": "string"}


# ─────────────── 跨层字母表守卫：base 全集 ⊆ L0 工具名字母表 ───────────────


class TestBaseAlphabetIsToolNameCompatible:
    """[D-095] ④：投影边界断言形状相容——`sk_` base 必落在 L0 `TOOL_NAME_PATTERN` 内。"""

    @pytest.mark.parametrize("base", tuple(
        seed["base"] for seed in OFFICIAL_PACK
    ) + (
        "sk_my_thing",
        "sk_with.dots_in.name",
        "sk_with-dash",
        "sk_0digit",
        "sk_" + "a" * 61,  # 最长合法 base（共 64 字符）
    ))
    def test_legal_base_passes_l0_tool_name_check(self, base: str) -> None:
        assert check_tool_name(base) == base
        assert TOOL_NAME_PATTERN.match(base)

    def test_longest_legal_base_fits_l0_limit(self) -> None:
        base = "sk_" + "a" * 61
        assert len(base) == 64
        assert check_tool_name(base) == base

    def test_projected_names_are_l0_acceptable(self, registry: SkillRegistry) -> None:
        ensure_official_pack(registry)
        _register(registry, "sk_with.dots_in.name")
        for entry in registry.tool_catalog():
            assert check_tool_name(entry.name) == entry.name
