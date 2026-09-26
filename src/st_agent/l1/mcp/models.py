"""MCP Server 记录与传输数据形态（T-L1-002.1；03 §5.1 §5.4；01 §10）。

- ``McpServerRecord``：一个 MCP Server 的注册记录（落 ``config`` 分区
  ``mcp-server/<server_id>.json``）。``transport`` 二选一，各自必填项由
  构造期校验强制——stdio 要 ``command``，远程要 ``url`` + ``remote_acknowledged``。
- ``ToolSpec``：MCP tool 的最小描述（``tools/list`` 的解析结果；``.2`` 据其
  派生 SkillDescriptor，故 schema 字段原样保留，不在本层解释）。
- ``ConnectionTestResult``：一次连接测试的结论（GWT-1 的显式回执）。
- ``PermissionApproval``：一条权限声明的批准状态（GWT-5；01 §10 逐项批准）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.registry_types import validate_permissions
from st_agent.l1.mcp.errors import McpValidationError
from st_agent.l1.mcp.ids import check_remote_url, check_server_id

__all__ = [
    "DATA_EGRESS_RISK_TEXT",
    "McpPermissionDecision",
    "McpServerRecord",
    "McpTransportKind",
    "PermissionApproval",
    "ToolSpec",
    "ConnectionTestResult",
]

McpTransportKind = Literal["stdio", "remote"]
"""传输种类：``stdio`` 本地子进程 / ``remote`` HTTP-SSE（03 §5.1）。"""

McpPermissionDecision = Literal["pending", "approved", "rejected"]

DATA_EGRESS_RISK_TEXT = "远程 MCP Server：请求与数据经 L0 出网网关离开本机并留审计记录"
"""远程 Server 的数据外发风险标注（固定中性措辞；03 §5.1「数据会离开本机」）。"""


class ToolSpec(BaseModel):
    """MCP tool 的最小描述（``tools/list`` 解析结果，原样承载 schema）。"""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)


class McpServerRecord(BaseModel):
    """一个 MCP Server 的注册记录（03 §5.1；落 ``config`` 分区）。"""

    model_config = ConfigDict(frozen=True)

    server_id: str
    display_name: str = Field(min_length=1, max_length=128)
    transport: McpTransportKind
    command: str | None = None
    """stdio 传输的可执行命令（``transport="stdio"`` 时必填）。"""
    args: tuple[str, ...] = ()
    env_refs: tuple[str, ...] = ()
    """环境变量名的引用清单（**只存变量名**，取值经 ``CredentialVault`` 取，不落注册表）。"""
    url: str | None = None
    """remote 传输的端点 URL（``transport="remote"`` 时必填）。"""
    remote_acknowledged: bool = False
    """用户是否已显式确认「这是远程 Server，数据会离开本机」（03 §5.1）。"""
    data_egress_risk: str = ""
    """数据外发风险标注（remote 必填，取 ``DATA_EGRESS_RISK_TEXT``）。"""
    enabled: bool = True
    permissions: tuple[str, ...] = ()
    """权限声明（01 §10 语法，构造期经 ``validate_permissions`` 复核）。"""
    created_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> "McpServerRecord":
        check_server_id(self.server_id)
        if self.created_at.tzinfo is None:
            raise McpValidationError("created_at 必须带时区语义（01 §8）")
        if self.transport == "stdio":
            if not (self.command or "").strip():
                raise McpValidationError("transport=stdio 时 command 必填（本地子进程命令）")
            if self.url is not None:
                raise McpValidationError("transport=stdio 时不得带 url")
        else:
            if not (self.url or "").strip():
                raise McpValidationError("transport=remote 时 url 必填（HTTP-SSE 端点）")
            check_remote_url(self.url)
            if self.command is not None:
                raise McpValidationError("transport=remote 时不得带 command")
            if not self.remote_acknowledged:
                raise McpValidationError(
                    "远程 MCP Server 须用户显式确认：这是远程 Server，数据会离开本机（03 §5.1）"
                )
            if not self.data_egress_risk.strip():
                raise McpValidationError("远程 MCP Server 必须带数据外发风险标注（03 §5.1）")
        try:
            validate_permissions(tuple(self.permissions))
        except Exception as exc:  # 01 §10 语法非法（ContractViolation）
            raise McpValidationError(f"权限声明非法：{exc}") from exc
        return self


class ConnectionTestResult(BaseModel):
    """一次连接测试的结论（GWT-1；``ok=False`` 时 ``reason`` 必填）。"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    server_id: str
    server_name: str = ""
    protocol_version: str = ""
    permissions: tuple[str, ...] = ()
    """该 Server 在注册时声明的权限项（回执用；批准状态见 ``McpPermissionBook``）。"""
    tools: tuple[ToolSpec, ...] = ()
    reason: str = ""
    tested_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> "ConnectionTestResult":
        check_server_id(self.server_id)
        if self.tested_at.tzinfo is None:
            raise McpValidationError("tested_at 必须带时区语义（01 §8）")
        if not self.ok and not self.reason.strip():
            raise McpValidationError("连接测试失败必须给出可读原因（失败显式化）")
        if self.ok and not self.server_name.strip():
            raise McpValidationError("连接测试成功必须带回 Server 名（GWT-1）")
        return self


class PermissionApproval(BaseModel):
    """一条权限声明的批准状态（GWT-5；01 §10 逐项批准）。"""

    model_config = ConfigDict(frozen=True)

    permission: str = Field(min_length=1)
    decision: McpPermissionDecision
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def _shape(self) -> "PermissionApproval":
        if self.decision == "pending" and self.decided_at is not None:
            raise McpValidationError("pending 权限不得带 decided_at")
        if self.decision != "pending" and self.decided_at is None:
            raise McpValidationError("已决权限必须带 decided_at（逐项批准留痕）")
        if self.decided_at is not None and self.decided_at.tzinfo is None:
            raise McpValidationError("decided_at 必须带时区语义（01 §8）")
        return self
