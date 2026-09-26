"""MCP Hub 的标识与端点形状校验（T-L1-002.1）。

- ``server_id``：既是注册表主键，也是 ``config`` 分区内的安全相对路径段
  （``mcp-server/<server_id>.json``）与出网审计的 ``initiator``，故取与
  ``net`` 能力标识同款口径——字母数字开头，仅含字母/数字/``_``/``.``/``-``。
- ``remote_url``：远程 Server 的 HTTP-SSE 端点。只接受 ``http``/``https``
  且必须带主机；主机是出网审计登记的 ``target_host``（02 §6 只记主机，
  不记完整 URL 与 query）。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from st_agent.l1.mcp.errors import McpValidationError

__all__ = [
    "SERVER_ID_PATTERN",
    "check_remote_url",
    "check_server_id",
    "remote_host",
]

SERVER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
"""Server 标识形状（同时是 ``config`` 分区内的安全相对路径段）。"""

_ALLOWED_SCHEMES = ("http", "https")


def check_server_id(value: str) -> str:
    """校验 Server 标识（非法 → ``McpValidationError``）。"""
    if not isinstance(value, str) or not SERVER_ID_PATTERN.match(value):
        raise McpValidationError(
            f"非法 MCP Server 标识 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，"
            "≤64 字符——该标识同时用作注册文件名与出网审计的 initiator）"
        )
    return value


def check_remote_url(value: str) -> str:
    """校验远程端点 URL（只接受 http/https 且须带主机）。"""
    if not isinstance(value, str) or not value.strip():
        raise McpValidationError("远程 MCP Server 的 url 不得为空")
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise McpValidationError(
            f"远程 MCP Server 的 url 只接受 http/https，收到 {parts.scheme or '（无 scheme）'!r}"
        )
    if not parts.hostname:
        raise McpValidationError(f"远程 MCP Server 的 url 必须带主机：{value!r}")
    return url


def remote_host(url: str) -> str:
    """取端点主机（出网审计的 ``target_host``；非法 URL 即拒）。"""
    host = urlsplit(check_remote_url(url)).hostname
    assert host is not None  # check_remote_url 已保证
    return host
