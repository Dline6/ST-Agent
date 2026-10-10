"""Chat 主界面在**真 M5 组合根**上的取数面（[`T-UI-012.1`] / [`.2`]）。

与 `tests/ui/test_ui_chat_page.py`（替身钉形状）分工：本组用真 [`build_m5_runtime`](../../src/st_agent/app.py)
造出的门面走一遍真链路——

1. `GET /api/chat/context` 出**真** `context_card` 描述（六段、`greeting` 来自 L3）；
2. `GET /api/chat/commands` 出**真** `CommandRegistry` 的官方指令集（≥5 条）；
3. `POST /api/chat` 的返回携 `clarification` 键（澄清轮的线面），且 `dispatch` 返程带描述。

两枚端点均为**只读**；写面仍只 `/api/chat`。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

_FACES = ("chat", "commands")


def _get(running, path: str) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request("GET", path, headers={TOKEN_HEADER: running.token})
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


def _post(running, body: dict) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request(
            "POST", "/api/chat", body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={TOKEN_HEADER: running.token, "Content-Type": "application/json"},
        )
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


@pytest.fixture()
def m5(tmp_path):
    return seeded_m5(tmp_path / ROOT_NAME)


def test_context_card_on_the_real_root(m5) -> None:
    """真组合根上 `/api/chat/context` 出 `context_card` 描述，六段齐、带 `greeting`。"""
    runtime = m5.m5
    with serve(dev=False, chat=runtime.chat) as running:
        payload = _get(running, "/api/chat/context")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "context_card"
    assert len(description["slots"]["sections"]) == 6
    assert description["slots"]["greeting"]


def test_commands_on_the_real_root(m5) -> None:
    """真组合根上 `/api/chat/commands` 出官方指令集（≥5 条、来源分组为 official）。"""
    runtime = m5.m5
    with serve(dev=False, commands=runtime.commands) as running:
        payload = _get(running, "/api/chat/commands")
    assert payload["status"] == "ok"
    names = {item["name"] for item in payload["data"]}
    assert {"盯盘", "回测", "压测", "今日看板", "反思"} <= names
    assert all(item["source"] == "official" for item in payload["data"])


def test_chat_reply_carries_the_clarification_key_and_a_description(tmp_path) -> None:
    """`POST /api/chat` 的返回恒携 `clarification` 键；`dispatch` 返程带生成式 UI 描述。

    用 M1 组合根（规则更全）走一段「输入 → 确认 → 派发」反向流。
    """
    from rig_m1 import seeded_m1_plaintext

    runtime = seeded_m1_plaintext(tmp_path / ROOT_NAME).m1
    with serve(dev=False, chat=runtime.chat) as running:
        posted = _post(running, {"action": "post", "text": "查 sh.600000 退市风险"})
        assert posted["status"] == "ok"
        assert posted["needs_confirmation"] is True
        assert "clarification" in posted  # 澄清轮的线面（可为 null）

        dispatched = _post(
            running, {"action": "dispatch", "session_id": posted["session_id"]}
        )
    assert dispatched["status"] == "ok"
    assert dispatched["description"] is not None
    assert dispatched["description"]["data"]["component_type"]
