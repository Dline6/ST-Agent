"""Chat 主界面（首页 `/`）的取数面与前端接线（[`T-UI-012.1`]）。

本组钉四件事（用替身，快）：

1. **两枚新读端点**：`/api/chat/context` · `/api/chat/commands` 未注入端口 ⇒ `unavailable`
   + 点名（不 500、不装空表）；注入即 `ok`；两者**都要令牌**、都**不是写面**。
2. **`context_card` 取数口缺席**与「面在但无数据」分得开——前者 `unavailable` + 点名，
   不是 `failed`。
3. **`clarification` 透传**：`POST /api/chat` 把门面返回的澄清轮（问项 / 方向候选）原样带出。
4. **前端接线**：`pages.js` 的 `/` 登记项挂上 `render`（首页不再是可用性探针占位）。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_PAGES_JS = _UI / "web" / "js" / "pages.js"
_CHAT_JS = _UI / "web" / "js" / "pages" / "chat.js"


def _entry_block(path: str) -> str:
    """从 `pages.js` 取某条登记项的源码块（扁平对象字面量；同既有守卫的静态解析法）。"""
    text = _PAGES_JS.read_text(encoding="utf-8")
    for block in re.findall(r"\{([^{}]*)\}", text):
        if re.search(rf"path:\s*'{re.escape(path)}'", block):
            return block
    raise AssertionError(f"未在 pages.js 找到路径 {path!r} 的登记项")


# ── 前端接线 ───────────────────────────────────────────────────────────────────
def test_root_page_has_a_render() -> None:
    """首页 `/` 挂上 `render`——不再是可用性探针占位（GWT-1）。"""
    assert "render:" in _entry_block("/"), "首页登记项缺少 render（仍是占位态）"


def test_chat_page_module_is_imported_and_exported() -> None:
    """宿主模块被 `pages.js` 静态 import，且导出一枚 `renderChatPage`。"""
    text = _PAGES_JS.read_text(encoding="utf-8")
    assert "from './pages/chat.js'" in text
    module = _CHAT_JS.read_text(encoding="utf-8")
    assert "export async function renderChatPage" in module


# ── 两枚新读端点：fail-closed / 令牌 / 写面白名单 ────────────────────────────────
@pytest.mark.parametrize("path", ["/api/chat/context", "/api/chat/commands"])
def test_new_read_endpoints_fail_closed_without_ports(ui_server, http_get, auth, path: str) -> None:
    """未注入端口 ⇒ `unavailable` + 点名（不 500、不装空表）。"""
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["reason"] and "未接入" in payload["reason"]
    assert payload["render"]["presentation"] == "delayed"


@pytest.mark.parametrize("path", ["/api/chat/context", "/api/chat/commands"])
def test_new_read_endpoints_need_the_token(ui_server, http_get, path: str) -> None:
    """数据面恒要令牌——新增端点不得放宽守卫。"""
    assert http_get(ui_server, path).status == 401


@pytest.mark.parametrize("path", ["/api/chat/context", "/api/chat/commands"])
def test_new_read_endpoints_are_not_write_paths(ui_server, http_post, auth, path: str) -> None:
    """写面白名单**逐条不变**：把新读端点当 POST 用 ⇒ 404。"""
    assert http_post(ui_server, path, {}, headers=auth(ui_server)).status == 404


# ── 上下文卡片取数口 ───────────────────────────────────────────────────────────
def test_context_card_is_unavailable_when_the_chat_port_lacks_it(http_get, auth) -> None:
    """`chat` 注入但**未持 `context_card`** ⇒ `unavailable` + 点名（不是 `failed`）。"""
    from st_agent.ui.server import serve

    with serve(dev=False, chat=SimpleNamespace(turn=lambda body: {})) as running:
        payload = http_get(running, "/api/chat/context", headers=auth(running)).json()
    assert payload["status"] == "unavailable"
    assert "未接入上下文卡片面" in payload["reason"]


def test_context_card_passes_through_the_facade_envelope(http_get, auth) -> None:
    """`chat` 持 `context_card` ⇒ 原样出它给的 `context_card` 描述信封。"""
    from st_agent.contracts.result_envelope import ResultEnvelope
    from st_agent.ui.server import serve

    card = ResultEnvelope.ok(
        {"component_type": "context_card", "slots": {"greeting": "开始跟踪关注标的。"}}
    )
    with serve(dev=False, chat=SimpleNamespace(context_card=lambda **_: card)) as running:
        payload = http_get(running, "/api/chat/context", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "context_card"


# ── 快捷指令清单 ───────────────────────────────────────────────────────────────
def test_commands_list_is_forwarded_with_shape(http_get, auth) -> None:
    """`commands` 端口注入 ⇒ `ok` + 逐条 {name, description, intent, source}。"""
    registry = SimpleNamespace(
        list=lambda: (
            SimpleNamespace(name="盯盘", description="持续跟踪", intent="configure", source="official"),
        )
    )
    from st_agent.ui.server import serve

    with serve(dev=False, commands=registry) as running:
        payload = http_get(running, "/api/chat/commands", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"] == [
        {"name": "盯盘", "description": "持续跟踪", "intent": "configure", "source": "official"}
    ]


def test_empty_command_registry_is_empty_not_ok(http_get, auth) -> None:
    """空注册表 ⇒ `empty` + 原因（不伪装成 `ok` 的空列表，[01 §5]）。"""
    from st_agent.ui.server import serve

    with serve(dev=False, commands=SimpleNamespace(list=lambda: ())) as running:
        payload = http_get(running, "/api/chat/commands", headers=auth(running)).json()
    assert payload["status"] == "empty"
    assert payload["reason"]


# ── `clarification` 透传（GWT-4 的线面） ────────────────────────────────────────
def test_chat_forwards_the_clarification_round(http_post, auth) -> None:
    """`POST /api/chat` 把门面返回的澄清轮原样带出（问句 / 方向候选 / deferred）。"""
    from st_agent.contracts.result_envelope import ResultEnvelope

    round_ = {
        "questions": [
            {"name": "window_days", "prompt": "参数 window_days：跟踪窗口（天）", "default": 30,
             "choices": [], "skippable": True},
        ],
        "directions": [],
        "deferred": [],
    }
    reply = ResultEnvelope.ok(
        [{"text": "意图 configure", "value": "盯盘", "source": "intent"}]
    )

    def _turn(body):  # noqa: ANN001, ANN202（鸭子面）
        return {
            "reply": reply,
            "session_id": "sess_1",
            "needs_confirmation": True,
            "description": None,
            "clarification": round_,
        }

    from st_agent.ui.security import TOKEN_HEADER
    from st_agent.ui.server import serve

    with serve(dev=False, chat=SimpleNamespace(turn=_turn)) as running:
        payload = http_post(
            running, "/api/chat", {"action": "post", "text": "盯盘"},
            headers={TOKEN_HEADER: running.token},
        ).json()
    assert payload["status"] == "ok"
    assert payload["needs_confirmation"] is True
    assert payload["clarification"] == round_
