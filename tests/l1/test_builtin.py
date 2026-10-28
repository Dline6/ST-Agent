"""T-AGT-008.1 / T-AGT-008.2 测试：`builtin` 来源与内置原语安装面。

GWT 对照：
- 008.1 GWT-1：builtin 走同一注册面，工具目录照常投影、版本折叠与 Skill 一致
- 008.1 语义：不可编辑（`set_parameters` 拒）/ 不可版本化（`publish_version` 拒）
- 008.2 GWT-1：经 `SkillRunner` 执行，留痕与 Trace 与 Skill 同口径
- 008.2 GWT-2：未注册执行器 → 显式失败
- 008.2 GWT-3：权限未批准 / 沙箱越界 → 拦截且不执行原动作
- 008.2 安装面：幂等、执行器重绑、冲突与依赖缺失显式拒
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.builtin import (
    BuiltinPrimitive,
    BuiltinRegistrationError,
    install_builtins,
)
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.sandbox import SkillSandbox
from st_agent.l1.skills import (
    SkillRegistry,
    SkillValidationError,
    split_arguments,
)

PASS = "correct horse battery staple"

BASE = "sk_builtin_probe"
SKILL_ID = f"{BASE}_v1.0"
CACHE_SCOPE = "local_read:<data/cache/**>"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    return SkillRegistry(store)


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry) -> SkillRunner:
    return SkillRunner(store, registry)


def _ok(payload: dict | None = None):
    return lambda ctx, params: ResultEnvelope.ok(payload or {"probe": "ok"})


def _primitive(**over) -> BuiltinPrimitive:
    seed: dict = {
        "base": BASE,
        "name": "内置探针",
        "description": "内置原语注册面用例",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"probe": {"type": "string"}}},
        "parameters": (),
        "permissions": (),
        "executor": lambda ctx: _ok(),
    }
    seed.update(over)
    return BuiltinPrimitive(**seed)


# ───────────────────────── 008.1 · 同一注册面与版本折叠 ─────────────────────────


class TestBuiltinSource:
    def test_registers_through_same_surface_and_folds_versions(self, registry, runner):
        added = install_builtins(
            registry, runner,
            primitives=(_primitive(version="1.0"), _primitive(version="1.1")),
        )
        assert added == (SKILL_ID, f"{BASE}_v1.1")

        first = registry.get(SKILL_ID)
        assert first.source == "builtin"
        assert first.version_policy == "locked"
        assert first.provenance.sharer is None

        catalog = registry.tool_catalog()
        assert [entry.name for entry in catalog] == [BASE]
        assert catalog[0].skill_id == f"{BASE}_v1.1"

    def test_parameters_are_not_editable(self, registry, runner):
        seed = _primitive(parameters=(
            ParameterSpec(name="limit", type="integer", default=10, description="条数"),
        ))
        install_builtins(registry, runner, primitives=(seed,))
        with pytest.raises(SkillValidationError):
            registry.set_parameters(SKILL_ID, {"limit": 20})
        assert registry.get(SKILL_ID).parameters[0].default == 10

    def test_publish_version_is_rejected(self, registry, runner):
        install_builtins(registry, runner, primitives=(_primitive(),))
        with pytest.raises(SkillValidationError):
            registry.publish_version(BASE, version="2.0", changelog="x", impact="y")
        assert len(registry.list_versions(BASE)) == 1


# ───────────────────────── 008.2 · 执行流水线与拦截面 ─────────────────────────


class TestBuiltinExecution:
    def test_runs_through_the_same_pipeline(self, store, registry, runner):
        install_builtins(registry, runner, primitives=(_primitive(),))
        out = runner.run(SKILL_ID)
        assert out.envelope.status == "ok"
        assert [step.step_type for step in out.trace.steps] == ["skill_run"]
        assert f"{RUN_PREFIX}{out.skill_run_id}.json" in store.list_files("execution_log")

    def test_missing_executor_fails_explicitly(self, registry, runner):
        registry.register(
            BASE, name="内置探针", description="缺执行器的内置原语",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            source="builtin",
        )
        out = runner.run(SKILL_ID)
        assert out.envelope.status == "failed"
        assert "未注册执行器" in (out.envelope.reason or "")
        assert out.envelope.log_ref

    def test_unapproved_permission_blocks_execution(self, registry, runner):
        called: list[int] = []

        def factory(ctx):
            return lambda c, p: called.append(1) or ResultEnvelope.ok({"probe": "ok"})

        install_builtins(
            registry, runner,
            primitives=(_primitive(permissions=(CACHE_SCOPE,), executor=factory),),
        )
        blocked = runner.run(SKILL_ID)
        assert blocked.envelope.status == "validation_failed"
        assert called == []

        allowed = runner.run(SKILL_ID, approved_permissions=(CACHE_SCOPE,))
        assert allowed.envelope.status == "ok"
        assert called == [1]

    def test_sandbox_blocks_out_of_scope_without_running_the_action(self, store, registry):
        hit: list[str] = []

        def factory(ctx):
            def fn(c, p):
                outcome = c.sandbox.read_file(
                    "secrets/token.txt", lambda path: hit.append(path) or "x")
                if outcome.status != "ok":
                    return outcome
                return ResultEnvelope.ok({"probe": "read"})
            return fn

        sandbox = SkillSandbox(store)
        guarded = SkillRunner(store, registry, sandbox=sandbox)
        install_builtins(
            registry, guarded,
            primitives=(_primitive(permissions=(CACHE_SCOPE,), executor=factory),),
        )
        out = guarded.run(SKILL_ID, approved_permissions=(CACHE_SCOPE,))
        assert out.envelope.status == "validation_failed"
        assert hit == []


# ───────────────────────── 008.2 · 安装面语义 ─────────────────────────


class TestInstallSemantics:
    def test_install_is_idempotent_and_rebinds_executors(self, registry, runner):
        first = install_builtins(registry, runner, primitives=(_primitive(),))
        assert first == (SKILL_ID,)

        second = install_builtins(
            registry, runner,
            primitives=(_primitive(executor=lambda ctx: _ok({"probe": "rebound"})),),
        )
        assert second == ()
        assert registry.get(SKILL_ID).source == "builtin"
        assert runner.run(SKILL_ID).envelope.data == {"probe": "rebound"}

    def test_non_builtin_collision_is_rejected(self, registry, runner):
        registry.register(
            BASE, name="同名自建", description="与内置原语同 id 的自建能力",
            source="user-built",
        )
        with pytest.raises(BuiltinRegistrationError):
            install_builtins(registry, runner, primitives=(_primitive(),))

    def test_duplicate_seeds_are_rejected(self, registry, runner):
        with pytest.raises(BuiltinRegistrationError):
            install_builtins(
                registry, runner, primitives=(_primitive(), _primitive()),
            )

    def test_missing_required_context_is_rejected_at_install_time(self, registry, runner):
        seed = _primitive(requires=("market_db",))
        with pytest.raises(BuiltinRegistrationError):
            install_builtins(registry, runner, primitives=(seed,))
        assert registry.list_all() == ()

        seen: dict[str, set[str]] = {}

        def factory(ctx):
            seen["keys"] = set(ctx)
            return _ok()

        added = install_builtins(
            registry, runner, context={"market_db": object()},
            primitives=(_primitive(requires=("market_db",), executor=factory),),
        )
        assert added == (SKILL_ID,)
        assert seen["keys"] == {"market_db"}

    def test_empty_seed_table_installs_nothing(self, registry, runner):
        assert install_builtins(registry, runner, primitives=()) == ()
        assert registry.list_all() == ()


# ───────────────────────── 008.3 · 通道声明随条目带出 ─────────────────────────


class TestChannelDeclaration:
    def test_entry_carries_both_channel_keys(self, registry):
        registry.register(
            BASE, name="双通道探针", description="标量与对象参数并列",
            input_schema={
                "type": "object",
                "properties": {"bindings": {"type": "array"}},
                "required": ["bindings"],
            },
            output_schema={"type": "object"},
            parameters=(ParameterSpec(name="sql", type="string", description="查询语句"),),
        )
        entry = registry.tool_catalog()[0]
        assert entry.scalar_parameters == ("sql",)
        assert entry.object_properties == ("bindings",)

    def test_split_routes_by_declaration(self, registry):
        registry.register(
            BASE, name="双通道探针", description="标量与对象参数并列",
            input_schema={
                "type": "object",
                "properties": {"bindings": {"type": "array"}},
            },
            output_schema={"type": "object"},
            parameters=(ParameterSpec(name="sql", type="string", description="查询语句"),),
        )
        entry = registry.tool_catalog()[0]
        values, inputs = split_arguments(
            entry, {"sql": "select 1", "bindings": [1], "typo": 2},
        )
        assert values == {"sql": "select 1", "typo": 2}
        assert inputs == {"bindings": [1]}
