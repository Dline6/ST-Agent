"""执行沙箱子包（T-L1-001.3；03 §1.5 + 01 §10 / §11）。

- ``SkillSandbox``：门面（会话创建 + 禁用选项 + 越界留痕）
- ``SandboxSession``：一次执行的三类出口统一核对（文件 / 网络 / 命令）
- ``SandboxedGateway``：受限出网出口（越界不触碰底层 sender）

布局（只经 ``Store`` 读写，不直连文件系统）：
- 越界留痕 → ``execution_log`` 分区 ``sandbox-violation/<id>.json``
- 禁用标记 → ``config`` 分区 ``sandbox-disabled/<skill_id>.json``
"""

from st_agent.l1.sandbox.errors import (
    SandboxError,
    SandboxValidationError,
    SandboxViolationError,
)
from st_agent.l1.sandbox.models import (
    DISABLED_PREFIX,
    VIOLATION_PREFIX,
    GuardVerdict,
    ViolationKind,
    ViolationRecord,
    check_violation_id,
    new_violation_id,
)
from st_agent.l1.sandbox.sandbox import (
    WARNING_TEXT,
    SandboxedGateway,
    SandboxSession,
    SkillSandbox,
)
from st_agent.l1.sandbox.scopes import (
    host_in_scope,
    is_escaping,
    normalize_path,
    path_in_scope,
)

__all__ = [
    "DISABLED_PREFIX",
    "VIOLATION_PREFIX",
    "WARNING_TEXT",
    "GuardVerdict",
    "SandboxError",
    "SandboxSession",
    "SandboxValidationError",
    "SandboxViolationError",
    "SandboxedGateway",
    "SkillSandbox",
    "ViolationKind",
    "ViolationRecord",
    "check_violation_id",
    "host_in_scope",
    "is_escaping",
    "new_violation_id",
    "normalize_path",
    "path_in_scope",
]
