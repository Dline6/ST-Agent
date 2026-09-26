"""MCP Hub 错误类型（T-L1-002.1；03 §5.1 §5.4）。

与 L1 既有口径一致：子类挂在一个层内根错误下，便于上层按类别捕获；
全部继承 ``Exception`` 而非裸 ``ValueError``（沿用 skills / sandbox 子包先例）。
"""

from __future__ import annotations

__all__ = [
    "McpConnectionError",
    "McpError",
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
    """权限操作非法（未声明的权限不得批准/拒绝）。"""
