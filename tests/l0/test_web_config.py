"""开放网络配置（T-AGT-015；01 §7 / 02 §6）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.net.errors import NetWebConfigError
from st_agent.l0.net.web_config import (
    ALLOWED_HOSTS_CONFIG_ID,
    DEFAULT_EGRESS_ENABLED,
    DEFAULT_SEARCH_PROVIDER,
    EGRESS_ENABLED_CONFIG_ID,
    KEY_CONFIG_ID,
    PROVIDER_CONFIG_ID,
    allowed_host_patterns,
    get_allowed_hosts,
    get_egress_enabled,
    get_search_provider,
    load_web_settings,
    set_allowed_hosts,
    set_egress_enabled,
    set_search_provider,
    validate_host_patterns,
    web_config_entries,
)
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def test_defaults_are_off_and_unconfigured(store: Store) -> None:
    assert get_search_provider(store) == DEFAULT_SEARCH_PROVIDER
    assert get_egress_enabled(store) == DEFAULT_EGRESS_ENABLED
    assert get_allowed_hosts(store) == ""
    settings = load_web_settings(store)
    assert settings.provider == ""
    assert settings.egress_enabled is False
    assert settings.allowed_hosts == ()


def test_provider_and_switch_roundtrip(store: Store) -> None:
    assert set_search_provider(store, "brave") == "brave"
    assert get_search_provider(store) == "brave"
    assert set_egress_enabled(store, True) is True
    assert get_egress_enabled(store) is True
    settings = load_web_settings(store)
    assert settings.provider == "brave"
    assert settings.egress_enabled is True


@pytest.mark.parametrize("provider", ["", "brave", "tavily"])
def test_known_providers_are_accepted(store: Store, provider: str) -> None:
    assert set_search_provider(store, provider) == provider


def test_unknown_provider_is_rejected(store: Store) -> None:
    with pytest.raises(NetWebConfigError):
        set_search_provider(store, "nope")


def test_host_patterns_are_split_and_lowercased() -> None:
    raw = "*.Example.com, foo.com\nbar.cn； baz.io"
    assert validate_host_patterns(raw) == ("*.example.com", "foo.com", "bar.cn", "baz.io")
    assert allowed_host_patterns(raw) == ("*.example.com", "foo.com", "bar.cn", "baz.io")


@pytest.mark.parametrize("raw", ["", "  ", "a.example.com"])
def test_valid_host_patterns_roundtrip(store: Store, raw: str) -> None:
    assert set_allowed_hosts(store, raw) == raw
    assert get_allowed_hosts(store) == raw


@pytest.mark.parametrize("raw", ["http://example.com", "example.com:8080", "a/b", "a b"])
def test_invalid_host_patterns_are_rejected(store: Store, raw: str) -> None:
    with pytest.raises(NetWebConfigError):
        set_allowed_hosts(store, raw)


def test_corrupted_config_is_explicit(store: Store) -> None:
    store.put("config", "web-egress/enabled.json", b"{not json")
    with pytest.raises(NetWebConfigError):
        get_egress_enabled(store)


def test_config_entries_have_the_expected_shape() -> None:
    entries = {e.config_id: e for e in web_config_entries()}
    assert set(entries) == {
        PROVIDER_CONFIG_ID, KEY_CONFIG_ID,
        EGRESS_ENABLED_CONFIG_ID, ALLOWED_HOSTS_CONFIG_ID,
    }
    assert entries[PROVIDER_CONFIG_ID].default == ""
    assert entries[PROVIDER_CONFIG_ID].panel_form_spec.widget == "select"
    assert entries[EGRESS_ENABLED_CONFIG_ID].default is False
    assert entries[EGRESS_ENABLED_CONFIG_ID].change_policy.requires_confirmation is True
    assert entries[ALLOWED_HOSTS_CONFIG_ID].default == ""
    assert entries[KEY_CONFIG_ID].value_schema == {"type": "string"}
    # 条目形态自洽：default 与声明类型匹配由 ConfigEntry 自身校验。
    for entry in entries.values():
        assert json.dumps(entry.model_dump(), ensure_ascii=False)
