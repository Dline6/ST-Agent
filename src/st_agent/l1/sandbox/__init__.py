"""执行沙箱子包（T-L1-001.3；03 §1.5 + 01 §10 / §11）。

- ``SkillSandbox``：门面（会话创建 + 禁用选项 + 越界留痕）
- ``SandboxSession``：一次执行的三类出口统一核对（文件 / 网络 / 命令）+ LLM 出口
- ``SandboxedGateway``：受限出网出口（越界不触碰底层 sender）；
  ``llm_transport`` 为结构性受限传输（T-L1-001.5 / A4）
- ``GuardedLlmClient``：受限 LLM 调用代理（``invoke`` 边界核对；T-L1-001.5 / A3）
- ``ProviderHostRegistry``：``provider → host`` 映射（01 §7 条目形态；D-005）

布局（只经 ``Store`` 读写，不直连文件系统）：
- 越界留痕 → ``execution_log`` 分区 ``sandbox-violation/<id>.json``
- 禁用标记 → ``config`` 分区 ``sandbox-disabled/<skill_id>.json``
- 提供方主机映射 → ``config`` 分区 ``llm-provider-host/<provider>.json``
"""

from st_agent.l1.sandbox.errors import (
    ProviderHostError,
    ProviderHostExistsError,
    ProviderHostNotFoundError,
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
from st_agent.l1.sandbox.provider_hosts import (
    PROVIDER_HOST_PREFIX,
    PROVIDER_PATTERN,
    ProviderHostRegistry,
    check_host,
    check_provider,
)
from st_agent.l1.sandbox.sandbox import (
    WARNING_TEXT,
    GuardedLlmClient,
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
    "PROVIDER_HOST_PREFIX",
    "PROVIDER_PATTERN",
    "VIOLATION_PREFIX",
    "WARNING_TEXT",
    "GuardVerdict",
    "GuardedLlmClient",
    "ProviderHostError",
    "ProviderHostExistsError",
    "ProviderHostNotFoundError",
    "ProviderHostRegistry",
    "SandboxError",
    "SandboxSession",
    "SandboxValidationError",
    "SandboxViolationError",
    "SandboxedGateway",
    "SkillSandbox",
    "ViolationKind",
    "ViolationRecord",
    "check_host",
    "check_provider",
    "check_violation_id",
    "host_in_scope",
    "is_escaping",
    "new_violation_id",
    "normalize_path",
    "path_in_scope",
]
