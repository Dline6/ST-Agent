"""数据源开关族（[`T-UI-018.1`]；[01 §7] + 决策 [D-067] 的同款适配）。

与 ``retention`` / ``net-audit`` 同形：owner 只落开关值、**不产生** ``ChangeRecord``，
由本族补落一条（``data-source-change/``）；门面是统一入口、**不旁路写盘**。

锁三件事：

1. 条目按**已注册源**派生、`default` 取**当前值**（面板回显）；
2. 落值真生效（读回一致）并留痕；
3. 取值未变 ⇒ 无留痕、无写入；越界条目 / 非法取值显式拒。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.info.sources import INFO_SOURCES
from st_agent.l0.info.switch_config import get_source_switches
from st_agent.l0.storage import Store
from st_agent.l1.registry import CHANGE_PARTITION, ConfigRegistryFacade
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.families import DataSourceSwitchFamily

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> ConfigRegistryFacade:
    facade = ConfigRegistryFacade(store)
    facade.register_family(DataSourceSwitchFamily(store))
    return facade


def test_entries_come_from_the_registered_sources(registry: ConfigRegistryFacade) -> None:
    """条目按已注册源派生，`default` ＝当前值（首装即缺省＝开）。"""
    entries = [e for e in registry.list() if e.config_id.startswith("data-source/")]
    assert len(entries) == len(INFO_SOURCES)
    assert {e.display_name for e in entries} == {s.name for s in INFO_SOURCES}
    assert all(e.default is True for e in entries)


def test_apply_persists_and_records_a_change(
    store: Store, registry: ConfigRegistryFacade
) -> None:
    """落值真生效（owner 侧读回一致）且补落一条 ``ChangeRecord``。"""
    change = registry.set("data-source/eastmoney", False, trace_id="t-1")
    assert change is not None
    assert change.config_id == "data-source/eastmoney"
    assert change.old_value is True and change.new_value is False
    assert get_source_switches(store)["eastmoney"] is False

    files = [n for n in store.list_files(CHANGE_PARTITION) if n.startswith("data-source-change/")]
    assert len(files) == 1
    assert registry.entry("data-source/eastmoney").default is False


def test_unchanged_value_leaves_no_trace(
    store: Store, registry: ConfigRegistryFacade
) -> None:
    """取值未变 ⇒ 返回 ``None``、不留痕、不落盘（与各 owner 的既有取向一致）。"""
    assert registry.set("data-source/sse", True) is None
    assert not [n for n in store.list_files(CHANGE_PARTITION)
                if n.startswith("data-source-change/")]
    with pytest.raises(KeyError):
        store.get("config", "data-source/sse.json")


def test_non_boolean_value_is_rejected(registry: ConfigRegistryFacade) -> None:
    """取值须为布尔（值域校验经门面照样生效）。"""
    with pytest.raises(RegistryValidationError):
        registry.set("data-source/cninfo", "false")


def test_unknown_entry_is_rejected(registry: ConfigRegistryFacade) -> None:
    """未注册的条目一律显式拒——门面不猜、不静默建条目。"""
    with pytest.raises(RegistryValidationError):
        registry.set("data-source/不存在的源", False)
    assert registry.entry("data-source/不存在的源") is None
