"""T-AGT-023.1：运行事件 SSE 端点与后台派发（真 HTTP 面）。

三条性质只在真 socket 下可见：① SSE 帧按序号先补发再收束（`stream_end`）；②
`Last-Event-ID` 续传；③ 未知 id 在写出响应头前就是 404，**不挂空连接**。另验
「仅 SSE 路径接受查询串令牌」与后台派发「立即返回 `agent_run_id`、最终轮仍落会话」。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.runtime import RunEvent
from st_agent.ui.server import serve
from st_agent.ui.security import TOKEN_HEADER

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
RUN = "agr_000000000000000000aa"


def _event(seq: int, kind: str) -> RunEvent:
    return RunEvent(seq=seq, event=kind, agent_run_id=RUN, at=NOW, payload={})


EVENTS = (
    _event(1, "run_started"),
    _event(2, "step_started"),
    _event(3, "step_finished"),
    _event(4, "run_finished"),
)


class _RunFace:
    """运行面桩：事件回放 + 活动态；后台派发模拟「登记 → 跑完 → 落会话」。"""

    run_available = True

    def __init__(self, *, events=EVENTS, active=False, has_log=True) -> None:
        self._events = tuple(events)
        self._active = active
        self._has_log = has_log
        self.recorded: list[tuple[str, dict]] = []
        self.recorded_event = threading.Event()
        self.next_id = RUN

    # 运行只读面
    def new_run_id(self) -> str:
        return self.next_id

    def run_events(self, agent_run_id: str, *, after_seq: int = 0) -> tuple[RunEvent, ...]:
        return tuple(event for event in self._events if event.seq > after_seq)

    def is_active(self, agent_run_id: str) -> bool:
        return self._active

    def has_event_log(self, agent_run_id: str) -> bool:
        return self._has_log

    def wait_started(self, agent_run_id: str, timeout: float = 5.0) -> bool:
        return True

    # 对话门面面
    def turn(self, body: dict) -> dict:
        self._active = True
        try:
            return {
                "reply": ResultEnvelope.ok({"task": "演示"}),
                "session_id": body.get("session_id"),
                "needs_confirmation": False,
                "description": None,
                "clarification": None,
                "agent_continuation": None,
                "traces": [],
            }
        finally:
            self._active = False

    def record_reply(self, session_id, *, text: str, payload=None) -> bool:
        self.recorded.append((session_id, dict(payload or {})))
        self.recorded_event.set()
        return True


def _sse_body(http_get, running, path, **headers):  # noqa: ANN001
    response = http_get(running, path, headers=headers)
    return response, response.body.decode("utf-8")


def test_stream_replays_events_and_ends_explicitly(http_get, auth):
    with serve(chat=_RunFace()) as running:
        response, body = _sse_body(
            http_get, running, f"/api/chat/run/{RUN}/events", **auth(running),
        )
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" in body and "event: run_started" in body
    assert body.rstrip().endswith('{"reason": "finished"}')
    assert "event: stream_end" in body


def test_last_event_id_resumes_after_that_seq(http_get, auth):
    with serve(chat=_RunFace()) as running:
        response, body = _sse_body(
            http_get,
            running,
            f"/api/chat/run/{RUN}/events",
            **auth(running, **{"Last-Event-ID": "2"}),
        )
    assert response.status == 200
    assert "id: 1\n" not in body and "id: 2\n" not in body
    assert "id: 3\n" in body and "id: 4\n" in body


def test_query_token_is_accepted_for_sse_only(http_get):
    with serve(chat=_RunFace()) as running:
        sse = http_get(running, f"/api/chat/run/{RUN}/events?t={running.token}")
        other = http_get(running, f"/api/chat/context?t={running.token}")
    assert sse.status == 200
    assert other.status == 401, "查询串令牌只对 SSE 路径开放"


def test_missing_token_is_rejected(http_get):
    with serve(chat=_RunFace()) as running:
        response = http_get(running, f"/api/chat/run/{RUN}/events")
    assert response.status == 401


def test_unknown_run_is_404_and_does_not_hang(http_get, auth):
    with serve(chat=_RunFace(events=(), active=False, has_log=False)) as running:
        response = http_get(
            running, "/api/chat/run/agr_missing/events", headers=auth(running),
        )
    assert response.status == 404
    assert "error" in response.json()


def test_background_dispatch_returns_run_id_and_records_final_turn(http_post, auth):
    face = _RunFace()
    with serve(chat=face) as running:
        response = http_post(
            running,
            "/api/chat",
            {"action": "dispatch", "session_id": "sess_1", "background": True},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
        payload = response.json()
        assert payload["running"] is True
        assert payload["agent_run_id"] == RUN
        assert payload["session_id"] == "sess_1"
        assert face.recorded_event.wait(3.0), "工作线程应落助手轮"
    assert face.recorded and face.recorded[0][1]["agent_run_id"] == RUN


# ─────────────────── 前端事件表与子运行进度（T-AGT-032.2） ───────────────────


def test_frontend_event_types_mirror_the_backend_enum():
    """前端 `RUN_EVENT_TYPES` 必须与后端枚举同序（漏一项即该事件永不送达面板）。"""
    import re
    from pathlib import Path

    from st_agent.l3.runtime import RUN_EVENT_TYPES

    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "st_agent" / "ui" / "web" / "js" / "run_events.js"
    )
    text = path.read_text(encoding="utf-8")
    block = text.split("export const RUN_EVENT_TYPES = [", 1)[1].split("];", 1)[0]
    names = tuple(re.findall(r"'([a-z_]+)'", block))
    assert names == RUN_EVENT_TYPES


def test_chat_panel_handles_subagent_progress_and_step_accounting():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "st_agent" / "ui" / "web" / "js" / "pages" / "chat.js"
    )
    text = path.read_text(encoding="utf-8")
    assert "sub_agent_started" in text
    assert "watchChildEvents" in text
    assert "stepNote" in text
