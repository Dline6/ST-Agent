"""T-INT-012 GWT-1 · 真实端点下的 M11 默认入口（live，默认排除，不进 CI）。

离线装配面由 ``tests/integration/test_m11_agent_entry.py`` 覆盖（真组合根 + 脚本化出网一跳）；
本文件证明**同一入口**在真网络上成立：

    POST /api/chat → 默认入口预铸 agent_run_id → 循环 → 真工具目录 + 真 L1 执行
    → LlmClient → EgressGateway（按次 sender）→ 真实 HTTP/SSE

配置取自环境变量 / 仓库根 ``.env``（不入库），缺三键即 skip 并在执行日志写明原因
（[工作流「测试分层」](项目管理/工作流.md) / [D-059](项目管理/决策日志.md)）。

跑法：

    python -m pytest -m live tests/live/test_m11_agent_entry_live.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# 复用集成关卡的播种与真回环客户端（tests/integration 不在本目录的 import 路径上）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from rig import PASS, ROOT_NAME, MarketData, RecordingSender, seed_market_db  # noqa: E402
from rig_m6 import UiClient  # noqa: E402

from st_agent.__main__ import ui_ports  # noqa: E402
from st_agent.app import build_m5_runtime  # noqa: E402
from st_agent.l1.runtime import LLM_ENDPOINT_ID  # noqa: E402
from st_agent.l3.runtime import load_agent_run, read_run_events  # noqa: E402
from st_agent.l6 import EvolutionRiskRule  # noqa: E402
from st_agent.ui.server import serve  # noqa: E402

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT = "最近ST股票有没有什么热门板块?"


def _config(name: str) -> str:
    """先取环境变量，缺省回落仓库根 ``.env``（仅本机、不入库）。"""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def _wait_final_turn(ui: UiClient, session_id: str, run_id: str, timeout: float = 240.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, session = ui.get(f"/api/chat/session?id={session_id}")
        assert status == 200
        for item in (session.get("data") or {}).get("messages", []):
            payload = item.get("payload") or {}
            if item.get("role") == "agent" and payload.get("agent_run_id") == run_id:
                return item
        time.sleep(0.2)
    return None


def test_real_endpoint_drives_the_default_entry_to_an_evidence_pack(tmp_path: Path) -> None:
    """GWT-1：真端点上原句进循环，最终非 empty；证据包与留痕同号。（无工具调用则显式 skip）"""
    if not all(_config(key) for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")

    root = tmp_path / ROOT_NAME
    seed_market_db(root, PASS)
    m5 = build_m5_runtime(
        root, PASS, market_query=MarketData(), sender=RecordingSender(), audit=True,
        dotenv_path=REPO_ROOT / ".env",
    )
    capability = m5.m1.runtime.endpoints.get(LLM_ENDPOINT_ID).capability
    if not capability.supports_function_calling:
        pytest.skip("该真实端点未被探测判为支持工具调用——本用例需原生 function calling")

    # 关卡验的是装配而非风险策略：让读类动作可自主，避免 mount 到闸门交还面
    m5.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    m5.l6.agent_authorization.set_tier("autonomous")

    with serve(dev=False, **ui_ports(m5)) as running:
        ui = UiClient(running.host, running.port, running.token, app=running.app)
        status, ack = ui.post(
            "/api/chat", {"action": "post", "text": TEXT, "background": True},
        )
        assert status == 200
        assert ack.get("running") is True and ack.get("agent_run_id")
        session_id, run_id = ack["session_id"], ack["agent_run_id"]

        message = _wait_final_turn(ui, session_id, run_id)
        assert message is not None, "最终助手轮应带同一 agent_run_id 落会话"
        payload = message["payload"]
        assert payload["agent_run_id"] == run_id
        assert payload["status"] != "empty", "GWT-1：真实链路不得以 empty 收口"

        record = load_agent_run(m5.store, run_id)
        assert record.agent_run_id == run_id
        refs = payload.get("evidence_refs") or ()
        if not refs:
            pytest.skip(
                "真实端点本次未选择调用工具（模型行为，非链路缺陷）；"
                "GWT-1 证据包由离线关卡用例覆盖"
            )
        assert payload["status"] == "ok"
        assert record.termination in ("finished", "step_limit", "llm_call_limit"), (
            f"GWT-1 只要求非 empty 的证据包；真实端点以有界终止收口属合法形态，得到 {record.termination}"
        )
        assert set(step.skill_run_id for step in record.steps) >= set(
            item["ref"] for item in refs
        ), "证据引用必须指向本次留痕里的真实步骤"
        assert len(refs) == sum(1 for step in record.steps if step.status == "ok"), (
            "证据引用只收成功步（失败步照实留在留痕里，不粉饰成证据）"
        )

        events = read_run_events(m5.store, run_id)
        assert events[0].event == "run_started"
        assert events[-1].event == "run_finished"
        assert events[-1].payload["termination"] == record.termination
        assert any(event.event == "step_finished" for event in events)

        audits = m5.m1.runtime.gateway.query(kind="llm_call")
        assert audits, "真实调用应经网关留痕（02 §6 唯一出口）"
