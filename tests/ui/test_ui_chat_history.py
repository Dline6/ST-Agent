"""会话历史读面与助手轮落盘（[`T-L3-008`]；05 §1）。

本组钉四件事（用替身，快）：

1. **fail-closed**：未注入对话门面 ⇒ 两条读端点 `unavailable` + 点名（不伪造空列表）。
2. **守卫不变**：两条读面都要令牌、都不是写面。
3. **转发口径**：门面的 `list_sessions` / `read_session` 原样出，查询串 `id` 逐字传入。
4. **助手轮落盘**：`POST /api/chat` 成功后调用 `record_reply`，带中性摘要与最终出站载荷。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_PAGES_JS = _UI / "web" / "js" / "pages.js"
_MAIN_JS = _UI / "web" / "js" / "main.js"
_CHAT_JS = _UI / "web" / "js" / "pages" / "chat.js"
_ROUTER_JS = _UI / "web" / "js" / "router.js"

HISTORY_PATHS = ("/api/chat/sessions", "/api/chat/session?id=sess_1")


@pytest.mark.parametrize("path", HISTORY_PATHS)
def test_history_read_endpoints_fail_closed_without_the_face(
    ui_server, http_get, auth, path: str,
) -> None:
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert payload["reason"] and "未接入" in payload["reason"]
    assert payload["render"]["presentation"] == "delayed"


@pytest.mark.parametrize("path", HISTORY_PATHS)
def test_history_read_endpoints_need_the_token(ui_server, http_get, path: str) -> None:
    assert http_get(ui_server, path).status == 401


def test_history_read_endpoints_are_not_write_paths(ui_server, http_post, auth) -> None:
    for path in ("/api/chat/sessions", "/api/chat/session"):
        assert http_post(ui_server, path, {}, headers=auth(ui_server)).status == 404


def test_sessions_list_is_forwarded(http_get, auth) -> None:
    data = {
        "sessions": [
            {
                "session_id": "sess_1",
                "title": "会话标题",
                "created_at": "2026-10-27T10:00:00+08:00",
                "updated_at": "2026-10-27T10:05:00+08:00",
                "message_count": 2,
                "preview": "末条摘要",
            }
        ],
        "skipped": [],
    }

    from st_agent.ui.server import serve

    with serve(dev=False, chat=SimpleNamespace(list_sessions=lambda: ResultEnvelope.ok(data))) as running:
        payload = http_get(running, "/api/chat/sessions", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["sessions"][0]["session_id"] == "sess_1"


def test_session_read_passes_the_query_id(http_get, auth) -> None:
    seen: list[str] = []

    def _read(session_id: str) -> ResultEnvelope:
        seen.append(session_id)
        return ResultEnvelope.ok({"session_id": session_id, "messages": []})

    from st_agent.ui.server import serve

    with serve(dev=False, chat=SimpleNamespace(read_session=_read)) as running:
        payload = http_get(
            running, "/api/chat/session?id=sess_42", headers=auth(running),
        ).json()
    assert seen == ["sess_42"]
    assert payload["status"] == "ok"
    assert payload["data"]["session_id"] == "sess_42"


def test_chat_reply_is_recorded_with_a_neutral_summary(http_post, auth) -> None:
    recorded: dict = {}

    def _turn(body):  # noqa: ANN001, ANN202（鸭子面）
        return {
            "reply": ResultEnvelope.ok({"component_type": "report_card"}),
            "session_id": "sess_1",
            "needs_confirmation": False,
            "description": None,
            "clarification": None,
            "traces": [],
        }

    def _record(session_id, *, text, payload=None):  # noqa: ANN001, ANN202
        recorded.update(session_id=session_id, text=text, payload=payload)
        return True

    from st_agent.ui.security import TOKEN_HEADER
    from st_agent.ui.server import serve

    with serve(dev=False, chat=SimpleNamespace(turn=_turn, record_reply=_record)) as running:
        payload = http_post(
            running, "/api/chat", {"action": "post", "text": "查退市风险"},
            headers={TOKEN_HEADER: running.token},
        ).json()
    assert payload["status"] == "ok"
    assert recorded["session_id"] == "sess_1"
    assert recorded["text"] == "已返回结果"
    assert recorded["payload"]["status"] == "ok"


# ── 前端接线（静态断言；不跑 JS） ───────────────────────────────────────────────

def test_chat_history_page_is_registered() -> None:
    text = _PAGES_JS.read_text(encoding="utf-8")
    assert "path: '/chat'" in text
    assert "renderChatHistoryPage" in text


def test_chat_rail_survives_client_navigation() -> None:
    """外壳改用客户端路由；应用内刷新不再重挂右栏。"""
    main = _MAIN_JS.read_text(encoding="utf-8")
    assert "from './router.js'" in main
    assert "startRouter(" in main
    assert _ROUTER_JS.is_file()
    # `refreshCurrentPage` 只重绘主区：函数体内不得再出现 `mountChat()`。
    start = main.index("async function refreshCurrentPage")
    end = main.index("function shortcutTable", start)
    assert "mountChat()" not in main[start:end]


def test_chat_view_has_waiting_timeout_and_retry() -> None:
    chat = _CHAT_JS.read_text(encoding="utf-8")
    assert "正在处理" in chat
    assert "chat__pending" in chat
    assert "chat__retry" in chat
    assert "timeoutMs" in chat
    assert "if (!trimmed || state.sending) return;" in chat, "等待期间必须拒绝重复提交"
    assert "st.chat.active" in chat, "活动会话要能在冷启动时回填"
