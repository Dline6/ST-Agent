"""T-INT-005 两个真实 LLM 适配器的真链路冒烟（live 用例，默认排除，不进 CI）。

`LlmTrainingUnderstander`（训练对话概念性理解，[08 §3]）与 `LlmPatternObserver`
（模式观察面，[08 §4]）都要经 ``runtime.llm`` 出网——本用例用本机已配的 OpenAI 兼容
端点各走一次，证明**新适配器真的把 prompt 送出去了**（离线分支由
``tests/integration/test_m4_reflection_eco.py`` 以注入替身覆盖）。

跑法：

    python -m pytest -m live -s tests/live/test_l6_adapters_live.py

配置取自环境变量 ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``；缺省回落仓库根的
``.env``（不入库）。缺端点即 ``skip`` 并写明原因。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from st_agent.app import LlmPatternObserver, LlmTrainingUnderstander
from st_agent.l1.runtime import open_runtime
from st_agent.l3.chat import SessionStore

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
THROWAWAY_PASS = "live-throwaway-passphrase"
_ENDPOINT = "cloud-main"


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


class _Feed:
    def query(self, sql: str, params: tuple = ()):
        raise AssertionError("本用例不取数")


def _runtime(tmp_path: Path):
    if not (_config("LLM_API_KEY") and _config("LLM_BASE_URL") and _config("LLM_MODEL")):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")
    return open_runtime(tmp_path / "root", THROWAWAY_PASS, create=True,
                        market_query=_Feed(), audit=True)


def test_training_understander_reaches_the_real_endpoint(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    understander = LlmTrainingUnderstander(rt.llm, _ENDPOINT)

    draft = understander.understand("只看公告面的信息", target="")

    call = rt.gateway.query(kind="llm_call")
    assert call, "训练理解适配器未真正出网（无 llm_call 审计记录）"
    if draft is not None:                       # 端点返回合形态时逐字段可核
        assert draft.restatement and draft.pattern
        print(f"\n训练理解复述：{draft.restatement[:120]}")


def test_pattern_observer_reaches_the_real_endpoint(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    sessions = SessionStore(rt.store)
    session = sessions.create()
    for text in ("这个指标怎么算的？", "这个指标的计算口径是什么？", "这个指标怎么算？"):
        sessions.append(session.session_id, text)

    observer = LlmPatternObserver(rt.llm, _ENDPOINT, sessions=sessions)
    observations = observer.observations()

    assert rt.gateway.query(kind="llm_call"), "模式观察适配器未真正出网（无 llm_call 审计记录）"
    assert isinstance(observations, tuple)
    for item in observations:                   # 产出必附草稿（[08 §4]「附草稿」）
        assert item.draft is not None and item.key
    print(f"\n模式观察条数：{len(observations)}")
