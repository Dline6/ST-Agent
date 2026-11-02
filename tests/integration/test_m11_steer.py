"""T-AGT-027：插话的真装配端到端（M11；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

两段验收：

- **真链路**：自然语言后台开跑 → 运行到第二步时提交插话 → 下一步的模型上下文含
  「运行中插入」段，SSE 有 `steer` 事件，会话里留一条带 `steer=true` 的用户消息；
- **竞态去向**：运行已结束时提交，仍落为普通用户消息（`delivered=false`），**不丢**。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from rig import ROOT_NAME
from rig_m5 import SK_A, SK_B, ScriptedEndpoint, seeded_m5
from rig_m6 import ui_server

from st_agent.l1.runtime import PROBE_TOOL
from st_agent.l6 import EvolutionRiskRule

TEXT = "查证 sh.600000 的 ST 状态与退市风险"
STEER = "补充：只看新能源板块"


class _BlockingEndpoint(ScriptedEndpoint):
    """在指定循环轮次阻塞，供测试同步提交插话（避免与循环抢时序）。"""

    def __init__(self, *, block_turn: int = 1, **kwargs) -> None:
        super().__init__(**kwargs)
        self._block_turn = block_turn
        self.reached = threading.Event()
        self.release = threading.Event()

    def __call__(self, url, headers, body, timeout_s):
        payload = json.loads(body)
        names = tuple(
            (entry.get("function") or {}).get("name")
            for entry in (payload.get("tools") or ())
        )
        if (
            PROBE_TOOL.name not in names
            and self._turn == self._block_turn
            and not self.reached.is_set()
        ):
            self.reached.set()
            if not self.release.wait(10):
                raise AssertionError("测试未在 10s 内放行被阻塞的循环轮次")
        return super().__call__(url, headers, body, timeout_s)


def _make_autonomous(runtime) -> None:  # noqa: ANN001
    runtime.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    runtime.l6.agent_authorization.set_tier("autonomous")


def _wait_final_turn(ui, session_id: str, run_id: str):  # noqa: ANN001, ANN202
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        status, session = ui.get(f"/api/chat/session?id={session_id}")
        assert status == 200
        for item in (session.get("data") or {}).get("messages", []):
            payload = item.get("payload") or {}
            if item.get("role") == "agent" and payload.get("agent_run_id") == run_id:
                return item
        time.sleep(0.05)
    return None


def test_steer_reaches_the_next_round_of_a_real_run(tmp_path: Path) -> None:
    """GWT-1 / GWT-2（.1）与 GWT-1 / GWT-2（.2）的真装配面。"""
    endpoint = _BlockingEndpoint(
        block_turn=1,
        script=[(SK_A, {"format": "brief"}), (SK_B, {"top_n": 3})],
    )
    rig = seeded_m5(tmp_path / ROOT_NAME, endpoint=endpoint)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        status, ack = ui.post(
            "/api/chat", {"action": "post", "text": TEXT, "background": True},
        )
        assert status == 200 and ack["running"] is True
        run_id = ack["agent_run_id"]
        session_id = ack["session_id"]
        assert endpoint.reached.wait(5.0), "循环应到达第二个模型轮次（已被测试阻塞）"

        status, reply = ui.post(
            f"/api/chat/run/{run_id}/steer",
            {"text": STEER, "session_id": session_id},
        )
        assert status == 200
        assert reply["status"] == "ok"
        assert reply["data"]["delivered"] is True
        assert reply["data"]["agent_run_id"] == run_id

        endpoint.release.set()
        final = _wait_final_turn(ui, session_id, run_id)
        assert final is not None, "插话不打断运行：最终助手轮仍应落会话"

        later_prompts = [call["prompt"] for call in endpoint.loop_calls[2:]]
        assert any(f"[运行中插入] {STEER}" in prompt for prompt in later_prompts), (
            "插话应在下一个步骤边界并入模型上下文"
        )

        status, session = ui.get(f"/api/chat/session?id={session_id}")
        assert status == 200
        steered = [
            item for item in session["data"]["messages"] if item.get("steer")
        ]
        assert [item["text"] for item in steered] == [STEER]

        status, stream = ui.text(f"/api/chat/run/{run_id}/events")
        assert status == 200
        assert "event: steer" in stream
        assert '"delivered": true' in stream or '"delivered":true' in stream


def test_steer_after_run_end_is_kept_as_an_ordinary_message(tmp_path: Path) -> None:
    """GWT-2（.1）：与结束竞态 / 运行已结束 → 转为下一条普通消息，不丢。"""
    rig = seeded_m5(tmp_path / ROOT_NAME)
    session = rig.m5.m1.sessions.create()
    reply = rig.m5.chat.steer_run("agr_not_running", STEER, session_id=session.session_id)

    assert reply.status == "ok"
    assert reply.data["delivered"] is False
    assert "普通消息" in reply.data["note"]

    stored = rig.m5.m1.sessions.load(session.session_id)
    assert [(item.text, item.steer) for item in stored.messages] == [(STEER, True)]
    read = rig.m5.chat.read_session(session.session_id)
    assert read.status == "ok"
    assert read.data["messages"][0]["steer"] is True
