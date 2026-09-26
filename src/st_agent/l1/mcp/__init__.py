"""MCP Hub 子包（T-L1-002；03 §5）。

对外统一从 ``st_agent.l1.mcp`` import。

布局（只经 ``Store`` 读写，不直连文件系统）：
- Server 注册记录 → ``config`` 分区 ``mcp-server/<server_id>.json``
- 权限批准状态 → ``config`` 分区 ``mcp-permission/<server_id>.json``
- tool → Skill 映射记录 → ``config`` 分区 ``mcp-mapping/<server_id>/<tool>.json``
- 生命周期当前态 → ``config`` 分区 ``mcp-lifecycle/<server_id>.json``
- 重连策略 → ``config`` 分区 ``mcp-hub/reconnect-*.json``（01 §7 条目）
- 转移 / 提示 / 策略变更留痕 → ``execution_log`` 分区 ``mcp-lifecycle/**`` · ``mcp-hub-change/**``
- 出网审计 → ``execution_log`` 分区（**经 L0 网关**写入，本包不直写）

批次：``T-L1-002.1`` 交付传输（stdio / HTTP-SSE）、注册表、权限批准；
``T-L1-002.2`` 交付 tool → Skill 自动映射与版本化；
``T-L1-002.3`` 交付状态机、崩溃重连、降级候选与禁用中止。
"""

from st_agent.l1.mcp.client import McpClient
from st_agent.l1.mcp.errors import (
    McpCancelledError,
    McpConnectionError,
    McpError,
    McpMappingError,
    McpMappingNotFoundError,
    McpPermissionError,
    McpServerExistsError,
    McpServerNotFoundError,
    McpStateError,
    McpStateTransitionError,
    McpValidationError,
)
from st_agent.l1.mcp.ids import (
    SERVER_ID_PATTERN,
    check_remote_url,
    check_server_id,
    remote_host,
)
from st_agent.l1.mcp.lifecycle import (
    ALLOWED_TRANSITIONS,
    DEFAULT_INTERVAL_MS,
    DEFAULT_MAX_RETRIES,
    HUB_CHANGE_PREFIX,
    HUB_CONFIG_PREFIX,
    HUB_STATE_PREFIX,
    INTERVAL_CONFIG_ID,
    MAX_RETRIES_CONFIG_ID,
    NOTICE_PREFIX,
    TRANSITION_PREFIX,
    ActiveCall,
    DegradeCandidate,
    McpHubConfig,
    McpHubNotice,
    McpHubStateMachine,
    McpLifecycleRecord,
    McpServerState,
    ReconnectSettings,
    StateTransition,
    check_state,
)
from st_agent.l1.mcp.mapping import (
    BASE_LIMIT,
    MAPPING_PREFIX,
    TOOL_NAME_PATTERN,
    MappingStatus,
    McpSkillMapper,
    McpToolMapping,
    check_tool_name,
    tool_fingerprints,
)
from st_agent.l1.mcp.models import (
    DATA_EGRESS_RISK_TEXT,
    ConnectionTestResult,
    McpPermissionDecision,
    McpServerRecord,
    McpTransportKind,
    PermissionApproval,
    ToolSpec,
)
from st_agent.l1.mcp.permissions import (
    PERMISSION_KINDS,
    PERMISSION_PREFIX,
    McpPermissionBook,
    describe_permission,
)
from st_agent.l1.mcp.registry import (
    SERVER_PREFIX,
    EnvResolver,
    McpServerRegistry,
    TransportFactory,
)
from st_agent.l1.mcp.transports import (
    CLIENT_INFO,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_TIMEOUT_MS,
    REMOTE_PURPOSE,
    HttpSseTransport,
    McpTransport,
    StdioTransport,
    extract_response,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ActiveCall",
    "BASE_LIMIT",
    "CLIENT_INFO",
    "ConnectionTestResult",
    "DATA_EGRESS_RISK_TEXT",
    "DEFAULT_INTERVAL_MS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT_MS",
    "DegradeCandidate",
    "EnvResolver",
    "HUB_CHANGE_PREFIX",
    "HUB_CONFIG_PREFIX",
    "HUB_STATE_PREFIX",
    "HttpSseTransport",
    "INTERVAL_CONFIG_ID",
    "MAPPING_PREFIX",
    "MAX_RETRIES_CONFIG_ID",
    "MappingStatus",
    "McpCancelledError",
    "McpClient",
    "McpConnectionError",
    "McpError",
    "McpHubConfig",
    "McpHubNotice",
    "McpHubStateMachine",
    "McpLifecycleRecord",
    "McpMappingError",
    "McpMappingNotFoundError",
    "McpPermissionBook",
    "McpPermissionDecision",
    "McpPermissionError",
    "McpServerExistsError",
    "McpServerNotFoundError",
    "McpServerRecord",
    "McpServerRegistry",
    "McpServerState",
    "McpSkillMapper",
    "McpStateError",
    "McpStateTransitionError",
    "McpToolMapping",
    "McpTransport",
    "McpTransportKind",
    "McpValidationError",
    "NOTICE_PREFIX",
    "PERMISSION_KINDS",
    "PERMISSION_PREFIX",
    "PermissionApproval",
    "REMOTE_PURPOSE",
    "ReconnectSettings",
    "SERVER_ID_PATTERN",
    "SERVER_PREFIX",
    "StateTransition",
    "StdioTransport",
    "TOOL_NAME_PATTERN",
    "TRANSITION_PREFIX",
    "ToolSpec",
    "TransportFactory",
    "check_remote_url",
    "check_server_id",
    "check_state",
    "check_tool_name",
    "describe_permission",
    "extract_response",
    "remote_host",
    "tool_fingerprints",
]
