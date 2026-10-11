"""MCP Hub（`/mcp`）在**真 M5 组合根**上的端到端（[`T-UI-015.2`]）。

与 `tests/ui/test_ui_mcp.py`（替身钉异常翻译 / 两段权限面 / 路由）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 造出的 [`McpFacade`] 走一遍真链路——真
`McpServerRegistry` / 真权限账本 / 真映射器 / 真生命周期状态机 / 真回收序列。

**只有「出网那一跳」是替身**（`mcp_transport_factory` 注入离线传输：握手 + 固定 tool 列表）——
与 `rig_m5` 对 LLM `llm_post` 的口径一致：装配、注册、账本、映射、回收**全是真件**。

1. **挂载真落盘**——`add_server` 经真 `_probe`（替身传输）后落 `config` 分区；
2. **远程未确认被拒**——硬门在 `add_server`（[03 §5.1]），本组在真件上验；
3. **权限逐项落账**——经真 `McpPermissionBook`，读回同一决定；
4. **tool 映射为 Skill 可见**——真 `McpSkillMapper.sync_tools` 派生描述体，tool 浏览器
   的参数面取自该派生体（[03 §5.2]）；
5. **移除即回收**——经 `L1Runtime.remove_mcp_server`，派生 Skill 与映射记录一并清除。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

RULES: list[tuple[str, object]] = []

SERVER_ID = "srv_offline_demo"
"""本组挂载的 Server 标识（真注册表里的一条真记录）。"""

TOOL_NAME = "read_document"
"""替身 Server 提供的一个 tool（真映射器会为它派生一个 Skill）。"""

PERMISSION = "net_access:<api.example.com>"
"""真权限声明（[01 §10] 语法：`net_access:<host-pattern>`）。"""


class _OfflineTransport:
    """离线 MCP 传输替身：握手 ＋ 固定 tool 列表（**只在出网那一跳**是替身）。"""

    protocol_version = "2026-06-18"
    server_name = "离线示例 Server"
    server_version = "1.0"

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def initialize(self) -> dict:
        return {
            "protocolVersion": self.protocol_version,
            "serverInfo": {"name": self.server_name, "version": self.server_version},
        }

    def request(self, method: str, params: dict | None = None) -> dict:
        if method == "tools/list":
            return {"tools": [{
                "name": TOOL_NAME,
                "description": "读取一份文档",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }]}
        raise AssertionError(f"替身不实现 {method}")

    def notify(self, method: str, params: dict | None = None) -> None:
        return None


def _factory(record) -> _OfflineTransport:
    return _OfflineTransport(record.server_id)


def _request(running, method: str, path: str, body: dict | None = None) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=60)
    try:
        headers = {TOKEN_HEADER: running.token}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


def _get(running, path: str) -> dict:
    return _request(running, "GET", path)


def _post(running, path: str, body: dict) -> dict:
    return _request(running, "POST", path, body)


@pytest.fixture()
def m5(tmp_path):
    return seeded_m5(tmp_path / ROOT_NAME, rules=RULES, mcp_transport_factory=_factory)


def _serving(m5):
    return serve(dev=False, mcp=m5.m5.mcp)


def _mount(running, **overrides) -> dict:
    body = {
        "server_id": SERVER_ID,
        "display_name": "离线示例 Server",
        "transport": "remote",
        "url": "https://mcp.example.com/sse",
        "remote_acknowledged": True,
        "permissions": [PERMISSION],
    }
    body.update(overrides)
    return _post(running, "/api/mcp/servers", body)


def test_a_remote_server_mounts_and_reads_back(m5) -> None:
    """GWT-1 / GWT-3：确认后挂载成功、清单与详情都读得到真记录。"""
    with _serving(m5) as running:
        mounted = _mount(running)
        assert mounted["status"] == "ok", mounted.get("reason")
        assert mounted["data"]["connected"] is True
        assert mounted["data"]["protocol_version"] == _OfflineTransport.protocol_version

        listed = _get(running, "/api/mcp/servers")
        assert listed["status"] == "ok"
        rows = listed["data"]["slots"]["rows"]
        assert [row["server_id"] for row in rows] == [SERVER_ID]
        assert rows[0]["transport"] == "远程 HTTP/SSE"
        # 权限未批准 ⇒ 状态由账本**推导**为 permission_pending（[03 §5.3]）
        assert rows[0]["state"] == "待批准权限"

        detail = _get(running, f"/api/mcp/server?id={SERVER_ID}")
        assert detail["status"] == "ok"
        sections = {item["title"]: item["lines"] for item in detail["data"]["slots"]["sections"]}
        assert sections["数据外发风险"], "远程 Server 必须如实出数据外发风险段"


def test_a_remote_server_without_acknowledgement_is_refused_by_the_real_registry(m5) -> None:
    """GWT-3：硬门在 `add_server`——未确认即拒，且**真注册表里没有它**。"""
    with _serving(m5) as running:
        refused = _mount(running, remote_acknowledged=False)
        assert refused["status"] == "validation_failed"
        assert "离开本机" in refused["reason"]
        assert _get(running, "/api/mcp/servers")["status"] == "empty"


def test_permissions_are_decided_one_by_one_and_read_back(m5) -> None:
    """GWT-2：逐项批准经真账本落账；再读回**同一决定**（不是只在描述里改了个字）。"""
    with _serving(m5) as running:
        assert _mount(running)["status"] == "ok"

        before = _get(running, f"/api/mcp/permissions?id={SERVER_ID}")
        assert before["declaration"]["status"] == "ok"
        assert before["declaration"]["data"]["component_type"] == "permission_approval_card"
        assert before["declaration"]["data"]["slots"]["items"][0]["state"] == "pending"
        assert before["decisions"]["status"] == "ok"
        assert before["decisions"]["data"]["slots"]["surface"] == "mcp-permissions"

        decided = _post(running, "/api/mcp/permissions", {
            "server_id": SERVER_ID, "permission": PERMISSION, "decision": "approve",
        })
        assert decided["status"] == "ok", decided.get("reason")
        assert decided["data"]["decision"] == "approved"

        after = _get(running, f"/api/mcp/permissions?id={SERVER_ID}")
        assert after["declaration"]["data"]["slots"]["items"][0]["state"] == "approved"
        assert after["decisions"]["data"]["slots"]["entries"][0]["current"] == "已批准"
        # 账本真变了 ⇒ 状态推导随之离开 permission_pending
        listed = _get(running, "/api/mcp/servers")
        assert listed["data"]["slots"]["rows"][0]["state"] == "未连接"


def test_the_tool_browser_shows_the_mapped_skill_and_its_parameter_surface(m5) -> None:
    """GWT-4：真 `sync_tools` 派生描述体 ⇒ tool 浏览器出映射状态与参数面。"""
    with _serving(m5) as running:
        assert _mount(running)["status"] == "ok"
        _post(running, "/api/mcp/permissions", {
            "server_id": SERVER_ID, "permission": PERMISSION, "decision": "approve",
        })
        # 真映射器（[03 §5.2]）：权限全部批准后才允许同步 tool 列表
        mapped = m5.m5.m1.runtime.mcp_mapper.sync_tools(SERVER_ID)
        assert mapped, "真映射器应为一个 tool 派生一条映射记录"

        payload = _get(running, f"/api/mcp/tools?id={SERVER_ID}")
        assert payload["status"] == "ok", payload.get("reason")
        row = payload["data"]["slots"]["rows"][0]
        assert row["tool"] == TOOL_NAME
        assert row["status"] == "生效"
        # 参数面取自**派生的描述体**（schema 已随映射落到 Skill 上，[03 §5.2]）
        assert row["inputs"] == "path"


def test_removing_a_server_recycles_its_mappings_and_derived_skills(m5) -> None:
    """GWT-4 / [03 §5.2]：移除经整条回收序列——派生 Skill 与映射一并清除。"""
    with _serving(m5) as running:
        assert _mount(running)["status"] == "ok"
        _post(running, "/api/mcp/permissions", {
            "server_id": SERVER_ID, "permission": PERMISSION, "decision": "approve",
        })
        mapped = m5.m5.m1.runtime.mcp_mapper.sync_tools(SERVER_ID)
        derived = mapped[0].skill_id
        assert any(
            item.skill_id == derived for item in m5.m5.m1.runtime.skills.list_all()
        ), "派生 Skill 应先真的在注册表里"

        removed = _post(running, "/api/mcp/servers/update", {
            "server_id": SERVER_ID, "action": "remove",
        })
        assert removed["status"] == "ok", removed.get("reason")
        assert removed["data"]["removed"] is True

        assert not any(
            item.skill_id == derived for item in m5.m5.m1.runtime.skills.list_all()
        ), "移除后派生 Skill 应已被回收（不静默留孤儿）"
        assert m5.m5.m1.runtime.mcp_mapper.mappings(SERVER_ID) == ()
        assert _get(running, "/api/mcp/servers")["status"] == "empty"


def test_enable_disable_and_connection_test_go_through_the_real_machine(m5) -> None:
    """启停经真状态机；连接测试按已存记录重跑一次（真 `_probe` ＋ 替身传输）。"""
    with _serving(m5) as running:
        assert _mount(running)["status"] == "ok"

        tested = _post(running, "/api/mcp/servers/update", {
            "server_id": SERVER_ID, "action": "test",
        })
        assert tested["status"] == "ok" and tested["data"]["connected"] is True

        disabled = _post(running, "/api/mcp/servers/update", {
            "server_id": SERVER_ID, "action": "disable",
        })
        assert disabled["status"] == "ok"
        assert _get(running, "/api/mcp/servers")["data"]["slots"]["rows"][0]["state"] == "已停用"

        enabled = _post(running, "/api/mcp/servers/update", {
            "server_id": SERVER_ID, "action": "enable",
        })
        assert enabled["status"] == "ok"
        assert _get(running, "/api/mcp/servers")["data"]["slots"]["rows"][0]["state"] != "已停用"


def test_an_unknown_server_reads_as_unavailable_not_failed(m5) -> None:
    """GWT-4：真件上「未注册」也走 `unavailable` ＋ 点名（MCP 异常被显式翻译）。"""
    with _serving(m5) as running:
        payload = _get(running, "/api/mcp/server?id=srv_absent")
        assert payload["status"] == "unavailable"
        assert payload["reason"]
