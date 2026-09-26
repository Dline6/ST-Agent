"""MCP Hub 错误类型（T-L1-002.1 传输 / 注册 / 权限；T-L1-002.2 映射）。

与 L1 既有口径一致：子类挂在一个层内根错误下，便于上层按类别捕获；
全部继承 ``Exception`` 而非裸 ``ValueError``（沿用 skills / sandbox 子包先例）。
"""

from __future__ import annotations

__all__ = [
    "McpConnectionError",
    "McpError",
    "McpMappingError",
    "McpMappingNotFoundError",
    "McpPermissionError",
    "McpServerExistsError",
    "McpServerNotFoundError",
    "McpValidationError",
]


class McpError(Exception):
    """MCP Hub 层内根错误。"""


class McpValidationError(McpError):
    """配置 / 权限声明非法（构造期拒绝，不落盘）。"""


class McpServerNotFoundError(McpError):
    """引用了未注册的 MCP Server。"""


class McpServerExistsError(McpError):
    """同 id 的 MCP Server 已存在（更新走 ``update_server``，不静默覆盖）。"""


class McpConnectionError(McpError):
    """连接 / 握手 / 调用失败（远程路径另有 ``Egress*`` 系列经 ``__cause__`` 保留）。"""


class McpPermissionError(McpError):
    """权限操作非法（未声明的权限不得批准/拒绝；未批准的 Server 不得装载映射）。"""


class McpMappingNotFoundError(McpError):
    """引用了不存在的映射记录（该 Server 未装载过此 tool）。"""


class McpMappingError(McpError):
    """映射操作非法（该映射无待重新映射事项；Server 已不再提供该 tool）。"""
