"""T-L1-002.1 测试：03 §5.1 传输与注册 + §5.4 权限模型。

GWT 对照（任务文件 5 条）：
- GWT-1 添加即连接测试（成功落盘 / 失败不落盘）
- GWT-2 默认只允许本地 stdio；远程未显式确认即拒
- GWT-3 远程显式开启 + 数据外发风险标注 + 出网经网关登记
- GWT-4 注册表增删启停
- GWT-5 权限声明经 01 §10 校验 + 逐项批准
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from st_agent.l1.mcp import (
    HttpSseTransport,
    McpConnectionError,
    McpPermissionError,
    McpServerExistsError,
    McpServerNotFoundError,
    McpServerRecord,
    McpServerRegistry,
    McpTransport,
    McpValidationError,
)

PASS = "correct horse battery staple"
STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"
URL = "https://mcp.example.com/rpc"

CACHE_SCOPE = "local_read:<data/cache/**>"
BAOSTOCK_NET = "net_access:<*.baostock.com>"
EXEC = "exec_command"

STUB_TOOLS = [{
    "name": "echo_symbol",
    "description": "回显证券代码",
    "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
}]

FAKE_COMMAND = "python"
"""占位命令：注入 ``transport_factory`` 的用例下，记录仍须合法，但命令不会被真实执行。"""


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


class FakeTransport(McpTransport):
    """离线替身传输：不碰子进程与网络，行为可控（故障注入用）。"""

    def __init__(self, *, server_name: str = "fake-mcp", tools=(), fail: str | None = None):
        super().__init__()
        self.config_server_name = server_name
        self.config_tools = list(tools)
        self.fail = fail
        self.sent: list[dict] = []
        self.opened = False
        self.closed = False

    def _open(self) -> None:
        self.opened = True
        if self.fail == "open":
            raise McpConnectionError("模拟：无法建立连接")

    def _close(self) -> None:
        self.closed = True

    def _write(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def _read(self, request_id: int, timeout_ms: int) -> str:
        if self.fail == "read":
            raise McpConnectionError("模拟：读取失败")
        method = self.sent[-1]["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": self.config_server_name, "version": "1.0"},
            }
        elif method == "tools/list":
            result = {"tools": self.config_tools}
        else:
            result = {}
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


def make_post():
    """HTTP-SSE 的离线发包替身（走真实 HttpSseTransport + 真实网关）。"""

    def _post(_url: str, body: str, _timeout_ms: int) -> str:
        message = json.loads(body)
        if message.get("method") == "notifications/initialized":
            return ""
        result = (
            {"protocolVersion": "2025-06-18",
             "serverInfo": {"name": "remote-stub", "version": "3.0"}}
            if message.get("method") == "initialize"
            else {"tools": STUB_TOOLS}
        )
        return json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result})

    return _post


def stub_args(*extra: str) -> list[str]:
    return [str(STUB), *extra]


def _persisted(store: Store) -> list[str]:
    return [n for n in store.list_files("config") if n.startswith("mcp-server/")]


class TestAddAndConnect:
    """GWT-1：添加即连接测试；失败不落盘。"""

    def test_stdio_add_runs_connection_test_and_persists(self, store: Store):
        registry = McpServerRegistry(store)
        result = registry.add_server(
            "stub_local", display_name="本地存根 Server",
            command=sys.executable, args=stub_args(), env_refs=("STUB_MODE",),
            permissions=(BAOSTOCK_NET,),
        )
        assert result.ok
        assert result.server_name == "stub-mcp-server"
        assert result.protocol_version == "2025-06-18"
        assert [t.name for t in result.tools] == ["echo_symbol", "list_watch_groups"]
        assert result.permissions == (BAOSTOCK_NET,)
        record = registry.get_server("stub_local")
        assert record.transport == "stdio"
        assert record.env_refs == ("STUB_MODE",)
        assert record.command == sys.executable
        assert _persisted(store) == ["mcp-server/stub_local.json"]

    def test_connection_failure_returns_result_without_persisting(self, store: Store):
        registry = McpServerRegistry(store)
        result = registry.add_server(
            "stub_crash", display_name="会崩的 Server",
            command=sys.executable, args=stub_args("--crash"),
        )
        assert not result.ok
        assert result.reason.strip()
        assert _persisted(store) == []

    def test_missing_command_fails_without_persisting(self, store: Store):
        registry = McpServerRegistry(store)
        result = registry.add_server(
            "stub_missing", display_name="不存在的命令",
            command="definitely-not-a-real-binary-xyz",
        )
        assert not result.ok
        assert _persisted(store) == []

    def test_test_connection_on_existing_server(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport(
            server_name="fake-mcp", tools=STUB_TOOLS))
        registry.add_server("srv_a", display_name="A", command=FAKE_COMMAND)
        fresh = registry.test_connection("srv_a")
        assert fresh.ok
        assert fresh.server_name == "fake-mcp"
        assert fresh.tools[0].name == "echo_symbol"

    def test_duplicate_id_is_rejected(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        registry.add_server("srv_a", display_name="A", command=FAKE_COMMAND)
        with pytest.raises(McpServerExistsError):
            registry.add_server("srv_a", display_name="A 又一份", command=FAKE_COMMAND)


class TestLocalOnlyByDefault:
    """GWT-2：默认只允许本地 stdio。"""

    def test_remote_without_acknowledgement_is_rejected(self, store: Store):
        registry = McpServerRegistry(store, gateway=EgressGateway(store))
        with pytest.raises(McpValidationError) as excinfo:
            registry.add_server("srv_remote", display_name="远程 Server", transport="remote", url=URL)
        assert "离开本机" in str(excinfo.value)
        assert _persisted(store) == []

    def test_remote_url_requires_http_scheme(self, store: Store):
        registry = McpServerRegistry(store, gateway=EgressGateway(store))
        with pytest.raises(McpValidationError):
            registry.add_server(
                "srv_remote", display_name="远程 Server", transport="remote",
                url="ftp://mcp.example.com/rpc", remote_acknowledged=True,
            )

    def test_stdio_carries_no_url(self):
        with pytest.raises(McpValidationError):
            McpServerRecord(
                server_id="srv_a", display_name="A", transport="stdio",
                command="python", url=URL, created_at=datetime.now().astimezone(),
            )


class TestRemoteServer:
    """GWT-3：远程显式开启 + 风险标注 + 出网经网关登记。"""

    def test_acknowledged_remote_persists_and_audits(self, store: Store):
        gateway = EgressGateway(store)
        registry = McpServerRegistry(
            store, gateway=gateway,
            transport_factory=lambda r: HttpSseTransport(r.url, gateway, r.server_id, post=make_post()),
        )
        result = registry.add_server(
            "srv_remote", display_name="远程行情 Server", transport="remote",
            url=URL, remote_acknowledged=True, permissions=(BAOSTOCK_NET,),
        )
        assert result.ok
        record = registry.get_server("srv_remote")
        assert record.remote_acknowledged is True
        assert record.data_egress_risk.strip()
        audit = gateway.query(kind="remote_mcp", initiator="srv_remote")
        assert audit and all(e.target_host == "mcp.example.com" for e in audit)

    def test_remote_without_gateway_is_explicit(self, store: Store):
        registry = McpServerRegistry(store)
        result = registry.add_server(
            "srv_remote", display_name="远程 Server", transport="remote",
            url=URL, remote_acknowledged=True,
        )
        assert not result.ok
        assert "网关" in result.reason
        assert _persisted(store) == []


class TestLifecycle:
    """GWT-4：增删启停。"""

    @pytest.fixture()
    def registry(self, store: Store) -> McpServerRegistry:
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        registry.add_server("srv_a", display_name="A", command=FAKE_COMMAND)
        registry.add_server("srv_b", display_name="B", command=FAKE_COMMAND)
        return registry

    def test_list_is_sorted(self, registry: McpServerRegistry):
        assert [r.server_id for r in registry.list_servers()] == ["srv_a", "srv_b"]

    def test_disable_enable_are_idempotent(self, registry: McpServerRegistry):
        assert registry.disable_server("srv_a").enabled is False
        assert registry.disable_server("srv_a").enabled is False
        assert registry.get_server("srv_a").enabled is False
        assert registry.enable_server("srv_a").enabled is True
        assert registry.get_server("srv_a").enabled is True

    def test_disable_survives_reload(self, store: Store, registry: McpServerRegistry):
        registry.disable_server("srv_a")
        assert McpServerRegistry(store).get_server("srv_a").enabled is False

    def test_remove_drops_record_and_approvals(self, store: Store, registry: McpServerRegistry):
        registry.permissions.declare("srv_a", (BAOSTOCK_NET,))
        registry.remove_server("srv_a")
        with pytest.raises(McpServerNotFoundError):
            registry.get_server("srv_a")
        assert registry.permissions.approvals("srv_a") == ()

    def test_unknown_server_is_explicit(self, registry: McpServerRegistry):
        with pytest.raises(McpServerNotFoundError):
            registry.disable_server("srv_zzz")
        with pytest.raises(McpServerNotFoundError):
            registry.remove_server("srv_zzz")

    def test_remove_invokes_hook(self, store: Store):
        """T-L1-007：注入 `on_server_removed` 时删除即回调（组合根的接线点）。"""
        seen: list[str] = []
        registry = McpServerRegistry(
            store, transport_factory=lambda _r: FakeTransport(),
            on_server_removed=seen.append,
        )
        registry.add_server("srv_a", display_name="A", command=FAKE_COMMAND)
        registry.remove_server("srv_a")
        assert seen == ["srv_a"]

    def test_remove_without_hook_is_unchanged(self, registry: McpServerRegistry):
        """缺省 `None` 时不触发任何回调，行为与既有完全一致。"""
        registry.remove_server("srv_a")
        with pytest.raises(McpServerNotFoundError):
            registry.get_server("srv_a")
        assert [r.server_id for r in registry.list_servers()] == ["srv_b"]

    def test_failed_remove_does_not_invoke_hook(self, store: Store):
        """未注册的 id → 抛错且不回调（不误报删除成功）。"""
        seen: list[str] = []
        registry = McpServerRegistry(store, on_server_removed=seen.append)
        with pytest.raises(McpServerNotFoundError):
            registry.remove_server("srv_zzz")
        assert seen == []


class TestPermissions:
    """GWT-5：01 §10 声明 + 逐项批准。"""

    @pytest.fixture()
    def registry(self, store: Store) -> McpServerRegistry:
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        registry.add_server(
            "srv_a", display_name="A", command=FAKE_COMMAND,
            permissions=(CACHE_SCOPE, BAOSTOCK_NET, EXEC),
        )
        return registry

    def test_declared_permissions_start_pending(self, registry: McpServerRegistry):
        book = registry.permissions
        assert book.pending_permissions("srv_a") == (CACHE_SCOPE, BAOSTOCK_NET, EXEC)
        assert book.approved_permissions("srv_a") == ()

    def test_approve_and_reject_are_per_item(self, registry: McpServerRegistry):
        book = registry.permissions
        book.approve("srv_a", BAOSTOCK_NET)
        book.reject("srv_a", EXEC)
        assert book.approved_permissions("srv_a") == (BAOSTOCK_NET,)
        assert book.pending_permissions("srv_a") == (CACHE_SCOPE,)
        assert [a.decision for a in book.approvals("srv_a")] == ["pending", "approved", "rejected"]

    def test_approval_survives_reload(self, store: Store, registry: McpServerRegistry):
        registry.permissions.approve("srv_a", CACHE_SCOPE)
        assert McpServerRegistry(store).permissions.approved_permissions("srv_a") == (CACHE_SCOPE,)

    def test_undeclared_permission_cannot_be_approved(self, registry: McpServerRegistry):
        with pytest.raises(McpPermissionError):
            registry.permissions.approve("srv_a", "net_access:<other.example.com>")

    def test_invalid_permission_syntax_is_rejected(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        with pytest.raises(McpValidationError):
            registry.add_server(
                "srv_bad", display_name="B", command=FAKE_COMMAND,
                permissions=("net_access",),
            )
        assert _persisted(store) == []

    def test_redeclare_keeps_existing_decisions(self, registry: McpServerRegistry):
        registry.permissions.approve("srv_a", BAOSTOCK_NET)
        registry.permissions.declare("srv_a", (BAOSTOCK_NET, EXEC))
        assert registry.permissions.approved_permissions("srv_a") == (BAOSTOCK_NET,)
        assert registry.permissions.pending_permissions("srv_a") == (EXEC,)

    def test_describe_explains_what_the_server_wants(self, registry: McpServerRegistry):
        lines = registry.permissions.describe("srv_a")
        assert len(lines) == 3
        assert any("读取声明范围内的本地文件" in ln for ln in lines)
        assert any("访问声明的远程主机" in ln for ln in lines)
        assert any("最高风险" in ln for ln in lines)
        assert all("待批准" in ln for ln in lines)


class TestRecordValidation:
    def test_server_id_shape(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        for bad in ("", "../escape", "a/b", "has space"):
            with pytest.raises(McpValidationError):
                registry.add_server(bad, display_name="X", command=FAKE_COMMAND)

    def test_display_name_required(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        with pytest.raises(McpValidationError):
            registry.add_server("srv_a", display_name="", command=FAKE_COMMAND)

    def test_update_server_reuses_existing_id(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        registry.add_server("srv_a", display_name="A", command=FAKE_COMMAND)
        updated = registry.update_server(
            "srv_a", display_name="A（改名）", command=FAKE_COMMAND
        )
        assert updated.ok
        assert registry.get_server("srv_a").display_name == "A（改名）"

    def test_update_unknown_server_is_explicit(self, store: Store):
        registry = McpServerRegistry(store, transport_factory=lambda _r: FakeTransport())
        with pytest.raises(McpServerNotFoundError):
            registry.update_server("srv_zzz", display_name="X", command=FAKE_COMMAND)
