"""开放网络原语（T-AGT-015.1 / T-AGT-015.2 / T-AGT-015.3）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.net.gateway import EgressGateway
from st_agent.l0.net.web_config import (
    set_allowed_hosts,
    set_egress_enabled,
    set_search_provider,
)
from st_agent.l0.net.web_http import FetchedPayload
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store
from st_agent.l1.builtin import WebAccess, install_builtins
from st_agent.l1.builtin.web import WEB_FETCH, WEB_SEARCH
from st_agent.l1.runner import SkillRunner
from st_agent.l1.sandbox import SkillSandbox
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"
SEARCH_ID = "sk_web_search_v1.0"
FETCH_ID = "sk_web_fetch_v1.0"


class FakeHttp:
    def __init__(self) -> None:
        self.payload = FetchedPayload(200, "application/json", b'{"web": {"results": []}}')
        self.calls: list[tuple] = []

    def __call__(self, method, url, headers, body, timeout_s, max_bytes):
        self.calls.append((method, url, headers, body, timeout_s, max_bytes))
        return self.payload


class Env:
    def __init__(self, store: Store, vault: CredentialVault,
                 runner: SkillRunner, gateway: EgressGateway, http: FakeHttp):
        self.store = store
        self.vault = vault
        self.runner = runner
        self.gateway = gateway
        self.http = http

    def search(self, query: str, *, approved=None, **params):
        return self.runner.run(
            SEARCH_ID, {"query": query, **params},
            approved_permissions=(WEB_SEARCH.permissions if approved is None else approved),
        ).envelope

    def fetch(self, url: str, *, approved=None, **params):
        return self.runner.run(
            FETCH_ID, {"url": url, **params},
            approved_permissions=(WEB_FETCH.permissions if approved is None else approved),
        ).envelope


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    store = Store.create(tmp_path / "root", PASS)
    vault = CredentialVault(store)
    gateway = EgressGateway(store, audit=True)
    registry = SkillRegistry(store)
    sandbox = SkillSandbox(store)
    runner = SkillRunner(store, registry, gateway=gateway, sandbox=sandbox)
    http = FakeHttp()
    web = WebAccess(store, vault, http=http)
    install_builtins(
        registry, runner, context={"web": web},
        primitives=(WEB_SEARCH, WEB_FETCH),
    )
    return Env(store, vault, runner, gateway, http)


def _configure_search(env: Env, *, provider: str = "brave", key: str = "k") -> None:
    set_egress_enabled(env.store, True)
    set_search_provider(env.store, provider)
    if key:
        env.vault.add("search-api-key", "search_api_key", key)


def _brave_body(query: str) -> FetchedPayload:
    return FetchedPayload(
        200, "application/json",
        json.dumps({"web": {"results": [
            {"title": "结果", "url": "https://r.example/1", "description": query,
             "page_age": "2026-10-20"},
        ]}}).encode("utf-8"),
    )


class TestSearch:
    def test_returns_results_and_keeps_the_query_out_of_the_audit(self, env):
        _configure_search(env)
        env.http.payload = _brave_body("秘密查询")
        out = env.search("秘密查询", limit=3)
        assert out.status == "ok"
        assert out.data["count"] == 1
        assert out.data["results"][0]["source"] == "brave"
        events = env.gateway.query(kind="web_search")
        assert len(events) == 1
        blob = " ".join(e.model_dump_json() for e in events)
        assert "秘密查询" not in blob
        assert events[0].bytes_out > 0
        assert events[0].bytes_in > 0
        assert "联网搜索" in events[0].purpose

    def test_egress_off_is_unavailable_and_sends_nothing(self, env):
        assert env.search("q").status == "unavailable"
        assert "开放网络未开启" in env.search("q").reason
        assert env.http.calls == []

    def test_missing_provider_is_explicit(self, env):
        set_egress_enabled(env.store, True)
        out = env.search("q")
        assert out.status == "unavailable"
        assert "未配置搜索提供方" in out.reason
        assert env.http.calls == []

    def test_missing_key_is_explicit(self, env):
        _configure_search(env, key="")
        out = env.search("q")
        assert out.status == "unavailable"
        assert "搜索密钥不可用" in out.reason
        assert env.http.calls == []

    def test_malformed_provider_response_is_unavailable(self, env):
        _configure_search(env)
        env.http.payload = FetchedPayload(200, "application/json", b'{"web": {}}')
        out = env.search("q")
        assert out.status == "unavailable"
        assert "结果列表" in out.reason

    def test_unapproved_permissions_block_before_the_executor(self, env):
        _configure_search(env)
        out = env.search("q", approved=())
        assert out.status == "validation_failed"
        assert env.http.calls == []


class TestFetch:
    def test_allowlisted_host_returns_text(self, env):
        set_egress_enabled(env.store, True)
        set_allowed_hosts(env.store, "*.example.com")
        env.http.payload = FetchedPayload(
            200, "text/html; charset=utf-8",
            b"<html><head><title>T</title></head><body><p>hello</p></body></html>",
        )
        out = env.fetch("https://docs.example.com/a")
        assert out.status == "ok"
        assert out.data["title"] == "T"
        assert "hello" in out.data["text"]
        assert out.data["host"] == "docs.example.com"
        assert out.data["truncated"] is False
        assert env.http.calls[0][1] == "https://docs.example.com/a"

    def test_host_outside_the_list_is_blocked_before_sending(self, env):
        set_egress_enabled(env.store, True)
        set_allowed_hosts(env.store, "*.example.com")
        out = env.fetch("https://evil.test/a")
        assert out.status == "validation_failed"
        assert "不在允许抓取" in out.reason
        assert env.http.calls == []

    def test_empty_list_blocks_everything(self, env):
        set_egress_enabled(env.store, True)
        out = env.fetch("https://docs.example.com/a")
        assert out.status == "validation_failed"
        assert "名单为空" in out.reason
        assert env.http.calls == []

    def test_non_text_is_unavailable_not_mojibake(self, env):
        set_egress_enabled(env.store, True)
        set_allowed_hosts(env.store, "docs.example.com")
        env.http.payload = FetchedPayload(200, "image/png", b"\x89PNG")
        out = env.fetch("https://docs.example.com/a")
        assert out.status == "unavailable"
        assert "不支持" in out.reason

    def test_redirect_is_failed_with_a_log_pointer(self, env):
        set_egress_enabled(env.store, True)
        set_allowed_hosts(env.store, "docs.example.com")
        env.http.payload = FetchedPayload(302, "text/html", b"")
        out = env.fetch("https://docs.example.com/a")
        assert out.status == "failed"
        assert out.log_ref
        assert "重定向" in out.reason

    def test_truncates_to_max_chars(self, env):
        set_egress_enabled(env.store, True)
        set_allowed_hosts(env.store, "docs.example.com")
        env.http.payload = FetchedPayload(200, "text/plain", b"x" * 1500)
        out = env.fetch("https://docs.example.com/a", max_chars=1000)
        assert out.status == "ok"
        assert len(out.data["text"]) == 1000
        assert out.data["truncated"] is True
        assert out.data["total_chars"] == 1500
