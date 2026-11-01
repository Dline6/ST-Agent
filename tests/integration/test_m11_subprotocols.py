"""T-AGT-030 · 子协议动作在 M5 真装配上的接线（[05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

验的是**组合根后置绑定**这条接线：L1 工具目录里的五个动作在 L3 面造好之后由
`build_m5_runtime` 把 `ConfigDraftProtocol` / `ConflictAdjudicator` / L4 / L6 端口绑到
循环端口；动作产出的卡片经响应 `description` 落助手轮 payload（前端同一渲染注册表）。
动作本身的逐条行为在 `tests/l3/test_agent_subprotocols.py`。
"""

from __future__ import annotations

import time
from pathlib import Path

from rig import ROOT_NAME
from rig_m5 import SK_A, seeded_m5
from rig_m6 import ui_server

from st_agent.l3.runtime import load_agent_run
from st_agent.l6 import EvolutionRiskRule

TEXT = "帮我把数据聚合的形态改成简版"


def _make_autonomous(rig) -> None:  # noqa: ANN001
    """让读类动作在本次集成里可自主（生产缺省把 `skill.` 判为必须协作）。"""
    rig.m5.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    rig.m5.l6.agent_authorization.set_tier("autonomous")


def _wait_final_turn(ui, session_id: str, run_id: str):  # noqa: ANN001, ANN202
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        status, session = ui.get(f"/api/chat/session?id={session_id}")
        assert status == 200
        for item in (session.get("data") or {}).get("messages", []):
            payload = item.get("payload") or {}
            if item.get("role") == "agent" and payload.get("agent_run_id") == run_id:
                return item
        time.sleep(0.05)
    return None


def _run(ui, text: str):  # noqa: ANN001, ANN202
    status, ack = ui.post(
        "/api/chat", {"action": "post", "text": text, "background": True},
    )
    assert status == 200
    assert ack.get("running") is True
    message = _wait_final_turn(ui, ack["session_id"], ack["agent_run_id"])
    assert message is not None, "最终助手轮应带同一 agent_run_id 落会话"
    return ack["agent_run_id"], message["payload"]


def test_configure_card_is_bound_and_returned(tmp_path: Path) -> None:
    rig = seeded_m5(
        tmp_path / ROOT_NAME,
        script=[("sk_configure", {
            "target": SK_A, "values": {"format": "brief"},
        })],
    )
    with ui_server(rig) as ui:
        run_id, payload = _run(ui, TEXT)

        assert payload["status"] == "ok"
        description = payload["description"]
        assert description is not None
        assert description["status"] == "ok"
        assert description["data"]["component_type"] == "config_draft_card"
        assert description["data"]["slots"]["target"] == SK_A
        # 动作只产卡：运行表零步，终止原因是 card_ready
        record = load_agent_run(rig.m5.store, run_id)
        assert record.termination == "card_ready"
        assert record.steps == ()


def test_explain_card_expands_the_real_run_trace(tmp_path: Path) -> None:
    rig = seeded_m5(
        tmp_path / ROOT_NAME,
        script=[
            (SK_A, {"format": "brief"}),
            ("sk_explain", {}),
        ],
    )
    _make_autonomous(rig)
    with ui_server(rig) as ui:
        run_id, payload = _run(ui, "查证 sh.600000 的 ST 状态并说明本次推理链")

        assert payload["status"] == "ok"
        description = payload["description"]
        assert description is not None
        assert description["data"]["component_type"] == "trace_timeline"
        record = load_agent_run(rig.m5.store, run_id)
        assert record.termination == "card_ready"
        assert len(record.steps) == 1
        assert record.steps[0].tool_name == SK_A
