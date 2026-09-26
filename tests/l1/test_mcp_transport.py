"""T-L1-002.1 测试：03 §5.1 传输层（stdio / HTTP-SSE）。

覆盖：
- stdio 传输对真实子进程走通 ``initialize`` / ``tools/list`` / ``tools/call``
- stdio 故障路径：进程立刻退出、命令不存在、握手无响应（超时）
- HTTP-SSE 传输**经 L0 网关**：注入发包替身即可离线验证，且每次出网留审计
- 网关离线 → 传输显式失败 + 审计记 ``unavailable``
- ``extract_response`` 的 JSON / SSE / 空 / 查无此 id 四条分支
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from st_agent.l0.net import EgressGateway, EgressUnavailableError
from st_agent.l0.storage import Store
from st_agent.l1.mcp import (
    HttpSseTransport,
    McpClient,
    McpConnectionError,
    StdioTransport,
    extract_response,
)

PASS = "correct horse battery staple"
STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"
URL = "https://mcp.example.com/rpc"
SERVER_ID = "srv_remote"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store)


def _stub(*extra: str) -> tuple[str, list[str]]:
    return sys.executable, [str(STUB), *extra]


def make_post(*, style: str = "json", broken: bool = False):
    """造一个发包替身：按请求的 method 回包（HTTP-SSE 的离线对端）。"""

    def _post(_url: str, body: str, _timeout_ms: int) -> str:
        if broken:
            raise EgressUnavailableError("模拟：端点不可达")
        message = json.loads(body)
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            return ""
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "remote-stub", "version": "3.0"},
            }
        elif method == "tools/list":
            result = {"tools": [{
                "name": "remote_probe",
                "description": "远程存根 tool",
                "inputSchema": {"type": "object", "properties": {}},
            }]}
        else:
            result = {}
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
        return f"data: {payload}\n\n" if style == "sse" else payload

    return _post


class TestStdioTransport:
    """本地 stdio 传输（03 §5.1「本地运行优先」）。"""

    def test_handshake_and_tools(self):
        command, args = _stub()
        with StdioTransport(command, args, timeout_ms=20_000) as transport:
            client = McpClient(transport)
            client.initialize()
            assert transport.server_name == "stub-mcp-server"
            assert transport.server_version == "2.1"
            assert transport.protocol_version == "2025-06-18"
            tools = client.list_tools()
        assert [t.name for t in tools] == ["echo_symbol", "list_watch_groups"]
        first = tools[0]
        assert first.input_schema["required"] == ["symbol"]
        assert first.output_schema["type"] == "object"

    def test_call_tool(self):
        command, args = _stub()
        with StdioTransport(command, args, timeout_ms=20_000) as transport:
            client = McpClient(transport)
            client.initialize()
            result = client.call_tool("echo_symbol", {"symbol": "600519"})
        assert result["content"][0]["text"] == "stub called echo_symbol"

    def test_process_exits_immediately(self):
        command, args = _stub("--crash")
        with StdioTransport(command, args, timeout_ms=5_000) as transport:
            with pytest.raises(McpConnectionError):
                McpClient(transport).initialize()

    def test_command_missing(self):
        with pytest.raises(McpConnectionError):
            StdioTransport("definitely-not-a-real-binary-xyz", [], timeout_ms=5_000).open()

    def test_silent_server_times_out(self):
        command, args = _stub("--silent")
        with StdioTransport(command, args, timeout_ms=400) as transport:
            with pytest.raises(McpConnectionError) as excinfo:
                McpClient(transport).initialize()
        assert "超时" in str(excinfo.value)


class TestHttpSseTransport:
    """远程 HTTP-SSE 传输——出网一律经 L0 网关（02 §6）。"""

    @pytest.mark.parametrize("style", ["json", "sse"])
    def test_round_trip_through_gateway(self, gateway: EgressGateway, style: str):
        transport = HttpSseTransport(
            URL, gateway, SERVER_ID, post=make_post(style=style), timeout_ms=5_000
        )
        client = McpClient(transport)
        client.initialize()
        tools = client.list_tools()
        transport.close()
        assert [t.name for t in tools] == ["remote_probe"]
        # 三次出网（initialize + 通知 + tools/list）全部留下审计
        audit = gateway.query(kind="remote_mcp", initiator=SERVER_ID)
        assert len(audit) == 3
        assert {e.target_host for e in audit} == {"mcp.example.com"}
        assert all(e.status == "ok" for e in audit)

    def test_gateway_offline_is_explicit(self, gateway: EgressGateway):
        gateway.set_online(False)
        transport = HttpSseTransport(
            URL, gateway, SERVER_ID, post=make_post(), timeout_ms=5_000
        )
        with pytest.raises(McpConnectionError) as excinfo:
            McpClient(transport).initialize()
        assert "unavailable" in str(excinfo.value)
        assert gateway.query(status="unavailable")

    def test_sender_failure_is_audited(self, gateway: EgressGateway):
        transport = HttpSseTransport(
            URL, gateway, SERVER_ID, post=make_post(broken=True), timeout_ms=5_000
        )
        with pytest.raises(McpConnectionError):
            McpClient(transport).initialize()
        assert gateway.query(kind="remote_mcp", status="unavailable")

    def test_requires_gateway(self):
        transport = HttpSseTransport(URL, None, SERVER_ID, post=make_post())
        with pytest.raises(McpConnectionError) as excinfo:
            McpClient(transport).initialize()
        assert "网关" in str(excinfo.value)


class TestExtractResponse:
    def test_plain_json(self):
        assert extract_response('{"id": 1, "result": {}}', 1) == '{"id": 1, "result": {}}'

    def test_sse_picks_matching_id(self):
        raw = 'data: {"id": 7, "result": {"a": 1}}\n\ndata: {"id": 8, "result": {}}\n\n'
        assert json.loads(extract_response(raw, 7))["result"] == {"a": 1}

    def test_empty_response(self):
        with pytest.raises(McpConnectionError):
            extract_response("", 1)

    def test_id_not_found(self):
        with pytest.raises(McpConnectionError) as excinfo:
            extract_response('{"id": 2, "result": {}}', 1)
        assert "id=1" in str(excinfo.value)
