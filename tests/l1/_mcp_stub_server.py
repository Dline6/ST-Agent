"""测试用的最小 MCP Server（stdio 传输的对端；不进 pytest 收集）。

只实现 ``T-L1-002.1`` 协议面用到的三个方法 + 通知，外加两个故障开关：

- ``--crash``：立刻退出（模拟 Server 崩溃 / 启动失败）
- ``--silent``：只读不回（模拟握手无响应，用于超时路径）

用法：``python _mcp_stub_server.py [--crash|--silent]``
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "stub-mcp-server"
SERVER_VERSION = "2.1"

TOOLS = [
    {
        "name": "echo_symbol",
        "description": "回显传入的证券代码（离线存根，不做任何真实查询）",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
        },
    },
    {
        "name": "list_watch_groups",
        "description": "列出关注分组（离线存根）",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _reply(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return                                     # 通知无响应
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        result = {
            "content": [{"type": "text", "text": f"stub called {params.get('name')}"}],
            "isError": False,
        }
    else:
        _reply({
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        })
        return
    _reply({"jsonrpc": "2.0", "id": request_id, "result": result})


def main(argv: list[str]) -> int:
    if "--crash" in argv:
        return 3
    silent = "--silent" in argv
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if silent:
            continue
        _handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
