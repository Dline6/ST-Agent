"""`POST /api/chat` 的失败显式化（[00 §6]；[T-UI-003]）。

两条性质只在**真 HTTP** 下可见：① 非法请求体不把连接丢给客户端——回 200 + **合法信封**，
而不是让客户端吃 ``RemoteDisconnected``；② 门面的**内部错误**既回信封（``failed`` + ``log_ref``）
又落**可查**日志，不静默。桩能满足 ``turn`` 签名，却表达不了「服务端到底吞没吞异常」
（[D-059] 的教训），故这里走真 socket；真 ``DialogFacade`` 的端到端回归见
``tests/integration/test_ui_chat_boundary.py``。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve


class StubChat:
    """对话门面桩：``turn`` 按注入行为回载荷或抛异常。

    真实 ``DialogFacade`` 对空白 ``text`` 在构造 ``ChatMessage`` 时抛 pydantic 包装的
    ``ValidationError``（``ValueError`` 族）——本桩以同族 ``ValueError`` 模拟「门面拒绝」。
    """

    def __init__(self, *, reject_blank: bool = False, boom: Exception | None = None) -> None:
        self._reject_blank = reject_blank
        self._boom = boom
        self.seen: list[dict[str, Any]] = []

    def turn(self, body: dict[str, Any]) -> dict[str, Any]:
        self.seen.append(dict(body))
        if self._boom is not None:
            raise self._boom
        text = body.get("text")
        if self._reject_blank and not str(text or "").strip():
            raise ValueError("消息文本不得为空白（空消息无意义）")
        return {
            "reply": ResultEnvelope.ok({"echo": text}),
            "session_id": "sess_stub",
            "needs_confirmation": False,
            "description": None,
        }


@pytest.fixture
def stub_chat() -> StubChat:
    """拒绝空白 ``text`` 的桩（模拟真实门面的输入契约）。"""
    return StubChat(reject_blank=True)


def _assert_envelope(payload: dict[str, Any]) -> None:
    """断言 body 是**合法信封载荷**（字段形态同 `envelope_payload`）。"""
    assert payload["status"] in {
        "ok", "empty", "unavailable", "failed", "dependency_failed", "validation_failed",
    }
    assert "render" in payload and payload["render"]["presentation"], "六态渲染语义应随信封下发"
    if payload["status"] != "ok":
        assert payload["reason"], "非 ok 必须带中性原因（01 §5）"


def _json_headers(running) -> dict[str, str]:  # noqa: ANN001（本地助手）
    """带令牌与 JSON ``Content-Type`` 的请求头。"""
    return {TOKEN_HEADER: running.token, "Content-Type": "application/json"}


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("malformed-json", b"{not json"),
        ("empty-body", b""),
        ("missing-text", {"action": "post"}),
        ("wrong-key", {"message": "你好"}),
    ],
)
def test_malformed_bodies_return_envelope_not_disconnect(
    stub_chat, http_post, label, body
) -> None:  # noqa: ANN001（参数化）
    """畸形 / 空 / 缺 text / 错键四种体都得信封（不是断连）——GWT-1 / GWT-2 / GWT-3 的边界面。"""
    with serve(chat=stub_chat) as running:
        response = http_post(running, "/api/chat", body, _json_headers(running))
        assert response.status == 200, label
        payload = response.json()
        _assert_envelope(payload)
        assert payload["status"] == "validation_failed", label
    # 请求确已抵达门面（不是被传输层短路）
    assert stub_chat.seen, label


def test_internal_error_returns_failed_envelope_and_is_logged(
    http_post, caplog
) -> None:  # noqa: ANN001（caplog）
    """门面抛内部错误 → 信封 `failed` + 非空 `log_ref`，且日志含该异常的 traceback（不吞）。"""
    boom = StubChat(boom=RuntimeError("boom-内部错误"))
    with caplog.at_level(logging.DEBUG, logger="st_agent.ui.app"):
        with serve(chat=boom) as running:
            response = http_post(
                running, "/api/chat", {"action": "post", "text": "查退市风险"},
                _json_headers(running),
            )
    assert response.status == 200
    payload = response.json()
    _assert_envelope(payload)
    assert payload["status"] == "failed"
    assert payload["log_ref"], "failed 必带可查日志入口（01 §5）"
    # 日志可查：记录里同时出现 log_ref 与异常文本，且带 traceback
    hit = [r for r in caplog.records if payload["log_ref"] in r.getMessage()]
    assert hit, "服务端日志须含该 log_ref（可 grep 定位）"
    assert any(r.exc_info for r in hit), "须记录 traceback，不得静默"
    assert "boom-内部错误" in hit[-1].getMessage()


def test_valid_request_is_unaffected(stub_chat, http_post) -> None:
    """捕获不得改变正常路径语义（GWT-5）：合法请求仍 `ok`。"""
    with serve(chat=stub_chat) as running:
        response = http_post(
            running, "/api/chat", {"action": "post", "text": "查退市风险"},
            _json_headers(running),
        )
    assert response.status == 200
    payload = response.json()
    _assert_envelope(payload)
    assert payload["status"] == "ok"
    assert stub_chat.seen == [{"action": "post", "text": "查退市风险"}]


def test_chat_endpoint_requires_token(stub_chat, http_post) -> None:
    """回归：`/api/*` 仍必带令牌——失败显式化不放松鉴权闸门。"""
    with serve(chat=stub_chat) as running:
        assert http_post(running, "/api/chat", {"action": "post", "text": "x"}).status == 401
        assert not stub_chat.seen, "未过鉴权的请求不得抵达门面"
