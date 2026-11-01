"""T-AGT-029.1/.2 · 默认入口承接循环与快捷指令短路（M5 真装配 + 真回环服务）。

两叶的验收面：

- `.1`：任何自然语言直接开一次 run（不再先分类）；会话历史与记忆进循环前预取；模型直接
  回答时正文经 `slots.answer` 产出。未装配循环端口的根上 fail-closed，不回落旧路径。
- `.2`：`/` 快捷指令仍在入口短路（意图模板 + 参数预填）；未知指令显式报错、不交循环。
"""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path

from rig import ROOT_NAME
from rig_m1 import seeded_m1_plaintext
from rig_m5 import seeded_m5
from rig_m6 import ui_server

from st_agent.l6 import EvolutionRiskRule
from st_agent.ui.security import TOKEN_HEADER

TEXT = "查证 sh.600000 的 ST 状态与退市风险"


def _make_autonomous(runtime) -> None:  # noqa: ANN001
    runtime.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    runtime.l6.agent_authorization.set_tier("autonomous")


def _wait_final_turn(ui, session_id: str, run_id: str):  # noqa: ANN001, ANN202
    """轮询会话直到带同一 `agent_run_id` 的最终轮出现（前端同一判据）。"""
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


def _post(running, body: dict):  # noqa: ANN001, ANN202
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    headers = {"Content-Type": "application/json", TOKEN_HEADER: running.token}
    conn.request("POST", "/api/chat", body=json.dumps(body), headers=headers)
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    return resp.status, payload


# ───────────────────────── T-AGT-029.1 默认入口 ─────────────────────────

def test_default_natural_language_runs_the_loop_and_answers(tmp_path: Path) -> None:
    """GWT-1 / GWT-3：自然语言直接进循环；模型直接回答 → 正文经 `answer` 槽产出。"""
    rig = seeded_m5(tmp_path / ROOT_NAME)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        status, ack = ui.post(
            "/api/chat", {"action": "post", "text": TEXT, "background": True},
        )
        assert status == 200
        assert ack["running"] is True
        assert ack["agent_run_id"] and ack["session_id"]
        assert ack["needs_confirmation"] is False, "默认入口不再先出意图确认卡"

        status, stream = ui.text(f"/api/chat/run/{ack['agent_run_id']}/events")
        assert status == 200
        assert "event: run_started" in stream and "event: run_finished" in stream

        message = _wait_final_turn(ui, ack["session_id"], ack["agent_run_id"])
        assert message is not None, "最终助手轮应带同一 agent_run_id 落会话"
        payload = message["payload"]
        assert payload["status"] == "ok"
        slots = payload["data"]["slots"]
        assert slots["answer"] == "证据已收集完毕", "模型结束轮的正文进 answer 槽"
        assert slots["termination"]
        assert payload["needs_confirmation"] is False


def test_second_turn_prefetches_session_history_into_the_loop(tmp_path: Path) -> None:
    """GWT-2：同一会话的第二条消息，循环工作上下文含上一轮的会话历史与记忆段。

    脚本置空（每轮一次 LLM 调用即结束）：上一轮的助手载荷因此很小，不会因其体积
    触发「优先保近、收不下即停」的预算取舍，历史段的断言才指向预取本身。
    """
    rig = seeded_m5(tmp_path / ROOT_NAME, script=[])
    # 预算足以同时装下工具目录与近端历史（缺省 12000 下按「先舍历史」取舍，本用例要验的是
    # 预取本身而非预算行为——取舍面由 `test_agent_context` 覆盖）。
    rig.m5.investigations._bounds.set_context_budget(32_000)  # noqa: SLF001
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        status, first = ui.post(
            "/api/chat", {"action": "post", "text": TEXT, "background": True},
        )
        assert first["running"] is True
        assert _wait_final_turn(ui, first["session_id"], first["agent_run_id"]) is not None

        rig.endpoint.calls.clear()
        status, second = ui.post(
            "/api/chat",
            {
                "action": "post", "text": "再看一眼板块热度",
                "session_id": first["session_id"], "background": True,
            },
        )
        assert second["running"] is True
        assert _wait_final_turn(ui, second["session_id"], second["agent_run_id"]) is not None

        prompts = [call["prompt"] for call in rig.endpoint.loop_calls]
        assert prompts, "第二轮应至少发生一次循环 LLM 调用"
        assert "会话历史" in prompts[0], "工作上下文应含历史段（不是无历史降级）"
        assert TEXT in prompts[0], "上一轮的用户消息应在历史里"


def test_default_entry_without_the_loop_is_fail_closed(tmp_path: Path) -> None:
    """A1：未装配循环端口的根上 fail-closed `unavailable`，不回落既有意图确认卡。"""
    from st_agent.ui.server import serve

    runtime = seeded_m1_plaintext(tmp_path / ROOT_NAME).m1
    with serve(dev=False, chat=runtime.chat) as running:
        status, payload = _post(running, {"action": "post", "text": "查 sh.600000 退市风险"})
    assert status == 200
    assert payload["status"] == "unavailable"
    assert "循环端口" in payload["reason"]
    assert payload["session_id"], "会话仍已建立（用户消息已落盘，标识须回给调用方）"
    assert payload["needs_confirmation"] is False


# ───────────────────────── T-AGT-029.2 快捷指令 ─────────────────────────

def test_shortcut_still_short_circuits_into_a_confirmation_card(tmp_path: Path) -> None:
    """GWT-1：`/盯盘` 短路取意图模板与参数预填，仍出确认卡（不进循环）。"""
    from st_agent.ui.server import serve

    runtime = seeded_m1_plaintext(tmp_path / ROOT_NAME).m1
    with serve(dev=False, chat=runtime.chat) as running:
        status, payload = _post(running, {"action": "post", "text": "/盯盘"})
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["needs_confirmation"] is True
    assert not payload.get("agent_run_id"), "快捷指令同步回确认卡，不起运行"
    items = payload["data"]
    assert items[0]["text"].startswith("意图 configure")
    # 指令预填：/盯盘 的 window_days=30 出现在卡上（来源「指令预填」或默认值）
    assert any("window_days" in item["text"] for item in items)


def test_unknown_shortcut_is_explicit_and_never_reaches_the_loop(tmp_path: Path) -> None:
    """GWT-2：未知指令显式报错（`empty` + 可用清单），不交循环。"""
    from st_agent.ui.server import serve

    runtime = seeded_m1_plaintext(tmp_path / ROOT_NAME).m1
    with serve(dev=False, chat=runtime.chat) as running:
        status, payload = _post(running, {"action": "post", "text": "/不存在的指令"})
    assert status == 200
    assert payload["status"] == "empty"
    assert "未知指令" in payload["reason"]
    assert payload["needs_confirmation"] is False
    assert not payload.get("agent_run_id")
