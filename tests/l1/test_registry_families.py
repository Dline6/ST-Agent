"""T-L1-012.2 测试：存量族适配与孤儿条目纳入（01 §7 + 决策 D-067）。

GWT 对照（任务文件 5 条，GWT-3 的 L2 那半见 ``tests/l2/test_registry_adapter.py``）：

- GWT-1 孤儿纳入：`retention.*` 两条在列、经门面落值真生效并补上变更留痕
- GWT-2 L1 三族委托既有 API：越界取值经门面一样被 owner 的值域门拒
- GWT-4 不改名 · 存量零回归：各族落盘路径与 owner 单跑时一致
- GWT-5 门面为唯一登记入口、前缀冲突即拒

另覆盖 A1（适配＝委托 owner，不旁路写盘）、A2（retention 留痕由门面补）、
A4（族集合由组合根定）、A5（写面按取值形状划界）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.backup import get_policy
from st_agent.l0.storage import Store
from st_agent.l1.mcp.errors import McpValidationError
from st_agent.l1.mcp.lifecycle import (
    INTERVAL_CONFIG_ID,
    MAX_RETRIES_CONFIG_ID,
    McpHubConfig,
)
from st_agent.l1.registry import (
    ConfigRegistryFacade,
    McpHubPolicyFamily,
    ProviderHostFamily,
    RegistryFamilyConflict,
    RegistryValidationError,
    RetentionFamily,
    SchedulerPolicyFamily,
)
from st_agent.l1.sandbox.provider_hosts import ProviderHostRegistry
from st_agent.l1.scheduler import (
    OFFLINE_CATCH_UP_CONFIG_ID,
    SchedulerPolicy,
    SchedulerValidationError,
)

PASS = "correct horse battery staple"

RETENTION_CHAT = "retention.chat_history_days"
RETENTION_LOG = "retention.execution_log_days"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def hub(store: Store) -> McpHubConfig:
    config = McpHubConfig(store)
    config.register_defaults()
    return config


@pytest.fixture()
def facade(store: Store, hub: McpHubConfig) -> ConfigRegistryFacade:
    """组合根口径的装配：门面 + L0 / L1 侧各族（A4：族集合由调用方定）。"""
    registry = ConfigRegistryFacade(store)
    registry.register_family(RetentionFamily(store))
    registry.register_family(SchedulerPolicyFamily(SchedulerPolicy(store)))
    registry.register_family(McpHubPolicyFamily(hub))
    registry.register_family(ProviderHostFamily(ProviderHostRegistry(store), store))
    return registry


# ───────────────────────── GWT-1 孤儿纳入 ─────────────────────────


def test_retention_orphans_enter_the_registry(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    ids = {e.config_id for e in facade.list("global")}
    assert {RETENTION_CHAT, RETENTION_LOG} <= ids
    entry = facade.entry(RETENTION_CHAT)
    assert entry is not None
    assert entry.default == 365  # 产品默认（02 §9），此前从未落盘


def test_retention_set_takes_effect_and_leaves_a_trace(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    change = facade.set(RETENTION_CHAT, 30)
    assert change is not None
    assert (change.old_value, change.new_value) == (365, 30)
    assert get_policy(store).chat_history_days == 30          # 经 owner 真生效
    assert facade.entry(RETENTION_CHAT).default == 30
    # A2：该族在 L0 侧本不产生 ChangeRecord，由门面补落
    assert store.list_files("execution_log") == (
        f"retention-change/{change.change_id}.json",
    )


def test_retention_rejects_out_of_domain(facade: ConfigRegistryFacade) -> None:
    for bad in (-1, True, "30", 1.5):
        with pytest.raises(RegistryValidationError):
            facade.set(RETENTION_CHAT, bad)
    assert facade.set(RETENTION_CHAT, 365) is None  # 值未变不留痕


# ───────────────────────── GWT-2 L1 三族委托既有 API ─────────────────────────


def test_scheduler_policy_delegates_to_owner(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    assert facade.entry(OFFLINE_CATCH_UP_CONFIG_ID) is not None
    change = facade.set(OFFLINE_CATCH_UP_CONFIG_ID, "skip")
    assert change is not None and change.new_value == "skip"
    assert SchedulerPolicy(store).value() == "skip"
    assert facade.set(OFFLINE_CATCH_UP_CONFIG_ID, "skip") is None
    # A1：owner 的值域门经门面照样生效（不旁路写盘）
    with pytest.raises(SchedulerValidationError):
        facade.set(OFFLINE_CATCH_UP_CONFIG_ID, "nope")


def test_mcp_hub_delegates_to_owner(facade: ConfigRegistryFacade, store: Store) -> None:
    assert {MAX_RETRIES_CONFIG_ID, INTERVAL_CONFIG_ID} <= {
        e.config_id for e in facade.list("global")
    }
    change = facade.set(MAX_RETRIES_CONFIG_ID, 5)
    assert change is not None and change.new_value == 5
    assert McpHubConfig(store).get().max_retries == 5
    with pytest.raises(McpValidationError):
        facade.set(INTERVAL_CONFIG_ID, -1)   # owner 的非负整数门


def test_provider_host_delegates_and_records_change(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    registry = ProviderHostRegistry(store)
    registry.register("openai", "api.openai.com")
    config_id = "llm-provider-host/openai"
    assert facade.entry(config_id) is not None
    change = facade.set(config_id, "api.deepseek.com")
    assert change is not None
    assert (change.old_value, change.new_value) == ("api.openai.com", "api.deepseek.com")
    assert ProviderHostRegistry(store).resolve("openai") == "api.deepseek.com"
    # 该 owner 同样不产 ChangeRecord（A2 同型），由门面补落
    assert f"provider-host-change/{change.change_id}.json" in store.list_files("execution_log")


def test_unregistered_family_target_is_rejected(facade: ConfigRegistryFacade) -> None:
    with pytest.raises(RegistryValidationError):
        facade.set("memory-policy/write-whitelist", ["thesis"])
    assert facade.entry("memory-policy/write-whitelist") is None


# ───────────────────────── GWT-4 不改名 · 存量零回归 ─────────────────────────


def test_existing_paths_are_untouched(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    facade.set(OFFLINE_CATCH_UP_CONFIG_ID, "skip")
    facade.set(MAX_RETRIES_CONFIG_ID, 5)
    facade.set(RETENTION_CHAT, 30)
    paths = set(store.list_files("config"))
    # 各族沿用其既有落盘路径（D-067 ③「不改名」）
    assert "scheduler-policy/offline-catch-up.json" in paths
    assert "mcp-hub/reconnect-max-retries.json" in paths
    assert "retention/policy.json" in paths


# ───────────────────────── GWT-5 唯一入口 · 前缀冲突 ─────────────────────────


def test_family_prefix_conflict_is_rejected(
    facade: ConfigRegistryFacade, store: Store
) -> None:
    with pytest.raises(RegistryFamilyConflict):
        facade.register_family(RetentionFamily(store))


def test_families_are_listed_by_prefix(facade: ConfigRegistryFacade) -> None:
    prefixes = [f.config_prefix for f in facade.families()]
    assert prefixes == sorted(prefixes)
    assert {"retention.", "scheduler-policy/", "mcp-hub/", "llm-provider-host/"} <= set(prefixes)
