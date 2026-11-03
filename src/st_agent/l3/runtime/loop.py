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
- **安全停止**（[D-154](../../../项目管理/决策日志.md) B6）——``cancel`` 事件置位后，循环只在
  **边界**终止（LLM 调用前 / 返回后 / 批执行前 / 串行每步前），已开始的工具**跑完再停**、
  不强杀；终止原因 ``cancelled``，已执行步骤（含写操作）**如实带走、不回滚**。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import AgentRunId, TraceId
from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l0.llm import ToolCall, estimate_tokens
from st_agent.l1.builtin.ask_user import ASK_USER, normalize_questions
from st_agent.l1.builtin.budget_request import BUDGET_REQUEST, normalize_budget_request
from st_agent.l1.builtin.plan_update import PLAN_UPDATE, normalize_plan
from st_agent.l1.builtin.spawn import SPAWN_AGENT, normalize_spawn_task
from st_agent.l1.skills.tool_catalog import split_arguments
from st_agent.l2.memory.reader import MemorySlice
from st_agent.l3.chat.session import ChatMessage
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.context import (
    AskQuestion,
    PlanStep,
    WorkContext,
    WorkStep,
    assemble_work_context,
    new_work_step,
    result_will_truncate,
    summarize_result,
)
from st_agent.l3.runtime.envelope import (
    ContextEnvelope,
    derive_context_envelope,
)
from st_agent.l3.runtime.truncation import derive_result_chars
from st_agent.l3.runtime.protocol import (
    DEFAULT_TURN_INITIATOR,
    DEFAULT_TURN_PURPOSE,
    ToolCallProtocol,
)
from st_agent.l3.runtime.steer import SteerMessage
from st_agent.l3.runtime.subprotocols import (
    SUBPROTOCOL_TOOLS,
    handle as handle_subprotocol,
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
    "DEFAULT_LLM_MAX_ATTEMPTS",
    "DEFAULT_LLM_RETRY_BACKOFF_MS",
    "DEFAULT_SUBAGENT_LLM_MAX_ATTEMPTS",
    "DEFAULT_SUBAGENT_LLM_TIMEOUT_MS",
    "GATE_VERDICTS",
    "NestedEventFactory",
    "NestedEventStream",
    "NestedRecorder",
    "NestedRunRef",
    "PLAN_UPDATE_TOOL",
    "SPAWN_AGENT_TOOL",
    "BUDGET_REQUEST_TOOL",
    "PROGRESS_REPEAT_LIMIT",
    "SUBPROTOCOL_TOOLS",
    "GateDecision",
    "GateVerdict",
    "LoopOutcome",
    "MAX_UNKNOWN_TOOL_ROUNDS",
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

DEFAULT_LLM_MAX_ATTEMPTS = 3
"""单轮模型调用的**最多尝试次数**（含首次；[D-165](../../../项目管理/决策日志.md)）。

只对结构化失败里 ``retryable=True`` 的类别生效（超时 / 429 / 5xx / 连接重置）；
每次尝试计一次 ``llm_calls``，不重放工具步、不计步。缺省与配置条目
``investigate.llm_max_attempts`` 同源。
"""

DEFAULT_LLM_RETRY_BACKOFF_MS = 500
"""重试退避基数（毫秒）：第 n 次重试等待 ``n × 基数``；缺省与配置条目
``investigate.llm_retry_backoff_ms`` 同源。"""

DEFAULT_SUBAGENT_LLM_TIMEOUT_MS = 120_000
"""子 Agent 单次 LLM 调用的**独立总时限**（毫秒；[T-AGT-032.2](../../../项目管理/tasks/T-AGT-032.2-子Agent事件流与独立超时.md)）。

比父运行的 300000 短：子任务应更快失败或继续，不与父运行争同一份长时限；
缺省与配置条目 ``investigate.subagent_llm_timeout_ms`` 同源。
"""

DEFAULT_SUBAGENT_LLM_MAX_ATTEMPTS = 2
"""子 Agent 单轮模型调用的**最多尝试次数**（含首次；同 ``investigate.subagent_llm_max_attempts``）。"""

MAX_UNKNOWN_TOOL_ROUNDS = 2
"""未知工具的**连续轮次**上限（工具调用自愈，[D-154](../../../项目管理/决策日志.md) A6）。

第 1 轮请求目录外工具 → 回灌说明并继续；**连续第 2 轮**仍如此即终止（终止原因
``unknown_tool``，说明写明已回灌仍未改选）。取 2 是给模型一次看到说明与目录后改选的机会；
再宽会放任它在同一错误上打转，而双上界本身也会兜底。
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

BUDGET_REQUEST_TOOL = BUDGET_REQUEST.base
"""预算申请原语的工具名（[`T-AGT-033.6`](../../../项目管理/tasks/T-AGT-033.6-Agent自调申请与进展检查.md)）。

循环按工具名识别：它在信封内直接批准、超出则显式拒绝；不产 ``skill_run`` / Trace 步。
"""

PROGRESS_REPEAT_LIMIT = 3
"""连续重复读取同一 ``skill_run_id`` 同一 offset 的容忍次数（到界即按无进展终止）。"""

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
    "cancelled",
    "manual_step_done",
    "card_ready",
    "context_overflow",
    "no_progress",
]
"""循环的终止原因（**穷尽列举**——产出与留痕据此如实标注，不造未列出的原因）。

``cancelled`` 是**安全停止**（[D-154](../../../项目管理/决策日志.md) B6）：已开始的工具
跑完再停、不启动下一步；已执行的写操作**不回滚**，在产出里照实列出。

``manual_step_done`` 是 **`manual` 档的「每步单独批、不自动续跑」**（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)，
[`T-AGT-028.2`](../../../项目管理/tasks/T-AGT-028.2-manual与collaborative差异补齐.md)）：按用户确认
执行完待确认的那一步后**主动停下**，不发起下一轮模型调用——它与 ``needs_confirmation``（待确认、
该步未执行）和 ``finished``（模型给出结束）都不同。

``card_ready`` 是**子协议动作成功产卡**（[05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md) /
[`T-AGT-030`](../../../项目管理/tasks/T-AGT-030-子协议动作化.md)）：循环把卡片交还用户确认，
不是模型给结束、也没有待确认的未执行步；**不可续跑**（用户对卡片另行处置）。
"""

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


class BudgetRequestRecord(BaseModel):
    """一次预算申请的留痕（[`T-AGT-033.6`](../../../项目管理/tasks/T-AGT-033.6-Agent自调申请与进展检查.md)）。"""

    model_config = ConfigDict(frozen=True)

    extra_steps: int = 0
    extra_llm_calls: int = 0
    context_tokens: int = 0
    approved: bool = False
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
    cards: tuple[Any, ...] = ()
    """成功交还的描述件（``card_ready`` 时给出；其余终止为空）。

    子协议动作（`sk_configure` / `sk_memory_op` / `sk_analyze` / `sk_explain`）产出的
    `UiDescription` 载荷；表现层把它放进响应的 `description` 走同一个渲染注册表。
    """
    max_steps: int = DEFAULT_MAX_STEPS
    """本次生效的步数上界（留痕用；取值可注入，故如实记录实际生效值）。"""
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    """本次生效的 LLM 调用次数上界（同上）。"""
    budget_chars: int = 0
    """本次生效的**上下文字符预算**（由 :class:`ContextEnvelope` 派生；0 即未跑装配的终止）。"""
    context_window_tokens: int = 0
    """端点声明的上下文窗口（信封来源；0 即未派生）。"""
    context_available_tokens: int = 0
    """信封扣除预留 / 工具 / 系统提示词后的可用 token（0 即未派生）。"""
    context_envelope_note: str = ""
    """信封派生说明（端点窗口未知 / 用户封顶生效 / 可用预算触底；无即空）。"""
    max_result_chars: int = 1
    """本次生效的单条结果截断阈值（留痕据此如实标「已截断」，与运行事件同一判定）。"""
    dropped: tuple[str, ...] = ()
    """被预算**舍弃**的上下文项（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：
    超限如实取舍并标注；末次装配的取值）。"""
    tool_searches: tuple[ToolSearchRecord, ...] = ()
    """本次循环里的工具检索记录（**决策**，不产 Trace 步 / ``skill_run``；进 ``agent_run``）。"""
    budget_requests: tuple[BudgetRequestRecord, ...] = ()
    """本次循环里的**预算申请**（批准 / 拒绝均留痕；不产 Trace 步）。"""
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
    steered: tuple[SteerMessage, ...] = ()
    """本次循环收到的**运行中插话**（按到达顺序；含未投递的竞态消息）。

    ``delivered=True`` 者已进入某个后续轮次的工作上下文；``False`` 者在排空前运行已结束，
    转为下一条普通消息（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。
    本段只做留痕与回执，不进 Trace（不新增步骤类型）。
    """

    @property
    def at_bound(self) -> bool:
        """是否因**达到上界**而终止（[D-090](../../../项目管理/决策日志.md) ⑥ 的如实标注）。"""
        return self.termination in BOUND_REASONS

    def result_truncated(self, step: WorkStep) -> bool:
        """该步结果进上下文时是否会被截断（事件 / 留痕 / 产出表**同一判定**）。

        循环里的 ``WorkStep`` 原件**不被改写**（``truncated`` 仍为假）；本方法按本次生效的
        ``max_result_chars`` 实时判定，供留痕与描述件如实标「结果已截断」
        （[`T-AGT-026.1`](../../../../项目管理/tasks/T-AGT-026.1-步骤流与展开详情.md)）。
        """
        return result_will_truncate(step.envelope, self.max_result_chars)

    @property
    def finished(self) -> bool:
        """模型是否自行给出了「结束」。"""
        return self.termination == "finished"


NestedRecorder = Callable[[LoopOutcome, str], NestedRunRef]
"""子循环的落痕端口（组合根注入；缺省未注入即派生显式不可用）。

第二个参数是**开跑前已铸好的子运行标识**——事件日志与 ``agent_run`` 留痕因此指向
同一个 ``agent_run_id``（[T-AGT-032.2](../../../项目管理/tasks/T-AGT-032.2-子Agent事件流与独立超时.md)）。
"""


@dataclass(frozen=True)
class NestedEventStream:
    """子运行事件流的句柄（组合根注入的工厂产出）。

    ``emit`` 与父循环同签名的运行事件回调；``close`` 在子运行收口后释放活动登记
    （缺省为空操作）。事件日志与 ``agent_run`` 留痕共用 :attr:`agent_run_id`。
    """

    agent_run_id: str
    emit: Callable[[str, Mapping[str, Any]], None]
    close: Callable[[], None] = lambda: None


NestedEventFactory = Callable[[], NestedEventStream]
"""子运行事件工厂（缺省未注入时子循环不产生事件，但派生仍可落痕）。"""


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
    llm_timeout_ms: int | None = None,
    llm_idle_timeout_ms: int | None = None,
    llm_max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS,
    llm_retry_backoff_ms: int = DEFAULT_LLM_RETRY_BACKOFF_MS,
    subagent_llm_timeout_ms: int | None = None,
    subagent_llm_max_attempts: int | None = None,
    retry_sleep: Callable[[float], None] | None = None,
    budget_chars: int | None = None,
    context_window_tokens: int | None = None,
    context_budget_tokens: int = 0,
    max_result_chars: int | None = None,
    tool_lazy_chars: int | None = None,
    trace: Trace | None = None,
    session_id: str | None = None,
    main_task: str = "",
    follow_up: bool = False,
    prior_dropped: Sequence[str] = (),
    history: Sequence[ChatMessage] = (),
    memory: Sequence[MemorySlice] = (),
    prior_steps: Sequence[WorkStep] = (),
    plan: Sequence[PlanStep] = (),
    plan_updated: bool = False,
    user_inputs: dict[str, Any] | None = None,
    parallel_safe: bool = False,
    allow_sub_agents: bool = True,
    nested_recorder: NestedRecorder | None = None,
    child_events: NestedEventFactory | None = None,
    parent_trace_id: str = "",
    prior_sub_agents: Sequence[SubAgentRun] = (),
    subprotocols: Any = None,
    emit: Callable[[str, Mapping[str, Any]], None] | None = None,
    cancel: threading.Event | None = None,
    steer_inbox: Any = None,
    system_prompt: str = "",
    system_prompt_version: str = "",
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
    :param budget_chars: **显式**字符预算（测试 / 既有调用方的覆盖；``None`` ⇒ 按
        端点窗口派生 :class:`ContextEnvelope`）
    :param context_window_tokens: 端点声明的上下文窗口；``None`` ⇒ 保守 fallback 并显式标注
    :param context_budget_tokens: 用户封顶（0 即不封顶；`investigate.context_budget`）
    :param max_llm_calls: **LLM 调用次数**上界（可注入；同上）
    :param llm_timeout_ms: 单次 LLM 调用的**总时限**（缺省 ``None`` ⇒ 用端点缺省）
    :param llm_idle_timeout_ms: 单次 LLM 调用的**空闲时限**（缺省取总时限）
    :param llm_max_attempts: 单轮模型调用的最多尝试次数（仅可重试失败退避重试）
    :param llm_retry_backoff_ms: 重试退避基数（第 n 次等待 n × 基数）
    :param subagent_llm_timeout_ms: 子 Agent 单次 LLM 调用的**独立总时限**
        （缺省 ``DEFAULT_SUBAGENT_LLM_TIMEOUT_MS``；父循环自身的调用不受影响）
    :param subagent_llm_max_attempts: 子 Agent 单轮调用的最多尝试次数
        （缺省 ``DEFAULT_SUBAGENT_LLM_MAX_ATTEMPTS``）
    :param retry_sleep: 重试等待函数（测试注入以跳过真实等待；缺省 :func:`time.sleep`）
    :param system_prompt: 每轮请求携带的确定性系统提示词（空串＝不带）
    :param system_prompt_version: 系统提示词版本（只进用量记录）
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
    :param child_events: 子运行**事件流工厂**（组合根注入；缺省时子循环不产生事件，
        但仍铸 ``agent_run_id`` 并落痕，父事件流照常给 ``sub_agent_started``）
    :param parent_trace_id: 子循环的父链 ``trace_id``（主循环留空；只进留痕，不改 Trace 结构）
    :param prior_sub_agents: 续跑承接的**子 Agent 摘要**（取自旧 ``agent_run`` 留痕；
        子步骤不重建，但结论、引用与留痕链接仍进工作上下文与本次留痕）
    :param subprotocols: 子协议动作的执行端口（鸭子类型，含 ``configs`` / ``adjudicator`` /
        ``deliberations`` / ``trainings``；组合根注入）。缺省 ``None`` ⇒ 五个动作显式
        不可用并回灌说明，循环照常运行（[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）
    :param emit: 运行事件回调（``(事件类型, 载荷) -> None``；[D-154](../../../项目管理/决策日志.md) B5）。
        缺省 ``None`` 即不产生事件——存量调用方逐字节不变；事件类型与落盘由
        :mod:`st_agent.l3.runtime.events` 定义，本模块只发**客观字段**
    :param cancel: **协作式**取消事件（[D-154](../../../项目管理/决策日志.md) B6 /
        [`T-AGT-024.1`](../../../项目管理/tasks/T-AGT-024.1-取消透传与cancelled终止.md)）；
        缺省 ``None`` 即不可取消，存量调用方逐字节不变。置位后循环在**下一处边界**
        终止：LLM 调用前 / 调用返回后 / 批执行前 / 串行每步前——**已开始的工具跑完再停**，
        不强杀在途动作；终止原因 `cancelled`，已执行步骤照实带走（不回滚）
    :param steer_inbox: **运行中插话的收件箱**（鸭子类型 ``drain() -> Sequence[SteerMessage]``；
        `ActiveRun.steer_inbox`）。循环在每轮 LLM 调用前（步骤边界）排空，按到达顺序并入
        工作上下文（带「运行中插入」标记）；与「模型给出结束」竞态时未排空者以
        ``delivered=False`` 收口，**不丢**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。
        缺省 ``None`` 即不接收插话，存量调用方逐字节不变
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
    if not isinstance(llm_max_attempts, int) or isinstance(llm_max_attempts, bool) \
            or llm_max_attempts < 1:
        raise AgentRuntimeValidationError(
            f"llm_max_attempts 须为 ≥1 的整数，得到 {llm_max_attempts!r}"
        )
    if not isinstance(llm_retry_backoff_ms, int) or isinstance(llm_retry_backoff_ms, bool) \
            or llm_retry_backoff_ms < 0:
        raise AgentRuntimeValidationError(
            f"llm_retry_backoff_ms 须为 ≥0 的整数，得到 {llm_retry_backoff_ms!r}"
        )
    for name, value in (
        ("llm_timeout_ms", llm_timeout_ms), ("llm_idle_timeout_ms", llm_idle_timeout_ms),
    ):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise AgentRuntimeValidationError(f"{name} 须为 ≥1 的整数或 None，得到 {value!r}")
    if retry_sleep is not None and not callable(retry_sleep):
        raise AgentRuntimeValidationError("重试等待函数须为可调用对象（或 None）")
    if runner is None:
        raise AgentRuntimeValidationError("循环需要执行面（L1 SkillRunner；组合根注入）")
    if tool_lazy_chars is not None and (
        not isinstance(tool_lazy_chars, int) or isinstance(tool_lazy_chars, bool)
        or tool_lazy_chars < 1
    ):
        raise AgentRuntimeValidationError(
            f"工具惰性阈值须为 ≥1 的整数，得到 {tool_lazy_chars!r}"
        )
    if context_budget_tokens is not None and (
        not isinstance(context_budget_tokens, int) or isinstance(context_budget_tokens, bool)
        or context_budget_tokens < 0
    ):
        raise AgentRuntimeValidationError(
            f"上下文档位封顶须为 ≥0 的整数，得到 {context_budget_tokens!r}"
        )
    if budget_chars is not None and (
        not isinstance(budget_chars, int) or isinstance(budget_chars, bool)
        or budget_chars < 1
    ):
        raise AgentRuntimeValidationError(
            f"显式字符预算须为 ≥1 的整数或 None，得到 {budget_chars!r}"
        )
    try:
        envelope: ContextEnvelope = derive_context_envelope(
            window_tokens=context_window_tokens,
            tools=tools,
            system_prompt=system_prompt,
            user_cap_tokens=context_budget_tokens or 0,
            explicit_budget_chars=budget_chars,
        )
    except ValueError as exc:
        raise AgentRuntimeValidationError(str(exc)) from exc
    effective_budget_chars = envelope.budget_chars
    effective_tool_lazy_chars = (
        tool_lazy_chars if tool_lazy_chars is not None else envelope.catalog_budget_chars
    )
    if max_result_chars is not None and (
        not isinstance(max_result_chars, int) or isinstance(max_result_chars, bool)
        or max_result_chars < 1
    ):
        raise AgentRuntimeValidationError(
            f"单条结果上限须为 ≥1 的整数或 None，得到 {max_result_chars!r}"
        )
    effective_max_result_chars = (
        max_result_chars if max_result_chars is not None
        else derive_result_chars(effective_budget_chars, max_steps)
    )
    for name, value in (("parallel_safe", parallel_safe), ("allow_sub_agents", allow_sub_agents)):
        if not isinstance(value, bool):
            raise AgentRuntimeValidationError(f"{name} 须为布尔值，得到 {value!r}")
    for name, value in (
        ("subagent_llm_timeout_ms", subagent_llm_timeout_ms),
        ("subagent_llm_max_attempts", subagent_llm_max_attempts),
    ):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise AgentRuntimeValidationError(f"{name} 须为 ≥1 的整数或 None，得到 {value!r}")
    if nested_recorder is not None and not callable(nested_recorder):
        raise AgentRuntimeValidationError("子 Agent 的落痕端口须为可调用对象（或 None）")
    if child_events is not None and not callable(child_events):
        raise AgentRuntimeValidationError("子运行事件工厂须为可调用对象（或 None）")
    if emit is not None and not callable(emit):
        raise AgentRuntimeValidationError("运行事件回调须为可调用对象（或 None）")
    if cancel is not None and not callable(getattr(cancel, "is_set", None)):
        raise AgentRuntimeValidationError("取消事件须提供 is_set()（threading.Event 形态，或 None）")
    if steer_inbox is not None and not callable(getattr(steer_inbox, "drain", None)):
        raise AgentRuntimeValidationError(
            "插话收件箱须提供 drain()（ActiveRun.steer_inbox 形态，或 None）"
        )

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
    unknown_rounds = 0
    """最近**连续**请求目录外工具的轮次（模型改选即清零；见 MAX_UNKNOWN_TOOL_ROUNDS）。"""
    unknown_tool_note = ""
    """下一轮工作上下文的回灌说明（点名未知工具 + 指向本轮目录；无回灌即空）。"""
    subprotocol_note = ""
    """上一次子协议动作的回灌说明（失败原因 / 训练复述 / 无卡说明；无即空）。"""
    budget_note = ""
    """上一次预算申请的结果（批准 / 拒绝与原因；一次性回灌）。"""
    progress_note = ""
    """进展检查提示（重复读取同一 offset；无重复即空）。"""
    budget_requests: list[BudgetRequestRecord] = []
    sub_runs: list[SubAgentRun] = list(prior_sub_agents)
    subagent_notes: list[str] = [render_subagent_note(sub) for sub in prior_sub_agents]
    parallel_notes: list[str] = []
    parallel_used = False
    steered: list[SteerMessage] = []
    """本次运行收到的插话（已投递与竞态未投递合并；留痕与回执共用）。"""

    def _emit(kind: str, payload: Mapping[str, Any] | None = None) -> None:
        """发一条运行事件（``emit`` 缺省即无操作；载荷只由本模块给定客观字段）。"""
        if emit is not None:
            emit(kind, dict(payload or {}))

    def _cancelled() -> bool:
        """是否已请求停止（无事件即永不取消）。"""
        return cancel is not None and cancel.is_set()

    def _stop_reason(unstarted: int = 0) -> str:
        """安全停止的中性说明（已执行步骤照实带走、不回滚；同轮未启动的调用点名）。"""
        pieces = [
            "已开始的动作跑完再停，不启动下一步",
            f"已执行 {len(steps)} 步如实列出，写操作不回滚",
        ]
        if unstarted:
            pieces.append(f"同轮其余 {unstarted} 个调用未执行（不静默丢）")
        return "；".join(pieces)

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
        cards: Sequence[Any] = (),
    ) -> LoopOutcome:
        """装配终止产出——**计划与上下文状态随每次终止照实带走**。"""
        deferred = _take_steers(
            steer_inbox, delivered=False, after_steps=executed,
        )
        if deferred:
            steered.extend(deferred)
            _emit("steer", _steer_event_payload(
                deferred, delivered=False, after_steps=executed,
                reason=f"运行已结束（{termination}），插话转为下一条普通消息",
            ))
        result = _outcome(
            task, endpoint_id, termination, chain, steps, llm_calls,
            reason=reason, answer=answer, pending=pending, gate_reason=gate_reason,
            failure=failure,
            max_steps=max_steps, max_llm_calls=max_llm_calls, session_id=session_id,
            dropped=dropped, budget_chars=effective_budget_chars,
            context_window_tokens=envelope.window_tokens,
            context_available_tokens=envelope.available_tokens,
            context_envelope_note=envelope.note,
            max_result_chars=effective_max_result_chars,
            plan=current_plan, plan_updated=plan_given, plan_error=plan_error,
            questions=tuple(questions), directions=tuple(directions), ask_note=ask_note,
            cards=tuple(cards),
            tool_searches=tuple(searches),
            budget_requests=tuple(budget_requests),
            parent_trace_id=parent_trace_id, executed_steps=executed,
            parallel_used=parallel_used, parallel_note="；".join(parallel_notes),
            sub_agents=tuple(sub_runs),
            steered=tuple(steered),
        )
        _emit("run_finished", {
            "termination": termination,
            "reason": reason,
            "at_bound": result.at_bound,
            "executed_steps": executed,
            "llm_calls": llm_calls,
            "steps": len(result.steps),
            "cards": len(result.cards),
        })
        return result

    _emit("run_started", {
        "task": task,
        "endpoint_id": endpoint_id,
        "session_id": session_id,
        "max_steps": max_steps,
        "max_llm_calls": max_llm_calls,
    })

    if not protocol.tool_calls_supported():
        return finish(
            "endpoint_unavailable",
            reason=(
                f"端点 {endpoint_id!r} 未声明支持工具调用，自主查证循环无法运行"
                "（02 §4 能力诚实性：缺省不得假定为真）"
            ),
        )

    while True:
        # ── 取消（LLM 调用前）：已请求停止即不再发起下一轮 ──
        if _cancelled():
            return finish("cancelled", reason=_stop_reason())

        # ── 上界（LLM 调用次数）：问之前判 ──
        if llm_calls >= max_llm_calls:
            return finish(
                "llm_call_limit",
                reason=f"已达到 LLM 调用次数上界（{max_llm_calls}），终止",
            )

        # ── 进展检查（确定性）：同一结果同一 offset 被反复读取即先提示、到界终止 ──
        repeated = _repeated_read(steps)
        if repeated is not None:
            (run_id, offset), count = repeated
            if count >= PROGRESS_REPEAT_LIMIT:
                return finish(
                    "no_progress",
                    reason=(
                        f"同一结果（{run_id} 的 offset {offset}）已重复读取 {count} 次，"
                        "没有产生新证据；终止并交还，请补充新的线索或换用其他能力"
                    ),
                )
            progress_note = (
                f"同一结果（{run_id} 的 offset {offset}）已重复读取 {count} 次，"
                "该段不会产生新证据；请改用 next_offset 续读或换用其他能力"
            )
        else:
            progress_note = ""

        # ── 插话（步骤边界）：排空收件箱，按到达顺序并入下一轮上下文 ──
        delivered_steers = _take_steers(
            steer_inbox, delivered=True, after_steps=executed,
        )
        if delivered_steers:
            steered.extend(delivered_steers)
            _emit("steer", _steer_event_payload(
                delivered_steers, delivered=True, after_steps=executed,
            ))

        # ── ① 出动作（工作上下文每轮重装：任务 + 计划 + 目录 + 已执行动作与结果） ──
        effective_max_result_chars = (
            max_result_chars if max_result_chars is not None
            else derive_result_chars(
                effective_budget_chars, max(1, max_steps - executed)
            )
        )
        note_for_context = subprotocol_note
        subprotocol_note = ""              # 回灌是一次性的：本轮装配后即消费
        budget_for_context = budget_note
        budget_note = ""
        assembled = assemble_work_context(
            task, tuple(tools), steps,
            main_task=main_task, follow_up=follow_up,
            history=history, memory=memory,
            steer_messages=tuple(steered),
            plan=current_plan, plan_updated=plan_given, plan_error=plan_error,
            user_inputs=user_inputs,
            budget_chars=effective_budget_chars, max_result_chars=effective_max_result_chars,
            retrieved_tools=tuple(retrieved), tool_search_note=tool_search_note,
            subagent_note="\n".join(subagent_notes),
            unknown_tool_note=unknown_tool_note,
            subprotocol_note=note_for_context,
            budget_note=budget_for_context,
            progress_note=progress_note,
            tool_lazy_chars=effective_tool_lazy_chars,
            system_prompt=system_prompt,
            system_prompt_version=system_prompt_version,
        )
        if assembled.status != "ok":
            return finish("catalog_empty", reason=assembled.reason or "")
        context: WorkContext = assembled.data
        dropped = tuple(dict.fromkeys((*prior_dropped, *context.dropped)))
        if context.over_budget:
            # 装配后仍超「端点窗口」才是真装不下；超「安全比例」只如实标注，不终止。
            estimated = (
                estimate_tokens(context.render_prompt(include_tools=True))
                + estimate_tokens(context.system_prompt)
            )
            if estimated > envelope.window_tokens:
                return finish(
                    "context_overflow",
                    reason=(
                        f"上下文约 {estimated} tokens（含工具目录与系统提示词），"
                        f"超出端点窗口 {envelope.window_tokens} tokens；"
                        "请从已有证据继续或减少本轮上下文"
                    ),
                )
        turn = None
        attempts = 0
        retry_reason = ""
        sleep_fn = retry_sleep if retry_sleep is not None else time.sleep
        while True:
            if _cancelled():
                return finish("cancelled", reason=_stop_reason())
            if llm_calls >= max_llm_calls:
                if turn is not None and turn.envelope is not None:
                    # 重试尚未用尽但调用次数上界先到：如实以 llm_failed 收口并保留续跑卡
                    return finish(
                        "llm_failed",
                        reason=(
                            f"{turn.envelope.reason or 'LLM 调用失败'}"
                            f"（重试受 LLM 调用次数上界 {max_llm_calls} 限制）"
                        ),
                        failure=turn.envelope,
                    )
                return finish(
                    "llm_call_limit",
                    reason=f"已达到 LLM 调用次数上界（{max_llm_calls}），终止",
                )
            turn_kwargs: dict[str, Any] = {
                "initiator": initiator, "purpose": purpose, "cancel": cancel,
            }
            if llm_timeout_ms is not None:
                turn_kwargs["timeout_ms"] = llm_timeout_ms
            if llm_idle_timeout_ms is not None:
                turn_kwargs["idle_timeout_ms"] = llm_idle_timeout_ms
            turn = protocol.invoke_turn(context, **turn_kwargs)
            llm_calls += 1
            attempts += 1
            _emit("model_turn", {
                "index": llm_calls,
                "tool_calls": len(turn.tool_calls),
                "final": not turn.tool_calls,
                "failed": turn.envelope is not None,
                "attempt": attempts,
                "max_attempts": llm_max_attempts,
                "retry_reason": retry_reason,
            })
            # ── 取消（LLM 调用后）：在途调用已按事件中断，不再使用本轮产出 ──
            if _cancelled():
                return finish("cancelled", reason=_stop_reason())
            if turn.envelope is None:
                break
            failure = turn.failure
            if failure is not None and failure.kind == "context_overflow":
                return finish(
                    "context_overflow",
                    reason=turn.envelope.reason or "上下文超出端点窗口",
                    failure=turn.envelope,
                )
            if (
                failure is not None and failure.retryable
                and attempts < llm_max_attempts
            ):
                retry_reason = turn.envelope.reason or "LLM 调用失败"
                delay_ms = llm_retry_backoff_ms * attempts
                if delay_ms > 0:
                    sleep_fn(delay_ms / 1000)
                continue
            return finish(
                "llm_failed",
                reason=turn.envelope.reason or "LLM 调用失败",
                failure=turn.envelope,
            )

        # 模型给出「结束」：本轮文本是交付物的来源（中间轮次的文本不在此列）
        if not turn.tool_calls:
            _emit("text_delta", {"text": turn.text})
            return finish("finished", answer=turn.text)

        # ── 本轮多调用：先解析（检索入口惰性态不过闸门；未知工具在预算判定后回灌或终止）──
        calls = tuple(turn.tool_calls)
        planned: list[tuple[ToolCall, Any]] = []
        unknown: list[ToolCall] = []
        executable_count = 0
        for call in calls:
            if call.name == TOOL_SEARCH_TOOL and context.lazy_tools:
                planned.append((call, None))       # 上下文控制：不占步数、不过闸门
                continue
            entry = by_name.get(call.name)
            if entry is None:
                unknown.append(call)
                continue
            planned.append((call, entry))
            if (
                call.name not in (
                    PLAN_UPDATE_TOOL, ASK_USER_TOOL, SPAWN_AGENT_TOOL, BUDGET_REQUEST_TOOL,
                )
                and call.name not in SUBPROTOCOL_TOOLS
            ):
                executable_count += 1

        # ── 上界（步数）：有动作要执行才判；一轮多调用按**多个动作**计 ──
        # 单调用时与既有「到界即停」等价；检索入口不占步数（同既有）。
        if any(entry is not None for _, entry in planned) or unknown:
            if executed >= max_steps or executed + executable_count > max_steps:
                return finish(
                    "step_limit",
                    reason=f"已达到步数上界（{max_steps}），终止",
                )

        # ── 工具调用自愈（[D-154](../../../项目管理/决策日志.md) A6 / 05 §10）──
        # 目录外工具：整轮不执行、只回灌说明；连续第 MAX_UNKNOWN_TOOL_ROUNDS 轮才终止。
        if unknown:
            unknown_rounds += 1
            names = "、".join(f"「{call.name}」" for call in unknown)
            if unknown_rounds >= MAX_UNKNOWN_TOOL_ROUNDS:
                return finish(
                    "unknown_tool",
                    reason=(
                        f"模型连续 {unknown_rounds} 轮请求目录外的工具（{names}），"
                        "已回灌当前工具目录仍未改选，终止"
                        "（目录是 01 §2 描述体的投影，循环不臆造能力）"
                    ),
                )
            unknown_tool_note = (
                f"请求的工具 {names} 不在工具目录内（目录是唯一可选来源），"
                "该轮未执行任何调用；请从本轮工具目录中选择，或直接结束。"
            )
            continue
        unknown_rounds = 0
        unknown_tool_note = ""

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
                record = searches[-1]
                _emit("tool_search", {
                    "query": record.query,
                    "matched": list(record.matched),
                    "note": record.note,
                })
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
                _emit("gate_pending", {
                    "tool": call.name,
                    "skill_id": entry.skill_id,
                    "verdict": decision.verdict,
                    "reason": decision.reason,
                })
                return finish(
                    termination, reason=reason,
                    pending=call, gate_reason=decision.reason,
                )

            if call.name == BUDGET_REQUEST_TOOL:
                request, error = normalize_budget_request(dict(call.arguments))
                if error:
                    budget_note = error
                    continue
                approved, reason = _budget_decision(
                    request,
                    remaining_steps=max(0, max_steps - executed),
                    remaining_llm_calls=max(0, max_llm_calls - llm_calls),
                    available_tokens=envelope.available_tokens,
                )
                budget_requests.append(BudgetRequestRecord(
                    **request, approved=approved, reason=reason,
                ))
                _emit("budget_request", {
                    "request": request, "approved": approved, "reason": reason,
                })
                budget_note = ("已批准：" if approved else "未批准：") + reason
                continue

            if call.name == PLAN_UPDATE_TOOL:
                normalized, error = normalize_plan(dict(call.arguments).get("plan"))
                if error:
                    plan_error = error
                    _emit("plan_updated", {"plan": [], "error": error})
                else:
                    plan_error = ""
                    plan_given = True
                    current_plan = tuple(
                        PlanStep(title=item["title"], status=item["status"])
                        for item in normalized
                    )
                    _emit("plan_updated", {
                        "plan": [step.model_dump() for step in current_plan],
                        "error": "",
                    })
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
                _emit("ask_user", {
                    "questions": [item.model_dump() for item in questions],
                    "directions": list(ask.directions),
                    "note": ask_note,
                })
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
                child_stream = child_events() if child_events is not None else None
                child_run_id = (
                    child_stream.agent_run_id if child_stream is not None
                    else AgentRunId.generate().value
                )
                child_trace = Trace(trace_id=TraceId.generate())
                effective_subagent_timeout = (
                    subagent_llm_timeout_ms
                    if subagent_llm_timeout_ms is not None
                    else DEFAULT_SUBAGENT_LLM_TIMEOUT_MS
                )
                effective_subagent_attempts = (
                    subagent_llm_max_attempts
                    if subagent_llm_max_attempts is not None
                    else DEFAULT_SUBAGENT_LLM_MAX_ATTEMPTS
                )
                child_idle_timeout = llm_idle_timeout_ms
                if child_idle_timeout is not None:
                    # 空闲读超时不得超过子运行总时限（否则一次阻塞读能越过总时限）
                    child_idle_timeout = min(child_idle_timeout, effective_subagent_timeout)
                _emit("sub_agent_started", {
                    "task": task_text,
                    "agent_run_id": child_run_id,
                    "trace_id": child_trace.trace_id.value,
                    "timeout_ms": effective_subagent_timeout,
                    "max_attempts": effective_subagent_attempts,
                })
                try:
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
                        llm_timeout_ms=effective_subagent_timeout,
                        llm_idle_timeout_ms=child_idle_timeout,
                        llm_max_attempts=effective_subagent_attempts,
                        llm_retry_backoff_ms=llm_retry_backoff_ms,
                        retry_sleep=retry_sleep,
                        budget_chars=effective_budget_chars,
                        context_window_tokens=context_window_tokens,
                        context_budget_tokens=context_budget_tokens,
                        max_result_chars=max_result_chars,
                        tool_lazy_chars=effective_tool_lazy_chars,
                        trace=child_trace,
                        session_id=None,
                        history=(),
                        memory=memory,
                        parallel_safe=parallel_safe,
                        allow_sub_agents=False,
                        nested_recorder=None,
                        parent_trace_id=chain.trace_id.value,
                        cancel=cancel,
                        emit=child_stream.emit if child_stream is not None else None,
                        system_prompt=system_prompt,
                        system_prompt_version=system_prompt_version,
                    )
                    ref = nested_recorder(child, child_run_id)
                finally:
                    if child_stream is not None:
                        child_stream.close()
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
                _emit("sub_agent", {
                    "task": sub.task,
                    "agent_run_id": sub.agent_run_id,
                    "trace_id": sub.trace_id,
                    "termination": sub.termination,
                    "reason": sub.reason,
                    "answer": sub.answer,
                    "evidence": [item.ref for item in sub.evidence],
                    "steps": sub.steps,
                    "llm_calls": sub.llm_calls,
                    # 运行面板的嵌套展示（T-AGT-026.1）：子步骤的客观引用，不复制结果载荷
                    "step_list": [
                        {
                            "tool": step.tool_name,
                            "skill_id": step.skill_id,
                            "status": step.envelope.status,
                            "skill_run_id": step.skill_run_id,
                            "truncated": result_will_truncate(
                                step.envelope, effective_max_result_chars),
                        }
                        for step in child.steps
                    ],
                })
                executed += child_steps
                llm_calls += child.llm_calls
                subagent_notes.append(render_subagent_note(sub))
                continue

            if call.name in SUBPROTOCOL_TOOLS:
                handled = handle_subprotocol(
                    call.name, dict(call.arguments), trace=chain, ports=subprotocols,
                )
                if handled.card_ready:
                    reason = (
                        f"{handled.note}；" if handled.note else ""
                    ) + (
                        f"子协议动作 {call.name} 已产出卡片，循环终止并交还确认"
                        "（05 §3.1；卡片随响应描述交还）"
                    )
                    if rest:
                        reason += f"；同轮其余 {rest} 个调用未执行（不静默丢）"
                    return finish("card_ready", reason=reason, cards=handled.cards)
                subprotocol_note = handled.note or (
                    f"子协议动作 {call.name} 未产出卡片"
                    f"（{handled.envelope.status}）"
                )
                if rest:
                    subprotocol_note = _join_note(
                        subprotocol_note, f"同轮其余 {rest} 个调用未执行",
                    )
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
        # ── 取消（批执行前）：整批尚未开始，一步都不执行 ──
        if _cancelled():
            return finish("cancelled", reason=_stop_reason(len(batch)))

        # ── ③④ 执行与回填 —— 并发安全须由装配方**显式声明**，未声明即退回串行并标注 ──
        if len(batch) == 1 or not parallel_safe:
            if len(batch) > 1:
                parallel_notes.append(
                    f"本轮 {len(batch)} 个调用按顺序串行执行"
                    "（底层未声明并发安全，显式退回串行）"
                )
            for position, (call, entry) in enumerate(batch):
                # ── 取消（串行每步前）：当前工具已跑完，不再启动下一步 ──
                if _cancelled():
                    return finish(
                        "cancelled", reason=_stop_reason(len(batch) - position),
                    )
                values, object_inputs = split_arguments(entry, dict(call.arguments))
                _emit("step_started", {
                    "call_id": call.call_id,
                    "tool": call.name, "skill_id": entry.skill_id,
                    "arguments": dict(call.arguments),
                })
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
                work = new_work_step(
                    tool_name=call.name,
                    skill_id=entry.skill_id,
                    arguments=dict(call.arguments),
                    skill_run_id=run.skill_run_id,
                    envelope=run.envelope,
                )
                steps.append(work)
                _emit("step_finished", {
                    "call_id": call.call_id,
                    "tool": call.name, "skill_id": entry.skill_id,
                    "skill_run_id": work.skill_run_id,
                    "status": run.envelope.status,
                    "truncated": result_will_truncate(run.envelope, effective_max_result_chars),
                    "summary": summarize_result(run.envelope),
                    "log_ref": work.log_ref,
                })
                chain = run.trace
                executed += 1
            continue

        # ── 取消（并行提交前）：整批尚未提交，一步都不执行 ──
        if _cancelled():
            return finish("cancelled", reason=_stop_reason(len(batch)))
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
        for call, entry, _values, _inputs, _approved in prepared:
            _emit("step_started", {
                "call_id": call.call_id,
                "tool": call.name, "skill_id": entry.skill_id,
                "arguments": dict(call.arguments),
            })
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
            work = new_work_step(
                tool_name=call.name,
                skill_id=entry.skill_id,
                arguments=dict(call.arguments),
                skill_run_id=run.skill_run_id,
                envelope=run.envelope,
            )
            steps.append(work)
            _emit("step_finished", {
                "call_id": call.call_id,
                "tool": call.name, "skill_id": entry.skill_id,
                "skill_run_id": work.skill_run_id,
                "status": run.envelope.status,
                "truncated": result_will_truncate(run.envelope, effective_max_result_chars),
                "summary": summarize_result(run.envelope),
                "log_ref": work.log_ref,
            })
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
    budget_chars: int = 0,
    context_window_tokens: int = 0,
    context_available_tokens: int = 0,
    context_envelope_note: str = "",
    max_result_chars: int | None = None,
    plan: Sequence[PlanStep] = (),
    plan_updated: bool = False,
    plan_error: str = "",
    questions: Sequence[AskQuestion] = (),
    directions: Sequence[str] = (),
    ask_note: str = "",
    cards: Sequence[Any] = (),
    tool_searches: Sequence[ToolSearchRecord] = (),
    budget_requests: Sequence[BudgetRequestRecord] = (),
    parent_trace_id: str = "",
    executed_steps: int = 0,
    parallel_used: bool = False,
    parallel_note: str = "",
    sub_agents: Sequence[SubAgentRun] = (),
    steered: Sequence[SteerMessage] = (),
) -> LoopOutcome:
    """装配一次循环的产出（``failure`` 即端点侧给的原信封，产出装配原样透出）。"""
    return LoopOutcome(
        task=task, endpoint_id=endpoint_id, termination=termination, reason=reason,
        steps=tuple(steps), llm_calls=llm_calls, trace=chain, answer=answer,
        pending=pending, gate_reason=gate_reason,
        max_steps=max_steps, max_llm_calls=max_llm_calls, session_id=session_id,
        context_note=context_note, dropped=dropped, budget_chars=budget_chars,
        context_window_tokens=context_window_tokens,
        context_available_tokens=context_available_tokens,
        context_envelope_note=context_envelope_note,
        max_result_chars=max_result_chars,
        failure=failure,
        plan=tuple(plan), plan_updated=plan_updated, plan_error=plan_error,
        questions=tuple(questions), directions=tuple(directions), ask_note=ask_note,
        cards=tuple(cards),
        tool_searches=tuple(tool_searches),
        budget_requests=tuple(budget_requests),
        parent_trace_id=parent_trace_id, executed_steps=executed_steps,
        parallel_used=parallel_used, parallel_note=parallel_note,
        sub_agents=tuple(sub_agents), steered=tuple(steered),
    )


def _join_note(*parts: str) -> str:
    """把若干显式说明拼成一条（空串跳过；分隔与既有留痕口径一致）。"""
    return "；".join(part for part in parts if part)


def _budget_decision(
    request: Mapping[str, int],
    *,
    remaining_steps: int,
    remaining_llm_calls: int,
    available_tokens: int,
) -> tuple[bool, str]:
    """按**当前余量**判定一次预算申请（全部在信封内才批准，逐项点名超出量）。

    模型只能申请，不能抬高总边界：``max_steps`` / ``max_llm_calls`` 与端点窗口派生的
    token 可用量都是硬边界，超出即显式拒绝（[`T-AGT-033.6`](../../../项目管理/tasks/T-AGT-033.6-Agent自调申请与进展检查.md)）。
    """
    limits = {
        "extra_steps": (remaining_steps, "步数"),
        "extra_llm_calls": (remaining_llm_calls, "LLM 调用次数"),
        "context_tokens": (available_tokens, "上下文 token"),
    }
    shortfalls: list[str] = []
    for key, value in request.items():
        available, label = limits[key]
        if value > available:
            shortfalls.append(f"{label}申请 {value}，余量仅 {available}")
    if shortfalls:
        return False, "；".join(shortfalls) + "（总边界不可由模型抬高，请交还用户决定）"
    approved = "、".join(
        f"{label} {request[key]}" for key, (_, label) in limits.items() if key in request
    )
    return True, f"在信封内直接批准（{approved}）"


def _repeated_read(steps: Sequence[WorkStep]) -> tuple[tuple[str, int], int] | None:
    """找出重复读取最多的 ``(skill_run_id, offset)`` 及次数（无读取即 ``None``）。"""
    counts: dict[tuple[str, int], int] = {}
    for step in steps:
        if step.tool_name != "sk_result_read":
            continue
        run_id = str(step.arguments.get("skill_run_id") or "").strip()
        offset = step.arguments.get("offset", 0)
        if not run_id or not isinstance(offset, int) or isinstance(offset, bool):
            continue
        key = (run_id, offset)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])


def _take_steers(
    inbox: Any, *, delivered: bool, after_steps: int,
) -> tuple[SteerMessage, ...]:
    """排空插话收件箱并标上本次排空的投递结果（无收件箱即空）。"""
    if inbox is None:
        return ()
    drained = tuple(inbox.drain() or ())
    out: list[SteerMessage] = []
    for item in drained:
        if not isinstance(item, SteerMessage):
            raise AgentRuntimeValidationError(
                "插话收件箱须返回 SteerMessage"
                f"（得到 {type(item).__name__}；收件箱形态见 05 §10）"
            )
        out.append(item.model_copy(update={
            "delivered": delivered, "after_steps": after_steps,
        }))
    return tuple(out)


def _steer_event_payload(
    items: Sequence[SteerMessage],
    *,
    delivered: bool,
    after_steps: int,
    reason: str = "",
) -> dict[str, Any]:
    """``steer`` 运行事件的载荷（只给客观字段；文本是用户消息本身）。"""
    payload: dict[str, Any] = {
        "messages": [
            {"text": item.text, "received_at": item.received_at.isoformat()}
            for item in items
        ],
        "count": len(items),
        "delivered": delivered,
        "after_steps": after_steps,
    }
    if reason:
        payload["reason"] = reason
    return payload


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
