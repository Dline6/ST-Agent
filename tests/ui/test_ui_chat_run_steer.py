"""T-AGT-027.2：插话端点（真 HTTP 面）。

验四件事：① 活动运行 → `ok` + `delivered=true`，文本与会话标识逐字转发；② 运行已结束
→ 仍 `ok` 且 `delivered=false`（不丢、不报错）；③ 空文本 / 缺端口 → 显式失败；④ 路径
形状与令牌守卫不放松。
"""

from __future__ import annotations

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.server import serve

RUN = "agr_000000000000000000bb"
SESSION = "sess_00000000000000000001"


class _SteerFace:
    """插话面桩：只实现 `run_available` 与 `steer_run`（其余端点不参与本文件）。"""

    def __init__(self, *, delivered: bool = True, available: bool = True) -> None:
        self.run_available = available
        self._delivered = delivered
        self.calls: list[tuple[str, str, str | None]] = []

    def steer_run(self, agent_run_id: str, text: str, *, session_id=None) -> ResultEnvelope:
        self.calls.append((agent_run_id, text, session_id))
        return ResultEnvelope.ok({
            "agent_run_id": agent_run_id,
            "session_id": session_id or SESSION,
            "delivered": self._delivered,
            "note": (
                "已送达运行中任务，将在下一个步骤边界生效"
                if self._delivered else "运行已结束，插话已作为普通消息保留"
            ),
        })


def test_steer_active_run_acknowledges_delivery(http_post, auth):
    face = _SteerFace(delivered=True)
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/steer",
            {"text": "补充：只看新能源", "session_id": SESSION},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"]["delivered"] is True
    assert payload["data"]["agent_run_id"] == RUN
    assert face.calls == [(RUN, "补充：只看新能源", SESSION)]


def test_steer_after_run_end_is_ok_but_not_delivered(http_post, auth):
    face = _SteerFace(delivered=False)
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/steer",
            {"text": "补充：再补一句", "session_id": SESSION},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "ok", "运行已结束不是错误：消息已作为普通消息保留"
    assert payload["data"]["delivered"] is False
    assert "普通消息" in payload["data"]["note"]


def test_steer_requires_non_blank_text(http_post, auth):
    face = _SteerFace()
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/steer",
            {"text": "   ", "session_id": SESSION},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    payload = response.json()
    assert payload["status"] == "validation_failed"
    assert face.calls == [], "空文本不应进入写面"


def test_steer_without_run_port_is_unavailable(http_post, auth):
    face = _SteerFace(available=False)
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/steer",
            {"text": "补一句"},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "插话不可用" in payload["reason"]


def test_steer_requires_token(http_post):
    with serve(chat=_SteerFace()) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/steer",
            {"text": "补一句"},
            headers={"Content-Type": "application/json"},
        )
    assert response.status == 401


def test_steer_path_shape_is_enumerated(http_post, auth):
    with serve(chat=_SteerFace()) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/steer/extra",
            {"text": "补一句"},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    assert response.status == 404
