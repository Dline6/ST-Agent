"""T-INT-006 真实端点下的**工具调用链路**（live 用例，默认排除，不进 CI）。

证明 [02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的 `tools` 通道在**真网络**上可用：
`LlmClient.invoke(tools=…)` 的请求体真的带上了工具条目、端点的回包真的被解析成结构化
`tool_call`，且 `NativeToolCallProtocol` + `run_agent_loop` 整条循环在真端点上跑得通
（离线分支由 `tests/integration/test_m5_autonomy.py` 以脚本化端点覆盖）。

装配走**真组合根** `open_runtime`（不传 ``llm_env`` ⇒ 按环境变量 / 仓库根 ``.env`` 引导装载）——
故**启动探测**也真的跑过并写回能力档，这正是 GWT-4 的口径在真端点上的体现。

跑法：

    python -m pytest -m live -s tests/live/test_llm_tools_live.py

明文（prompt / key / 回复）只落 pytest 临时目录里的加密分区，不进 Git。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner import SkillRunner
from st_agent.l1.runtime import LLM_ENDPOINT_ID, open_runtime
from st_agent.l1.skills import SkillRegistry
from st_agent.l0.llm import ToolSpec
from st_agent.l3.runtime import (
    GateDecision,
    NativeToolCallProtocol,
    run_agent_loop,
)

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
THROWAWAY_PASS = "live-throwaway-passphrase"

SKILL_BASE = "sk_live_echo"
SKILL_ID = f"{SKILL_BASE}_v1.0"

ECHO_TOOL = ToolSpec(
    name=SKILL_BASE,
    description="回显给定的一段文本（note），用于验证工具调用通道",
    parameters={
        "type": "object",
        "properties": {"note": {"type": "string", "description": "要回显的文本"}},
    },
)


class _Feed:
    """官方 Pack 的取数面存根（本用例不取数）。"""

    def query(self, sql: str, params: tuple = ()):
        raise AssertionError("本用例不取数")

    def snapshot_id(self) -> str:
        return "live-tools"


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


def _real_runtime(tmp_path: Path):
    """按环境引导装载的真组合根（`audit=True`：断言真调用经网关留痕）。"""
    if not all(_config(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")
    return open_runtime(
        tmp_path / "root", THROWAWAY_PASS, create=True, market_query=_Feed(),
        audit=True,
    )


def _require_tool_support(rt) -> None:
    """端点必须**被探测判为**支持工具调用才继续（能力档由启动探测写回，不是缺省）。"""
    if not rt.endpoints.get(LLM_ENDPOINT_ID).capability.supports_function_calling:
        pytest.skip("该真实端点未回工具调用（不支持原生 function calling）——本用例需支持工具调用的端点")


def test_real_endpoint_accepts_tools_and_returns_parsed_calls(tmp_path: Path) -> None:
    """`invoke(tools=…)` 在真端点上不报错，且回的 `tool_call` 是**已解析的对象**。"""
    rt = _real_runtime(tmp_path)
    _require_tool_support(rt)

    events = list(rt.llm.invoke(
        LLM_ENDPOINT_ID,
        "请调用 sk_live_echo 工具，参数 note 填 'channel-ok'。不要输出其它内容。",
        initiator="live-tools", purpose="工具调用通道冒烟（T-INT-006 live）",
        tools=[ECHO_TOOL],
    ))
    assert events, "空事件流"
    if events[0].kind == "error":
        pytest.fail(
            f"带 tools 的真实调用失败：{events[0].error_envelope.status} · "
            f"{events[0].error_envelope.reason}"
        )
    assert events[-1].kind == "done"

    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    if not calls:
        pytest.skip("端点支持工具调用但本次未选择调用它（模型行为，非本链路缺陷）")
    for call in calls:
        assert call.name == SKILL_BASE
        assert isinstance(call.arguments, dict)     # 已解析的对象，不是增量串
        assert call.call_id


def test_real_endpoint_drives_the_agent_loop(tmp_path: Path) -> None:
    """整条循环在真端点上跑通：真 `SkillRunner` 执行 + 真留痕，终止原因在穷尽枚举内。"""
    rt = _real_runtime(tmp_path)
    _require_tool_support(rt)

    skills = SkillRegistry(rt.store)
    skills.register(
        SKILL_BASE, version="1.0", name="回显", description="回显给定的一段文本",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"note": {"type": "string"}}},
        parameters=({
            "name": "note", "type": "string", "default": "",
            "description": "要回显的文本",
        },),
        source="user-built",
    )
    runner = SkillRunner(rt.store, skills)
    runner.register_executor(
        SKILL_ID, lambda ctx, params: ResultEnvelope.ok({"note": params.get("note")})
    )

    class _Allow:
        """放行一切动作（本用例测链路，不测闸门语义——闸门另有离线用例）。"""

        def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
            return GateDecision(verdict="autonomous-ok")

    protocol = NativeToolCallProtocol(
        llm=rt.llm, endpoints=rt.endpoints, endpoint_id=LLM_ENDPOINT_ID,
    )
    outcome = run_agent_loop(
        task="调用 sk_live_echo 工具，参数 note 填 'loop-ok'；拿到结果后用一句话说明。",
        endpoint_id=LLM_ENDPOINT_ID, protocol=protocol, runner=runner,
        tools=skills.tool_catalog(), gate=_Allow(),
        max_steps=2, max_llm_calls=4,
    )

    assert outcome.termination in (
        "finished", "step_limit", "llm_call_limit", "unknown_tool",
    ), f"意外的终止原因：{outcome.termination} · {outcome.reason}"
    # 模型真调了工具 ⇒ 该步必须经真 L1 执行成功，且指针指向真落下的记录
    for step in outcome.steps:
        assert step.envelope.status == "ok", step.envelope.reason
        assert rt.store.get("execution_log", step.log_ref)
