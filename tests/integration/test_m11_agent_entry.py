"""T-INT-012 · M11 集成关卡：入口 → 循环 → 运行控制面（真装配 + 真回环服务）。

本文件只验**跨叶装配**；单叶行为分别见同目录的 ``test_m11_default_entry`` /
``test_m11_run_stream`` / ``test_m11_steer`` / ``test_m11_session_tier``。三处验收：

- GWT-1：GWT 原句从默认入口进入循环，事件流可见步骤，最终证据包非 ``empty``；
  证据引用、``agent_run`` 留痕、``skill-run`` 记录与会话终轮四者同号同源。
- GWT-2：同一条运行上插话与安全停止均可用、留痕一致；运行中切档在下一次闸门生效。
- GWT-3：范围套件 + ``verify_docs.py --strict`` 由 ⑥ 执行（本文件不跑测试）。
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
from st_agent.l3.runtime import load_agent_run, read_run_events
from st_agent.l6 import EvolutionRiskRule

TEXT = "最近ST股票有没有什么热门板块?"
STEER = "补充：只看新能源板块"


class _BlockingEndpoint(ScriptedEndpoint):
    """在指定循环轮次阻塞，供测试在**运行中**提交插话 / 切档 / 停止。"""

    def __init__(self, *, block_turns: set[int], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._block_turns = set(block_turns)
        self.reached = {turn: threading.Event() for turn in self._block_turns}
        self.release = {turn: threading.Event() for turn in self._block_turns}

    def __call__(self, url, headers, body, timeout_s):  # noqa: ANN001, ANN002, ANN003
        payload = json.loads(body)
        names = tuple(
            (entry.get("function") or {}).get("name")
            for entry in (payload.get("tools") or ())
        )
        turn = self._turn
        if PROBE_TOOL.name not in names and turn in self._block_turns:
            self.reached[turn].set()
            if not self.release[turn].wait(10):
                raise AssertionError(f"测试未在 10s 内放行第 {turn} 个循环轮次")
        return super().__call__(url, headers, body, timeout_s)


def _make_autonomous(runtime) -> None:  # noqa: ANN001
    """出厂只读放行的口径：`skill.` 全族可自主 + 全局档 autonomous。"""
    runtime.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    runtime.l6.agent_authorization.set_tier("autonomous")


def _wait_final_turn(ui, session_id: str, run_id: str, timeout: float = 8.0):  # noqa: ANN001, ANN202
    """轮询会话直到带同一 `agent_run_id` 的最终轮出现（前端同一判据）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, session = ui.get(f"/api/chat/session?id={session_id}")
        assert status == 200
        for item in (session.get("data") or {}).get("messages", []):
            payload = item.get("payload") or {}
            if item.get("role") == "agent" and payload.get("agent_run_id") == run_id:
                return item
        time.sleep(0.05)
    return None


def _start(ui, *, session_id: str | None = None):  # noqa: ANN001, ANN202
    body: dict[str, object] = {"action": "post", "text": TEXT, "background": True}
    if session_id:
        body["session_id"] = session_id
    status, ack = ui.post("/api/chat", body)
    assert status == 200
    assert ack.get("running") is True, "默认入口应后台开跑"
    return ack["session_id"], ack["agent_run_id"]


def test_question_yields_evidence_pack_across_artifacts(tmp_path: Path) -> None:
    """GWT-1：原句进循环 → 步骤可见 → 证据包非 empty，四处留痕同号同源。"""
    rig = seeded_m5(tmp_path / ROOT_NAME)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        session_id, run_id = _start(ui)

        status, stream = ui.text(f"/api/chat/run/{run_id}/events")
        assert status == 200
        for expected in (
            "event: run_started",
            "event: step_started",
            "event: step_finished",
            "event: run_finished",
        ):
            assert expected in stream, expected

        message = _wait_final_turn(ui, session_id, run_id)
        assert message is not None, "最终助手轮应带同一 agent_run_id 落会话"
        payload = message["payload"]
        assert payload["status"] == "ok", "GWT-1：证据包不得以 empty 收口"
        assert payload["needs_confirmation"] is False
        assert payload["data"]["slots"]["answer"] == "证据已收集完毕"
        refs = [item["ref"] for item in payload["evidence_refs"]]
        assert len(refs) == 2 and len(set(refs)) == 2, "两步各一条证据引用"

        events = read_run_events(rig.m5.store, run_id)
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        kinds = [event.event for event in events]
        assert kinds.count("step_started") == 2
        assert kinds.count("step_finished") == 2
        assert kinds[-1] == "run_finished"
        assert events[-1].payload["termination"] == "finished"

        record = load_agent_run(rig.m5.store, run_id)
        assert record.agent_run_id == run_id
        assert record.session_id == session_id
        assert record.termination == "finished"
        assert [step.skill_run_id for step in record.steps] == refs
        for step in record.steps:
            assert rig.m5.store.get("execution_log", step.log_ref), (
                "证据引用应指向真实存在的 skill-run 记录"
            )


def test_steer_and_stop_on_one_run_leave_consistent_traces(tmp_path: Path) -> None:
    """GWT-2：同一运行内插话投递、安全停止，事件 / 留痕 / 会话终轮三处一致。"""
    endpoint = _BlockingEndpoint(
        block_turns={1, 2},
        script=[(SK_A, {"format": "brief"}), (SK_B, {"top_n": 3}), (SK_A, {})],
    )
    rig = seeded_m5(tmp_path / ROOT_NAME, endpoint=endpoint)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        session_id, run_id = _start(ui)

        assert endpoint.reached[1].wait(5), "循环应到达第二个模型轮次（已被测试阻塞）"
        status, reply = ui.post(
            f"/api/chat/run/{run_id}/steer",
            {"text": STEER, "session_id": session_id},
        )
        assert status == 200
        assert reply["status"] == "ok"
        assert reply["data"]["delivered"] is True
        endpoint.release[1].set()

        assert endpoint.reached[2].wait(5), "插话应在下一个步骤边界投递后再问模型"
        status, stopped = ui.post(f"/api/chat/run/{run_id}/stop", {})
        assert status == 200
        assert stopped["status"] == "ok"
        assert stopped["data"] == {"agent_run_id": run_id, "stopping": True}
        endpoint.release[2].set()

        message = _wait_final_turn(ui, session_id, run_id)
        assert message is not None, "停止不丢已执行步骤：最终助手轮仍应落会话"
        payload = message["payload"]
        assert payload["status"] == "ok", "有已执行步的 cancelled 仍照实给证据包"
        assert "停止" in payload["data"]["slots"]["termination"]
        assert len(payload["evidence_refs"]) == 2
        assert any(
            f"[运行中插入] {STEER}" in call["prompt"] for call in endpoint.loop_calls
        ), "插话应进入后续轮次的工作上下文"

        events = read_run_events(rig.m5.store, run_id)
        kinds = [event.event for event in events]
        assert "steer" in kinds
        steer_event = next(event for event in events if event.event == "steer")
        assert steer_event.payload["delivered"] is True
        assert events[-1].event == "run_finished"
        assert events[-1].payload["termination"] == "cancelled"

        record = load_agent_run(rig.m5.store, run_id)
        assert record.termination == "cancelled"
        assert len(record.steps) == 2
        assert [item.delivered for item in record.steered] == [True]
        assert rig.m5.investigations.is_active(run_id) is False, "收口后应释放活动注册表"

        status, session = ui.get(f"/api/chat/session?id={session_id}")
        assert status == 200
        steered = [
            item["text"]
            for item in session["data"]["messages"]
            if item.get("steer")
        ]
        assert steered == [STEER], "插话立即落会话且只落一次"


def test_tier_switch_mid_run_applies_to_the_next_gate(tmp_path: Path) -> None:
    """GWT-2：运行中切到 manual，下一次闸门即按会话档判（已开始的步骤不回溯改判）。"""
    endpoint = _BlockingEndpoint(
        block_turns={0},
        script=[(SK_A, {"format": "brief"}), (SK_B, {"top_n": 3})],
    )
    rig = seeded_m5(tmp_path / ROOT_NAME, endpoint=endpoint)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        session_id, run_id = _start(ui)

        assert endpoint.reached[0].wait(5), "循环应停在首个模型轮次"
        status, switched = ui.post(
            "/api/chat/session/tier", {"session_id": session_id, "tier": "manual"},
        )
        assert status == 200
        assert switched["data"]["tier"] == "manual"
        assert switched["data"]["source"] == "session"
        endpoint.release[0].set()

        message = _wait_final_turn(ui, session_id, run_id)
        assert message is not None
        payload = message["payload"]
        assert payload["status"] == "empty", "交还不是「已完成」，不把半程结果呈现为 ok"
        assert payload["agent_continuation"]["kind"] == "pending_step"
        assert payload["agent_continuation"]["pending"]["tool_name"] == SK_A
        assert payload["agent_continuation"]["evidence"] == []

        events = read_run_events(rig.m5.store, run_id)
        assert any(event.event == "gate_pending" for event in events)
        record = load_agent_run(rig.m5.store, run_id)
        assert record.termination == "needs_confirmation"
        assert record.steps == ()
        assert record.pending is not None and record.pending.tool_name == SK_A
