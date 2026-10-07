"""T-L1-011 测试：LLM 端点与会话装配接线（03 §1.1 + 02 §3 / §4；T-L0-016 的下游）。

GWT 对照（任务文件 4 条）：

- GWT-1 `.env` 三键 → 端点登记 + 凭据入 ``secrets``（掩码可查）+ ``provider → host``
  播种，且 ``LlmClient`` 的 transport **非 None**
- GWT-2 装配后端到端拿到 ``chunk* → done``，审计留 ``llm_call`` 记录
- GWT-3 技能侧越界仍被 ``GuardedLlmClient`` 拦为 ``validation_failed``，**不触碰**
  真实发送器（组合根接线不放宽沙箱）
- GWT-4 `.env` 缺失 / 三键不全 → 不抛错，调用仍 ``unavailable``（fail-closed）

**全离线**：真实发送器的 HTTP 发送口经 ``llm_post`` 注入替身。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import (
    LLM_CREDENTIAL_ID,
    LLM_ENDPOINT_ID,
    LlmBootstrap,
    build_l1_runtime,
    llm_env_from_ambient,
    load_dotenv,
    open_runtime,
)

PASS = "correct horse battery staple"
BASE_URL = "https://api.example.com/v1"
MODEL = "test-model"
KEY = "sk-proj-abcdef1234567890-KEYSECRET"
ENV = {"LLM_API_KEY": KEY, "LLM_BASE_URL": BASE_URL, "LLM_MODEL": MODEL}
LOCAL_CAP = {"max_context_tokens": 4_096, "supports_structured_output": False}
PROMPT = "PROMPT-MARKER-4821"


class _FakeMarketQuery:
    def query(self, sql: str, params: tuple = ()):
        return ResultEnvelope.empty("本用例不取数")


_FAKE_MARKET = _FakeMarketQuery()


class Post:
    """注入式 HTTP 发送口替身（产出 SSE 行；记录调用）。"""

    def __init__(self, *contents: str) -> None:
        self.calls: list[dict] = []
        self._lines = [
            f"data: {json.dumps({'choices': [{'delta': {'content': c}}]}, ensure_ascii=False)}"
            for c in contents
        ] + ["data: [DONE]"]

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        return iter(self._lines)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离宿主环境：本机若设了 ``LLM_*``，不得影响用例判定。"""
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)


def _build(store: Store, **kwargs):
    kwargs.setdefault("llm_config", LlmBootstrap.from_env(ENV))
    # 本套件断言「LLM 调用经网关并留痕」，故显式开审计（产品缺省＝关，02 §6 / D-073；
    # 默认关语义见 tests/l0/test_gateway_audit_mode.py）
    kwargs.setdefault("audit", True)
    return build_l1_runtime(store, market_query=_FAKE_MARKET, **kwargs)


# ───────────────────────── GWT-1 三键 → 装配即用 ─────────────────────────


class TestGwt1EnvSeedsAndWires:
    def test_endpoint_credential_and_host_are_seeded(self, store: Store):
        rt = _build(store, llm_post=Post("ok"))

        assert rt.endpoints.get(LLM_ENDPOINT_ID).credential_id == LLM_CREDENTIAL_ID
        assert rt.endpoints.get(LLM_ENDPOINT_ID).provider == "openai-compatible"
        assert [c.credential_id for c in rt.vault.list_credentials()] == [LLM_CREDENTIAL_ID]
        assert rt.provider_hosts.resolve("openai-compatible") == "api.example.com"

    def test_credential_is_masked_in_views(self, store: Store):
        rt = _build(store, llm_post=Post("ok"))

        view = rt.vault.get_view(LLM_CREDENTIAL_ID)

        assert KEY not in json.dumps(view.model_dump(), ensure_ascii=False, default=str)

    def test_open_runtime_reads_injected_env(self, tmp_path: Path):
        rt = open_runtime(tmp_path / "root", PASS, create=True,
                          market_query=_FAKE_MARKET, llm_env=ENV,
                          llm_post=Post("ok"))

        assert rt.endpoints.get(LLM_ENDPOINT_ID).provider == "openai-compatible"
        assert rt.provider_hosts.resolve("openai-compatible") == "api.example.com"

    def test_reseed_never_overwrites_existing_records(self, store: Store):
        """幂等播种（与官方 Pack 同口径）：既有配置一字不改。"""
        first = build_l1_runtime(store, market_query=_FAKE_MARKET)
        first.vault.add("user-own-key", "llm_api_key", "sk-user-own-KEYSECRET")
        first.endpoints.register(LLM_ENDPOINT_ID, kind="cloud",
                                 provider="openai-compatible", capability=LOCAL_CAP,
                                 credential_id="user-own-key")
        first.provider_hosts.register("openai-compatible", "user.example.com")

        rt = _build(store, llm_post=Post("ok"))

        assert rt.endpoints.get(LLM_ENDPOINT_ID).credential_id == "user-own-key"
        assert rt.provider_hosts.resolve("openai-compatible") == "user.example.com"
        assert [c.credential_id for c in rt.vault.list_credentials()] == ["user-own-key"]


# ───────────────────────── GWT-2 端到端 + 审计 ─────────────────────────


class TestGwt2EndToEnd:
    def test_call_completes_and_is_audited(self, store: Store):
        post = Post("你好", "，世界")
        rt = _build(store, llm_post=post)

        events = list(rt.llm.invoke(LLM_ENDPOINT_ID, PROMPT,
                                    initiator="t", purpose="意图理解"))

        assert [e.kind for e in events] == ["chunk", "chunk", "done"]
        assert "".join(e.text for e in events) == "你好，世界"
        (call,) = post.calls
        assert call["url"] == f"{BASE_URL}/chat/completions"
        assert call["headers"]["Authorization"] == f"Bearer {KEY}"
        (audit,) = rt.gateway.query(kind="llm_call")
        assert audit.status == "ok"
        assert audit.target_host == "api.example.com"
        assert audit.bytes_out == len(PROMPT.encode("utf-8"))

    def test_explicit_transport_wins_over_bootstrap(self, store: Store):
        """显式 ``transport`` 优先：引导装载完全不启动（不播种记录）。"""
        def transport(endpoint, prompt, key, timeout_ms, tools=None):
            yield "stub"

        rt = _build(store, transport=transport, llm_post=Post("ok"))

        assert rt.endpoints.list_endpoints() == ()
        assert rt.vault.list_credentials() == ()
        assert rt.provider_hosts.list() == ()


# ───────────────────────── GWT-3 技能侧沙箱不回退 ─────────────────────────


class TestGwt3SkillPathStaysSandboxed:
    def test_out_of_scope_endpoint_is_blocked_without_touching_sender(self, store: Store):
        post = Post("不应发生")
        rt = _build(store, llm_post=post)
        descriptor = rt.skills.register(
            base="sk_demo_llm", name="演示功能", description="演示用 Skill 描述",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            permissions=("net_access:<*.ok.com>",),
        )
        session = rt.sandbox.session(descriptor, descriptor.permissions,
                                     trace_id="trace-llm-guard")
        guarded = session.guard_llm(rt.llm)

        events = list(guarded.invoke(LLM_ENDPOINT_ID, "hi", initiator="t", purpose="t"))

        assert [e.kind for e in events] == ["error"]
        assert events[0].error_envelope.status == "validation_failed"
        assert post.calls == []            # 底层发送器未被触碰

    def test_in_scope_endpoint_passes_the_guard(self, store: Store):
        post = Post("放行")
        rt = _build(store, llm_post=post)
        descriptor = rt.skills.register(
            base="sk_demo_llm_ok", name="演示功能", description="演示用 Skill 描述",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            permissions=("net_access:<*.example.com>",),
        )
        session = rt.sandbox.session(descriptor, descriptor.permissions,
                                     trace_id="trace-llm-allow")
        guarded = session.guard_llm(rt.llm)

        events = list(guarded.invoke(LLM_ENDPOINT_ID, "hi", initiator="t", purpose="t"))

        assert events[-1].kind == "done"
        assert len(post.calls) == 1


# ───────────────────────── GWT-4 缺配置即 fail-closed ─────────────────────────


class TestGwt4FailClosed:
    def test_missing_env_keeps_llm_unavailable(self, store: Store):
        rt = _build(store, llm_config=None)
        rt.endpoints.register("local-main", kind="local",
                              provider="local-llama", capability=LOCAL_CAP)

        events = list(rt.llm.invoke("local-main", "你好", initiator="t", purpose="p"))

        assert [e.kind for e in events] == ["error"]
        assert events[0].error_envelope.status == "unavailable"

    @pytest.mark.parametrize("env", [
        {},
        {"LLM_API_KEY": KEY},
        {"LLM_API_KEY": KEY, "LLM_BASE_URL": BASE_URL},
        {"LLM_API_KEY": KEY, "LLM_BASE_URL": "   ", "LLM_MODEL": MODEL},
    ])
    def test_partial_env_produces_no_bootstrap(self, env):
        assert LlmBootstrap.from_env(env) is None

    def test_open_runtime_without_env_seeds_nothing(self, tmp_path: Path):
        rt = open_runtime(tmp_path / "root", PASS, create=True,
                          market_query=_FAKE_MARKET, llm_env={})

        assert rt.endpoints.list_endpoints() == ()
        assert rt.vault.list_credentials() == ()
        assert rt.provider_hosts.list() == ()


# ───────────────────────── 取值面：.env 解析与优先级 ─────────────────────────


class TestEnvResolution:
    def test_load_dotenv_handles_comments_and_quotes(self, tmp_path: Path):
        path = tmp_path / ".env"
        path.write_text(
            "# 注释行\n"
            'LLM_API_KEY="sk-quoted"\n'
            "LLM_BASE_URL=            # 行尾注释\n"
            "LLM_MODEL=x-inline # 尾注释\n"
            "NOT_A_KEY=ignored-by-callers\n",
            encoding="utf-8",
        )
        assert load_dotenv(path) == {
            "LLM_API_KEY": "sk-quoted",
            "LLM_BASE_URL": "",
            "LLM_MODEL": "x-inline",
            "NOT_A_KEY": "ignored-by-callers",
        }

    def test_load_dotenv_missing_file_is_empty(self, tmp_path: Path):
        assert load_dotenv(tmp_path / "nope.env") == {}

    def test_environment_variable_wins_over_dotenv(self, tmp_path: Path, monkeypatch):
        path = tmp_path / ".env"
        path.write_text("LLM_API_KEY=from-file\nLLM_BASE_URL=https://f.example.com/v1\n"
                        "LLM_MODEL=m\n", encoding="utf-8")
        monkeypatch.setenv("LLM_API_KEY", "from-env")

        env = llm_env_from_ambient(path)

        assert env["LLM_API_KEY"] == "from-env"
        assert env["LLM_BASE_URL"] == "https://f.example.com/v1"

    def test_all_from_environment_skips_dotenv(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", KEY)
        monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
        monkeypatch.setenv("LLM_MODEL", MODEL)

        assert llm_env_from_ambient(tmp_path / "不存在的.env") == ENV

    def test_no_dotenv_and_no_env_is_empty(self, tmp_path: Path):
        assert llm_env_from_ambient(tmp_path / "nope.env") == {}
