"""T-L1-012.2 测试（L2 半）：``memory-policy`` 族的**只读**门面适配器。

对应任务 GWT-3（L2 族经组合根注入）与假设 A3 / A5：

- 未注入 → 该族不在列、条目取不到（不臆测、不伪造）
- 注入后 → `list(scope="global")` 含该族、`entry(config_id)` 可读，且取值随
  ``MemoryPolicyStore`` 的当前值刷新
- **写面 fail-closed**（取值是对象 / 列表，不在统一标量落值面内，写面归 owner API）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.storage import Store
from st_agent.l1.registry import ConfigRegistryFacade, RegistryValidationError
from st_agent.l2.memory import MemoryPolicyFamily, memory_policy_family
from st_agent.l2.memory.write_policy import (
    DEFAULT_WRITE_WHITELIST,
    WRITE_WHITELIST_CONFIG_ID,
    WritePolicy,
)

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def _facade(store: Store, policy: WritePolicy) -> ConfigRegistryFacade:
    registry = ConfigRegistryFacade(store)
    registry.register_family(memory_policy_family(store, declarations=lambda: (policy.entry(),)))
    return registry


def test_family_is_absent_until_injected(store: Store) -> None:
    facade = ConfigRegistryFacade(store)
    assert facade.list("global") == ()
    assert facade.entry(WRITE_WHITELIST_CONFIG_ID) is None


def test_injected_family_is_listed_and_readable(store: Store) -> None:
    policy = WritePolicy(store)
    facade = _facade(store, policy)
    assert [e.config_id for e in facade.list("global")] == [WRITE_WHITELIST_CONFIG_ID]
    entry = facade.entry(WRITE_WHITELIST_CONFIG_ID)
    assert entry is not None
    assert entry.scope == "global"
    assert entry.default == list(DEFAULT_WRITE_WHITELIST)


def test_entry_overlays_the_current_value(store: Store) -> None:
    policy = WritePolicy(store)
    facade = _facade(store, policy)
    full = ("identity", "attention", "thesis", "history", "pattern", "evolution")
    assert policy.set_dimensions(full) is not None
    assert facade.entry(WRITE_WHITELIST_CONFIG_ID).default == list(full)


def test_write_face_is_fail_closed(store: Store) -> None:
    policy = WritePolicy(store)
    facade = _facade(store, policy)
    with pytest.raises(RegistryValidationError) as excinfo:
        facade.set(WRITE_WHITELIST_CONFIG_ID, ["thesis"])
    assert "set_*" in str(excinfo.value)      # 点名 owner 的写面


def test_family_prefix_is_declared() -> None:
    assert MemoryPolicyFamily.config_prefix == "memory-policy/"
