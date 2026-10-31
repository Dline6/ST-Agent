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

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l0.llm import ToolCall
from st_agent.l1.builtin.ask_user import ASK_USER, normalize_questions
from st_agent.l1.builtin.plan_update import PLAN_UPDATE, normalize_plan
from st_agent.l1.builtin.spawn import SPAWN_AGENT, normalize_spawn_task
from st_agent.l1.skills.tool_catalog import split_arguments
from st_agent.l2.memory.reader import MemorySlice
from st_agent.l3.chat.session import ChatMessage
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.context import (
    DEFAULT_WORK_CONTEXT_BUDGET,
    RESULT_TRUNCATE_CHARS,
    AskQuestion,
    PlanStep,
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
from st_agent.l3.runtime.tool_search import (
    TOOL_SEARCH_TOOL,
    ToolSearchRecord,
    normalize_limit,
    normalize_query,
    search_catalog,
)

__all__ = [
    "AGENT_GATE_METHOD",
    "ASK_USER_TOOL",
    "BOUND_REASONS",
    "DEFAULT_MAX_LLM_CALLS",
    "DEFAULT_MAX_STEPS",
    "GATE_VERDICTS",
    "NestedRecorder",
    "NestedRunRef",
    "PLAN_UPDATE_TOOL",
    "SPAWN_AGENT_TOOL",
    "GateDecision",
    "GateVerdict",
    "LoopOutcome",
    "SubAgentRun",
    "TerminationReason",
    "render_subagent_note",
    "resolve_approved",
    "run_agent_loop",
]

ApprovedPermissions = (
    Sequence[str] | Mapping[str, Sequence[str]] | Callable[[str], Sequence[str]]
)
"""「已批准权限」的供给形态（T-AGT-014.3）。

- **序列**：全局同意（缺省；既有调用方的形态，行为不变）；
- **映射**：``{skill_id: 声明序列}``；
- **可调用**：``(skill_id) -> 声明序列``——组合根接 `SkillPermissionBook`，
  按**实际执行的 Skill** 取批准态，避免把 A 的批准借给 B。
"""

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

PLAN_UPDATE_TOOL = PLAN_UPDATE.base
"""计划原语的工具名（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

循环按**工具名**识别这一条控制原语：它的效果是改工作上下文里的计划，**不经 L1 执行面**
（不产 ``skill_run`` 记录、不产 Trace 步，[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md)）。
登记进工具目录的目的是让模型看得见、让闸门照常问——**不是**第二条执行路径。
"""

ASK_USER_TOOL = ASK_USER.base
"""提问原语的工具名（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

循环按工具名识别：它的效果是**终止并交还结构化问项**，同样不经 L1 执行面。
"""

SPAWN_AGENT_TOOL = SPAWN_AGENT.base
"""派生子 Agent 原语的工具名（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

循环按工具名识别：它在**隔离子上下文**里再跑一次本循环，只把结论与引用带回主上下文。
派生本身是**决策式控制动作**（不产 Trace 步 / ``skill_run``，同计划原语），子循环的动作
另起一条 Trace（[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md)）。
"""

GateVerdict = Literal["autonomous-ok", "needs-confirmation", "denied"]

TerminationReason = Literal[
    "finished",
    "step_limit",
    "llm_call_limit",
    "gate_absent",
    "needs_confirmation",
    "needs_user_input",
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


class NestedRunRef(BaseModel):
    """一次子 Agent 运行落痕后的**定位串**（由注入的记录端口返回）。

    L3 循环本体不持存储，故「把子循环落成 ``agent_run``」由组合根注入的记录端口完成；
    端口回传子运行的 ``agent_run_id`` 与留痕路径，供主产出引用与 ``explain`` 展开。
    """

    model_config = ConfigDict(frozen=True)

    agent_run_id: str
    log_ref: str


class SubAgentRun(BaseModel):
    """一次子 Agent 派生的**回传摘要**（结论 + 引用；不携子步骤明细）。

    子步骤明细在子循环自己的 Trace 与 ``skill_run`` 记录里，以 ``trace_id`` /
    ``agent_run_id`` 定位；本摘要只把主上下文与主产出需要的东西带回来。
    """

    model_config = ConfigDict(frozen=True)

    task: str
    agent_run_id: str
    trace_id: str
    termination: str
    reason: str = ""
    answer: str = ""
    evidence: tuple[EvidenceRef, ...] = ()
    steps: int = 0
    llm_calls: int = 0


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
    session_id: str | None = None
    """本轮的**会话标识**（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的会话身份进
    派发载荷；本叶只透传与留痕，不据此取历史）。``None`` ⇒ 无历史的单次查证，由产出装配显式标注。"""
    context_note: str = ""
    """上下文形态的**显式标注**（无会话 / 无历史 / 取舍说明等）——与 ``reason`` 分开：
    `reason` 讲终止原因，本字段讲「模型看到的是什么上下文」，两者不互相顶替。"""
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
    plan: tuple[PlanStep, ...] = ()
    """模型当前给出的计划（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的计划原语）。"""
    plan_updated: bool = False
    """模型是否给过计划更新（含清空）——区分「尚未给出」与「已显式清空」。"""
    plan_error: str = ""
    """最后一次计划更新未被采用的原因（非法形态不终止循环，留给下一轮纠正）。"""
    questions: tuple[AskQuestion, ...] = ()
    """交还的结构化问项（``needs_user_input`` 时给出；其余终止为空）。"""
    directions: tuple[str, ...] = ()
    """交还时给的方向候选（[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：不强行猜）。"""
    ask_note: str = ""
    """提问交还的说明（如问项超预算被截断的显式记录；无则空）。"""
    max_steps: int = DEFAULT_MAX_STEPS
    """本次生效的步数上界（留痕用；取值可注入，故如实记录实际生效值）。"""
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    """本次生效的 LLM 调用次数上界（同上）。"""
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET
    """本次生效的**工作上下文预算**（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
    的 `investigate.context_budget`；记实际生效值，供留痕核对）。"""
    dropped: tuple[str, ...] = ()
    """被预算**舍弃**的上下文项（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：
    超限如实取舍并标注；末次装配的取值）。"""
    tool_searches: tuple[ToolSearchRecord, ...] = ()
    """本次循环里的工具检索记录（**决策**，不产 Trace 步 / ``skill_run``；进 ``agent_run``）。"""
    failure: ResultEnvelope | None = None
    """``llm_failed`` 时端点侧给的信封（[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md)）
    ——**产出装配原样透出它**，不重包、不改 status、不吞 reason（既有口径）。"""
    parent_trace_id: str = ""
    """子循环派生的父链 ``trace_id``（主循环为空）——子链与父链因此可关联（[01 §4]）。"""
    executed_steps: int = 0
    """本次循环**实际消耗**的步数（含子 Agent 内的步；可见 ``steps`` 不含子步骤）。"""
    parallel_used: bool = False
    """本次循环是否**真的**并行执行过一批调用（串行回退时为假）。"""
    parallel_note: str = ""
    """并发形态的显式标注（并行 / 因底层未声明并发安全而退回串行）。"""
    sub_agents: tuple[SubAgentRun, ...] = ()
    """本次循环派生的子 Agent 摘要（按派生顺序；子步骤不在 ``steps`` 内）。"""

    @property
    def at_bound(self) -> bool:
        """是否因**达到上界**而终止（[D-090](../../../项目管理/决策日志.md) ⑥ 的如实标注）。"""
        return self.termination in BOUND_REASONS

    @property
    def finished(self) -> bool:
        """模型是否自行给出了「结束」。"""
        return self.termination == "finished"


NestedRecorder = Callable[[LoopOutcome], NestedRunRef]
"""子循环的落痕端口（组合根注入；缺省未注入即派生显式不可用）。"""


def resolve_approved(source: ApprovedPermissions, skill_id: str) -> tuple[str, ...]:
    """按供给形态取出该 Skill 本次的已批准权限集（空即空，不猜）。"""
    if source is None:
        return ()
    if callable(source):
        return tuple(source(skill_id) or ())
    if isinstance(source, Mapping):
        return tuple(source.get(skill_id) or ())
    return tuple(source)


def run_agent_loop(
    *,
    task: str,
    endpoint_id: str,
    protocol: ToolCallProtocol,
    runner: Any,
    tools: Sequence[Any],
    gate: Any = None,
    approved_permissions: ApprovedPermissions = (),
    initiator: str = DEFAULT_TURN_INITIATOR,
    purpose: str = DEFAULT_TURN_PURPOSE,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET,
    max_result_chars: int = RESULT_TRUNCATE_CHARS,
    tool_lazy_chars: int | None = None,
    trace: Trace | None = None,
    session_id: str | None = None,
    history: Sequence[ChatMessage] = (),
    memory: Sequence[MemorySlice] = (),
    prior_steps: Sequence[WorkStep] = (),
    plan: Sequence[PlanStep] = (),
    plan_updated: bool = False,
    user_inputs: dict[str, Any] | None = None,
    parallel_safe: bool = False,
    allow_sub_agents: bool = True,
    nested_recorder: NestedRecorder | None = None,
    parent_trace_id: str = "",
    prior_sub_agents: Sequence[SubAgentRun] = (),
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
    :param approved_permissions: 用户已批准的权限声明（转交 L1 逐项核对）。
        可为序列（全局）、映射或可调用 ``(skill_id) -> 声明序列``——后者由组合根
        按实际执行的 Skill 取批准态（T-AGT-014.3）
    :param max_steps: **步数**上界（可注入；到界即终止并如实标注）
    :param max_llm_calls: **LLM 调用次数**上界（可注入；同上）
    :param trace: 调用方传入的推理链（``None`` 即新建一条）
    :param session_id: 本次循环所属会话（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）；
        ``None`` ⇒ 无历史的单次查证（本叶只透传与留痕，取历史归 `.2`）
    :param history: 近端会话历史（组合根预取后注入——循环本体**不读存储**，保持可单测）
    :param memory: 记忆切片（同上；受 ``budget_chars`` 约束，超限时先被舍）
    :param prior_steps: **续跑承接的前序步骤**（已执行过、不再重跑；只进工作上下文与留痕，
        **不计入**本次新循环的步数上界——[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的续跑口径）
    :param plan: 续跑承接的计划（同 ``prior_steps``）
    :param plan_updated: 续跑前的计划是否已给出过（区分「尚未给出」与「已清空」）
    :param user_inputs: 续跑时用户补充的答案（一问一参；进工作上下文，恒保留）
    :param tool_lazy_chars: 工具目录的**惰性阈值**（工具段字符数；``None`` 即取本次预算）。
        超阈值时只给摘要与检索入口，模型经 ``sk_tool_search`` 按需展开（T-AGT-017.1）
    :param parallel_safe: 底层（执行面 / 存储 / 原语）是否**已声明并发安全**——
        **缺省 ``False``**：一轮多个调用显式退回串行并在产出标注；装配方核验后才可置 ``True``
    :param allow_sub_agents: 本循环是否允许派生子 Agent（子循环置 ``False``，只允许一层）
    :param nested_recorder: 子循环的**落痕端口**（组合根注入；缺省即派生显式不可用）
    :param parent_trace_id: 子循环的父链 ``trace_id``（主循环留空；只进留痕，不改 Trace 结构）
    :param prior_sub_agents: 续跑承接的**子 Agent 摘要**（取自旧 ``agent_run`` 留痕；
        子步骤不重建，但结论、引用与留痕链接仍进工作上下文与本次留痕）
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
    if tool_lazy_chars is not None and (
        not isinstance(tool_lazy_chars, int) or isinstance(tool_lazy_chars, bool)
        or tool_lazy_chars < 1
    ):
        raise AgentRuntimeValidationError(
            f"工具惰性阈值须为 ≥1 的整数，得到 {tool_lazy_chars!r}"
        )
    for name, value in (("parallel_safe", parallel_safe), ("allow_sub_agents", allow_sub_agents)):
        if not isinstance(value, bool):
            raise AgentRuntimeValidationError(f"{name} 须为布尔值，得到 {value!r}")
    if nested_recorder is not None and not callable(nested_recorder):
        raise AgentRuntimeValidationError("子 Agent 的落痕端口须为可调用对象（或 None）")

    chain = trace if trace is not None else Trace(trace_id=TraceId.generate())
    steps: list[WorkStep] = list(prior_steps)
    executed = 0
    """**本次新执行**的步数（续跑承接的前序步骤不计入上界）。"""
    llm_calls = 0
    dropped: tuple[str, ...] = ()
    current_plan: tuple[PlanStep, ...] = tuple(plan)
    plan_given = bool(plan_updated)
    plan_error = ""
    by_name = {entry.name: entry for entry in tools}
    retrieved: list[str] = []
    """已按检索展开的工具名（惰性态下完整参数只给这些条目；按首次命中顺序）。"""
    searches: list[ToolSearchRecord] = []
    tool_search_note = ""
    sub_runs: list[SubAgentRun] = list(prior_sub_agents)
    subagent_notes: list[str] = [render_subagent_note(sub) for sub in prior_sub_agents]
    parallel_notes: list[str] = []
    parallel_used = False

    def finish(
        termination: TerminationReason,
        *,
        reason: str = "",
        answer: str = "",
        pending: ToolCall | None = None,
        gate_reason: str = "",
        failure: ResultEnvelope | None = None,
        questions: Sequence[AskQuestion] = (),
        directions: Sequence[str] = (),
        ask_note: str = "",
    ) -> LoopOutcome:
        """装配终止产出——**计划与上下文状态随每次终止照实带走**。"""
        return _outcome(
            task, endpoint_id, termination, chain, steps, llm_calls,
            reason=reason, answer=answer, pending=pending, gate_reason=gate_reason,
            failure=failure,
            max_steps=max_steps, max_llm_calls=max_llm_calls, session_id=session_id,
            dropped=dropped, budget_chars=budget_chars,
            plan=current_plan, plan_updated=plan_given, plan_error=plan_error,
            questions=tuple(questions), directions=tuple(directions), ask_note=ask_note,
            tool_searches=tuple(searches),
            parent_trace_id=parent_trace_id, executed_steps=executed,
            parallel_used=parallel_used, parallel_note="；".join(parallel_notes),
            sub_agents=tuple(sub_runs),
        )

    if not protocol.tool_calls_supported():
        return finish(
            "endpoint_unavailable",
            reason=(
                f"端点 {endpoint_id!r} 未声明支持工具调用，自主查证循环无法运行"
                "（02 §4 能力诚实性：缺省不得假定为真）"
            ),
        )

    while True:
        # ── 上界（LLM 调用次数）：问之前判 ──
        if llm_calls >= max_llm_calls:
            return finish(
                "llm_call_limit",
                reason=f"已达到 LLM 调用次数上界（{max_llm_calls}），终止",
            )

        # ── ① 出动作（工作上下文每轮重装：任务 + 计划 + 目录 + 已执行动作与结果） ──
        assembled = assemble_work_context(
            task, tuple(tools), steps,
            history=history, memory=memory,
            plan=current_plan, plan_updated=plan_given, plan_error=plan_error,
            user_inputs=user_inputs,
            budget_chars=budget_chars, max_result_chars=max_result_chars,
            retrieved_tools=tuple(retrieved), tool_search_note=tool_search_note,
            subagent_note="\n".join(subagent_notes),
            tool_lazy_chars=tool_lazy_chars,
        )
        if assembled.status != "ok":
            return finish("catalog_empty", reason=assembled.reason or "")
        context: WorkContext = assembled.data
        dropped = context.dropped
        turn = protocol.invoke_turn(context, initiator=initiator, purpose=purpose)
        llm_calls += 1
        if turn.envelope is not None:
            return finish(
                "llm_failed",
                reason=turn.envelope.reason or "LLM 调用失败",
                failure=turn.envelope,
            )

        # 模型给出「结束」：本轮文本是交付物的来源（中间轮次的文本不在此列）
        if not turn.tool_calls:
            return finish("finished", answer=turn.text)

        # ── 本轮多调用：先解析（检索入口惰性态不过闸门；未知工具在预算判定后终止）──
        calls = tuple(turn.tool_calls)
        planned: list[tuple[ToolCall, Any]] = []
        unknown: ToolCall | None = None
        executable_count = 0
        for call in calls:
            if call.name == TOOL_SEARCH_TOOL and context.lazy_tools:
                planned.append((call, None))       # 上下文控制：不占步数、不过闸门
                continue
            entry = by_name.get(call.name)
            if entry is None:
                unknown = call
                continue
            planned.append((call, entry))
            if call.name not in (PLAN_UPDATE_TOOL, ASK_USER_TOOL, SPAWN_AGENT_TOOL):
                executable_count += 1

        # ── 上界（步数）：有动作要执行才判；一轮多调用按**多个动作**计 ──
        # 单调用时与既有「到界即停」等价；检索入口不占步数（同既有）。
        if any(entry is not None for _, entry in planned) or unknown is not None:
            if executed >= max_steps or executed + executable_count > max_steps:
                return finish(
                    "step_limit",
                    reason=f"已达到步数上界（{max_steps}），终止",
                )
        if unknown is not None:
            return finish(
                "unknown_tool",
                reason=(
                    f"模型请求的工具 {unknown.name!r} 不在工具目录内，无法执行"
                    "（目录是 01 §2 描述体的投影，循环不臆造能力）"
                ),
            )

        # ── 控制面：按调用顺序逐个过闸门（在任何执行之前）──
        # 检索入口（惰性态）不过闸门、只改上下文；计划 / 提问 / 派生子 Agent 同属控制动作。
        batch: list[tuple[ToolCall, Any]] = []
        for index, (call, entry) in enumerate(planned):
            # 终止交还时点名「同轮其余未执行」：已收进 batch 的可执行调用 + 之后未处理的调用
            rest = len(batch) + sum(
                1 for _later_call, later_entry in planned[index + 1:]
                if later_entry is not None
            )
            if entry is None:
                tool_search_note = _apply_tool_search(
                    call, tuple(tools), retrieved, searches,
                )
                continue

            # 控制动作可能在本轮内消耗预算（如派生子 Agent），故每个动作前**再看一次**上界
            if executed >= max_steps:
                return finish(
                    "step_limit",
                    reason=f"已达到步数上界（{max_steps}），终止",
                )

            decision = _gate_decision(gate, entry, call)
            if decision is None:
                return finish(
                    "gate_absent",
                    reason=(
                        "未注入授权端口，按 fail-closed 拒绝执行任何动作"
                        "（闸门缺省不得放行；见 05 §10 授权闸门与交还）"
                    ),
                )
            if decision.verdict != "autonomous-ok":
                termination: TerminationReason = (
                    "needs_confirmation" if decision.verdict == "needs-confirmation"
                    else "denied"
                )
                reason = (
                    f"下一步（调用 {call.name}）未获自主执行授权（{decision.verdict}），"
                    "终止并把该步交还用户确认"
                )
                if rest:
                    reason += f"；同轮其余 {rest} 个调用未执行（不静默丢）"
                return finish(
                    termination, reason=reason,
                    pending=call, gate_reason=decision.reason,
                )

            if call.name == PLAN_UPDATE_TOOL:
                normalized, error = normalize_plan(dict(call.arguments).get("plan"))
                if error:
                    plan_error = error
                else:
                    plan_error = ""
                    plan_given = True
                    current_plan = tuple(
                        PlanStep(title=item["title"], status=item["status"])
                        for item in normalized
                    )
                continue

            if call.name == ASK_USER_TOOL:
                arguments = dict(call.arguments)
                ask = normalize_questions(
                    arguments.get("questions"), arguments.get("directions"),
                )
                questions = tuple(AskQuestion(**item) for item in ask.questions)
                ask_note = ask.note or ask.error
                if rest:
                    ask_note = _join_note(ask_note, f"同轮其余 {rest} 个调用未执行")
                return finish(
                    "needs_user_input",
                    reason=ask.error or "模型请求用户补充信息，循环终止并交还结构化问项",
                    questions=questions,
                    directions=ask.directions,
                    ask_note=ask_note,
                )

            if call.name == SPAWN_AGENT_TOOL:
                if not allow_sub_agents:
                    return finish(
                        "unknown_tool",
                        reason=(
                            "子 Agent 只允许一层：本次循环不提供 sk_spawn_agent"
                            "（更深探索请由主循环决定）"
                        ),
                    )
                task_text, task_error = normalize_spawn_task(
                    dict(call.arguments).get("task")
                )
                executed += 1                      # 派生本身占一步（含形态非法）
                if task_error:
                    subagent_notes.append(f"子 Agent 派生未执行：{task_error}")
                    continue
                if nested_recorder is None:
                    subagent_notes.append(
                        "子 Agent 派生未执行：组合根未注入子循环留痕端口"
                        "（L3 不持存储，不假跑）"
                    )
                    continue
                remaining_steps = max_steps - executed
                remaining_llm = max_llm_calls - llm_calls
                if remaining_steps < 1:
                    subagent_notes.append(
                        "子 Agent 派生未执行：步数预算已用尽（派生本身占 1 步）"
                    )
                    continue
                if remaining_llm < 1:
                    subagent_notes.append("子 Agent 派生未执行：LLM 调用次数预算已用尽")
                    continue
                child = run_agent_loop(
                    task=task_text,
                    endpoint_id=endpoint_id,
                    protocol=protocol,
                    runner=runner,
                    tools=tuple(item for item in tools if item.name != SPAWN_AGENT_TOOL),
                    gate=gate,
                    approved_permissions=approved_permissions,
                    initiator=initiator,
                    purpose=purpose,
                    max_steps=remaining_steps,
                    max_llm_calls=remaining_llm,
                    budget_chars=budget_chars,
                    max_result_chars=max_result_chars,
                    tool_lazy_chars=tool_lazy_chars,
                    trace=Trace(trace_id=TraceId.generate()),
                    session_id=None,
                    history=(),
                    memory=memory,
                    parallel_safe=parallel_safe,
                    allow_sub_agents=False,
                    nested_recorder=None,
                    parent_trace_id=chain.trace_id.value,
                )
                ref = nested_recorder(child)
                evidence = tuple(
                    EvidenceRef(kind="skill_run_id", ref=step.skill_run_id)
                    for step in child.steps
                    if step.envelope.status == "ok"
                )
                child_steps = child.executed_steps or len(child.steps)
                sub = SubAgentRun(
                    task=task_text,
                    agent_run_id=ref.agent_run_id,
                    trace_id=child.trace.trace_id.value,
                    termination=child.termination,
                    reason=child.reason,
                    answer=child.answer,
                    evidence=evidence,
                    steps=child_steps,
                    llm_calls=child.llm_calls,
                )
                sub_runs.append(sub)
                executed += child_steps
                llm_calls += child.llm_calls
                subagent_notes.append(render_subagent_note(sub))
                continue

            batch.append((call, entry))

        if not batch:
            continue

        # 控制动作可能已消耗预算，执行前按**当前**已执行步数再判一次（不超上界）
        if executed + len(batch) > max_steps:
            return finish(
                "step_limit",
                reason=f"已达到步数上界（{max_steps}），终止",
            )

        # ── ③④ 执行与回填 —— 并发安全须由装配方**显式声明**，未声明即退回串行并标注 ──
        if len(batch) == 1 or not parallel_safe:
            if len(batch) > 1:
                parallel_notes.append(
                    f"本轮 {len(batch)} 个调用按顺序串行执行"
                    "（底层未声明并发安全，显式退回串行）"
                )
            for call, entry in batch:
                values, object_inputs = split_arguments(entry, dict(call.arguments))
                run = runner.run(
                    entry.skill_id,
                    values,
                    inputs=object_inputs,
                    trace=chain,
                    approved_permissions=resolve_approved(
                        approved_permissions, entry.skill_id),
                    initiator=initiator,
                    purpose=purpose,
                )
                steps.append(new_work_step(
                    tool_name=call.name,
                    skill_id=entry.skill_id,
                    arguments=dict(call.arguments),
                    skill_run_id=run.skill_run_id,
                    envelope=run.envelope,
                ))
                chain = run.trace
                executed += 1
            continue

        parallel_used = True
        parallel_notes.append(f"本轮 {len(batch)} 个调用已并行执行")
        prepared = [
            (
                call, entry,
                *split_arguments(entry, dict(call.arguments)),
                resolve_approved(approved_permissions, entry.skill_id),
            )
            for call, entry in batch
        ]
        with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
            futures = [
                pool.submit(
                    runner.run,
                    entry.skill_id,
                    values,
                    inputs=object_inputs,
                    trace=chain,
                    approved_permissions=approved,
                    initiator=initiator,
                    purpose=purpose,
                )
                for _call, entry, values, object_inputs, approved in prepared
            ]
            runs = [future.result() for future in futures]

        # 各分支以同一条 frozen base 起跑，故按**调用顺序**把各分支新增步 append 回主链
        # ——完成先后不影响上下文与 Trace 的顺序（05 §10）。
        base_len = len(chain.steps)
        merged = chain
        for (call, entry, _values, _inputs, _approved), run in zip(prepared, runs):
            if run.trace.trace_id != chain.trace_id:
                raise AgentRuntimeValidationError(
                    "并行分支返回的 Trace 与本次循环的链不一致"
                    f"（{run.trace.trace_id.value!r} ≠ {chain.trace_id.value!r}）"
                )
            for step in run.trace.steps[base_len:]:
                merged = merged.append_step(step)
            steps.append(new_work_step(
                tool_name=call.name,
                skill_id=entry.skill_id,
                arguments=dict(call.arguments),
                skill_run_id=run.skill_run_id,
                envelope=run.envelope,
            ))
        chain = merged
        executed += len(batch)


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
    session_id: str | None = None,
    context_note: str = "",
    dropped: tuple[str, ...] = (),
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET,
    plan: Sequence[PlanStep] = (),
    plan_updated: bool = False,
    plan_error: str = "",
    questions: Sequence[AskQuestion] = (),
    directions: Sequence[str] = (),
    ask_note: str = "",
    tool_searches: Sequence[ToolSearchRecord] = (),
    parent_trace_id: str = "",
    executed_steps: int = 0,
    parallel_used: bool = False,
    parallel_note: str = "",
    sub_agents: Sequence[SubAgentRun] = (),
) -> LoopOutcome:
    """装配一次循环的产出（``failure`` 即端点侧给的原信封，产出装配原样透出）。"""
    return LoopOutcome(
        task=task, endpoint_id=endpoint_id, termination=termination, reason=reason,
        steps=tuple(steps), llm_calls=llm_calls, trace=chain, answer=answer,
        pending=pending, gate_reason=gate_reason,
        max_steps=max_steps, max_llm_calls=max_llm_calls, session_id=session_id,
        context_note=context_note, dropped=dropped, budget_chars=budget_chars,
        failure=failure,
        plan=tuple(plan), plan_updated=plan_updated, plan_error=plan_error,
        questions=tuple(questions), directions=tuple(directions), ask_note=ask_note,
        tool_searches=tuple(tool_searches),
        parent_trace_id=parent_trace_id, executed_steps=executed_steps,
        parallel_used=parallel_used, parallel_note=parallel_note,
        sub_agents=tuple(sub_agents),
    )


def _join_note(*parts: str) -> str:
    """把若干显式说明拼成一条（空串跳过；分隔与既有留痕口径一致）。"""
    return "；".join(part for part in parts if part)


def render_subagent_note(sub: SubAgentRun) -> str:
    """子 Agent 回传段（结论 + 引用 + 留痕定位；中性措辞，不外显子步骤）。

    同一份渲染供**本次运行**与**续跑承接**共用——续跑从旧留痕的 ``sub_agents`` 重建
    这段说明，不重跑子 Agent、也不复制子步骤。
    """
    pieces = [f"子 Agent「{sub.task}」"]
    if sub.termination == "finished":
        pieces.append("已完成")
        if sub.answer:
            pieces.append(f"结论：{sub.answer}")
    else:
        pieces.append(f"未完成（终止原因 {sub.termination}）")
        if sub.reason:
            pieces.append(f"原因：{sub.reason}")
    if sub.evidence:
        pieces.append("引用：" + "、".join(item.ref for item in sub.evidence))
    pieces.append(f"留痕：{sub.agent_run_id}")
    return "；".join(pieces)


def _apply_tool_search(
    call: ToolCall,
    tools: tuple[Any, ...],
    retrieved: list[str],
    searches: list[ToolSearchRecord],
) -> str:
    """执行一次工具检索并把命中项加入已展开集（返回进下一轮上下文的显式说明）。

    参数非法 / 未命中都**不终止循环**——把原因写进上下文让模型下一轮纠正（同计划原语
    的「非法形态不终止」口径），到上界仍未纠正即按上界终止。
    """
    arguments = dict(call.arguments)
    query, error = normalize_query(arguments.get("query"))
    if error:
        searches.append(ToolSearchRecord(note=error))
        return error
    limit, limit_error = normalize_limit(arguments.get("limit"))
    if limit_error:
        searches.append(ToolSearchRecord(query=query, note=limit_error))
        return limit_error
    matches = search_catalog(tools, query, limit=limit)
    names = tuple(match.name for match in matches)
    for name in names:
        if name not in retrieved:
            retrieved.append(name)
    if names:
        note = f"「{query}」命中 {len(names)} 条：{'、'.join(names)}（完整参数已展开）"
    else:
        note = f"「{query}」未命中任何工具（目录里只有摘要，可换关键词再试）"
    searches.append(ToolSearchRecord(query=query, matched=names, note=note))
    return note
