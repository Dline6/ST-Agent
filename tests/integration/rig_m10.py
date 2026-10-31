"""T-INT-011 · M10 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M8 / M9 的 ``rig_m8.py`` / ``rig_m9.py`` 同一取向：真 ``Store``（口令派生、落盘加密、
清单校验）+ 真 ``MarketDb`` + 真官方 Pack + 真 L0 LLM 链路（``LlmClient`` / transport /
出网网关 / 能力协商）+ **真 L1 内置原语面**（含 MCP resources 两条）+ 真 L3 循环与 L6
授权闸门 + 真 ``McpServerRegistry``（stdio 子进程对端）。只替换**一处网络跳**：

``llm_post`` —— 脚本化模型端点（同 M5–M9 关卡），脚本支持：

- **一轮多调用**（并行工具调用的输入形态）；
- **按轮返回文本**（子 Agent 与父循环的收尾轮）；
- **动态参数**（回调按当前 prompt 生成调用，例如从截断件里取 ``skill_run_id`` 回读）。

MCP 对端用 ``tests/l1/_mcp_stub_server.py``（本机 stdio 子进程，不联网）；经
``McpServerRegistry.add_server`` 做一次真的握手 / 能力协商，与生产装配同一条路径。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import DeterministicUnderstander
from rig_m5 import ENV

from st_agent.app import M5Runtime, build_m5_runtime
from st_agent.l1.runtime import PROBE_TOOL
from st_agent.l3.intent import IntentDraft

__all__ = [
    "MCP_SERVER_ID",
    "M10Endpoint",
    "M10Rig",
    "STUB_SERVER",
    "TASK_TEXT",
    "Step",
    "first_skill_run_id",
    "latest_next_offset",
    "load_skill_run",
    "original_chars",
    "seeded_m10",
    "skill_run_ids",
]

STUB_SERVER = Path(__file__).resolve().parents[1] / "l1" / "_mcp_stub_server.py"
"""stdio 对端（本机子进程；不联网、无出网审计面）。"""

MCP_SERVER_ID = "stub"
"""MCP Server 的注册 id（与 ``tests/l1/_mcp_stub_server.py`` 的资源清单一致）。"""

TASK_TEXT = "盘点本地数据与 MCP 资源，逐项核验后形成完整证据包"
"""GWT 的用户原话（经 ``investigate`` 意图的 ``target`` 承载任务文本）。"""

Call = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class Step:
    """脚本的一轮：给出一组工具调用，或返回一段文本（空调用即「结束」）。"""

    calls: tuple[Call, ...] = ()
    text: str = ""


ScriptEntry = Step | Callable[[str], Step]
"""脚本条目；回调按**本轮 prompt** 生成下一轮（用于回读这种依赖前序结果的调用）。"""


def _text_lines(text: str) -> list[str]:
    payload = {"choices": [{"delta": {"content": text}}]}
    return [f"data: {json.dumps(payload, ensure_ascii=False)}", "data: [DONE]"]


def _call_lines(calls: Sequence[Call], *, round_index: int) -> list[str]:
    """一轮的多个工具调用（OpenAI 兼容 SSE；``index`` 区分同轮多个调用）。"""
    lines: list[str] = []
    for index, (name, arguments) in enumerate(calls):
        fragment = {"choices": [{"delta": {"tool_calls": [{
            "index": index,
            "id": f"call_{round_index}_{index}",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }]}}]}
        lines.append(f"data: {json.dumps(fragment, ensure_ascii=False)}")
    lines.append("data: [DONE]")
    return lines


class M10Endpoint:
    """脚本化端点（HTTP 发送口替身）：探测与循环轮次共用一个入口。

    判据同 M5–M9：``tools`` 里含 ``PROBE_TOOL`` 即探测；否则按 ``script`` 逐轮出动作。
    脚本用尽后回 ``finish``。动态条目（可调用）收到本轮 prompt，可据此生成下一轮调用。
    """

    def __init__(
        self,
        *,
        supported: bool = True,
        script: Sequence[ScriptEntry] | None = None,
        finish: str = "证据已收集完毕",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.supported = supported
        self._finish = finish
        self._script = list(script) if script is not None else []
        self._turn = 0

    def __call__(self, url: str, headers: Any, body: bytes, timeout_s: float):
        payload = json.loads(body)
        names = tuple(
            (entry.get("function") or {}).get("name")
            for entry in (payload.get("tools") or ())
        )
        prompt = (payload.get("messages") or [{}])[0].get("content") or ""
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "body": payload,
            "tools": names,
            "prompt": prompt,
            "tools_chars": len(json.dumps(payload.get("tools") or (), ensure_ascii=False)),
        })
        if PROBE_TOOL.name in names:
            return iter(self._probe_lines())
        index = self._turn
        self._turn += 1
        entry: ScriptEntry = (
            self._script[index] if index < len(self._script)
            else Step(text=self._finish)
        )
        step = entry(prompt) if callable(entry) else entry
        if step.calls:
            return iter(_call_lines(step.calls, round_index=index))
        return iter(_text_lines(step.text or self._finish))

    def _probe_lines(self) -> list[str]:
        if not self.supported:
            return _text_lines("好的")
        fragment = {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_probe",
            "function": {"name": PROBE_TOOL.name, "arguments": "{}"},
        }]}}]}
        return [f"data: {json.dumps(fragment, ensure_ascii=False)}", "data: [DONE]"]

    # ── 观测面 ──────────────────────────────────────────────────────────

    @property
    def probe_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if PROBE_TOOL.name in call["tools"]]

    @property
    def loop_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if PROBE_TOOL.name not in call["tools"]]


@dataclass(frozen=True)
class M10Rig:
    """一次 M10 装配的全部句柄。"""

    root: Path
    m10: M5Runtime
    feed: MarketData
    sender: RecordingSender
    understander: DeterministicUnderstander
    endpoint: M10Endpoint
    mcp_server_id: str = ""

    @property
    def store(self):
        return self.m10.store

    @property
    def gateway(self):
        return self.m10.m1.runtime.gateway


def skill_run_ids(rig: M10Rig) -> list[str]:
    """``execution_log/skill-run/`` 下的全部记录 id。"""
    prefix = "skill-run/"
    return [
        name[len(prefix):-len(".json")]
        for name in rig.store.list_files("execution_log")
        if name.startswith(prefix)
    ]


def load_skill_run(rig: M10Rig, skill_run_id: str):
    """读一条真实的 ``skill_run`` 记录（关卡断言「留痕真的落盘」）。"""
    from st_agent.l1.runner.models import SkillRun

    raw = rig.store.get("execution_log", f"skill-run/{skill_run_id}.json")
    return SkillRun.model_validate_json(raw)


def _assemble(
    root: Path,
    *,
    script: Sequence[ScriptEntry] | None,
    rules: list[tuple[str, IntentDraft]] | None,
    finish: str,
    with_mcp: bool,
    **l1_kwargs: Any,
) -> M10Rig:
    feed = MarketData()
    sender = RecordingSender()
    endpoint = M10Endpoint(script=script, finish=finish)
    understander = DeterministicUnderstander(
        _RULES if rules is None else rules
    )
    l1_kwargs.setdefault("dotenv_path", root.parent / "test.env")
    l1_kwargs.setdefault("bootstrap_file", root.parent / "test-bootstrap.json")
    runtime = build_m5_runtime(
        root, PASS,
        market_query=feed,
        sender=sender,
        llm_env=ENV,
        llm_post=endpoint,
        understander=understander,
        **l1_kwargs,
    )
    server_id = ""
    if with_mcp:
        result = runtime.m1.runtime.mcp_servers.add_server(
            MCP_SERVER_ID,
            display_name="资源存根",
            command=sys.executable,
            args=[str(STUB_SERVER)],
        )
        assert result.ok, result.reason
        server_id = MCP_SERVER_ID
    return M10Rig(
        root=root, m10=runtime, feed=feed, sender=sender,
        understander=understander, endpoint=endpoint, mcp_server_id=server_id,
    )


_RULES: list[tuple[str, IntentDraft]] = [
    ("盘点", IntentDraft(intent="investigate", target=TASK_TEXT)),
    ("核验", IntentDraft(intent="investigate", target=TASK_TEXT)),
]


def seeded_m10(
    root: Path,
    *,
    script: Sequence[ScriptEntry] | None = None,
    rules: list[tuple[str, IntentDraft]] | None = None,
    finish: str = "证据已收集完毕",
    with_mcp: bool = True,
    **l1_kwargs: Any,
) -> M10Rig:
    """已播种市场主档的 M10 全栈装配（生产组合根 ``build_m5_runtime``）。"""
    seed_market_db(root, PASS)
    return _assemble(
        root, script=script, rules=rules, finish=finish,
        with_mcp=with_mcp, **l1_kwargs,
    )


# ── 动态脚本的小工具（GWT-2 回读） ──────────────────────────────────────

SKILL_RUN_ID_RE = re.compile(r'\\?"skill_run_id\\?": \\?"(run_[0-9a-f]{20})\\?"')
NEXT_OFFSET_RE = re.compile(r'\\?"next_offset\\?": (\d+)')
ORIGINAL_CHARS_RE = re.compile(r"original_chars=(\d+)")


def latest_next_offset(prompt: str) -> int:
    """prompt 里最近一次回读的续读起点（无回读 → 0）。

    截断件在 prompt 里以 pydantic ``repr`` 形态出现（its ``preview`` 里的引号被 JSON
    转义），故正则须容忍可选的 ``\\`` 前缀——两处形态都能读到。
    """
    matches = NEXT_OFFSET_RE.findall(prompt)
    return int(matches[-1]) if matches else 0


def original_chars(prompt: str) -> int:
    """prompt 里大结果的原始字符数（截断件的 ``original_chars``；无 → 0）。"""
    match = ORIGINAL_CHARS_RE.search(prompt)
    return int(match.group(1)) if match else 0


def first_skill_run_id(prompt: str) -> str:
    """prompt 里最早出现的 ``skill_run_id``（截断件自带的那个）。"""
    match = SKILL_RUN_ID_RE.search(prompt)
    return match.group(1) if match else ""
