"""T-AGT-002 测试：端点能力启动探测（02 §4 能力诚实性，判据取真回调用）。

GWT 对照（任务文件 6 条）：

- GWT-1 探针**真回调用** → 能力档置 ``true``
- GWT-2 **静默忽略 ``tools``**、正常回文本 → 判**不支持**（假阳性是本条的存在理由）
- GWT-3 端点记录已存在（播种整组跳过）时，连续两次启动**第二次仍写入**
- GWT-4 不可达 / 无凭据 → 启动**照常完成**，该端点记未知、其余不受影响
- GWT-5 探测是**出网调用** → 落一条 ``NetworkEvent``（载荷不入审计）
- GWT-6 探测未跑到 → **未知 + fail-closed**，不回落 ``true``

**全离线**：真实发送器的 HTTP 发送口经 ``llm_post`` 注入替身，由脚本决定每次回应
「真工具调用」还是「纯文本」。**不打桩探测本身**——探针真的把 ``tools`` 发出去并
解析回包（这正是 GWT-2 要钉的反例）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import (
    LLM_ENDPOINT_ID,
    PROBE_TOOL,
    LlmBootstrap,
    build_l1_runtime,
)

PASS = "correct horse battery staple"
BASE_URL = "https://api.example.com/v1"
MODEL = "test-model"
KEY = "sk-proj-abcdef1234567890-KEYSECRET"
ENV = {"LLM_API_KEY": KEY, "LLM_BASE_URL": BASE_URL, "LLM_MODEL": MODEL}
HOST = "api.example.com"


class _FakeMarketQuery:
    def query(self, sql: str, params: tuple = ()):
        return ResultEnvelope.empty("本用例不取数")


class ScriptedPost:
    """注入式 HTTP 发送口替身：按脚本决定每次回应「真工具调用」还是「纯文本」。

    ``responses`` 逐次消费，用尽后**重复最后一项**——GWT-3 的「连续两次启动」用得上
    （第一次回文本、第二次回调用）。记录每次请求，供断言「``tools`` 真的发出去了」。
    """

    def __init__(self, *responses: bool) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses) or [False]

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        flag = self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)
        return iter(_tool_call_lines() if flag else _text_lines())


def _text_lines() -> list[str]:
    """**静默忽略 ``tools``** 的端点：正常回一段文本，**不含**任何工具调用。"""
    payload = {"choices": [{"delta": {"content": "好的"}}]}
    return [f"data: {json.dumps(payload, ensure_ascii=False)}", "data: [DONE]"]


def _tool_call_lines() -> list[str]:
    """支持工具调用的端点：回一条**拼完**的工具调用。"""
    fragment = {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": "call_probe_1",
        "function": {"name": PROBE_TOOL.name, "arguments": "{}"},
    }]}}]}
    return [f"data: {json.dumps(fragment, ensure_ascii=False)}", "data: [DONE]"]


def _broken_endpoint(store_or_rt) -> None:
    """登记一个**提供方未登记**的端点：探针发不出去（transport 显式拒绝）。"""
    store_or_rt.endpoints.register(
        "local-broken", kind="local", provider="local-llama",
        capability={"max_context_tokens": 4_096},
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离宿主环境：本机若设了 ``LLM_*``，不得影响用例判定。"""
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)


def _build(store: Store, post: ScriptedPost, **kwargs):
    kwargs.setdefault("llm_config", LlmBootstrap.from_env(ENV))
    kwargs.setdefault("audit", True)
    return build_l1_runtime(store, market_query=_FakeMarketQuery(), llm_post=post,
                            **kwargs)


def _cap(rt, endpoint_id: str = LLM_ENDPOINT_ID) -> bool:
    return rt.endpoints.get(endpoint_id).capability.supports_function_calling


# ───────────────────────── GWT-1 探针真回调用才判支持 ─────────────────────────


class TestGwt1RealCallIsSupport:
    def test_endpoint_returning_a_tool_call_is_marked_supported(self, store: Store):
        post = ScriptedPost(True)

        rt = _build(store, post)

        assert _cap(rt) is True
        # 探针**真的把 tools 发出去**了（判据成立的前提）
        body = json.loads(post.calls[0]["body"])
        assert [t["function"]["name"] for t in body["tools"]] == [PROBE_TOOL.name]


# ───────────────────────── GWT-2 静默忽略不得判为支持 ─────────────────────────


class TestGwt2SilentIgnoreIsNotSupport:
    def test_text_only_endpoint_is_marked_unsupported(self, store: Store):
        post = ScriptedPost(False)

        rt = _build(store, post)

        assert _cap(rt) is False
        # 发了 tools，端点只是**静默忽略**（不报错）——这正是弱判据的假阳性来源
        body = json.loads(post.calls[0]["body"])
        assert "tools" in body


# ───────────────────────── GWT-3 可重入、不被播种吞掉 ─────────────────────────


class TestGwt3ReentrantPastSeeding:
    def test_second_startup_still_writes_when_seeding_skips(self, store: Store):
        # 第一次启动：端点不存在 → 播种 + 探测（端点回文本 → 不支持）
        first = _build(store, ScriptedPost(False))
        assert _cap(first) is False
        created_at = first.endpoints.get(LLM_ENDPOINT_ID).created_at

        # 第二次启动：端点已存在 → 播种整组跳过；但探测**仍跑**并写回
        second = _build(store, ScriptedPost(True))

        assert second.endpoints.get(LLM_ENDPOINT_ID).created_at == created_at  # 播种确实跳过
        assert _cap(second) is True            # 第二次仍写入探测结果（不静默不写）


# ───────────────────────── GWT-4 失败不阻塞启动 ─────────────────────────


class TestGwt4FailureDoesNotBlockStartup:
    def test_unreachable_endpoint_is_unknown_and_others_unaffected(self, store: Store):
        seeded = _build(store, ScriptedPost(True))
        _broken_endpoint(seeded)
        post = ScriptedPost(True)

        again = _build(store, post)            # 启动照常完成（不抛）

        assert _cap(again, LLM_ENDPOINT_ID) is True     # 正常端点照常探到
        assert _cap(again, "local-broken") is False     # 失败 → 未知（非 true）
        assert len(post.calls) == 1                      # 坏端点未触到 HTTP 层

    def test_missing_credential_is_unknown_not_blocking(self, store: Store):
        seeded = _build(store, ScriptedPost(True))
        seeded.endpoints.register(
            "cloud-nokey", kind="cloud", provider="openai-compatible",
            capability={"max_context_tokens": 4_096}, credential_id="ghost-key",
        )
        post = ScriptedPost(True)

        again = _build(store, post)            # 凭据缺失也不阻塞启动

        assert _cap(again, "cloud-nokey") is False
        assert len(post.calls) == 1             # 缺凭据在发包之前即止


# ───────────────────────── GWT-5 探测是出网调用（留审计） ─────────────────────


class TestGwt5ProbeIsAudited:
    def test_probe_lands_a_network_event_without_payload(self, store: Store):
        rt = _build(store, ScriptedPost(True), audit=True)

        events = rt.gateway.query(kind="llm_call")

        assert len(events) == 1
        (event,) = events
        assert event.status == "ok"
        assert event.initiator == f"llm-endpoint:{LLM_ENDPOINT_ID}"
        assert event.target_host == HOST
        assert event.purpose                  # 人可读目的说明
        # 载荷（prompt / key）**不入审计**：只记字节数
        blob = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, default=str)
        assert PROBE_TOOL.name not in blob
        assert KEY not in blob


# ───────────────────────── GWT-6 不臆测：未跑到即未知 + fail-closed ───────────


class TestGwt6NoGuessing:
    def test_failed_probe_leaves_endpoint_fail_closed(self, store: Store):
        seeded = _build(store, ScriptedPost(True))
        _broken_endpoint(seeded)

        again = _build(store, ScriptedPost(True))

        assert _cap(again, "local-broken") is False
        ok, reason = again.endpoints.negotiate(
            "local-broken", {"needs_function_calling": True}
        )
        assert ok is False and reason          # 未知即拒，**不回落 true**

    def test_explicit_transport_is_not_probed(self, store: Store):
        """A5：显式注入 ``transport`` 的路径不探测（对手方不是真实端点面）。"""
        _build(store, ScriptedPost(True))      # 先播种出端点记录
        seen: list[str] = []

        def transport(endpoint, prompt, key, timeout_ms, tools=None):
            seen.append(endpoint.endpoint_id)
            yield "unused"

        build_l1_runtime(store, market_query=_FakeMarketQuery(), transport=transport)

        assert seen == []                      # 一条探测都没发
