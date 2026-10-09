"""T-INT-006 · M5 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M3 的 ``rig_m3.py`` / M4 的 ``rig_m4.py`` 同一取向：真实 ``Store`` + 真实 ``MarketDb`` +
真实官方 Pack + 真实 L2–L6 编排件 + **真实 L0 LLM 链路**（`LlmClient` / transport / 出网网关 /
能力协商），只把**HTTP 发送口**（``llm_post``）换成脚本化替身——故**启动探测**与**工具调用链路**
都是真跑的，只有线上那一跳是脚本（真端点路径由 ``tests/live/test_llm_tools_live.py`` 兜）。

三处与 M4 rig 的差别：

1. **端点经引导装载真播种 + 真探测**（``llm_env`` / ``llm_post``）：能力档 ``supports_function_calling``
   由探测写回，故 GWT-4 的两次断言取的都是**探测结果**，不是 ``LLM_CAPABILITY`` 的缺省；
2. **两个官方 base 装确定性执行件**（``register_executor``）：既保住「工具目录＝描述体投影」
   的真实性（描述体来自官方 Pack），又让执行结果离线可复算、且各步**恰好一条 ``skill_run`` 步**；
3. **确定性意图理解器**驱动 ``investigate`` 全流程（同 M1 的离线口径）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import DeterministicUnderstander

from st_agent.app import M5Runtime, build_m5_runtime
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runtime import PROBE_TOOL
from st_agent.l3.intent import IntentDraft

ENV: dict[str, str] = {
    "LLM_API_KEY": "sk-test-0123456789-abcdef",
    "LLM_BASE_URL": "https://api.example.com/v1",
    "LLM_MODEL": "test-model",
}
"""引导装载三键（`llm_env`）——端点由播种建、能力由探测写（``llm_post`` 拦截线上那一跳）。"""

SK_A = "sk_data_aggregate"
"""第一步调用的能力（官方 base；``dependencies=()`` ⇒ 一次调用＝一条 ``skill_run`` 步）。"""

SK_B = "sk_sector_heatmap"
"""第二步调用的能力（同 A，故两步共**恰好两条** ``skill_run`` 步）。"""

INVESTIGATE_QUERY = IntentDraft(
    intent="investigate", target="查证 sh.600000 的 ST 状态与退市风险"
)
"""`investigate` 意图草案——任务文本经 ``target`` 承载（[05 §3.2] 无 target Skill 的意图）。"""

DEFAULT_RULES: list[tuple[str, IntentDraft]] = [
    ("查证", INVESTIGATE_QUERY),
    ("调研", INVESTIGATE_QUERY),
]

__all__ = [
    "DEFAULT_RULES",
    "ENV",
    "INVESTIGATE_QUERY",
    "SK_A",
    "SK_B",
    "M5Rig",
    "ScriptedEndpoint",
    "register_demo_skills",
    "seeded_m5",
]


def _tool_call_lines(name: str, arguments: dict, *, call_id: str) -> list[str]:
    """一条**拼完**的工具调用（OpenAI 兼容 SSE 形态；同 ``test_runtime_llm_probe`` 的写法）。"""
    fragment = {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": call_id,
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }]}}]}
    return [f"data: {json.dumps(fragment, ensure_ascii=False)}", "data: [DONE]"]


def _text_lines(text: str) -> list[str]:
    """纯文本回应（探测的「静默忽略 `tools`」反例、循环的「结束」轮都用它）。"""
    payload = {"choices": [{"delta": {"content": text}}]}
    return [f"data: {json.dumps(payload, ensure_ascii=False)}", "data: [DONE]"]


class ScriptedEndpoint:
    """脚本化端点（HTTP 发送口替身）：**探测与循环轮次共用一个入口**。

    判据取 ``tools`` 里有没有 ``PROBE_TOOL``——探测的 ``tools`` 只有探针一枚，循环的
    ``tools`` 是整份工具目录（十余个官方 base + 探针不在其中）。

    - **探测**：``supported=True`` ⇒ 真回一条工具调用（能力档置 ``true``）；``False`` ⇒ 回纯文本
      （02 §4 的「静默忽略」反例，判**不支持**）。
    - **循环**：按 ``script`` 逐轮回工具调用，脚本用尽即回结束文本。
    """

    def __init__(
        self,
        *,
        supported: bool = True,
        script: list[tuple[str, dict]] | None = None,
        finish: str = "证据已收集完毕",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.supported = supported
        self._finish = finish
        self._script = list(script) if script is not None else [
            (SK_A, {"format": "brief"}),
            (SK_B, {"top_n": 5}),
        ]
        self._turn = 0

    def __call__(self, url: str, headers: Any, body: bytes, timeout_s: float):
        payload = json.loads(body)
        names = tuple(
            (entry.get("function") or {}).get("name")
            for entry in (payload.get("tools") or ())
        )
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "body": payload,
            "tools": names,
            "prompt": (payload.get("messages") or [{}])[0].get("content") or "",
        })
        if PROBE_TOOL.name in names:
            return iter(self._probe_lines())
        index = self._turn
        self._turn += 1
        if index < len(self._script):
            name, arguments = self._script[index]
            return iter(_tool_call_lines(name, arguments, call_id=f"call_{index + 1}"))
        return iter(_text_lines(self._finish))

    def _probe_lines(self) -> list[str]:
        if not self.supported:
            return _text_lines("好的")
        return _tool_call_lines(PROBE_TOOL.name, {}, call_id="call_probe")

    # ── 观测面 ──────────────────────────────────────────────────────────

    @property
    def probe_calls(self) -> list[dict[str, Any]]:
        """探测发出的调用（GWT-4 / GWT-7 断言「探测真的跑过」）。"""
        return [call for call in self.calls if PROBE_TOOL.name in call["tools"]]

    @property
    def loop_calls(self) -> list[dict[str, Any]]:
        """循环轮次的调用（GWT-1 断言「第二轮的上下文里已有第一步的结果」）。"""
        return [call for call in self.calls if PROBE_TOOL.name not in call["tools"]]


def register_demo_skills(m5: M5Runtime) -> None:
    """给两个官方 base 装**确定性执行件**（真 L1 流水线：参数 / 权限 / 沙箱 / 留痕全沿用）。

    描述体仍取自官方 Pack（工具目录因此是真投影），只有「算出来的结果」是脚本——
    与 ``tests/l3/test_agent_loop.py`` 对 ``sk_data_aggregate`` / ``sk_sector_heatmap``
    的注入口径一致。
    """
    runner = m5.m1.runtime.runner
    runner.register_executor(
        f"{SK_A}_v1.0",
        lambda ctx, params: ResultEnvelope.ok(
            {"cards": [{"step": "A", "format": params.get("format")}]}
        ),
    )
    runner.register_executor(
        f"{SK_B}_v1.0",
        lambda ctx, params: ResultEnvelope.ok(
            {"heatmap": {"step": "B", "top_n": params.get("top_n")}}
        ),
    )


@dataclass(frozen=True)
class M5Rig:
    """一次 M5 装配的全部句柄。"""

    root: Path
    m5: M5Runtime
    feed: MarketData
    sender: RecordingSender
    understander: DeterministicUnderstander
    endpoint: ScriptedEndpoint


def seeded_m5(
    root: Path,
    *,
    supported: bool = True,
    script: list[tuple[str, dict]] | None = None,
    rules: list[tuple[str, IntentDraft]] | None = None,
    register_skills: bool = True,
    **l1_kwargs: Any,
) -> M5Rig:
    """已播种数据面的 M5 全栈装配（生产组合根 `build_m5_runtime` + 脚本化端点）。

    ``supported=False`` ⇒ 探测判「不支持工具调用」（GWT-4 的反支）；``script`` 覆写循环轮次
    的脚本；``register_skills=False`` ⇒ 不装示例执行件（供「缺任务文本」一类不跑循环的用例）。
    """
    seed_market_db(root, PASS)
    feed = MarketData()
    sender = RecordingSender()
    endpoint = ScriptedEndpoint(supported=supported, script=script)
    understander = DeterministicUnderstander(
        DEFAULT_RULES if rules is None else rules
    )
    m5 = build_m5_runtime(
        root, PASS, market_query=feed, sender=sender, llm_env=ENV, llm_post=endpoint,
        understander=understander, **l1_kwargs,
    )
    if register_skills:
        register_demo_skills(m5)
    return M5Rig(
        root=root, m5=m5, feed=feed, sender=sender,
        understander=understander, endpoint=endpoint,
    )
