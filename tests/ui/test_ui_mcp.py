"""MCP Hub（`/mcp` —— 能力区 story-08 五条屏）的回环面（[`T-UI-015.2`]）。

本组钉六件事（真组合根上的装配由 `tests/integration/test_ui_mcp_wiring.py` 钉）：

1. **MCP 异常须显式翻译**——MCP 错误全部继承 ``Exception`` 而非 ``ValueError`` /
   ``KeyError``（[`l1/mcp/errors.py`] 的模块取向），故不翻译会被 `_call_face` 报成
   `failed`；本组逐档钉「未注册 ⇒ `unavailable` / 连不上 ⇒ `unavailable` / 其余 ⇒
   `validation_failed`」；
2. **权限面两段**——声明走 `permission_approval_card`（只渲染不落账）、动作走
   `setting_panel`（面键 `mcp-permissions`）；**没声明权限 ⇒ 处置段 `empty`**，不是空面板；
3. **远程 Server 须显式确认**——未确认由 `add_server` 拒（硬门在后端）；
4. **连接测试失败不落盘**——翻成 `unavailable` ＋ 原因，不是「工具坏了」；
5. **移除走整条回收序列**——经门面的 `remove_server` 引用，不由表现层自行拼两步；
6. **路由形态与页面登记**——四读三写；`/mcp` 挂上 `render`（不再是六态探针页）；
   `setting_panel` 渲染件认得 `mcp-permissions` 面键。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.permissions import PermissionApproval
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.mcp.errors import (
    McpConnectionError,
    McpServerNotFoundError,
    McpValidationError,
)
from st_agent.l1.mcp.models import DATA_EGRESS_RISK_TEXT, McpServerRecord
from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import _GET_ROUTES, _POST_ROUTES, serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"


def _record(
    server_id: str = "srv_demo",
    *,
    transport: str = "stdio",
    enabled: bool = True,
    permissions: tuple[str, ...] = (),
) -> McpServerRecord:
    remote = transport == "remote"
    return McpServerRecord(
        server_id=server_id,
        display_name="演示 Server",
        transport=transport,
        command=None if remote else "mcp-demo",
        url="https://mcp.example.com/sse" if remote else None,
        remote_acknowledged=remote,
        data_egress_risk=DATA_EGRESS_RISK_TEXT if remote else "",
        enabled=enabled,
        permissions=permissions,
        created_at=datetime.now().astimezone(),
    )


class _Probe:
    """`ConnectionTestResult` 的最小替身（只带本层消费的字段）。"""

    def __init__(self, ok: bool, server_id: str, *, reason: str = "") -> None:
        self.ok = ok
        self.server_id = server_id
        self.reason = reason
        self.server_name = "演示 Server"
        self.protocol_version = "2026-06-18"
        self.permissions: tuple[str, ...] = ()
        self.tools: tuple = ()


class _FakeServers:
    def __init__(self, *records: McpServerRecord, probe_ok: bool = True) -> None:
        self._by_id = {item.server_id: item for item in records}
        self._probe_ok = probe_ok
        self.added: list[dict] = []

    def list_servers(self) -> tuple[McpServerRecord, ...]:
        return tuple(self._by_id.values())

    def get_server(self, server_id: str) -> McpServerRecord:
        try:
            return self._by_id[server_id]
        except KeyError as exc:
            raise McpServerNotFoundError(f"MCP Server {server_id!r} 未注册") from exc

    def add_server(self, server_id: str, **fields: object) -> _Probe:
        self.added.append({"server_id": server_id, **fields})
        transport = str(fields.get("transport") or "stdio")
        if transport == "remote" and not fields.get("remote_acknowledged"):
            raise McpValidationError(
                "远程 MCP Server 须用户显式确认：这是远程 Server，数据会离开本机（03 §5.1）"
            )
        if not self._probe_ok:
            return _Probe(False, server_id, reason="命令不存在")
        record = _record(server_id, transport=transport)
        self._by_id[server_id] = record
        return _Probe(True, server_id)

    def test_connection(self, server_id: str) -> _Probe:
        self.get_server(server_id)
        return _Probe(self._probe_ok, server_id, reason="命令不存在")


class _FakePermissions:
    def __init__(self, *approvals: PermissionApproval) -> None:
        self._items = list(approvals)
        self.decisions: list[tuple[str, str, str]] = []

    def approvals(self, _key: str) -> tuple[PermissionApproval, ...]:
        return tuple(self._items)

    def _decide(self, permission: str, decision: str) -> PermissionApproval:
        if permission not in [item.permission for item in self._items]:
            from st_agent.l1.mcp.errors import McpPermissionError

            raise McpPermissionError(f"权限 {permission!r} 未声明，不得 {decision}")
        result = PermissionApproval(
            permission=permission, decision=decision, decided_at=datetime.now().astimezone()
        )
        self._items = [result if item.permission == permission else item for item in self._items]
        self.decisions.append((permission, decision, ""))
        return result

    def approve(self, _key: str, permission: str) -> PermissionApproval:
        return self._decide(permission, "approved")

    def reject(self, _key: str, permission: str) -> PermissionApproval:
        return self._decide(permission, "rejected")


class _FakeMapping:
    def __init__(self, tool: str, skill_id: str, status: str = "active") -> None:
        self.tool = tool
        self.skill_id = skill_id
        self.status = status
        self.mapping_version = "1.0"
        self.change_note = "" if status == "active" else "tool 从 Server 消失"
        self.updated_at = datetime.now().astimezone()


class _FakeMapper:
    def __init__(self, *mappings: _FakeMapping) -> None:
        self._items = list(mappings)

    def mappings(self, _server_id: str) -> tuple[_FakeMapping, ...]:
        return tuple(self._items)


class _FakeMachine:
    def __init__(self, state: str = "disconnected") -> None:
        self._state = state
        self.calls: list[tuple[str, str]] = []

    def state_of(self, _server_id: str) -> str:
        return self._state

    def transitions(self, _server_id: str | None = None) -> tuple:
        return ()

    def notices(self, _server_id: str | None = None) -> tuple:
        return ()

    def degrade_candidates(self, _server_id: str) -> tuple:
        return ()

    def enable(self, server_id: str) -> None:
        self.calls.append(("enable", server_id))
        self._state = "disconnected"

    def disable(self, server_id: str) -> None:
        self.calls.append(("disable", server_id))
        self._state = "disabled"


class _FakeSkills:
    def __init__(self, *descriptors: SkillDescriptor) -> None:
        self._by_id = {item.skill_id: item for item in descriptors}

    def get(self, skill_id: str) -> SkillDescriptor:
        try:
            return self._by_id[skill_id]
        except KeyError as exc:
            raise KeyError(skill_id) from exc


class _FakeMcp:
    def __init__(self, *, servers, permissions=None, mapper=None, machine=None, skills=None,
                 remove_server=None) -> None:
        self.servers = servers
        self.permissions = permissions or _FakePermissions()
        self.mapper = mapper
        self.machine = machine
        self.skills = skills
        self.remove_server = remove_server


def _descriptor(skill_id: str) -> SkillDescriptor:
    return SkillDescriptor(
        skill_id=skill_id,
        name="演示能力",
        description="做一件中性的事",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"rows": {"type": "array"}}},
        parameters=(),
        dependencies=(),
        source="mcp-mapped",
        provenance=Provenance(),
        permissions=(),
        offline_level="none",
        version_policy="follow-latest",
    )


def _get(http_get, auth, path, **faces):
    with serve(dev=False, **faces) as running:
        return http_get(running, path, headers=auth(running)).json()


def _post(http_post, auth, path, body, **faces):
    with serve(dev=False, **faces) as running:
        return http_post(running, path, body, headers=auth(running)).json()


# ───────────────────────── 清单与详情 ─────────────────────────


def test_the_server_list_is_a_table_with_the_lifecycle_state(http_get, auth) -> None:
    face = _FakeMcp(servers=_FakeServers(_record()), machine=_FakeMachine("connected"))
    payload = _get(http_get, auth, "/api/mcp/servers", mcp=face)
    assert payload["status"] == "ok"
    rows = payload["data"]["slots"]["rows"]
    assert rows[0]["server_id"] == "srv_demo"
    assert rows[0]["transport"] == "本地 stdio（子进程）"
    assert rows[0]["state"] == "已连接"


def test_no_server_reads_as_empty_with_a_next_step(http_get, auth) -> None:
    """没挂载任何 Server ⇒ `empty` ＋ 下一步（不是 `ok` 空表、不是 `unavailable`）。"""
    payload = _get(http_get, auth, "/api/mcp/servers", mcp=_FakeMcp(servers=_FakeServers()))
    assert payload["status"] == "empty"
    assert payload["reason"]


def test_the_detail_carries_lifecycle_and_the_egress_risk_for_remote(http_get, auth) -> None:
    """远程 Server 的数据外发风险**如实成段**（story-08 GWT-5）。"""
    face = _FakeMcp(
        servers=_FakeServers(_record("srv_remote", transport="remote")),
        machine=_FakeMachine("permission_pending"),
    )
    payload = _get(http_get, auth, "/api/mcp/server?id=srv_remote", mcp=face)
    assert payload["status"] == "ok"
    sections = {item["title"]: item["lines"] for item in payload["data"]["slots"]["sections"]}
    assert sections["生命周期状态"] == ["当前：待批准权限"]
    assert sections["数据外发风险"] == [DATA_EGRESS_RISK_TEXT]


def test_the_detail_of_an_unknown_server_is_unavailable(http_get, auth) -> None:
    """**未注册 ⇒ `unavailable` ＋ 点名**——不是 `failed`（「工具坏了」的误读）。"""
    face = _FakeMcp(servers=_FakeServers(), machine=_FakeMachine())
    payload = _get(http_get, auth, "/api/mcp/server?id=srv_absent", mcp=face)
    assert payload["status"] == "unavailable"
    assert payload["reason"]


@pytest.mark.parametrize("path", ["/api/mcp/server", "/api/mcp/tools", "/api/mcp/permissions"])
def test_a_missing_server_identifier_is_validation_failed(http_get, auth, path) -> None:
    payload = _get(http_get, auth, path, mcp=_FakeMcp(servers=_FakeServers()))
    assert payload["status"] == "validation_failed"


# ───────────────────────── tool 浏览器 ─────────────────────────


def test_the_tool_browser_shows_the_derived_descriptor_parameter_surface(http_get, auth) -> None:
    """tool schema 浏览器的参数面取自**派生的 Skill 描述体**（MCP schema 已随映射落到它）。"""
    face = _FakeMcp(
        servers=_FakeServers(_record()),
        mapper=_FakeMapper(_FakeMapping("read_file", "sk_mcp_read_file_v1.0")),
        skills=_FakeSkills(_descriptor("sk_mcp_read_file_v1.0")),
    )
    payload = _get(http_get, auth, "/api/mcp/tools?id=srv_demo", mcp=face)
    assert payload["status"] == "ok"
    row = payload["data"]["slots"]["rows"][0]
    assert row["tool"] == "read_file"
    assert row["status"] == "生效"
    assert row["inputs"] == "path"


def test_a_mapping_whose_descriptor_is_gone_still_lists(http_get, auth) -> None:
    """一条坏映射**不能**让整表失败——参数面显式写「描述体不在」，其余照出。"""
    face = _FakeMcp(
        servers=_FakeServers(_record()),
        mapper=_FakeMapper(_FakeMapping("gone_tool", "sk_absent_v1.0")),
        skills=_FakeSkills(),
    )
    payload = _get(http_get, auth, "/api/mcp/tools?id=srv_demo", mcp=face)
    assert payload["status"] == "ok"
    assert payload["data"]["slots"]["rows"][0]["inputs"] == "（描述体不在）"


def test_no_mapping_reads_as_empty_and_a_missing_mapper_as_unavailable(http_get, auth) -> None:
    empty = _get(
        http_get, auth, "/api/mcp/tools?id=srv_demo",
        mcp=_FakeMcp(servers=_FakeServers(_record()), mapper=_FakeMapper()),
    )
    assert empty["status"] == "empty"
    unwired = _get(
        http_get, auth, "/api/mcp/tools?id=srv_demo",
        mcp=_FakeMcp(servers=_FakeServers(_record())),
    )
    assert unwired["status"] == "unavailable"


# ───────────────────────── 权限面 ─────────────────────────


def test_the_permission_face_is_two_segments(http_get, auth) -> None:
    """声明段是 `permission_approval_card`（只渲染不落账），处置段是 `setting_panel`。"""
    face = _FakeMcp(
        servers=_FakeServers(_record()),
        permissions=_FakePermissions(
            PermissionApproval(permission="net_access:<api.example.com>", decision="pending")
        ),
    )
    payload = _get(http_get, auth, "/api/mcp/permissions?id=srv_demo", mcp=face)
    assert payload["declaration"]["status"] == "ok"
    assert payload["declaration"]["data"]["component_type"] == "permission_approval_card"
    item = payload["declaration"]["data"]["slots"]["items"][0]
    assert item["permission"] == "net_access:<api.example.com>"
    assert item["state"] == "pending"

    assert payload["decisions"]["status"] == "ok"
    assert payload["decisions"]["data"]["component_type"] == "setting_panel"
    assert payload["decisions"]["data"]["slots"]["surface"] == "mcp-permissions"
    entry = payload["decisions"]["data"]["slots"]["entries"][0]
    assert entry["current"] == "待批准"
    assert entry["actions"] == ["approve", "reject"]
    assert entry["params"] == {"server_id": "srv_demo", "permission": "net_access:<api.example.com>"}


def test_a_server_without_permissions_has_an_empty_decisions_segment(http_get, auth) -> None:
    """没声明权限 ⇒ **没有可批准项**（`empty` ＋ 原因），不是一块空面板。"""
    face = _FakeMcp(servers=_FakeServers(_record()), permissions=_FakePermissions())
    payload = _get(http_get, auth, "/api/mcp/permissions?id=srv_demo", mcp=face)
    assert payload["declaration"]["status"] == "ok"
    assert payload["decisions"]["status"] == "empty"
    assert payload["decisions"]["reason"]


def test_approving_and_rejecting_land_through_the_book(http_get, http_post, auth) -> None:
    face = _FakeMcp(
        servers=_FakeServers(_record()),
        permissions=_FakePermissions(
            PermissionApproval(permission="exec_command", decision="pending")
        ),
    )
    approved = _post(
        http_post, auth, "/api/mcp/permissions",
        {"server_id": "srv_demo", "permission": "exec_command", "decision": "approve"},
        mcp=face,
    )
    assert approved["status"] == "ok"
    assert approved["data"]["decision"] == "approved"
    assert approved["data"]["decided_at"]


def test_an_undeclared_permission_cannot_be_decided(http_get, http_post, auth) -> None:
    """未声明的权限不得批准 / 拒绝（[01 §10]）——MCP 异常翻成 `validation_failed`。"""
    face = _FakeMcp(servers=_FakeServers(_record()), permissions=_FakePermissions())
    payload = _post(
        http_post, auth, "/api/mcp/permissions",
        {"server_id": "srv_demo", "permission": "exec_command", "decision": "approve"},
        mcp=face,
    )
    assert payload["status"] == "validation_failed"
    assert payload["reason"]


@pytest.mark.parametrize(
    "body",
    [
        {"server_id": "srv_demo", "permission": "exec_command"},
        {"server_id": "srv_demo", "permission": "exec_command", "decision": "maybe"},
    ],
)
def test_a_bad_decision_request_is_validation_failed(http_get, http_post, auth, body) -> None:
    face = _FakeMcp(servers=_FakeServers(_record()), permissions=_FakePermissions())
    payload = _post(http_post, auth, "/api/mcp/permissions", body, mcp=face)
    assert payload["status"] == "validation_failed"


# ───────────────────────── 挂载与动作 ─────────────────────────


def test_a_local_server_is_mounted_and_tested(http_get, http_post, auth) -> None:
    servers = _FakeServers()
    payload = _post(
        http_post, auth, "/api/mcp/servers",
        {"server_id": "srv_new", "display_name": "新 Server", "transport": "stdio",
         "command": "mcp-demo", "permissions": ["exec_command"]},
        mcp=_FakeMcp(servers=servers),
    )
    assert payload["status"] == "ok"
    assert payload["data"]["connected"] is True
    assert servers.added[0]["command"] == "mcp-demo"
    assert servers.added[0]["permissions"] == ("exec_command",)


def test_a_remote_server_without_acknowledgement_is_refused(http_get, http_post, auth) -> None:
    """GWT-3：远程未显式确认 ⇒ `validation_failed` 且**不落盘**（硬门在 `add_server`）。"""
    servers = _FakeServers()
    payload = _post(
        http_post, auth, "/api/mcp/servers",
        {"server_id": "srv_remote", "display_name": "远程", "transport": "remote",
         "url": "https://mcp.example.com/sse"},
        mcp=_FakeMcp(servers=servers),
    )
    assert payload["status"] == "validation_failed"
    assert "离开本机" in payload["reason"]
    assert "srv_remote" not in [item.server_id for item in servers.list_servers()]


def test_a_failed_connection_test_does_not_mount_and_is_unavailable(http_get, http_post, auth) -> None:
    """连不上 ⇒ `unavailable` ＋ 原因（不是 `failed`），且**不落盘**（[03 §5.1]）。"""
    servers = _FakeServers(probe_ok=False)
    payload = _post(
        http_post, auth, "/api/mcp/servers",
        {"server_id": "srv_new", "display_name": "新 Server", "command": "nope"},
        mcp=_FakeMcp(servers=servers),
    )
    assert payload["status"] == "unavailable"
    assert payload["reason"]
    assert servers.list_servers() == ()


@pytest.mark.parametrize(
    ("action", "expected"),
    [("enable", "enable"), ("disable", "disable"), ("test", "test")],
)
def test_the_three_non_destructive_actions_go_through_the_right_handles(
    http_get, http_post, auth, action: str, expected: str
) -> None:
    machine = _FakeMachine()
    face = _FakeMcp(servers=_FakeServers(_record()), machine=machine)
    payload = _post(
        http_post, auth, "/api/mcp/servers/update",
        {"server_id": "srv_demo", "action": action}, mcp=face,
    )
    assert payload["status"] == "ok", payload.get("reason")
    if expected == "test":
        assert payload["data"]["connected"] is True
        assert machine.calls == []
    else:
        assert machine.calls == [(expected, "srv_demo")]


def test_remove_goes_through_the_recycle_sequence_not_the_registry_directly(
    http_get, http_post, auth
) -> None:
    """移除**必须**经 `remove_server` 引用（整条序列）——表现层不自行拼 `disable` + `remove`。"""
    machine = _FakeMachine()
    seen: list[str] = []
    face = _FakeMcp(
        servers=_FakeServers(_record()), machine=machine,
        remove_server=lambda server_id: (seen.append(server_id), machine.disable(server_id)),
    )
    payload = _post(
        http_post, auth, "/api/mcp/servers/update",
        {"server_id": "srv_demo", "action": "remove"}, mcp=face,
    )
    assert payload["status"] == "ok"
    assert payload["data"]["removed"] is True
    assert seen == ["srv_demo"]


def test_remove_without_the_hook_is_unavailable(http_get, http_post, auth) -> None:
    """未接入移除序列 ⇒ `unavailable` ＋ 点名，**不**退化成一个只删注册记录的假动作。"""
    face = _FakeMcp(servers=_FakeServers(_record()), machine=_FakeMachine())
    payload = _post(
        http_post, auth, "/api/mcp/servers/update",
        {"server_id": "srv_demo", "action": "remove"}, mcp=face,
    )
    assert payload["status"] == "unavailable"
    assert "移除" in payload["reason"]


def test_an_unknown_action_is_validation_failed(http_get, http_post, auth) -> None:
    face = _FakeMcp(servers=_FakeServers(_record()), machine=_FakeMachine())
    payload = _post(
        http_post, auth, "/api/mcp/servers/update",
        {"server_id": "srv_demo", "action": "explode"}, mcp=face,
    )
    assert payload["status"] == "validation_failed"


def test_a_connection_error_is_translated_to_unavailable(http_get, auth) -> None:
    """`McpConnectionError` ⇒ `unavailable`（目标不可达不是「工具坏了」）。"""

    class _Boom:
        def list_servers(self):
            raise McpConnectionError("连接被拒绝")

        def get_server(self, _server_id):
            raise McpConnectionError("连接被拒绝")

    payload = _get(http_get, auth, "/api/mcp/servers", mcp=_FakeMcp(servers=_Boom()))
    assert payload["status"] == "unavailable"


# ───────────────────────── 装配缺口 ─────────────────────────


def test_the_face_not_wired_reads_as_unavailable_with_the_task_named(http_get, auth) -> None:
    payload = _get(http_get, auth, "/api/mcp/servers")
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"] and "MCP" in payload["reason"]


# ───────────────────────── 路由与页面登记 ─────────────────────────


def test_routes_and_page_registration() -> None:
    for path in ("/api/mcp/servers", "/api/mcp/server", "/api/mcp/tools", "/api/mcp/permissions"):
        assert path in _GET_ROUTES, path
    for path in ("/api/mcp/servers", "/api/mcp/servers/update", "/api/mcp/permissions"):
        assert path in _POST_ROUTES, path
    assert "/mcp" in PAGE_PATHS

    pages = (_UI / "web" / "js" / "pages.js").read_text(encoding="utf-8")
    assert "renderMcpPage" in pages
    assert "render: renderMcpPage" in pages

    panel = (_UI / "web" / "js" / "plugins" / "reflection" / "setting_panel.js").read_text(
        encoding="utf-8"
    )
    assert "'mcp-permissions'" in panel and "/api/mcp/permissions" in panel


def test_new_endpoints_need_the_token(http_get) -> None:
    with serve(dev=False, mcp=_FakeMcp(servers=_FakeServers(_record()))) as running:
        for path in (
            "/api/mcp/servers",
            "/api/mcp/server?id=srv_demo",
            "/api/mcp/tools?id=srv_demo",
            "/api/mcp/permissions?id=srv_demo",
        ):
            assert http_get(running, path).status == 401, path
