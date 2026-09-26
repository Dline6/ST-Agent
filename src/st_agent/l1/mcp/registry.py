"""MCP Server 注册表（T-L1-002.1；03 §5.1 传输与注册 + §5.4 权限）。

职责与边界：

- **注册表本身**：增 / 删 / 启用 / 禁用 / 查询 + 「添加即连接测试」。
  记录落 ``config`` 分区 ``mcp-server/<server_id>.json``。
- **默认只允许本地 stdio**：远程（HTTP-SSE）记录必须带
  ``remote_acknowledged=True`` 与数据外发风险标注，否则构造期即拒（03 §5.1）。
- **权限**：声明经 01 §10 语法校验后交 ``McpPermissionBook`` 逐项批准（§5.4）。
- **连接测试失败即注册失败**（不落「已连接」记录，失败显式化）。
- **出网**：远程传输一律经注入的 ``EgressGateway``（02 §6），本模块不直连网络。

环境变量只存**变量名**（``env_refs``）；取值由注入的 ``env_resolver`` 提供
（凭据库读取属运行时装配职责，见 `T-L1-006` 组合根 / 决策日志 D-005）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import ValidationError

from st_agent.l1.mcp.client import McpClient
from st_agent.l1.mcp.errors import (
    McpConnectionError,
    McpError,
    McpServerExistsError,
    McpServerNotFoundError,
    McpValidationError,
)
from st_agent.l1.mcp.ids import check_server_id
from st_agent.l1.mcp.models import (
    DATA_EGRESS_RISK_TEXT,
    ConnectionTestResult,
    McpServerRecord,
)
from st_agent.l1.mcp.permissions import McpPermissionBook
from st_agent.l1.mcp.transports import (
    DEFAULT_TIMEOUT_MS,
    HttpSseTransport,
    McpTransport,
    StdioTransport,
)

__all__ = [
    "SERVER_PREFIX",
    "EnvResolver",
    "McpServerRegistry",
    "ServerRemovedHook",
    "TransportFactory",
]

SERVER_PREFIX = "mcp-server/"
"""``config`` 分区内 Server 注册记录的目录前缀。"""

TransportFactory = Callable[[McpServerRecord], McpTransport]
"""由记录造传输的工厂（测试注入替身即得离线用例）。"""

EnvResolver = Callable[[str], str]
"""环境变量名 → 取值（凭据库读取由上层注入；缺省不注入任何变量）。"""

ServerRemovedHook = Callable[[str], None]
"""``remove_server`` 成功后的回调形态（组合根接线到 ``McpSkillMapper.recycle_server``）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class McpServerRegistry:
    """MCP Server 注册表门面（03 §5.1 §5.4）。

    :param store: ``Store`` 句柄（记录与批准状态只写 ``config`` 分区）
    :param gateway: ``EgressGateway``——远程 Server 的唯一出网出口（02 §6）
    :param transport_factory: 可选传输工厂（缺省按记录种类造真实传输）
    :param env_resolver: 可选环境变量解析器（``env_refs`` → 取值）
    :param timeout_ms: 单次收发与出网超时（毫秒）
    :param on_server_removed: 可选回调——``remove_server`` 成功后被调用（T-L1-007：
        由组合根接 ``McpSkillMapper.recycle_server`` 做「删除即回收」；缺省 ``None``
        时 ``remove_server`` 行为与既有完全一致）
    """

    def __init__(
        self,
        store,
        *,
        gateway=None,
        transport_factory: TransportFactory | None = None,
        env_resolver: EnvResolver | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        on_server_removed: ServerRemovedHook | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._factory = transport_factory
        self._env_resolver = env_resolver
        self._timeout_ms = timeout_ms
        self._on_removed = on_server_removed
        self._permissions = McpPermissionBook(store)

    @property
    def permissions(self) -> McpPermissionBook:
        """本注册表对应的权限批准账本（03 §5.4）。"""
        return self._permissions

    # ───────────────────────── 写入 ─────────────────────────

    def add_server(
        self,
        server_id: str,
        *,
        display_name: str,
        transport: str = "stdio",
        command: str | None = None,
        args: Sequence[str] = (),
        env_refs: Sequence[str] = (),
        url: str | None = None,
        remote_acknowledged: bool = False,
        permissions: Sequence[str] = (),
    ) -> ConnectionTestResult:
        """添加一台 MCP Server 并**立即连接测试**（GWT-1）。

        记录非法（传输必填项缺失 / 远程未显式确认 / 权限语法非法）→
        ``McpValidationError``，不落盘（GWT-2 / GWT-5 下半）；
        连接测试失败 → 返回 ``ok=False`` 的结论，**同样不落盘**（GWT-1 失败显式化）。
        """
        sid = check_server_id(server_id)
        if self._path(sid) in self._store.list_files("config"):
            raise McpServerExistsError(
                f"MCP Server {sid!r} 已注册；更新请用 update_server，不得静默覆盖"
            )
        record = self._build_record(
            sid, display_name=display_name, transport=transport, command=command,
            args=args, env_refs=env_refs, url=url,
            remote_acknowledged=remote_acknowledged, permissions=permissions,
        )
        result = self._probe(record)
        if not result.ok:
            return result
        self._save(record)
        self._permissions.declare(sid, record.permissions)
        return result

    def update_server(
        self,
        server_id: str,
        *,
        display_name: str,
        transport: str = "stdio",
        command: str | None = None,
        args: Sequence[str] = (),
        env_refs: Sequence[str] = (),
        url: str | None = None,
        remote_acknowledged: bool = False,
        permissions: Sequence[str] = (),
    ) -> ConnectionTestResult:
        """更新一台已注册 Server（重新连接测试；已声明且未变的权限保留批准结果）。"""
        sid = check_server_id(server_id)
        if self._path(sid) not in self._store.list_files("config"):
            raise McpServerNotFoundError(f"MCP Server {sid!r} 未注册")
        record = self._build_record(
            sid, display_name=display_name, transport=transport, command=command,
            args=args, env_refs=env_refs, url=url,
            remote_acknowledged=remote_acknowledged, permissions=permissions,
        )
        result = self._probe(record)
        if not result.ok:
            return result
        self._save(record)
        self._permissions.declare(sid, record.permissions)
        return result

    def remove_server(self, server_id: str) -> None:
        """移除一台 Server（连同其批准状态；出网审计记录**不动**——审计不可注销）。

        若构造时注入了 ``on_server_removed``，删除成功后调用它（T-L1-007「删除即回收」
        的接线点，由组合根接 ``McpSkillMapper.recycle_server``）。
        """
        sid = check_server_id(server_id)
        if self._path(sid) not in self._store.list_files("config"):
            raise McpServerNotFoundError(f"MCP Server {sid!r} 未注册")
        self._store.delete("config", self._path(sid))
        self._permissions.forget(sid)
        if self._on_removed is not None:
            self._on_removed(sid)

    def enable_server(self, server_id: str) -> McpServerRecord:
        """启用（幂等）。"""
        return self._set_enabled(server_id, True)

    def disable_server(self, server_id: str) -> McpServerRecord:
        """禁用（幂等）：其派生 Skill 的可用性联动由 `T-L1-002.2` 消费。"""
        return self._set_enabled(server_id, False)

    # ───────────────────────── 读取 ─────────────────────────

    def get_server(self, server_id: str) -> McpServerRecord:
        """读一条注册记录（未注册 → ``McpServerNotFoundError``）。"""
        sid = check_server_id(server_id)
        try:
            raw = self._store.get("config", self._path(sid))
        except KeyError as exc:
            raise McpServerNotFoundError(f"MCP Server {sid!r} 未注册") from exc
        return self._parse(raw)

    def list_servers(self) -> tuple[McpServerRecord, ...]:
        """全部注册记录（按 ``server_id`` 升序）。"""
        records = [
            self._parse(self._store.get("config", name))
            for name in self._store.list_files("config")
            if name.startswith(SERVER_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(records, key=lambda r: r.server_id))

    def test_connection(self, server_id: str) -> ConnectionTestResult:
        """按已存记录重跑一次连接测试（GWT-1；失败返回 ``ok=False`` 而非抛错）。"""
        return self._probe(self.get_server(server_id))

    # ───────────────────────── 内部工具 ─────────────────────────

    def _build_record(self, sid: str, **fields) -> McpServerRecord:
        transport = fields.get("transport", "stdio")
        data_egress_risk = DATA_EGRESS_RISK_TEXT if transport == "remote" else ""
        try:
            return McpServerRecord(
                server_id=sid,
                display_name=fields.get("display_name", ""),
                transport=transport,
                command=fields.get("command"),
                args=tuple(fields.get("args") or ()),
                env_refs=tuple(fields.get("env_refs") or ()),
                url=fields.get("url"),
                remote_acknowledged=bool(fields.get("remote_acknowledged")),
                data_egress_risk=data_egress_risk,
                enabled=True,
                permissions=tuple(fields.get("permissions") or ()),
                created_at=_now(),
            )
        except McpValidationError:
            raise
        except ValidationError as exc:
            raise McpValidationError(f"MCP Server 记录非法：{exc}") from exc

    def _probe(self, record: McpServerRecord) -> ConnectionTestResult:
        """开一次会话做握手 + 拉 tool 列表（用完即关，失败即结论）。"""
        try:
            transport = self._make_transport(record)
        except McpError as exc:
            return self._failed(record, str(exc))
        client = McpClient(transport)
        try:
            client.open()
            client.initialize()
            tools = client.list_tools()
        except McpError as exc:
            return self._failed(record, str(exc))
        except Exception as exc:  # 底层异常（含网关 Egress*）一律显式化
            return self._failed(record, f"{type(exc).__name__}: {exc}")
        finally:
            client.close()
        return ConnectionTestResult(
            ok=True,
            server_id=record.server_id,
            server_name=client.server_name or record.display_name,
            protocol_version=client.protocol_version,
            permissions=record.permissions,
            tools=tools,
            tested_at=_now(),
        )

    @staticmethod
    def _failed(record: McpServerRecord, reason: str) -> ConnectionTestResult:
        return ConnectionTestResult(
            ok=False, server_id=record.server_id,
            permissions=record.permissions, reason=reason, tested_at=_now(),
        )

    def _make_transport(self, record: McpServerRecord) -> McpTransport:
        if self._factory is not None:
            return self._factory(record)
        if record.transport == "stdio":
            return StdioTransport(
                record.command or "", record.args,
                self._resolve_env(record.env_refs), timeout_ms=self._timeout_ms,
            )
        if self._gateway is None:
            raise McpConnectionError(
                "远程 MCP Server 需要 L0 出网网关（02 §6 基座不可绕过）"
            )
        return HttpSseTransport(
            record.url or "", self._gateway, record.server_id, timeout_ms=self._timeout_ms
        )

    def _resolve_env(self, env_refs: Sequence[str]) -> dict[str, str] | None:
        if self._env_resolver is None or not env_refs:
            return None
        return {name: self._env_resolver(name) for name in env_refs}

    def _set_enabled(self, server_id: str, enabled: bool) -> McpServerRecord:
        record = self.get_server(server_id)
        if record.enabled == enabled:
            return record
        updated = record.model_copy(update={"enabled": enabled})
        self._save(updated)
        return updated

    def _save(self, record: McpServerRecord) -> None:
        self._store.put(
            "config", self._path(record.server_id),
            record.model_dump_json().encode("utf-8"),
        )

    @staticmethod
    def _path(server_id: str) -> str:
        return f"{SERVER_PREFIX}{check_server_id(server_id)}.json"

    @staticmethod
    def _parse(raw: bytes) -> McpServerRecord:
        try:
            return McpServerRecord(**json.loads(raw.decode("utf-8")))
        except McpValidationError:
            raise
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP Server 注册记录损坏无法解析：{exc}") from exc
