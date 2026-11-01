"""T-AGT-024.2：停止端点（真 HTTP 面）。

验三件事：① 在册运行 → `ok` + `stopping`（协作式，不承诺已停止）；② 不在册 / 未接端口
→ 显式 no-op / unavailable，不报错；③ 停止后事件流仍以 `run_finished` 收束、不再有新帧。
"""

from __future__ import annotations

from datetime import datetime, timezone

from st_agent.l3.runtime import RunEvent
from st_agent.ui.server import serve

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
RUN = "agr_000000000000000000bb"


def _event(seq: int, kind: str, payload: dict | None = None) -> RunEvent:
    return RunEvent(seq=seq, event=kind, agent_run_id=RUN, at=NOW, payload=payload or {})


CANCELLED_EVENTS = (
    _event(1, "run_started"),
    _event(2, "step_started", {"tool": "sk_data_aggregate"}),
    _event(3, "step_finished", {"tool": "sk_data_aggregate", "status": "ok"}),
    _event(4, "run_finished", {"termination": "cancelled"}),
)


class _StopFace:
    """运行面桩：只有停止相关的读口与动作（其余端点不参与本文件）。"""

    def __init__(self, *, active: bool = True, available: bool = True, events=()) -> None:
        self.run_available = available
        self._active = active
        self._events = tuple(events)
        self.stopped: list[str] = []

    def stop_run(self, agent_run_id: str) -> bool:
        if not self._active:
            return False
        self.stopped.append(agent_run_id)
        return True

    def run_events(self, agent_run_id: str, *, after_seq: int = 0) -> tuple[RunEvent, ...]:
        return tuple(event for event in self._events if event.seq > after_seq)

    def is_active(self, agent_run_id: str) -> bool:
        return self._active

    def has_event_log(self, agent_run_id: str) -> bool:
        return True


def test_stop_marks_active_run_and_acknowledges(http_post, auth):
    face = _StopFace(active=True)
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/stop",
            {},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["data"] == {"agent_run_id": RUN, "stopping": True}
    assert face.stopped == [RUN]


def test_stop_without_active_run_is_explicit_noop(http_post, auth):
    face = _StopFace(active=False)
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/stop",
            {},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    assert response.status == 200          # 已结束 / 重复点击不是错误
    payload = response.json()
    assert payload["status"] == "empty"
    assert "已不在运行中" in payload["reason"]
    assert face.stopped == []


def test_stop_requires_token(http_post):
    with serve(chat=_StopFace()) as running:
        response = http_post(
            running, f"/api/chat/run/{RUN}/stop", {},
            headers={"Content-Type": "application/json"},
        )
    assert response.status == 401


def test_stop_without_run_port_is_unavailable(http_post, auth):
    face = _StopFace(available=False)
    with serve(chat=face) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/stop",
            {},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "停止不可用" in payload["reason"]


def test_stop_path_shape_is_enumerated(http_post, auth):
    with serve(chat=_StopFace()) as running:
        response = http_post(
            running,
            f"/api/chat/run/{RUN}/stop/extra",
            {},
            headers=auth(running, **{"Content-Type": "application/json"}),
        )
    assert response.status == 404


def test_stream_ends_at_run_finished_cancelled(http_get, auth):
    with serve(chat=_StopFace(events=CANCELLED_EVENTS)) as running:
        response = http_get(
            running, f"/api/chat/run/{RUN}/events", headers=auth(running),
        )
    body = response.body.decode("utf-8")
    assert response.status == 200
    assert '"cancelled"' in body
    assert body.rstrip().endswith('{"reason": "finished"}')
    assert "event: stream_end" in body
    tail = body.split("event: run_finished", 1)[1]
    assert "event: step_started" not in tail and "event: step_finished" not in tail
