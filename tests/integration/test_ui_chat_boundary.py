"""T-UI-003 · 对话端点失败显式化：**真实门面**下的端到端回归（[00 §6]）。

报告场景（2026-10-05）：畸形 / 空 / 缺 `text` 的 `POST /api/chat` 经**真实** `DialogFacade`
时把 pydantic `ValidationError`（「消息文本不得为空白」）从 `do_POST` 逸出 → 线程打印 traceback、
连接被关闭、客户端恒 `RemoteDisconnected`。本文件在真 HTTP + M1 组合根**真门面**上钉住：
三类非法输入都得**合法信封**而非断连。

与 `tests/ui/test_ui_chat_errors.py` 分工——那边是 `api_chat` 边界映射的**桩级**用例（快）；
这边要证明的是**真门面确实抛「被捕获的类型」**：桩能满足 ``turn`` 签名、模拟出 ``ValueError``，
却表达不了「``DialogFacade`` 真的这么抛」（[D-059] 的教训）。
"""

from __future__ import annotations

import http.client
import json
import logging
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m1 import M1Rig, seeded_m1_plaintext

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve


@pytest.fixture()
def rig(tmp_path: Path) -> M1Rig:
    return seeded_m1_plaintext(tmp_path / ROOT_NAME)


def _post(running, body, *, token: str | None = None):  # noqa: ANN001, ANN201（本地助手）
    """发一个 POST；``body`` 为 ``bytes`` 时原样发送（畸形体用），否则 JSON 序列化。

    **不吞** ``RemoteDisconnected``——「服务端把连接丢了」正是本文件要暴露的失败。
    """
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[TOKEN_HEADER] = token
    payload = (
        body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
    )
    try:
        conn.request("POST", "/api/chat", body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("malformed-json", b"{not json"),
        ("empty-body", b""),
        ("missing-text", {"action": "post"}),
        ("wrong-key", {"message": "你好"}),
    ],
)
def test_malformed_bodies_get_envelopes_not_disconnects(
    rig: M1Rig, label, body  # noqa: ANN001（参数化）
) -> None:
    """畸形 / 空 / 缺 text / 错键 → 合法信封（`validation_failed`），不是断连（GWT-1~3）。"""
    with serve(dev=False, chat=rig.m1.chat) as running:
        status, payload = _post(running, body, token=running.token)
    assert status == 200, label
    assert payload["status"] == "validation_failed", label
    assert payload["reason"], label
    assert payload["render"]["presentation"], label


def test_unknown_session_id_is_enveloped(rig: M1Rig) -> None:
    """寻址失败（`SessionNotFoundError`，`KeyError` 族）同样转信封，不断连。"""
    with serve(dev=False, chat=rig.m1.chat) as running:
        status, payload = _post(
            running,
            {"action": "post", "text": "查退市风险", "session_id": "sess_not_exist_0001"},
            token=running.token,
        )
    assert status == 200
    assert payload["status"] == "validation_failed"
    assert payload["reason"]


def test_internal_error_is_enveloped_with_log_ref(rig: M1Rig, caplog) -> None:  # noqa: ANN001
    """门面内部错误 → `failed` 信封（带 `log_ref`）且日志含 traceback（GWT-4）。"""

    class _Boom:
        def turn(self, body):  # noqa: ANN001, ANN201（鸭子面）
            raise RuntimeError("boom-内部错误")

    with caplog.at_level(logging.DEBUG, logger="st_agent.ui.app"):
        with serve(dev=False, chat=_Boom()) as running:
            status, payload = _post(
                running, {"action": "post", "text": "查退市风险"}, token=running.token,
            )
    assert status == 200
    assert payload["status"] == "failed"
    assert payload["log_ref"]
    assert any(payload["log_ref"] in r.getMessage() and r.exc_info for r in caplog.records), (
        "服务端须记录含 log_ref 的 traceback"
    )


def test_valid_request_unchanged(rig: M1Rig) -> None:
    """GWT-5 回归：真门面的合法请求仍 `ok` + 确认卡（捕获不改变正常路径）。"""
    with serve(dev=False, chat=rig.m1.chat) as running:
        status, payload = _post(
            running, {"action": "post", "text": "查 sh.600000 退市风险"}, token=running.token,
        )
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["needs_confirmation"] is True
    assert payload["session_id"]
