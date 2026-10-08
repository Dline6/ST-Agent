"""L3 自主查证循环（Agent 运行时；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

循环本体住 L3 自身（[D-090](../../../项目管理/决策日志.md) ② 落点 A）——**不新开层、不动
[铁律 7](../../../项目管理/工程宪法.md)**：L3 向下可取 L1（执行面）/ L2（记忆），向上一层
（L4 / L6）经 [§4](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的既有鸭子端口。

四个模块即循环的四件东西：

| 模块 | 交付（任务） |
| --- | --- |
| [`context`](context.py) | 工作上下文与工具目录的**装配件**（纯函数；截断 + 留痕指针）——[`T-AGT-004.1`](../../../项目管理/tasks/T-AGT-004.1-工具目录消费与循环工作上下文装配.md) |
| [`protocol`](protocol.py) | **工具调用协议端口**（接口 + T1 原生 function calling 实现）——[`T-AGT-004.2`](../../../项目管理/tasks/T-AGT-004.2-循环驱动、协议端口与上界.md) |
| [`loop`](loop.py) | **四段驱动 + 双上界 + 授权端口**——[`T-AGT-004.2`](../../../项目管理/tasks/T-AGT-004.2-循环驱动、协议端口与上界.md) |
| [`gate`](gate.py) | **逐步授权闸门**（动作标识派生 + 判据经鸭子端口取用 + fail-closed）——[`T-AGT-005.2`](../../../项目管理/tasks/T-AGT-005.2-逐步闸门与终止交还.md) |
| [`report`](report.py) | **产出装配 + 留痕 + 失败与降级 + 终止交还**——[`T-AGT-004.3`](../../../项目管理/tasks/T-AGT-004.3-产出接入、留痕与失败语义.md) · [`T-AGT-005.2`](../../../项目管理/tasks/T-AGT-005.2-逐步闸门与终止交还.md) |
| [`bounds`](bounds.py) | **双上界的 01 §7 配置项**（owner + 条目）——[`T-AGT-007`](../../../项目管理/tasks/T-AGT-007-循环上界开放为配置项.md) |
| [`registry_adapter`](registry_adapter.py) | **``investigate.*`` 族的门面适配器**（组合根注入 L1 门面）——[`T-AGT-007`](../../../项目管理/tasks/T-AGT-007-循环上界开放为配置项.md) |

**本包不接线**：把循环接到 `investigate` 意图上是 [`T-AGT-006`](../../../项目管理/tasks/T-AGT-006-第七类意图investigate与派发去向接线.md)
的事——本包只交出它要接住的**入口面**（:func:`run_agent_loop` 的 ``gate`` 端口与
:data:`AGENT_GATE_METHOD`、:class:`ActionGate`）与**产出面**（:func:`conclude_agent_run`）。
"""

from st_agent.l3.runtime.bounds import (
    INVESTIGATE_CONFIG_PREFIX,
    MAX_LLM_CALLS_CEILING,
    MAX_LLM_CALLS_CONFIG_ID,
    MAX_STEPS_CEILING,
    MAX_STEPS_CONFIG_ID,
    LoopBounds,
    loop_bounds_config_entries,
)
from st_agent.l3.runtime.context import (
    CATALOG_EMPTY_REASON,
    DEFAULT_WORK_CONTEXT_BUDGET,
    RESULT_TRUNCATE_CHARS,
    TRUNCATED_PREVIEW_CHARS,
    TruncatedResult,
    WorkContext,
    WorkStep,
    assemble_work_context,
    log_ref_of,
    new_work_step,
)
from st_agent.l3.runtime.gate import (
    AGENT_DECISION_METHOD,
    SKILL_ACTION_PREFIX,
    ActionGate,
    action_id_of,
)
from st_agent.l3.runtime.loop import (
    AGENT_GATE_METHOD,
    BOUND_REASONS,
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_STEPS,
    GATE_VERDICTS,
    GateDecision,
    GateVerdict,
    LoopOutcome,
    TerminationReason,
    run_agent_loop,
)
from st_agent.l3.runtime.protocol import (
    DEFAULT_TURN_INITIATOR,
    DEFAULT_TURN_PURPOSE,
    NativeToolCallProtocol,
    ToolCallProtocol,
    TurnResult,
)
from st_agent.l3.runtime.report import (
    AGENT_RUN_PREFIX,
    AGENT_UNAVAILABLE_NOTICE,
    AgentRunRecord,
    AgentRunReport,
    AgentRunStepRef,
    agent_log_ref_of,
    build_agent_output,
    conclude_agent_run,
    load_agent_run,
    new_agent_run_id,
    record_agent_run,
)
from st_agent.l3.runtime.registry_adapter import InvestigateFamily, investigate_family

__all__ = [
    "AGENT_DECISION_METHOD",
    "AGENT_GATE_METHOD",
    "AGENT_RUN_PREFIX",
    "AGENT_UNAVAILABLE_NOTICE",
    "BOUND_REASONS",
    "CATALOG_EMPTY_REASON",
    "DEFAULT_MAX_LLM_CALLS",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_TURN_INITIATOR",
    "DEFAULT_TURN_PURPOSE",
    "DEFAULT_WORK_CONTEXT_BUDGET",
    "GATE_VERDICTS",
    "INVESTIGATE_CONFIG_PREFIX",
    "MAX_LLM_CALLS_CEILING",
    "MAX_LLM_CALLS_CONFIG_ID",
    "MAX_STEPS_CEILING",
    "MAX_STEPS_CONFIG_ID",
    "RESULT_TRUNCATE_CHARS",
    "SKILL_ACTION_PREFIX",
    "TRUNCATED_PREVIEW_CHARS",
    "ActionGate",
    "AgentRunRecord",
    "AgentRunReport",
    "AgentRunStepRef",
    "GateDecision",
    "GateVerdict",
    "InvestigateFamily",
    "LoopBounds",
    "LoopOutcome",
    "NativeToolCallProtocol",
    "TerminationReason",
    "ToolCallProtocol",
    "TruncatedResult",
    "TurnResult",
    "WorkContext",
    "WorkStep",
    "action_id_of",
    "agent_log_ref_of",
    "assemble_work_context",
    "build_agent_output",
    "conclude_agent_run",
    "investigate_family",
    "load_agent_run",
    "log_ref_of",
    "loop_bounds_config_entries",
    "new_agent_run_id",
    "new_work_step",
    "record_agent_run",
    "run_agent_loop",
]
