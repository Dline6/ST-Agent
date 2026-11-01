"""T-AGT-028.1 · 会话级授权档位（M5 真装配 + 真回环服务）。

GWT 对照：

- GWT-1：会话内切到 `manual` ⇒ 下一步**只读**动作也需确认
- GWT-2：切回 `autonomous` ⇒ 只读重新自动放行
- GWT-3：覆盖是进程内瞬态——全局档 `agent.authorization` 与 `agent-change/` 留痕零变化

本文件验的是**判据按会话绑定**这条接线：窗口端点改的档位与闸门下一次取用的是**同一份**
（[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)）。档位矩阵本身的穷尽性在
`tests/l6/test_runtime_authorization_session.py`；`manual` 的「不自动续跑」在
`tests/l3/test_agent_resume.py`。
"""

from __future__ import annotations

import time
from pathlib import Path

from rig import ROOT_NAME
from rig_m5 import SK_A, seeded_m5
from rig_m6 import ui_server

from st_agent.composition.agent import _SessionBoundDecisions
from st_agent.l6 import EvolutionRiskRule

TEXT = "查证 sh.600000 的 ST 状态与退市风险"


def _make_autonomous(runtime) -> None:  # noqa: ANN001
    """出厂只读放行的口径：`skill.` 全族可自主 + 全局档 autonomous。"""
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


def _run(ui, rig, session_id: str | None):  # noqa: ANN001, ANN202
    """在同一会话里开一次后台运行并等最终轮；端点脚本从头回放。"""
    rig.endpoint.calls.clear()
    rig.endpoint._turn = 0  # noqa: SLF001 —— 脚本端点按轮次推进，复跑需归零
    body = {"action": "post", "text": TEXT, "background": True}
    if session_id:
        body["session_id"] = session_id
    status, ack = ui.post("/api/chat", body)
    assert status == 200
    assert ack.get("running") is True, "默认入口应后台开跑"
    message = _wait_final_turn(ui, ack["session_id"], ack["agent_run_id"])
    assert message is not None, "最终助手轮应带同一 agent_run_id 落会话"
    return ack["session_id"], message["payload"]


def test_session_tier_switch_reaches_the_gate(tmp_path: Path) -> None:
    rig = seeded_m5(tmp_path / ROOT_NAME)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        # ① 全局 autonomous：只读动作自主放行 → 跑完
        sid, payload = _run(ui, rig, None)
        assert payload["status"] == "ok"

        # ② 读面：默认跟随全局
        status, tier = ui.get(f"/api/chat/session/tier?id={sid}")
        assert status == 200
        assert tier["status"] == "ok"
        assert tier["data"]["tier"] == "autonomous"
        assert tier["data"]["source"] == "global"
        assert tier["data"]["global_tier"] == "autonomous"
        assert tier["data"]["tiers"] == ["manual", "collaborative", "autonomous"]

        # ③ GWT-1：切 manual → 下一步只读动作也需确认（该步未执行）
        status, switched = ui.post(
            "/api/chat/session/tier", {"session_id": sid, "tier": "manual"},
        )
        assert status == 200
        assert switched["status"] == "ok"
        assert switched["data"]["tier"] == "manual"
        assert switched["data"]["source"] == "session"

        _sid, blocked = _run(ui, rig, sid)
        assert blocked["status"] == "empty", "交还不是「已完成」，不把半程结果呈现为 ok"
        continuation = blocked["agent_continuation"]
        assert continuation["kind"] == "pending_step"
        assert continuation["pending"]["tool_name"] == SK_A
        assert continuation["evidence"] == []

        # ④ GWT-2：显式切回 autonomous → 只读重新自动放行
        status, restored = ui.post(
            "/api/chat/session/tier", {"session_id": sid, "tier": "autonomous"},
        )
        assert status == 200
        assert restored["data"]["tier"] == "autonomous"
        assert restored["data"]["source"] == "session"
        _sid, passed = _run(ui, rig, sid)
        assert passed["status"] == "ok"

        # ⑤ GWT-3：恢复跟随全局；全局条目与 agent-change/ 留痕零变化
        status, cleared = ui.post(
            "/api/chat/session/tier", {"session_id": sid, "tier": ""},
        )
        assert status == 200
        assert cleared["status"] == "ok"
        assert cleared["data"]["source"] == "global"
        assert cleared["data"]["tier"] == "autonomous"

        auth = rig.m5.l6.agent_authorization
        assert auth.tier() == "autonomous", "会话级覆盖不得改全局档"
        assert auth.changes() == (), "会话级覆盖不产 change_id、不写 agent-change/"


def test_unknown_session_is_explicitly_rejected(tmp_path: Path) -> None:
    """会话不存在 → `validation_failed`（不伪造一个档位）。"""
    rig = seeded_m5(tmp_path / ROOT_NAME)
    _make_autonomous(rig.m5)
    with ui_server(rig) as ui:
        status, payload = ui.get("/api/chat/session/tier?id=sess_does_not_exist")
        assert status == 200
        assert payload["status"] == "validation_failed"

        status, payload = ui.post(
            "/api/chat/session/tier",
            {"session_id": "sess_does_not_exist", "tier": "manual"},
        )
        assert status == 200
        assert payload["status"] == "validation_failed"


# ───────────────────── 会话绑定的鸭子口（组合根适配器） ─────────────────────


def test_decisions_adapter_falls_back_when_the_port_ignores_sessions():
    """判据面推出可调用的 `decide` 却不接受 `session_id`（包装层）⇒ 原样透传。

    曾因「能力标记被 `__getattr__` 透传、方法却不收关键字」而把 `TypeError` 交给闸门，
    使整轮判成 `denied`——故绑定按**签名**判，不由声明决定。
    """

    class _Plain:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def decide(self, action_id):
            self.seen.append(action_id)
            return "autonomous-ok"

    inner = _Plain()
    bound = _SessionBoundDecisions(inner, "sess_1")

    assert bound.decide("skill.demo") == "autonomous-ok"
    assert inner.seen == ["skill.demo"]


def test_decisions_adapter_passes_the_session_when_accepted():
    class _Scoped:
        def __init__(self) -> None:
            self.seen: list[tuple[str, str | None]] = []

        def decide(self, action_id, session_id=None):
            self.seen.append((action_id, session_id))
            return "denied"

    inner = _Scoped()
    bound = _SessionBoundDecisions(inner, "sess_7")

    assert bound.decide("skill.demo") == "denied"
    assert inner.seen == [("skill.demo", "sess_7")]
