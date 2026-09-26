"""MCP Hub 子包（T-L1-002；03 §5）。

对外统一从 ``st_agent.l1.mcp`` import。

布局（只经 ``Store`` 读写，不直连文件系统）：
- Server 注册记录 → ``config`` 分区 ``mcp-server/<server_id>.json``
- 权限批准状态 → ``config`` 分区 ``mcp-permission/<server_id>.json``
- 出网审计 → ``execution_log`` 分区（**经 L0 网关**写入，本包不直写）

当前批次（``T-L1-002.1``）交付：传输（stdio / HTTP-SSE）、注册表、权限批准。
``T-L1-002.2`` 在其上做 tool → Skill 映射，``T-L1-002.3`` 做状态机与降级。
"""

from st_agent.l1.mcp.client import McpClient
from st_agent.l1.mcp.errors import (
    McpConnectionError,
    McpError,
    McpPermissionError,
    McpServerExistsError,
    McpServerNotFoundError,
    McpValidationError,
)
from st_agent.l1.mcp.ids import (
    SERVER_ID_PATTERN,
    check_remote_url,
    check_server_id,
    remote_host,
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
    "CLIENT_INFO",
    "DATA_EGRESS_RISK_TEXT",
    "DEFAULT_PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT_MS",
    "PERMISSION_KINDS",
    "PERMISSION_PREFIX",
    "REMOTE_PURPOSE",
    "SERVER_ID_PATTERN",
    "SERVER_PREFIX",
    "ConnectionTestResult",
    "EnvResolver",
    "HttpSseTransport",
    "McpClient",
    "McpConnectionError",
    "McpError",
    "McpPermissionBook",
    "McpPermissionDecision",
    "McpPermissionError",
    "McpServerExistsError",
    "McpServerNotFoundError",
    "McpServerRecord",
    "McpServerRegistry",
    "McpTransport",
    "McpTransportKind",
    "McpValidationError",
    "PermissionApproval",
    "StdioTransport",
    "ToolSpec",
    "TransportFactory",
    "check_remote_url",
    "check_server_id",
    "describe_permission",
    "extract_response",
    "remote_host",
]
