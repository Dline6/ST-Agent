"""T-AGT-019 测试：MCP resources 协议面与注册表接入（03 §5.5）。

GWT 对照：
- GWT-1：已连 Server → ``list_resources`` 返回资源清单（含分页拼接）。
- GWT-2：``read_resource`` 返回内容（text / blob 恰一）。
- GWT-3：Server 未声明 resources 能力（或 -32601）→ 显式不可用，不返回假空清单。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from st_agent.l0.storage import Store
from st_agent.l1.mcp import (
    McpCapabilityUnavailableError,
    McpMethodNotFoundError,
    McpServerNotFoundError,
    McpServerRegistry,
    McpTransport,
    McpValidationError,
)
from st_agent.l1.mcp.client import MAX_RESOURCE_PAGES

PASS = "correct horse battery staple"
FAKE_COMMAND = "python"
STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"

RESOURCES_A = [
    {"uri": "stub://b/second", "name": "第二项", "description": "b", "mimeType": "text/plain"},
    {"uri": "stub://a/first", "name": "第一项", "mimeType": "application/json"},
]
RESOURCES_B = [
    {"uri": "stub://c/third", "name": "第三项", "mimeType": "text/plain"},
]
CONTENTS = [
    {"uri": "stub://a/first", "mimeType": "application/json", "text": '{"ok":true}'},
    {"uri": "stub://a/first", "mimeType": "application/octet-stream", "blob": "AAECAw=="},
]


class ResourceTransport(McpTransport):
    """离线替身：可控能力声明、分页与内容。"""

    def __init__(
        self,
        *,
        capabilities: dict | None = None,
        pages: list[list[dict]] | None = None,
        contents: list[dict] | None = None,
        method_error: str | None = None,
        endless: bool = False,
    ) -> None:
        super().__init__()
        self._capabilities = (
            capabilities
            if capabilities is not None
            else {"resources": {"listChanged": False}}
        )
        self._pages = pages if pages is not None else [RESOURCES_A]
        self._contents = contents if contents is not None else CONTENTS
        self._method_error = method_error
        self._endless = endless
        self.sent: list[dict] = []

    def _open(self) -> None:
        return None

    def _close(self) -> None:
        return None

    def _write(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def _read(self, request_id: int, timeout_ms: int) -> str:
        message = self.sent[-1]
        method = message["method"]
        params = message.get("params") or {}
        if method == self._method_error:
            return json.dumps({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            })
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": self._capabilities,
                "serverInfo": {"name": "resource-stub", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {"tools": []}
        elif method == "resources/list":
            page = 0 if "cursor" not in params else int(params["cursor"])
            result = {"resources": self._pages[page] if page < len(self._pages) else []}
            if self._endless or page + 1 < len(self._pages):
                result["nextCursor"] = str(page + 1)
        elif method == "resources/read":
            result = {"contents": self._contents}
        else:
            result = {}
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def _registry(store: Store, transport: McpTransport, server_id: str = "srv_a"):
    registry = McpServerRegistry(store, transport_factory=lambda _record: transport)
    registry.add_server(server_id, display_name="资源 Server", command=FAKE_COMMAND)
    return registry


class TestListResources:
    """GWT-1。"""

    def test_lists_all_pages_and_sorts_by_uri(self, store: Store):
        transport = ResourceTransport(pages=[RESOURCES_A, RESOURCES_B])
        registry = _registry(store, transport)
        resources = registry.list_resources("srv_a")
        assert [item.uri for item in resources] == [
            "stub://a/first", "stub://b/second", "stub://c/third",
        ]
        assert resources[0].name == "第一项"
        assert resources[0].mime_type == "application/json"
        list_calls = [msg for msg in transport.sent if msg["method"] == "resources/list"]
        assert len(list_calls) == 2
        assert list_calls[1]["params"] == {"cursor": "1"}

    def test_pagination_beyond_limit_fails_instead_of_truncating(self, store: Store):
        registry = _registry(store, ResourceTransport(endless=True))
        with pytest.raises(McpValidationError) as excinfo:
            registry.list_resources("srv_a")
        assert str(MAX_RESOURCE_PAGES) in str(excinfo.value)


class TestReadResource:
    """GWT-2。"""

    def test_read_returns_text_and_blob(self, store: Store):
        registry = _registry(store, ResourceTransport())
        contents = registry.read_resource("srv_a", "stub://a/first")
        assert len(contents) == 2
        assert contents[0].text == '{"ok":true}'
        assert contents[0].blob is None
        assert contents[1].blob == "AAECAw=="
        assert contents[1].text is None

    def test_empty_contents_is_an_empty_tuple(self, store: Store):
        registry = _registry(store, ResourceTransport(contents=[]))
        assert registry.read_resource("srv_a", "stub://a/first") == ()

    def test_blank_uri_is_rejected(self, store: Store):
        registry = _registry(store, ResourceTransport())
        with pytest.raises(McpValidationError):
            registry.read_resource("srv_a", "  ")


class TestUnavailable:
    """GWT-3。"""

    def test_capability_missing_is_explicitly_unavailable(self, store: Store):
        transport = ResourceTransport(capabilities={"tools": {"listChanged": False}})
        registry = _registry(store, transport)
        with pytest.raises(McpCapabilityUnavailableError) as excinfo:
            registry.list_resources("srv_a")
        assert "未声明 resources" in str(excinfo.value)
        assert not [msg for msg in transport.sent if msg["method"] == "resources/list"]

    def test_method_not_found_maps_to_explicit_error(self, store: Store):
        registry = _registry(
            store, ResourceTransport(method_error="resources/list"),
        )
        with pytest.raises(McpMethodNotFoundError):
            registry.list_resources("srv_a")

    def test_disabled_server_is_unavailable(self, store: Store):
        registry = _registry(store, ResourceTransport())
        registry.disable_server("srv_a")
        with pytest.raises(McpCapabilityUnavailableError) as excinfo:
            registry.list_resources("srv_a")
        assert "已禁用" in str(excinfo.value)

    def test_unregistered_server_is_reported(self, store: Store):
        registry = _registry(store, ResourceTransport())
        with pytest.raises(McpServerNotFoundError):
            registry.list_resources("srv_missing")


class TestStdioWireFormat:
    """真 stdio 往返：协议报文形态与能力开关。"""

    def test_stub_server_lists_and_reads(self, store: Store):
        registry = McpServerRegistry(store)
        registry.add_server(
            "stub", display_name="资源存根", command=sys.executable, args=[str(STUB)],
        )
        resources = registry.list_resources("stub")
        assert {item.uri for item in resources} == {
            "stub://watchlist/default", "stub://notes/readme",
        }
        contents = registry.read_resource("stub", "stub://notes/readme")
        assert contents[0].text.startswith("离线存根资源")
        assert contents[0].mime_type == "text/plain"

    def test_stub_without_resources_capability_is_unavailable(self, store: Store):
        registry = McpServerRegistry(store)
        registry.add_server(
            "stub_nr", display_name="无资源存根",
            command=sys.executable, args=[str(STUB), "--no-resources"],
        )
        with pytest.raises(McpCapabilityUnavailableError):
            registry.list_resources("stub_nr")
