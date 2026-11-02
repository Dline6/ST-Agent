"""T-UI-038：Agent 工具开关（[01 §7] / [01 §2]）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.registry.families import AgentToolFamily
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.tool_selection import (
    TOOL_SELECTION_PATH,
    ToolSelectionError,
    get_disabled_tools,
    is_tool_enabled,
    parse_tool_selection_config_id,
    set_tool_enabled,
    tool_selection_config_id,
    tool_selection_entry,
)

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    reg.register(
        base="sk_demo_tool", name="演示工具", description="用于测试的工具", permissions=(),
    )
    return reg


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry) -> SkillRunner:
    runner = SkillRunner(store, registry)
    runner.register_executor(
        "sk_demo_tool_v1.0", lambda ctx, params: ResultEnvelope.ok({"ok": True}),
    )
    return runner


class TestConfig:
    def test_absent_means_enabled(self, store: Store):
        assert get_disabled_tools(store) == frozenset()
        assert is_tool_enabled(store, "sk_demo_tool") is True

    def test_disable_and_reenable(self, store: Store):
        assert set_tool_enabled(store, "sk_demo_tool", False) is False
        assert get_disabled_tools(store) == {"sk_demo_tool"}
        assert is_tool_enabled(store, "sk_demo_tool") is False
        assert set_tool_enabled(store, "sk_demo_tool", True) is True
        assert get_disabled_tools(store) == frozenset()

    def test_config_id_round_trip(self):
        config_id = tool_selection_config_id("sk_demo_tool")
        assert config_id == "agent-tools/sk_demo_tool"
        assert parse_tool_selection_config_id(config_id) == "sk_demo_tool"
        assert parse_tool_selection_config_id("agent-tools/not-a-skill") is None

    def test_rejects_bad_values(self, store: Store):
        with pytest.raises(ToolSelectionError):
            set_tool_enabled(store, "sk_demo_tool", "no")  # type: ignore[arg-type]
        with pytest.raises(ToolSelectionError):
            set_tool_enabled(store, "bad-name", False)

    def test_corrupt_file_raises(self, store: Store):
        store.put("config", TOOL_SELECTION_PATH, b"{not json")
        with pytest.raises(ToolSelectionError):
            get_disabled_tools(store)

    def test_entry_carries_current_value(self, store: Store):
        assert tool_selection_entry("sk_demo_tool", current=False).default is False
        assert tool_selection_entry("sk_demo_tool", current=True).default is True


class TestFamily:
    def test_entries_list_persisted_disabled_tools(self, store: Store):
        set_tool_enabled(store, "sk_demo_tool", False)
        entries = {entry.config_id: entry for entry in AgentToolFamily(store).entries()}
        assert entries["agent-tools/sk_demo_tool"].default is False

    def test_apply_disable_records_change(self, store: Store):
        family = AgentToolFamily(store)
        change = family.apply("agent-tools/sk_demo_tool", False, trace_id="test")
        assert change is not None
        assert (change.old_value, change.new_value) == (True, False)
        assert family.entry("agent-tools/sk_demo_tool").default is False
        assert get_disabled_tools(store) == {"sk_demo_tool"}
        assert any(
            name.startswith("agent-tool-change/")
            for name in store.list_files("execution_log")
        )

    def test_apply_unchanged_value_no_record(self, store: Store):
        family = AgentToolFamily(store)
        assert family.apply("agent-tools/sk_demo_tool", True) is None


class TestRunner:
    def test_disabled_tool_is_not_executed(self, store: Store, runner: SkillRunner):
        set_tool_enabled(store, "sk_demo_tool", False)
        out = runner.run("sk_demo_tool_v1.0", {})
        assert out.envelope.status == "unavailable"
        assert "agent-tools/sk_demo_tool" in out.envelope.reason

    def test_enabled_tool_runs(self, store: Store, runner: SkillRunner):
        out = runner.run("sk_demo_tool_v1.0", {})
        assert out.envelope.status == "ok"

    def test_corrupt_config_fails_closed(self, store: Store, runner: SkillRunner):
        store.put("config", TOOL_SELECTION_PATH, b"{not json")
        out = runner.run("sk_demo_tool_v1.0", {})
        assert out.envelope.status == "unavailable"
        assert "fail-closed" in out.envelope.reason

    def test_disabled_dependency_blocks_downstream(
        self, store: Store, registry: SkillRegistry, runner: SkillRunner,
    ):
        registry.register(
            base="sk_downstream", name="下游", description="依赖演示工具",
            dependencies=("sk_demo_tool_v1.0",), permissions=(),
        )
        runner.register_executor(
            "sk_downstream_v1.0", lambda ctx, params: ResultEnvelope.ok({}),
        )
        set_tool_enabled(store, "sk_demo_tool", False)
        out = runner.run("sk_downstream_v1.0", {})
        assert out.envelope.status == "dependency_failed"
