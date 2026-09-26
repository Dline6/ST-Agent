"""T-L1-002.2 测试：03 §5.2 tool → Skill 自动映射与版本化。

GWT 对照（任务文件 5 条 + 回归）：
- GWT-1 tool 自动注册为 Skill（`source=mcp-mapped`，schema 派生，name 过 §6）
- GWT-2 幂等与增量（重复装载不重复注册；新增 tool 只注册新增者）
- GWT-3 契约变化标「待重新映射」＋显式变化说明，不静默覆盖描述体
- GWT-4 映射记录可复查（tool → skill_id + 指纹 + 映射版本）
- GWT-5 Server 禁用联动（派生 Skill 立即不在可用列表；记录保留）
- 边界：A1 超长显式拒 · A4 未批准权限不得装载 · A7 查询不重探 · 落盘路径安全
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from st_agent.contracts.schema_check import check_payload
from st_agent.l0.storage import Store
from st_agent.l1.mcp import (
    McpConnectionError,
    McpMappingError,
    McpMappingNotFoundError,
    McpPermissionError,
    McpServerNotFoundError,
    McpServerRegistry,
    McpSkillMapper,
    McpTransport,
    McpValidationError,
    ToolSpec,
    tool_fingerprints,
)
from st_agent.l1.skills import SkillRegistry, skill_id_for

PASS = "correct horse battery staple"
STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"
FAKE_COMMAND = "python"
EXEC = "exec_command"
CACHE_SCOPE = "local_read:<data/cache/**>"


# ───────────────────────── 离线替身 ─────────────────────────


class FakeTransport(McpTransport):
    """离线替身传输：不碰子进程与网络，tool 集合可控（增量 / 契约变化用）。"""

    def __init__(self, *, tools=(), server_name: str = "fake-mcp", fail: str | None = None):
        super().__init__()
        self.config_tools = list(tools)
        self.config_server_name = server_name
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


class Factory:
    """传输工厂替身：既喂 tool 集合，也记账探针次数（A7 用例用）。"""

    def __init__(self, tools=(), *, fail: str | None = None):
        self.tools = list(tools)
        self.fail = fail
        self.transports: list[FakeTransport] = []

    def __call__(self, _record) -> McpTransport:
        t = FakeTransport(tools=self.tools, fail=self.fail)
        self.transports.append(t)
        return t

    @property
    def opened(self) -> int:
        return sum(1 for t in self.transports if t.opened)


# ───────────────────────── 夹具与工具 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def tool(name: str, *, input_schema=None, output_schema=None,
         description: str = "存根 tool") -> dict:
    """造一个 ``tools/list`` 条目（MCP 原始形态）。"""
    raw: dict = {
        "name": name,
        "description": description,
        "inputSchema": input_schema if input_schema is not None
        else {"type": "object", "properties": {}},
    }
    if output_schema is not None:
        raw["outputSchema"] = output_schema
    return raw


ECHO = tool(
    "echo_symbol",
    input_schema={"type": "object", "properties": {"symbol": {"type": "string"}},
                  "required": ["symbol"]},
    output_schema={"type": "object", "properties": {"symbol": {"type": "string"}}},
    description="回显传入的证券代码",
)


def build(store: Store, tools=(ECHO,), *, permissions=(), server_id="srv_a",
          ) -> tuple[McpServerRegistry, McpSkillMapper, Factory]:
    """装好一条 Server 记录 + 映射门面（全程离线）。"""
    factory = Factory(tools)
    servers = McpServerRegistry(store, transport_factory=factory)
    servers.add_server(server_id, display_name="存根 Server", command=FAKE_COMMAND,
                       permissions=permissions)
    mapper = McpSkillMapper(store, servers=servers, skills=SkillRegistry(store))
    return servers, mapper, factory


def skill_ids(mapper: McpSkillMapper) -> set[str]:
    return {d.skill_id for d in mapper.available_skills()}


# ───────────────────────── GWT-1 自动注册 ─────────────────────────


class TestAutoRegister:
    """GWT-1：tool 自动注册为 Skill，元数据从 MCP schema 派生。"""

    def test_sync_registers_every_tool_as_mcp_mapped_skill(self, store: Store):
        _, mapper, _ = build(store)
        mappings = mapper.sync_tools("srv_a")
        assert [m.tool for m in mappings] == ["echo_symbol"]
        assert mappings[0].skill_id == "sk_mcp_srv_a_echo_symbol_v1.0"
        assert mappings[0].status == "active"
        assert mappings[0].mapping_version == "1.0"

        desc = SkillRegistry(store).get(mappings[0].skill_id)
        assert desc.source == "mcp-mapped"
        assert desc.name == "echo_symbol"
        assert desc.description == "回显传入的证券代码"
        assert desc.input_schema == ECHO["inputSchema"]
        assert desc.output_schema == ECHO["outputSchema"]
        assert desc.skill_id in skill_ids(mapper)

    def test_derived_metadata_follows_a5(self, store: Store):
        """A5：不派生可配参数；离线等级 none；权限随 Server 声明。"""
        servers, mapper, _ = build(store, permissions=(CACHE_SCOPE,))
        servers.permissions.approve("srv_a", CACHE_SCOPE)
        mappings = mapper.sync_tools("srv_a")
        desc = SkillRegistry(store).get(mappings[0].skill_id)
        assert desc.parameters == ()
        assert desc.offline_level == "none"
        assert desc.permissions == (CACHE_SCOPE,)
        assert desc.version_policy == "follow-latest"

    def test_real_stdio_stub_server_end_to_end(self, store: Store):
        """真实子进程路径（离线本地 stdio）：两个 tool 全部落映射。"""
        servers = McpServerRegistry(store)
        servers.add_server("stub_local", display_name="本地存根",
                           command=sys.executable, args=[str(STUB)])
        skills = SkillRegistry(store)
        mapper = McpSkillMapper(store, servers=servers, skills=skills)
        mappings = mapper.sync_tools("stub_local")
        assert [m.tool for m in mappings] == ["echo_symbol", "list_watch_groups"]
        assert {d.name for d in mapper.available_skills()} == {
            "echo_symbol", "list_watch_groups",
        }
        # A3：MCP 未给 output schema 的 tool，派生出的空 schema 走宽松态（不误伤执行）
        no_output = skills.get("sk_mcp_stub_local_list_watch_groups_v1.0")
        assert no_output.output_schema == {}
        assert check_payload(no_output.output_schema, {"any": "payload"}).passed

    def test_tool_name_failing_neutrality_is_rejected(self, store: Store):
        """GWT-1：`name` 须过 §6 中性化校验。"""
        _, mapper, _ = build(store, tools=(tool("投资顾问"),))
        with pytest.raises(McpValidationError) as excinfo:
            mapper.sync_tools("srv_a")
        assert "投资顾问" in str(excinfo.value)

    def test_mapping_record_path_and_shape(self, store: Store):
        """GWT-4：落盘位置与字段（tool → skill_id + 指纹 + 映射版本）。"""
        _, mapper, _ = build(store)
        mapper.sync_tools("srv_a")
        assert "mcp-mapping/srv_a/echo_symbol.json" in store.list_files("config")
        payload = json.loads(store.get("config", "mcp-mapping/srv_a/echo_symbol.json"))
        assert payload["skill_id"] == "sk_mcp_srv_a_echo_symbol_v1.0"
        assert payload["mapping_version"] == "1.0"
        assert payload["status"] == "active"
        assert payload["change_note"] == ""
        assert payload["fingerprint"] == payload["fingerprint"].strip()
        # 描述体走既有 T-L1-001.1 通道，不另立存储
        assert "skill-registry/sk_mcp_srv_a_echo_symbol_v1.0.json" in store.list_files("config")


# ───────────────────────── GWT-2 幂等与增量 ─────────────────────────


class TestIdempotentAndIncremental:
    """GWT-2：重复装载幂等；新增 tool 只注册新增者。"""

    def test_repeat_sync_does_not_duplicate(self, store: Store):
        _, mapper, _ = build(store)
        first = mapper.sync_tools("srv_a")
        second = mapper.sync_tools("srv_a")
        assert first == second
        assert len(SkillRegistry(store).list_all()) == 1

    def test_new_tool_registers_only_itself(self, store: Store):
        _, mapper, factory = build(store)
        before = mapper.sync_tools("srv_a")[0]
        factory.tools.append(tool("list_watch_groups"))
        after = mapper.sync_tools("srv_a")
        assert [m.tool for m in after] == ["echo_symbol", "list_watch_groups"]
        assert after[0] == before  # 既有映射逐字节不受影响
        assert len(SkillRegistry(store).list_all()) == 2

    def test_removed_tool_leaves_record_untouched(self, store: Store):
        """A8：tool 消失时不回收（无 spec 依据不删用户可见的 Skill）。"""
        _, mapper, factory = build(store, tools=(ECHO, tool("list_watch_groups")))
        mapper.sync_tools("srv_a")
        factory.tools = [ECHO]
        after = mapper.sync_tools("srv_a")
        assert [m.tool for m in after] == ["echo_symbol", "list_watch_groups"]
        assert len(SkillRegistry(store).list_all()) == 2


# ───────────────────────── GWT-3 契约变化 ─────────────────────────


class TestContractChange:
    """GWT-3：契约变化只标「待重新映射」，不静默覆盖。"""

    def test_input_change_marks_pending_without_overwriting(self, store: Store):
        _, mapper, factory = build(store)
        original = mapper.sync_tools("srv_a")[0]
        factory.tools = [tool("echo_symbol", input_schema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "market": {"type": "string"}},
            "required": ["symbol"],
        })]
        after = mapper.sync_tools("srv_a")[0]
        assert after.status == "pending-remap"
        # 显式提示＝记录自身（tool 名）＋变化说明（GWT-3）
        assert after.tool == "echo_symbol"
        assert "input_schema" in after.change_note
        assert after.fingerprint == original.fingerprint  # 基线不动
        # 已注册描述体未被覆盖
        desc = SkillRegistry(store).get(original.skill_id)
        assert desc.input_schema == ECHO["inputSchema"]

    def test_output_only_change_names_output_side(self, store: Store):
        _, mapper, factory = build(store)
        mapper.sync_tools("srv_a")
        factory.tools = [tool("echo_symbol", input_schema=ECHO["inputSchema"],
                              output_schema={"type": "object", "properties": {}})]
        after = mapper.sync_tools("srv_a")[0]
        assert after.status == "pending-remap"
        assert "output_schema" in after.change_note
        assert "input_schema" not in after.change_note

    def test_detection_writes_no_update_marker(self, store: Store):
        """A2：检测阶段不复用 `UpdateInfo`（不产生 `skill-update/`）。"""
        _, mapper, factory = build(store)
        mapper.sync_tools("srv_a")
        factory.tools = [tool("echo_symbol", input_schema={
            "type": "object", "properties": {}, "required": ["symbol"]})]
        mapper.sync_tools("srv_a")
        assert [n for n in store.list_files("config") if n.startswith("skill-update/")] == []

    def test_contract_reverted_heals_to_active(self, store: Store):
        _, mapper, factory = build(store)
        mapper.sync_tools("srv_a")
        factory.tools = [tool("echo_symbol", input_schema={
            "type": "object", "properties": {}, "required": ["symbol"]})]
        assert mapper.sync_tools("srv_a")[0].status == "pending-remap"
        factory.tools = [ECHO]
        healed = mapper.sync_tools("srv_a")[0]
        assert healed.status == "active"
        assert healed.change_note == ""

    def test_pending_remap_lists_across_servers(self, store: Store):
        _, mapper, factory = build(store)
        build(store, tools=(ECHO,), server_id="srv_b")
        mapper.sync_tools("srv_a")
        mapper.sync_tools("srv_b")
        assert mapper.pending_remap() == ()
        factory.tools = [tool("echo_symbol", input_schema={
            "type": "object", "properties": {}, "required": ["symbol"]})]
        mapper.sync_tools("srv_a")
        pending = mapper.pending_remap()
        assert [(m.server_id, m.tool) for m in pending] == [("srv_a", "echo_symbol")]

    def test_detection_does_not_probe(self, store: Store):
        """A7：查询不重探 Server（面板刷新无副作用）。"""
        _, mapper, factory = build(store)
        mapper.sync_tools("srv_a")
        before = factory.opened
        mapper.pending_remap()
        mapper.mappings("srv_a")
        mapper.available_skills()
        assert factory.opened == before


# ───────────────────────── GWT-3 下半：确认重新映射 ─────────────────────────


class TestApplyRemap:
    """`apply_remap`：按 01 §9 落成新版本（④ 定案 A 方案）。"""

    def _make_pending(self, store: Store, new_input: dict):
        _, mapper, factory = build(store)
        mapper.sync_tools("srv_a")
        factory.tools = [tool("echo_symbol", input_schema=new_input,
                              output_schema=ECHO["outputSchema"])]
        mapper.sync_tools("srv_a")
        return mapper, factory

    def test_compatible_change_bumps_minor(self, store: Store):
        """新增**非必填**属性 → 兼容 → 次版本，且不产生待检查标记。"""
        mapper, _ = self._make_pending(store, {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "market": {"type": "string"}},
            "required": ["symbol"],
        })
        applied = mapper.apply_remap("srv_a", "echo_symbol")
        assert applied.mapping_version == "1.1"
        assert applied.skill_id == skill_id_for("sk_mcp_srv_a_echo_symbol", "1.1")
        assert applied.status == "active"
        assert applied.change_note == ""
        assert [n for n in store.list_files("config") if n.startswith("skill-update/")] == []
        # 老版本保留、新版本登记（01 §9）
        versions = SkillRegistry(store).list_versions("sk_mcp_srv_a_echo_symbol")
        assert [v.skill_id for v in versions] == [
            "sk_mcp_srv_a_echo_symbol_v1.0", "sk_mcp_srv_a_echo_symbol_v1.1",
        ]

    def test_incompatible_change_bumps_major_and_marks_update(self, store: Store):
        """新增**必填**属性 → 契约不兼容 → 主版本 + `skill-update/` 待检查标记。"""
        mapper, _ = self._make_pending(store, {
            "type": "object", "properties": {"symbol": {"type": "string"}},
            "required": ["symbol", "market"],
        })
        applied = mapper.apply_remap("srv_a", "echo_symbol")
        assert applied.mapping_version == "2.0"
        assert applied.skill_id == "sk_mcp_srv_a_echo_symbol_v2.0"
        assert "skill-update/sk_mcp_srv_a_echo_symbol.json" in store.list_files("config")
        desc = SkillRegistry(store).get(applied.skill_id)
        assert desc.input_schema["required"] == ["symbol", "market"]

    def test_apply_requires_pending_state(self, store: Store):
        _, mapper, _ = build(store)
        mapper.sync_tools("srv_a")
        with pytest.raises(McpMappingError) as excinfo:
            mapper.apply_remap("srv_a", "echo_symbol")
        assert "无待重新映射事项" in str(excinfo.value)

    def test_apply_unknown_mapping_is_rejected(self, store: Store):
        _, mapper, _ = build(store)
        with pytest.raises(McpMappingNotFoundError):
            mapper.apply_remap("srv_a", "nope")

    def test_apply_on_vanished_tool_is_rejected(self, store: Store):
        """A8：Server 不再提供该 tool → 显式拒，不留死状态。"""
        mapper, factory = self._make_pending(store, {
            "type": "object", "properties": {}, "required": ["symbol"]})
        factory.tools = []
        with pytest.raises(McpMappingError) as excinfo:
            mapper.apply_remap("srv_a", "echo_symbol")
        assert "已不再提供" in str(excinfo.value)


# ───────────────────────── GWT-5 禁用联动 ─────────────────────────


class TestServerDisabledLinkage:
    """GWT-5：Server 禁用 → 派生 Skill 立即不在可用列表；映射记录保留。"""

    def test_disable_hides_derived_skills_and_keeps_records(self, store: Store):
        servers, mapper, _ = build(store)
        mapper.sync_tools("srv_a")
        assert skill_ids(mapper) == {"sk_mcp_srv_a_echo_symbol_v1.0"}
        servers.disable_server("srv_a")
        assert mapper.available_skills() == ()
        assert [m.tool for m in mapper.mappings("srv_a")] == ["echo_symbol"]  # 记录保留
        servers.enable_server("srv_a")
        assert skill_ids(mapper) == {"sk_mcp_srv_a_echo_symbol_v1.0"}  # 重新启用即恢复

    def test_removed_server_hides_derived_skills(self, store: Store):
        servers, mapper, _ = build(store)
        mapper.sync_tools("srv_a")
        servers.remove_server("srv_a")
        assert mapper.available_skills() == ()

    def test_non_mcp_skills_unaffected(self, store: Store):
        servers, mapper, _ = build(store)
        mapper.sync_tools("srv_a")
        SkillRegistry(store).register(
            "sk_official_probe", version="1.0", name="官方探查",
            description="官方 Pack 的探查 Skill", source="official",
            offline_level="full", version_policy="follow-latest",
        )
        servers.disable_server("srv_a")
        assert {d.name for d in mapper.available_skills()} == {"官方探查"}


# ───────────────────────── 边界 ─────────────────────────


class TestBoundaries:
    """A1 / A4 / 落盘路径安全。"""

    def test_unapproved_permission_blocks_sync(self, store: Store):
        """A4：映射只对已批准权限的 Server 执行，装载不自动批准。"""
        servers, mapper, _ = build(store, permissions=(CACHE_SCOPE,))
        with pytest.raises(McpPermissionError) as excinfo:
            mapper.sync_tools("srv_a")
        assert CACHE_SCOPE in str(excinfo.value)
        assert "待批准" in str(excinfo.value)
        servers.permissions.approve("srv_a", CACHE_SCOPE)
        assert [m.tool for m in mapper.sync_tools("srv_a")] == ["echo_symbol"]

    def test_rejected_permission_blocks_sync(self, store: Store):
        servers, mapper, _ = build(store, permissions=(EXEC,))
        servers.permissions.reject("srv_a", EXEC)
        with pytest.raises(McpPermissionError) as excinfo:
            mapper.sync_tools("srv_a")
        assert "已拒绝" in str(excinfo.value)

    def test_connection_failure_is_explicit(self, store: Store):
        """Server 已注册但此刻连不上 → 显式拒（不静默跳过）。"""
        build(store)
        failing = McpServerRegistry(store, transport_factory=Factory((), fail="open"))
        mapper = McpSkillMapper(store, servers=failing, skills=SkillRegistry(store))
        with pytest.raises(McpConnectionError) as excinfo:
            mapper.sync_tools("srv_a")
        assert "连接失败" in str(excinfo.value)

    def test_unknown_server_is_rejected(self, store: Store):
        _, mapper, _ = build(store)
        with pytest.raises(McpServerNotFoundError):
            mapper.sync_tools("nope")

    def test_tool_name_unsafe_as_path_segment_is_rejected(self, store: Store):
        """tool 名同时是落盘路径段：路径分隔符 / 越界形态显式拒。"""
        for index, bad in enumerate(("a/b", "..", "_private", "a\\b")):
            _, mapper, _ = build(
                store, tools=(tool(bad),), server_id=f"srv_{index}",
            )
            with pytest.raises(McpValidationError):
                mapper.sync_tools(f"srv_{index}")

    def test_overlong_derived_skill_base_is_rejected(self, store: Store):
        """A1：派生注册名超长显式拒（不静默截断）。"""
        long_id = "srv_" + "x" * 55
        _, mapper, _ = build(store, server_id=long_id)
        with pytest.raises(McpValidationError) as excinfo:
            mapper.sync_tools(long_id)
        assert "超长" in str(excinfo.value)

    def test_colliding_normalized_names_are_rejected(self, store: Store):
        """同一 Server 上归一撞车（仅大小写不同）→ 显式拒，不静默别名。"""
        _, mapper, factory = build(store, tools=(tool("echo_symbol"),))
        mapper.sync_tools("srv_a")
        factory.tools = [tool("Echo_Symbol")]
        with pytest.raises(McpMappingError) as excinfo:
            mapper.sync_tools("srv_a")
        assert "撞车" in str(excinfo.value)

    def test_fingerprint_is_key_order_insensitive(self):
        """指纹口径：同构 schema 的不同键序得同一指纹。"""
        first = tool("t", input_schema={
            "type": "object", "properties": {"x": {"type": "string"}}})
        second = tool("t", input_schema={
            "properties": {"x": {"type": "string"}}, "type": "object"})
        fp_a = tool_fingerprints(
            ToolSpec(name="t", input_schema=first["inputSchema"]))[0]
        fp_b = tool_fingerprints(
            ToolSpec(name="t", input_schema=second["inputSchema"]))[0]
        assert fp_a == fp_b

    def test_fingerprint_ignores_description(self):
        """A6：指纹只含 schema、不含 description。"""
        described = tool("t", description="一版说明")
        reworded = tool("t", description="换了个说法")
        assert (tool_fingerprints(ToolSpec(name="t", input_schema=described["inputSchema"]))
                == tool_fingerprints(ToolSpec(name="t", input_schema=reworded["inputSchema"])))
