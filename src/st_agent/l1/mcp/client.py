"""MCP 客户端（T-L1-002.1；03 §5.1）。

只做「一次会话内的协议动作」，不持有 Server 记录、不落盘：

- ``initialize``：MCP 握手（协议版本 + 能力 + 客户端标识）
- ``list_tools``：拉取 tool 列表 → ``ToolSpec``（``.2`` 据此派生 SkillDescriptor）
- ``call_tool``：调用一个 tool（``.3`` 接成状态机闭环时消费）

传输由上层注入（``StdioTransport`` / ``HttpSseTransport``），故客户端本身
对「本地还是远程」无知——两者共用同一份协议逻辑。
"""

from __future__ import annotations

from typing import Any

from st_agent.l1.mcp.errors import McpValidationError
from st_agent.l1.mcp.models import ToolSpec
from st_agent.l1.mcp.transports import McpTransport

__all__ = ["McpClient"]


def _tool_spec(raw: Any) -> ToolSpec:
    """把 ``tools/list`` 的一个条目转成 ``ToolSpec``（字段缺失即显式拒）。"""
    if not isinstance(raw, dict):
        raise McpValidationError("MCP tool 条目不是 JSON 对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise McpValidationError("MCP tool 缺少 name")
    description = raw.get("description")
    input_schema = raw.get("inputSchema")
    output_schema = raw.get("outputSchema")
    try:
        return ToolSpec(
            name=name.strip(),
            description=description if isinstance(description, str) else "",
            input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
            output_schema=dict(output_schema) if isinstance(output_schema, dict) else {},
        )
    except Exception as exc:  # pydantic 约束失败
        raise McpValidationError(f"MCP tool {name!r} 描述非法：{exc}") from exc


class McpClient:
    """一个 MCP Server 的会话客户端（协议面见模块说明）。"""

    def __init__(self, transport: McpTransport) -> None:
        self._transport = transport
        self._initialized = False

    # ───────────────────────── 生命周期 ─────────────────────────

    def open(self) -> None:
        """建立通道（幂等）。"""
        self._transport.open()

    def close(self) -> None:
        """释放通道（幂等）。"""
        self._transport.close()

    def __enter__(self) -> "McpClient":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ───────────────────────── 协议动作 ─────────────────────────

    def initialize(self) -> dict[str, Any]:
        """MCP 握手（幂等：同一会话重复调用直接返回首次结果）。"""
        if self._initialized:
            return {
                "protocolVersion": self.protocol_version,
                "serverInfo": {"name": self.server_name, "version": self.server_version},
            }
        result = self._transport.initialize()
        self._initialized = True
        return result

    def list_tools(self) -> tuple[ToolSpec, ...]:
        """拉取该 Server 的全部 tool（按 name 升序，便于比对与落盘）。"""
        result = self._transport.request("tools/list")
        raw_tools = result.get("tools")
        if raw_tools is None:
            return ()
        if not isinstance(raw_tools, list):
            raise McpValidationError("tools/list 的 tools 字段不是数组")
        return tuple(sorted((_tool_spec(t) for t in raw_tools), key=lambda t: t.name))

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用一个 tool（``.3`` 接成闭环时消费）。"""
        if not isinstance(name, str) or not name.strip():
            raise McpValidationError("call_tool 的 name 不得为空")
        return self._transport.request(
            "tools/call", {"name": name.strip(), "arguments": arguments or {}}
        )

    # ───────────────────────── 握手回执 ─────────────────────────

    @property
    def protocol_version(self) -> str:
        return self._transport.protocol_version

    @property
    def server_name(self) -> str:
        return self._transport.server_name

    @property
    def server_version(self) -> str:
        return self._transport.server_version
