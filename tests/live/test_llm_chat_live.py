"""T-INT-002 GWT-7 · 真实链路的意图理解冒烟（live 用例，默认排除，不进 CI）。

M1 关卡的离线用例（``tests/integration/test_m1_chat.py``）把理解端口换成确定性脚本
（A4）；本文件证明**生产组合根的默认接法**在真网络上成立：

    build_m1_runtime(不传 understander) → LlmIntentUnderstander → runtime.llm
    → EndpointRegistry/CredentialVault/EgressGateway（按次 sender）→ 真实 HTTP/SSE

即「对话面第一跳真过 L0 出网链路」。配置取自环境变量 / 仓库根 ``.env``（不入库），
缺三键即 skip 并在执行日志写明原因（不许默默略过，[工作流「测试分层」] / [D-059]）。

跑法：

    python -m pytest -m live tests/live/test_llm_chat_live.py

"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 复用 M1 关卡 rig 的播种与取数面（tests/integration 不在本目录的 import 路径上）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from st_agent.app import build_m1_runtime  # noqa: E402
from rig import ROOT_NAME, MarketData, RecordingSender, seed_market_db  # noqa: E402

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
THROWAWAY_PASS = "m1-live-throwaway-passphrase"
UTTERANCE = "帮我看下 sh.600000 有没有退市风险"


def _config(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def test_production_root_understands_via_real_endpoint(tmp_path: Path) -> None:
    """默认 `LlmIntentUnderstander` 经真实端点把一句话收敛为意图（或结构化失败）。"""
    if not (_config("LLM_API_KEY") and _config("LLM_BASE_URL") and _config("LLM_MODEL")):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")

    root = tmp_path / ROOT_NAME
    seed_market_db(root, THROWAWAY_PASS)
    feed = MarketData()
    sender = RecordingSender()
    # 不传 llm_env / understander ⇒ 缺省取值面（环境变量 → .env）+ 真实理解器（T-L1-011）
    m1 = build_m1_runtime(root, THROWAWAY_PASS, market_query=feed, sender=sender)

    understood = m1.intent.understand(UTTERANCE)
    if understood.status != "ok":
        pytest.fail(
            f"真实链路意图理解未通：{understood.status} · {understood.reason}"
        )
    draft = understood.data
    assert draft.intent is not None or draft.directions, (
        "ok 信封须携收敛的意图或方向候选（05 §3.2：不静默）"
    )
    print(f"\n真实端点收敛：intent={draft.intent} target={draft.target}")

    # 出网审计恰一条 llm_call（02 §6 唯一出口），且发起方为 L3 意图理解
    audits = m1.runtime.gateway.query(kind="llm_call")
    assert len(audits) == 1
    assert audits[0].status == "ok"

    # 明文不落盘（口令派生加密分区内不得出现原文 / key）
    key = _config("LLM_API_KEY")
    blob = "\n".join(
        p.read_bytes().decode("utf-8", errors="replace")
        for p in root.rglob("*") if p.is_file()
    )
    assert UTTERANCE not in blob and key not in blob, "明文出现在落盘文件里"
