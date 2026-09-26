"""工作流子系统（T-L1-003.1 / .2 / .3；03 §3–§4）。

交付 WorkflowDAG 模型、静态校验、持久化与版本（``.1``）、「工作流 → 复合 Skill」
（``.2``），以及工作流试跑的调试协议（``.3``）。上层入口一律
``from st_agent.l1.workflow import ...``。

布局（只经 ``Store`` 读写）：
- 工作流定义 → ``config`` 分区 ``workflow/<flow_id>.json``
- 激活指针 → ``config`` 分区 ``workflow-active/<base>.json``

复合 Skill 复用 Skill 库布局（``skill-registry/<skill_id>.json``，见 ``composite``）；
试跑记录落 ``execution_log`` 分区 ``workflow-trial/<trial_id>.json``（见 ``trial``）。

后续子任务：``T-L1-003.4`` 编辑面 / ``.5`` 模板库与空状态。
"""

from st_agent.l1.workflow.composite import (
    CompositeDependencyError,
    DependencyGap,
    composite_skill_id,
    derive_io_contract,
    missing_dependencies,
    save_as_composite_skill,
    source_flow_id,
)
from st_agent.l1.workflow.errors import (
    TrialError,
    TrialNotFoundError,
    TrialStateError,
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
from st_agent.l1.workflow.trial import (
    FATAL_ISSUE_CODES,
    TRIAL_STATUSES,
    TrialDiff,
    TrialRecord,
    TrialRunner,
    TrialStatus,
    TrialStep,
    ValueSource,
    WorkflowTrial,
)
from st_agent.l1.workflow.trial_history import (
    PARTITION,
    TRIAL_PREFIX,
    TrialHistory,
)
from st_agent.l1.workflow.validate import (
    WorkflowIssue,
    WorkflowValidation,
    dependency_graph,
    find_cycle,
    topological_order,
    validate_dag,
)

__all__ = [
    "ACTIVE_PREFIX",
    "FATAL_ISSUE_CODES",
    "FLOW_ID_PATTERN",
    "ID_PATTERN",
    "PARTITION",
    "TRIAL_PREFIX",
    "TRIAL_STATUSES",
    "WORKFLOW_PREFIX",
    "ActivePointer",
    "CompositeDependencyError",
    "DependencyGap",
    "ParamBinding",
    "TrialDiff",
    "TrialError",
    "TrialHistory",
    "TrialNotFoundError",
    "TrialRecord",
    "TrialRunner",
    "TrialStateError",
    "TrialStatus",
    "TrialStep",
    "ValueSource",
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
    "WorkflowTrial",
    "WorkflowValidation",
    "WorkflowValidationError",
    "base_of",
    "check_flow_id",
    "checked_dag",
    "composite_skill_id",
    "dependency_graph",
    "derive_io_contract",
    "find_cycle",
    "flow_id_for",
    "missing_dependencies",
    "parse_flow_id",
    "save_as_composite_skill",
    "source_flow_id",
    "topological_order",
    "validate_dag",
]
