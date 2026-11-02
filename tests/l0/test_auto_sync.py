"""数据同步的启动自动同步配置（T-UI-037.3）。"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from st_agent.__main__ import start_auto_sync
from st_agent.l0.market.auto_sync import (
    AUTO_SYNC_PATH,
    AutoSyncConfigError,
    auto_sync_config_id,
    auto_sync_entry,
    get_auto_sync_flags,
    get_auto_sync_keys,
    get_auto_sync_pairs,
    parse_auto_sync_config_id,
    set_auto_sync,
)
from st_agent.l1.registry.families import DataSyncAutoFamily

CID = auto_sync_config_id("all_a", "k_daily")


def test_round_trip_and_pair_view(store) -> None:
    assert get_auto_sync_keys(store) == ()
    assert set_auto_sync(store, CID, True) is True
    assert get_auto_sync_keys(store) == (CID,)
    assert get_auto_sync_flags(store) == {CID: True}
    assert get_auto_sync_pairs(store) == {"all_a:k_daily": True}
    set_auto_sync(store, CID, False)
    assert get_auto_sync_keys(store) == ()
    assert get_auto_sync_pairs(store) == {}


def test_config_id_validation() -> None:
    assert parse_auto_sync_config_id(CID) == ("all_a", "k_daily")
    assert parse_auto_sync_config_id("data-sync/auto.other.security") == (
        "other", "security"
    )
    assert parse_auto_sync_config_id("data-sync/auto.all_a.security") is None
    assert parse_auto_sync_config_id("data-sync/auto.other.k_daily") is None
    assert parse_auto_sync_config_id("something.else") is None


def test_invalid_key_is_rejected(store) -> None:
    with pytest.raises(AutoSyncConfigError):
        set_auto_sync(store, "data-sync/auto.nope.k_daily", True)
    with pytest.raises(AutoSyncConfigError):
        set_auto_sync(store, CID, "yes")  # type: ignore[arg-type]


def test_corrupt_file_is_explicit(store) -> None:
    store.put("config", AUTO_SYNC_PATH, b"{not json")
    with pytest.raises(AutoSyncConfigError):
        get_auto_sync_keys(store)


def test_family_records_change_and_exposes_entry(store) -> None:
    family = DataSyncAutoFamily(store)
    change = family.apply(CID, True, trace_id="tr_auto")
    assert change is not None and change.config_id == CID
    assert change.old_value is False and change.new_value is True
    entry = family.entry(CID)
    assert entry is not None and entry.default is True
    assert entry.value_schema == {"type": "boolean"}
    assert "启动自动同步" in entry.display_name
    assert any(
        name.startswith("data-sync-change/")
        for name in store.list_files("execution_log")
    )
    # 值未变 → 不落盘、不留痕
    assert family.apply(CID, True) is None


def test_entry_rejects_nonsense_keys() -> None:
    assert auto_sync_entry("data-sync/auto.all_a.security", current=False) is None


def test_startup_hook_runs_a_daemon_batch(store) -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.keys = None
            self.done = threading.Event()

        def start_auto_sync(self, keys):
            self.keys = tuple(keys)
            self.done.set()

    runner = FakeRunner()
    runtime = SimpleNamespace(
        data_sync=runner, settings=SimpleNamespace(store=store),
    )
    assert start_auto_sync(runtime) is None  # 未勾选 → 无操作

    set_auto_sync(store, CID, True)
    thread = start_auto_sync(runtime)
    assert thread is not None
    assert runner.done.wait(2.0)
    assert runner.keys == (CID,)


def test_startup_hook_is_a_noop_without_the_sync_face(store) -> None:
    runtime = SimpleNamespace(settings=SimpleNamespace(store=store))
    assert start_auto_sync(runtime) is None
