"""MCP 权限的「声明 → 逐项批准」账本（T-L1-002.1；03 §5.4；01 §10）。

01 §10 的统一权限模型：能力（Skill / **MCP Server** / 导入物）在安装/挂载时
声明权限，用户逐项批准；「权限申请展示必须说明这个能力想做什么」。

本模块只管**批准状态**与**展示措辞**；运行时越界拦截归 L1 执行沙箱
（``T-L1-001.3``/``T-L1-001.5``），二者共用同一份 01 §10 语法与作用域口径
（[D-004](../决策日志.md)）。

簿记逻辑与 Skill 侧账本同形，收在共用基类 :class:`~st_agent.l1.permission_book.PermissionBook`
（`T-L1-009.1`：消除两份近似实现）；批准态形态与展示措辞住契约层
:mod:`st_agent.contracts.permissions`，本模块按既有路径 re-export，不改调用方。

布局（只经 ``Store`` 读写）：``config`` 分区 ``mcp-permission/<server_id>.json``。
"""

from __future__ import annotations

from st_agent.contracts.permissions import PERMISSION_KINDS, describe_permission
from st_agent.l1.mcp.errors import McpPermissionError, McpValidationError
from st_agent.l1.mcp.ids import check_server_id
from st_agent.l1.permission_book import PermissionBook

__all__ = [
    "PERMISSION_PREFIX",
    "PERMISSION_KINDS",
    "McpPermissionBook",
    "describe_permission",
]

PERMISSION_PREFIX = "mcp-permission/"
"""``config`` 分区内批准状态记录的目录前缀。"""


class McpPermissionBook(PermissionBook):
    """某台 MCP Server 的权限批准账本（落 ``config`` 分区）。

    :param store: ``Store`` 句柄
    """

    def __init__(self, store) -> None:
        super().__init__(
            store,
            prefix=PERMISSION_PREFIX,
            check_key=check_server_id,
            key_label="MCP Server",
            error_cls=McpPermissionError,
            validation_cls=McpValidationError,
        )
