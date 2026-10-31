"""MCP 客户端（T-L1-002.1；03 §5.1；resources 见 03 §5.5）。

只做「一次会话内的协议动作」，不持有 Server 记录、不落盘：

- ``initialize``：MCP 握手（协议版本 + 能力 + 客户端标识）
- ``list_tools``：拉取 tool 列表 → ``ToolSpec``（``.2`` 据此派生 SkillDescriptor）
- ``call_tool``：调用一个 tool（``.3`` 接成状态机闭环时消费）
- ``list_resources`` / ``read_resource``：MCP resources（``T-AGT-019``）——
  以握手的 ``capabilities.resources`` 判定能力，未声明即显式不可用

传输由上层注入（``StdioTransport`` / ``HttpSseTransport``），故客户端本身
对「本地还是远程」无知——两者共用同一份协议逻辑。
"""

from __future__ import annotations

from typing import Any

from st_agent.l1.mcp.errors import (
    McpCapabilityUnavailableError,
    McpValidationError,
)
from st_agent.l1.mcp.models import McpResourceContent, McpResourceSpec, ToolSpec
from st_agent.l1.mcp.transports import McpTransport

__all__ = ["MAX_RESOURCE_PAGES", "McpClient"]

MAX_RESOURCE_PAGES = 100
"""``resources/list`` 分页跟随上限（超限显式失败，不静默截断——任务假设 A2）。"""


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


def _resource_spec(raw: Any) -> McpResourceSpec:
    """把 ``resources/list`` 的一个条目转成 ``McpResourceSpec``（缺字段即显式拒）。"""
    if not isinstance(raw, dict):
        raise McpValidationError("MCP resource 条目不是 JSON 对象")
    uri = raw.get("uri")
    name = raw.get("name")
    if not isinstance(uri, str) or not uri.strip():
        raise McpValidationError("MCP resource 缺少 uri")
    if not isinstance(name, str) or not name.strip():
        raise McpValidationError(f"MCP resource {uri!r} 缺少 name")
    description = raw.get("description")
    mime_type = raw.get("mimeType")
    try:
        return McpResourceSpec(
            uri=uri.strip(),
            name=name.strip(),
            description=description if isinstance(description, str) else "",
            mime_type=mime_type if isinstance(mime_type, str) else "",
        )
    except Exception as exc:
        raise McpValidationError(f"MCP resource {uri!r} 描述非法：{exc}") from exc


def _resource_content(raw: Any) -> McpResourceContent:
    """把 ``resources/read`` 的一个 ``contents`` 条目转成 ``McpResourceContent``。"""
    if not isinstance(raw, dict):
        raise McpValidationError("MCP resource 内容条目不是 JSON 对象")
    uri = raw.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise McpValidationError("MCP resource 内容缺少 uri")
    text = raw.get("text")
    blob = raw.get("blob")
    mime_type = raw.get("mimeType")
    try:
        return McpResourceContent(
            uri=uri.strip(),
            mime_type=mime_type if isinstance(mime_type, str) else "",
            text=text if isinstance(text, str) else None,
            blob=blob if isinstance(blob, str) else None,
        )
    except McpValidationError:
        raise
    except Exception as exc:
        raise McpValidationError(f"MCP resource {uri!r} 内容非法：{exc}") from exc


def _next_cursor(result: dict[str, Any]) -> str | None:
    """取 ``resources/list`` 的下一页游标（缺失或空串即取尽；形态非法即拒）。"""
    cursor = result.get("nextCursor")
    if cursor is None or cursor == "":
        return None
    if not isinstance(cursor, str):
        raise McpValidationError("resources/list 的 nextCursor 不是字符串")
    return cursor


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

    # ───────────────────────── resources（03 §5.5） ─────────────────────────

    def list_resources(self) -> tuple[McpResourceSpec, ...]:
        """拉取该 Server 的全部 resource（自动跟随 ``nextCursor``，按 URI 升序）。

        Server 未声明 ``capabilities.resources`` → ``McpCapabilityUnavailableError``
        （GWT-3 的显式不可用）；分页超上限 → ``McpValidationError``（不静默截断）。
        """
        self._require_resources()
        out: list[McpResourceSpec] = []
        cursor: str | None = None
        for _page in range(MAX_RESOURCE_PAGES):
            params = {"cursor": cursor} if cursor is not None else {}
            result = self._transport.request("resources/list", params)
            raw_resources = result.get("resources")
            if raw_resources is None:
                raw_resources = []
            if not isinstance(raw_resources, list):
                raise McpValidationError("resources/list 的 resources 字段不是数组")
            out.extend(_resource_spec(item) for item in raw_resources)
            cursor = _next_cursor(result)
            if cursor is None:
                break
        else:
            raise McpValidationError(
                f"resources/list 分页超过 {MAX_RESOURCE_PAGES} 页仍未取尽——"
                "拒绝静默截断清单"
            )
        return tuple(sorted(out, key=lambda item: (item.uri, item.name)))

    def read_resource(self, uri: str) -> tuple[McpResourceContent, ...]:
        """读取一个 resource 的内容（``contents`` 原样规范化；空清单即空元组）。"""
        if not isinstance(uri, str) or not uri.strip():
            raise McpValidationError("read_resource 的 uri 不得为空")
        self._require_resources()
        result = self._transport.request("resources/read", {"uri": uri.strip()})
        raw_contents = result.get("contents")
        if raw_contents is None:
            return ()
        if not isinstance(raw_contents, list):
            raise McpValidationError("resources/read 的 contents 字段不是数组")
        return tuple(_resource_content(item) for item in raw_contents)

    def _require_resources(self) -> None:
        """GWT-3：以握手能力判定；未声明即显式不可用，不硬发请求。"""
        if not self._initialized:
            self.initialize()
        if not self.supports_resources:
            name = self.server_name or "该 Server"
            raise McpCapabilityUnavailableError(
                f"{name} 未声明 resources 能力（initialize 的 capabilities.resources）"
            )

    # ───────────────────────── 握手回执 ─────────────────────────

    @property
    def capabilities(self) -> dict[str, Any]:
        """握手带回的 Server 能力声明（03 §5.5）。"""
        return self._transport.capabilities

    @property
    def supports_resources(self) -> bool:
        """Server 是否声明了 resources 能力（``capabilities.resources`` 为对象）。"""
        return isinstance(self.capabilities.get("resources"), dict)

    @property
    def protocol_version(self) -> str:
        return self._transport.protocol_version

    @property
    def server_name(self) -> str:
        return self._transport.server_name

    @property
    def server_version(self) -> str:
        return self._transport.server_version
