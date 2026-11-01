"""T-AGT-023.1/.2 · 运行事件流端到端（真组合根 + 真回环服务）。

单叶用例用桩验帧形态与后台派发；本文件在 **M5 真装配** 上验一件事：确认卡派发改走后
台之后，`agent_run_id` 立即可得、SSE 能回放到 `run_started` → `step_*` → `run_finished`，
且最终助手轮真落会话并带同一 `agent_run_id`（前端据此回读、不重复渲染）。
"""

from __future__ import annotations

import time
from pathlib import Path

from rig import ROOT_NAME
from rig_m5 import seeded_m5
from rig_m6 import ui_server

from st_agent.l6 import EvolutionRiskRule

TEXT = "查证 sh.600000 的 ST 状态与退市风险"


def _make_autonomous(runtime) -> None:  # noqa: ANN001
    """把示例 Skill 放进可自主清单（同 `test_m5_autonomy` 的关卡口径）。"""
    runtime.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    runtime.l6.agent_authorization.set_tier("autonomous")


def test_background_dispatch_streams_events_and_records_final_turn(tmp_path: Path) -> None:
    rig = seeded_m5(tmp_path / ROOT_NAME)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        status, card = ui.post("/api/chat", {"action": "post", "text": TEXT})
        assert status == 200
        assert card["needs_confirmation"] is True
        session_id = card["session_id"]

        status, ack = ui.post(
            "/api/chat",
            {"action": "dispatch", "session_id": session_id, "background": True},
        )
        assert status == 200
        assert ack["running"] is True
        run_id = ack["agent_run_id"]
        assert run_id

        status, stream = ui.text(f"/api/chat/run/{run_id}/events")
        assert status == 200
        for expected in ("event: run_started", "event: step_started",
                         "event: step_finished", "event: run_finished"):
            assert expected in stream, expected
        assert "event: stream_end" in stream
        assert stream.rstrip().endswith('{"reason": "finished"}')

        # 事件流在 run_finished 收束，落会话略后：轮询到最终轮出现（前端同一判据）。
        message = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status, session = ui.get(f"/api/chat/session?id={session_id}")
            assert status == 200
            candidates = [
                item for item in (session.get("data") or {}).get("messages", [])
                if item.get("role") == "agent"
                and (item.get("payload") or {}).get("agent_run_id") == run_id
            ]
            if candidates:
                message = candidates[-1]
                break
            time.sleep(0.1)
        assert message is not None, "最终助手轮应带同一 agent_run_id 落会话"
        assert message["payload"]["status"] == "ok"
