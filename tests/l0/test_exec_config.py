"""``local-exec`` 配置条目（T-AGT-016.2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.exec.exec_config import (
    DEFAULT_LOCAL_EXEC_ENABLED,
    LOCAL_EXEC_ENABLED_PATH,
    exec_config_entries,
    get_local_exec_enabled,
    set_local_exec_enabled,
)
from st_agent.l0.exec.errors import CommandConfigError
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def test_default_is_disabled(store: Store) -> None:
    assert DEFAULT_LOCAL_EXEC_ENABLED is False
    assert get_local_exec_enabled(store) is False


def test_round_trip(store: Store) -> None:
    assert set_local_exec_enabled(store, True) is True
    assert get_local_exec_enabled(store) is True
    assert set_local_exec_enabled(store, False) is False
    assert get_local_exec_enabled(store) is False


def test_non_bool_is_rejected(store: Store) -> None:
    with pytest.raises(CommandConfigError):
        set_local_exec_enabled(store, "true")  # type: ignore[arg-type]


def test_corrupt_file_is_explicit(store: Store) -> None:
    store.put("config", LOCAL_EXEC_ENABLED_PATH, b"{not json")
    with pytest.raises(CommandConfigError, match="损坏"):
        get_local_exec_enabled(store)


def test_missing_field_is_explicit(store: Store) -> None:
    store.put("config", LOCAL_EXEC_ENABLED_PATH, json.dumps({"on": True}).encode())
    with pytest.raises(CommandConfigError, match="enabled"):
        get_local_exec_enabled(store)


def test_entry_shape_is_boolean_and_requires_confirmation() -> None:
    entry = exec_config_entries()[0]
    assert entry.config_id == "local-exec/enabled"
    assert entry.value_schema == {"type": "boolean"}
    assert entry.default is False
    assert entry.scope == "global"
    assert entry.change_policy.requires_confirmation is True
    assert entry.panel_form_spec.widget == "toggle"
