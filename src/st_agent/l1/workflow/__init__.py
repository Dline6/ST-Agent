"""工作流子系统（T-L1-003.1；03 §3.1 + §4「校验」）。

交付 WorkflowDAG 模型、静态校验、持久化与版本。上层入口一律 ``from
st_agent.l1.workflow import ...``。

布局（只经 ``Store`` 读写）：
- 工作流定义 → ``config`` 分区 ``workflow/<flow_id>.json``
- 激活指针 → ``config`` 分区 ``workflow-active/<base>.json``

后续子任务：``T-L1-003.2`` 复合 Skill / ``.3`` 试跑调试 / ``.4`` 编辑面 /
``.5`` 模板库与空状态。
"""

from st_agent.l1.workflow.errors import (
    WorkflowError,
    WorkflowExistsError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from st_agent.l1.workflow.ids import (
    FLOW_ID_PATTERN,
    base_of,
    check_flow_id,
    flow_id_for,
    parse_flow_id,
)
from st_agent.l1.workflow.models import (
    ID_PATTERN,
    ParamBinding,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowGroup,
    WorkflowNode,
    WorkflowSchedule,
    checked_dag,
)
from st_agent.l1.workflow.store import (
    ACTIVE_PREFIX,
    WORKFLOW_PREFIX,
    ActivePointer,
    WorkflowDiff,
    WorkflowPendingCheck,
    WorkflowStore,
)
from st_agent.l1.workflow.validate import (
    WorkflowIssue,
    WorkflowValidation,
    dependency_graph,
    topological_order,
    validate_dag,
)

__all__ = [
    "ACTIVE_PREFIX",
    "FLOW_ID_PATTERN",
    "ID_PATTERN",
    "WORKFLOW_PREFIX",
    "ActivePointer",
    "ParamBinding",
    "WorkflowDAG",
    "WorkflowDiff",
    "WorkflowEdge",
    "WorkflowError",
    "WorkflowExistsError",
    "WorkflowGroup",
    "WorkflowIssue",
    "WorkflowNode",
    "WorkflowNotFoundError",
    "WorkflowPendingCheck",
    "WorkflowSchedule",
    "WorkflowStore",
    "WorkflowValidation",
    "WorkflowValidationError",
    "base_of",
    "check_flow_id",
    "checked_dag",
    "dependency_graph",
    "flow_id_for",
    "parse_flow_id",
    "topological_order",
    "validate_dag",
]
