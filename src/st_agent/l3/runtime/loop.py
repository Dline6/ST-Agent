"""循环驱动、授权端口与双上界（T-AGT-004.2；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的**四段循环**一轮：

| 段 | 本模块的落点 |
| --- | --- |
| ① 出动作 | :meth:`ToolCallProtocol.invoke_turn`（端口；模型据工具目录与已有证据选「调用某工具」或「结束」） |
| ② 过闸门 | :data:`AGENT_GATE_METHOD` 的**注入端口**（每步执行前；**缺省不注入即 fail-closed**） |
| ③ 执行 | ``SkillRunner.run``——参数 / 输入 / 输出核验、依赖 DAG、权限逐项核对、沙箱、留痕**全部沿用**，本模块**不绕过任何一环**、也不自行判参数（GWT-6） |
| ④ 回填 | 结果落 :class:`~st_agent.l3.runtime.context.WorkStep`，下一轮重新装配工作上下文 |

**三条口径**：

- **决策不入 Trace 步**（[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md)）——模型选步
  **不产步骤**，动作由 ``SkillRunner`` 自然追加 ``skill_run`` 步；本模块**一次都不追加**
  ``TraceStep``，故多步循环天然得到一条完整而**只含动作**的链（GWT-2）。
- **中间推理不外显**（[D-090](../../../项目管理/决策日志.md) ⑩）——**带工具调用的那一轮**里模型
  附带的文本一律**丢弃**（不落盘、不出站），只有模型给出「结束」的那一轮文本作为 `answer`
  交给产出装配——中性边界因此由**结构**成立，不靠事后过滤（GWT-1 / `.3` 的 GWT-4）。
- **双上界到界即停**（[D-090](../../../项目管理/决策日志.md) ⑥）——步数与 LLM 调用次数两条，
  先到者终止并**如实标注**「达到上界」，**不假装完成**；恰在第 N 步（N ＝ 步数上界）给出
  「结束」**不算**触界（GWT-4 / GWT-5）。两条取值**可注入**（:func:`run_agent_loop` 的
  ``max_steps`` / ``max_llm_calls``，带默认值），不做成模块常量——上界是预算，预算应由
  装配方决定（缺省值只为「不传也能跑」）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l0.llm import ToolCall
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.context import (
    DEFAULT_WORK_CONTEXT_BUDGET,
    RESULT_TRUNCATE_CHARS,
    WorkContext,
    WorkStep,
    assemble_work_context,
    new_work_step,
)
from st_agent.l3.runtime.protocol import (
    DEFAULT_TURN_INITIATOR,
    DEFAULT_TURN_PURPOSE,
    ToolCallProtocol,
)

__all__ = [
    "AGENT_GATE_METHOD",
    "BOUND_REASONS",
    "DEFAULT_MAX_LLM_CALLS",
    "DEFAULT_MAX_STEPS",
    "GATE_VERDICTS",
    "GateDecision",
    "GateVerdict",
    "LoopOutcome",
    "TerminationReason",
    "run_agent_loop",
]

DEFAULT_MAX_STEPS = 8
"""**步数**上界缺省值（可注入覆盖）。

取值属实现口径（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 只要求「存在且触界
如实标注」）：官方 12 个能力量级下，8 步已够查一轮证据链；再长说明方向发散了。
"""

DEFAULT_MAX_LLM_CALLS = 12
"""**LLM 调用次数**上界缺省值（可注入覆盖）。

**有意高于步数上界**：模型每执行一步要问一轮，结束还要再问一轮才拿得到收尾——两者相等会
把「收尾那一问」剪掉，让一次本该正常完成的循环被误判为触界。
"""

AGENT_GATE_METHOD = "authorize"
"""授权端口的**方法名**（供 [`T-AGT-005`](../../../项目管理/tasks/T-AGT-005-动作闸门接入.md) 注入）。

闸门本体归 [`T-AGT-005`](../../../项目管理/tasks/T-AGT-005-动作闸门接入.md)（共享风险清单 +
运行期档位）；本模块只**假定它存在**并规定调用点——每个待执行动作、**执行之前**，以
``gate.authorize(skill_id=…, tool_name=…, arguments=…)`` 问一次；返回
:class:`GateDecision`（或 verdict 字符串）方才执行。
"""

GATE_VERDICTS: tuple[str, ...] = ("autonomous-ok", "needs-confirmation", "denied")
"""闸门结论三态（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 的运行期授权条目）：
仅 ``autonomous-ok`` 自主执行；``needs-confirmation`` 终止并**交还用户**；``denied`` 终止。"""

GateVerdict = Literal["autonomous-ok", "needs-confirmation", "denied"]

TerminationReason = Literal[
    "finished",
    "step_limit",
    "llm_call_limit",
    "gate_absent",
    "needs_confirmation",
    "denied",
    "endpoint_unavailable",
    "llm_failed",
    "unknown_tool",
    "catalog_empty",
]
"""循环的终止原因（**穷尽列举**——产出与留痕据此如实标注，不造未列出的原因）。"""

BOUND_REASONS: tuple[str, ...] = ("step_limit", "llm_call_limit")
"""「达到上界」这一类终止原因（[D-090](../../../项目管理/决策日志.md) ⑥ 的如实标注口径）。"""


class GateDecision(BaseModel):
    """一次授权问询的结论（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)；闸门侧产出）。

    ``reason`` 是中性说明，供终止交还时如实告知「那一步为什么没执行」——缺省无。
    """

    model_config = ConfigDict(frozen=True)

    verdict: GateVerdict
    reason: str = ""


class LoopOutcome(BaseModel):
    """一次循环的完整产出（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的四段跑完或终止）。

    ``agent_run_id`` **不在此**——留痕与产出装配归 `.3`（本模块只管跑，不管记账）。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task: str
    endpoint_id: str
    termination: TerminationReason
    reason: str = ""
    """终止的中性说明（``finished`` 时可空；其余**必有**——不静默终止）。"""
    steps: tuple[WorkStep, ...] = ()
    """已执行的动作与结果（按发生顺序；失败步**照实**在内）。"""
    llm_calls: int = 0
    """本次循环实际发生的 LLM 调用次数（触界判定与留痕共用）。"""
    trace: Trace
    """本次循环的推理链（动作步由 ``SkillRunner`` 追加；**本模块不追加任何步**）。"""
    answer: str = ""
    """模型在**结束轮**给出的文本（中间轮次的文本已丢弃，故此处不含「为何选下一步」）。"""
    pending: ToolCall | None = None
    """被闸门拦下、**未执行**的那一步（``needs_confirmation`` / ``denied`` 时给出，供交还）。"""
    gate_reason: str = ""
    """闸门给出的原因（交还时一并告知；无闸门/无拦下时为空）。"""
    max_steps: int = DEFAULT_MAX_STEPS
    """本次生效的步数上界（留痕用；取值可注入，故如实记录实际生效值）。"""
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    """本次生效的 LLM 调用次数上界（同上）。"""
    failure: ResultEnvelope | None = None
    """``llm_failed`` 时端点侧给的信封（[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md)）
    ——**产出装配原样透出它**，不重包、不改 status、不吞 reason（既有口径）。"""

    @property
    def at_bound(self) -> bool:
        """是否因**达到上界**而终止（[D-090](../../../项目管理/决策日志.md) ⑥ 的如实标注）。"""
        return self.termination in BOUND_REASONS

    @property
    def finished(self) -> bool:
        """模型是否自行给出了「结束」。"""
        return self.termination == "finished"


def run_agent_loop(
    *,
    task: str,
    endpoint_id: str,
    protocol: ToolCallProtocol,
    runner: Any,
    tools: Sequence[Any],
    gate: Any = None,
    approved_permissions: Sequence[str] = (),
    initiator: str = DEFAULT_TURN_INITIATOR,
    purpose: str = DEFAULT_TURN_PURPOSE,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET,
    max_result_chars: int = RESULT_TRUNCATE_CHARS,
    trace: Trace | None = None,
) -> LoopOutcome:
    """驱动自主查证循环（四段一轮，直至模型给出「结束」或触界）。

    :param task: 本次循环的任务（中性转述；[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
        的 ``investigate`` 意图载荷）
    :param endpoint_id: 使用的 LLM 端点（[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)）
    :param protocol: 工具调用协议端口（T1 实现见
        :class:`~st_agent.l3.runtime.protocol.NativeToolCallProtocol`；同接口的 Fake 亦可，GWT-7）
    :param runner: 注入的 **L1 执行面**（鸭子类型 ``run(…) -> RunOutcome``，如 ``SkillRunner``）
    :param tools: 工具目录（``SkillRegistry.tool_catalog()`` 的产出）
    :param gate: 注入的**授权端口**（鸭子类型 ``authorize(…)``，如
        [`T-AGT-005`](../../../项目管理/tasks/T-AGT-005-动作闸门接入.md) 的闸门）；
        **缺省 ``None`` 即 fail-closed**——一步都不执行（GWT-3）
    :param approved_permissions: 用户已批准的权限声明集合（转交 L1 逐项核对）
    :param max_steps: **步数**上界（可注入；到界即终止并如实标注）
    :param max_llm_calls: **LLM 调用次数**上界（可注入；同上）
    :param trace: 调用方传入的推理链（``None`` 即新建一条）
    :returns: :class:`LoopOutcome`——终止原因、已执行步骤、链、以及被拦下未执行的那一步

    入参非法（上界非正 / 任务为空 / 目录形态坏）抛
    :class:`~st_agent.l3.errors.AgentRuntimeValidationError`；**运行期失败一律走
    ``ResultEnvelope``**（端点失败、单步失败），不抛裸异常。
    """
    for name, value in (("max_steps", max_steps), ("max_llm_calls", max_llm_calls)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise AgentRuntimeValidationError(
                f"{name} 须为 ≥1 的整数（上界是预算；到界即终止），得到 {value!r}"
            )
    if runner is None:
        raise AgentRuntimeValidationError("循环需要执行面（L1 SkillRunner；组合根注入）")

    chain = trace if trace is not None else Trace(trace_id=TraceId.generate())
    steps: list[WorkStep] = []
    llm_calls = 0
    by_name = {entry.name: entry for entry in tools}

    if not protocol.tool_calls_supported():
        return _outcome(
            task, endpoint_id, "endpoint_unavailable", chain, steps, llm_calls,
            reason=(
                f"端点 {endpoint_id!r} 未声明支持工具调用，自主查证循环无法运行"
                "（02 §4 能力诚实性：缺省不得假定为真）"
            ),
            max_steps=max_steps, max_llm_calls=max_llm_calls,
        )

    while True:
        # ── 上界（LLM 调用次数）：问之前判 ──
        if llm_calls >= max_llm_calls:
            return _outcome(
                task, endpoint_id, "llm_call_limit", chain, steps, llm_calls,
                reason=f"已达到 LLM 调用次数上界（{max_llm_calls}），终止",
                max_steps=max_steps, max_llm_calls=max_llm_calls,
            )

        # ── ① 出动作（工作上下文每轮重装：任务 + 目录 + 已执行动作与结果） ──
        assembled = assemble_work_context(
            task, tuple(tools), steps,
            budget_chars=budget_chars, max_result_chars=max_result_chars,
        )
        if assembled.status != "ok":
            return _outcome(
                task, endpoint_id, "catalog_empty", chain, steps, llm_calls,
                reason=assembled.reason or "",
                max_steps=max_steps, max_llm_calls=max_llm_calls,
            )
        context: WorkContext = assembled.data
        turn = protocol.invoke_turn(context, initiator=initiator, purpose=purpose)
        llm_calls += 1
        if turn.envelope is not None:
            return _outcome(
                task, endpoint_id, "llm_failed", chain, steps, llm_calls,
                reason=turn.envelope.reason or "LLM 调用失败",
                max_steps=max_steps, max_llm_calls=max_llm_calls,
                failure=turn.envelope,
            )

        # 模型给出「结束」：本轮文本是交付物的来源（中间轮次的文本不在此列）
        if not turn.tool_calls:
            return _outcome(
                task, endpoint_id, "finished", chain, steps, llm_calls,
                answer=turn.text,
                max_steps=max_steps, max_llm_calls=max_llm_calls,
            )

        # ── 上界（步数）：有动作要执行才判 —— 恰在第 N 步给出「结束」不算触界（GWT-5） ──
        if len(steps) >= max_steps:
            return _outcome(
                task, endpoint_id, "step_limit", chain, steps, llm_calls,
                reason=f"已达到步数上界（{max_steps}），终止",
                max_steps=max_steps, max_llm_calls=max_llm_calls,
            )

        # 顺序驱动（并行工具调用不在本期，D-090 冻结范围）：一轮只认第一个调用
        call = turn.tool_calls[0]
        entry = by_name.get(call.name)
        if entry is None:
            return _outcome(
                task, endpoint_id, "unknown_tool", chain, steps, llm_calls,
                reason=(
                    f"模型请求的工具 {call.name!r} 不在工具目录内，无法执行"
                    "（目录是 01 §2 描述体的投影，循环不臆造能力）"
                ),
                max_steps=max_steps, max_llm_calls=max_llm_calls,
            )

        # ── ② 过闸门（缺省 fail-closed：一步都不执行） ──
        decision = _gate_decision(gate, entry, call)
        if decision is None:
            return _outcome(
                task, endpoint_id, "gate_absent", chain, steps, llm_calls,
                reason=(
                    "未注入授权端口，按 fail-closed 拒绝执行任何动作"
                    "（闸门缺省不得放行；见 05 §10 授权闸门与交还）"
                ),
                max_steps=max_steps, max_llm_calls=max_llm_calls,
            )
        if decision.verdict != "autonomous-ok":
            termination: TerminationReason = (
                "needs_confirmation" if decision.verdict == "needs-confirmation" else "denied"
            )
            return _outcome(
                task, endpoint_id, termination, chain, steps, llm_calls,
                reason=(
                    f"下一步（调用 {call.name}）未获自主执行授权（{decision.verdict}），"
                    "终止并把该步交还用户确认"
                ),
                max_steps=max_steps, max_llm_calls=max_llm_calls,
                pending=call, gate_reason=decision.reason,
            )

        # ── ③ 执行（经 L1 既有流水线；参数/权限/沙箱/留痕全在那边判） ──
        run = runner.run(
            entry.skill_id,
            dict(call.arguments),
            trace=chain,
            approved_permissions=tuple(approved_permissions),
            initiator=initiator,
            purpose=purpose,
        )

        # ── ④ 回填（工作上下文下一轮重装时带上这一步） ──
        steps.append(new_work_step(
            tool_name=call.name,
            skill_id=entry.skill_id,
            arguments=dict(call.arguments),
            skill_run_id=run.skill_run_id,
            envelope=run.envelope,
        ))
        chain = run.trace


# ───────────────────────── 内部 ─────────────────────────


def _gate_decision(gate: Any, entry: Any, call: ToolCall) -> GateDecision | None:
    """问一次授权（无端口 → ``None``；端口返回不可识别 → fail-closed 视同拒）。"""
    if gate is None:
        return None
    authorize = getattr(gate, AGENT_GATE_METHOD, None)
    if authorize is None:
        return None
    raw = authorize(
        skill_id=entry.skill_id, tool_name=call.name, arguments=dict(call.arguments),
    )
    if isinstance(raw, GateDecision):
        return raw
    if isinstance(raw, str) and raw in GATE_VERDICTS:
        return GateDecision(verdict=raw)
    return GateDecision(
        verdict="denied",
        reason=f"授权端口返回不可识别的结论（{type(raw).__name__}），按 fail-closed 拒绝",
    )


def _outcome(
    task: str,
    endpoint_id: str,
    termination: TerminationReason,
    chain: Trace,
    steps: Sequence[WorkStep],
    llm_calls: int,
    *,
    max_steps: int,
    max_llm_calls: int,
    reason: str = "",
    answer: str = "",
    pending: ToolCall | None = None,
    gate_reason: str = "",
    failure: ResultEnvelope | None = None,
) -> LoopOutcome:
    """装配一次循环的产出（``failure`` 即端点侧给的原信封，产出装配原样透出）。"""
    return LoopOutcome(
        task=task, endpoint_id=endpoint_id, termination=termination, reason=reason,
        steps=tuple(steps), llm_calls=llm_calls, trace=chain, answer=answer,
        pending=pending, gate_reason=gate_reason,
        max_steps=max_steps, max_llm_calls=max_llm_calls, failure=failure,
    )
