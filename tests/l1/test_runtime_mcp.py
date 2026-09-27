"""T-L1-006.3 测试：组合根的 MCP 面装配与移除回收接线（03 §5.1 / §5.2 / §5.3）。

GWT 对照（任务文件 4 条）：
- GWT-1 回收接线生效（映射记录 + 派生 Skill 全部版本被硬回收）
- GWT-2 生命周期记录不留孤儿（销 L1 册 `B1`；重挂不继承旧态）
- GWT-3 移除序列与缺省行为（disable → cancel → remove；未接线时行为不变）
- GWT-4 审计留痕不受波及（只清当前态，`execution_log` 不动）

真机路径：经 `tests/l1/_mcp_stub_server.py` 的本地 stdio 子进程工具（离线；与
`test_mcp_mapping.py` 同一存根），不触网。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.mcp import (
    McpServerNotFoundError,
    McpServerRegistry,
    McpSkillMapper,
)
from st_agent.l1.runtime import L1Runtime, build_l1_runtime
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"
STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"
SERVER = "stub_local"
STATE_PATH = f"mcp-lifecycle/{SERVER}.json"


class _FakeMarketQuery:
    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        return ResultEnvelope.empty("测试数据面为空")


def _add_stub(rt: L1Runtime) -> None:
    rt.mcp_servers.add_server(
        SERVER, display_name="本地存根", command=sys.executable, args=[str(STUB)],
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def runtime(store: Store) -> L1Runtime:
    return build_l1_runtime(store, market_query=_FakeMarketQuery())


@pytest.fixture()
def mounted(store: Store, runtime: L1Runtime) -> L1Runtime:
    """已挂一台 Server、已同步映射、已落生命周期当前态记录的运行时。"""
    _add_stub(runtime)
    runtime.mcp_mapper.sync_tools(SERVER)
    runtime.mcp_machine.sync_from_registry(SERVER)
    assert STATE_PATH in store.list_files("config")
    return runtime


def _derived(rt: L1Runtime) -> list[str]:
    return [d.skill_id for d in rt.skills.list_all() if d.skill_id.startswith("sk_mcp_")]


class TestGwt1RecycleWiring:
    def test_remove_recycles_mappings_and_derived_skills(self, store: Store,
                                                         mounted: L1Runtime):
        assert mounted.mcp_mapper.mappings(SERVER)
        assert _derived(mounted)

        mounted.remove_mcp_server(SERVER, trace_id="tr_gwt1_remove")

        assert mounted.mcp_mapper.mappings(SERVER) == ()
        assert [n for n in store.list_files("config") if n.startswith("mcp-mapping/")] == []
        assert _derived(mounted) == []
        assert SERVER not in [s.server_id for s in mounted.mcp_servers.list_servers()]

    def test_recycled_skill_can_be_remounted(self, store: Store, mounted: L1Runtime):
        mounted.remove_mcp_server(SERVER, trace_id="tr_gwt1_remount")

        _add_stub(mounted)
        again = mounted.mcp_mapper.sync_tools(SERVER)

        assert [m.tool for m in again]              # 重新挂载即重新注册（id 确定性派生）
        assert _derived(mounted)


class TestGwt2NoOrphanLifecycleRecord:
    def test_current_state_record_is_cleared(self, store: Store, mounted: L1Runtime):
        mounted.remove_mcp_server(SERVER, trace_id="tr_gwt2")

        assert STATE_PATH not in store.list_files("config")
        assert [n for n in store.list_files("config") if n.startswith("mcp-lifecycle/")] == []

    def test_state_of_after_removal_fails_explicitly(self, mounted: L1Runtime):
        """`state_of` 对未注册 Server 抛错＝正确行为（不是缺陷，见任务 A2）。"""
        mounted.remove_mcp_server(SERVER, trace_id="tr_gwt2b")

        with pytest.raises(McpServerNotFoundError):
            mounted.mcp_machine.state_of(SERVER)

    def test_remount_does_not_inherit_old_state(self, store: Store, mounted: L1Runtime):
        mounted.remove_mcp_server(SERVER, trace_id="tr_gwt2c")
        _add_stub(mounted)

        assert mounted.mcp_machine.sync_from_registry(SERVER).state == "disconnected"

    def test_recycle_server_is_idempotent(self, store: Store, mounted: L1Runtime):
        assert mounted.mcp_machine.recycle_server(SERVER) is True
        assert mounted.mcp_machine.recycle_server(SERVER) is False


class TestGwt3RemovalSequence:
    def test_disable_precedes_removal(self, mounted: L1Runtime):
        # 登记发生在 `__enter__`（见 ActiveCall），故移除须在 with 块内发生
        with mounted.mcp_machine.begin_call(SERVER) as call:
            mounted.remove_mcp_server(SERVER, trace_id="tr_gwt3")
            # 取消信号只由 disable / cancel_inflight 发出
            assert call.cancelled is True

        # 转移留痕的写入要求 Server 当时仍在册（transition 会 get_server）→ 顺序为
        # 「先 disable 后 remove_server」
        assert "disabled" in [t.to_state for t in mounted.mcp_machine.transitions(SERVER)]

    def test_bare_registry_without_the_hook_keeps_records(self, store: Store):
        """缺省未接线的 `remove_server` 行为与既有一致（同 `T-L1-002.2` 口径）。"""
        servers = McpServerRegistry(store)
        servers.add_server(SERVER, display_name="本地存根", command=sys.executable,
                           args=[str(STUB)])
        mapper = McpSkillMapper(store, servers=servers, skills=SkillRegistry(store))
        mapper.sync_tools(SERVER)

        servers.remove_server(SERVER)

        assert [m.tool for m in mapper.mappings(SERVER)]


class TestGwt4AuditUntouched:
    def test_transitions_and_notices_survive_removal(self, store: Store, mounted: L1Runtime):
        before = mounted.mcp_machine.transitions(SERVER)

        mounted.remove_mcp_server(SERVER, trace_id="tr_gwt4")

        # 既有留痕一条不少（移除序列自身新追加的 `disabled` 转移不在比对范围）
        assert set(before) <= set(mounted.mcp_machine.transitions(SERVER))
        assert [n for n in store.list_files("execution_log")
                if n.startswith("mcp-lifecycle/")]      # 留痕仍在
