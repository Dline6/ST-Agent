"""T-AGT-028.1 · 会话级档位端点（面层：`/api/chat/session/tier`）。

验四件事：① `GET` 转述面的读值（档位 / 来源 / 全局档 / 三档清单）；② `POST` 把
`{session_id, tier}` 原样交给面（空 `tier` 即「恢复跟随全局」，**不**在面层改写语义）；
③ 面未持该口 ⇒ 显式 `unavailable` + 点名；④ 面的非 ok 态**原样透出**，不被吞成 ok。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.server import serve

SID = "sess_0000000000000001"


class _TierFace:
    """只有档位读 / 写两口的对话面桩。"""

    def __init__(self, *, envelope: ResultEnvelope | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._envelope = envelope

    def _payload(self, session_id: str, tier: str, source: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "tier": tier,
            "source": source,
            "global_tier": "autonomous",
            "tiers": ["manual", "collaborative", "autonomous"],
        }

    def session_tier(self, session_id: str) -> ResultEnvelope:
        self.calls.append(("get", session_id))
        if self._envelope is not None:
            return self._envelope
        return ResultEnvelope.ok(self._payload(session_id, "autonomous", "global"))

    def set_session_tier(self, session_id: str, tier: Any) -> ResultEnvelope:
        self.calls.append(("set", (session_id, tier)))
        if self._envelope is not None:
            return self._envelope
        if not str(tier or "").strip():
            return ResultEnvelope.ok(self._payload(session_id, "autonomous", "global"))
        return ResultEnvelope.ok(self._payload(session_id, str(tier), "session"))


class _NoTierFace:
    """未持档位口的对话面（旧装配的对照）。"""


def test_get_returns_the_effective_tier(http_get, auth):
    face = _TierFace()
    with serve(chat=face) as running:
        response = http_get(running, f"/api/chat/session/tier?id={SID}", auth(running))

    payload = response.json()
    assert response.status == 200
    assert payload["status"] == "ok"
    assert payload["data"]["tier"] == "autonomous"
    assert payload["data"]["source"] == "global"
    assert payload["data"]["tiers"] == ["manual", "collaborative", "autonomous"]
    assert face.calls == [("get", SID)]


def test_post_forwards_the_switch_and_the_reset(http_post, auth):
    face = _TierFace()
    with serve(chat=face) as running:
        switched = http_post(
            running, "/api/chat/session/tier",
            {"session_id": SID, "tier": "manual"}, auth(running),
        )
        reset = http_post(
            running, "/api/chat/session/tier",
            {"session_id": SID, "tier": ""}, auth(running),
        )

    assert switched.status == 200
    assert switched.json()["data"]["source"] == "session"
    assert switched.json()["data"]["tier"] == "manual"
    # 空 tier 原样交给面（清除语义归面，面层不改写）
    assert reset.json()["data"]["source"] == "global"
    assert face.calls == [("set", (SID, "manual")), ("set", (SID, ""))]


def test_absent_ports_are_explicitly_unavailable(http_get, http_post, auth):
    with serve(chat=_NoTierFace()) as running:
        read = http_get(running, f"/api/chat/session/tier?id={SID}", auth(running))
        write = http_post(
            running, "/api/chat/session/tier",
            {"session_id": SID, "tier": "manual"}, auth(running),
        )

    assert read.json()["status"] == "unavailable"
    assert "会话档位面" in read.json()["reason"]
    assert write.json()["status"] == "unavailable"


def test_face_failure_is_not_swallowed(http_post, auth):
    face = _TierFace(envelope=ResultEnvelope.validation_failed("会话不存在：x"))
    with serve(chat=face) as running:
        response = http_post(
            running, "/api/chat/session/tier",
            {"session_id": "x", "tier": "manual"}, auth(running),
        )

    assert response.json()["status"] == "validation_failed"
