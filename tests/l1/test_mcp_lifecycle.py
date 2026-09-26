"""T-L1-002.3 测试：03 §5.3 状态机与降级 + §5.2 禁用联动。

GWT 对照（任务文件 5 条 + 回归）：
- GWT-1 五态状态机 + 转移留痕（合法记录 / 非法显式拒绝 / ``permission_pending`` 由权限账本触发）
- GWT-2 崩溃 → 显式提示 + 按**可配**次数自动重连（策略经 01 §7 注册，改动留 ``change_id``）
- GWT-3 重连耗尽 → 降级候选**并列陈列**（不自动切换 / 不合并 / 不给统一建议）
- GWT-4 禁用 → 状态转 ``disabled`` + 进行中调用中止且走显式失败分支
- GWT-5 远程出网全登记可查；``remove_server`` 后历史审计保留
- 边界：A5 状态与留痕分置两分区 · A6 策略拆两条目 · A7 中止为检查点语义

不碰真实网络：注册走 ``transport_factory`` 替身，远程调用走 ``post`` 替身。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.registry_types import ConfigEntry
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from st_agent.l1.mcp import (
    HUB_CHANGE_PREFIX,
    HUB_STATE_PREFIX,
    INTERVAL_CONFIG_ID,
    MAX_RETRIES_CONFIG_ID,
    NOTICE_PREFIX,
    TRANSITION_PREFIX,
    HttpSseTransport,
    McpClient,
    McpConnectionError,
    McpHubConfig,
    McpHubStateMachine,
    McpServerNotFoundError,
    McpServerRegistry,
    McpStateError,
    McpStateTransitionError,
    McpTransport,
    McpValidationError,
    ReconnectSettings,
)
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack

PASS = "correct horse battery staple"
URL = "https://mcp.example.com/rpc"
EXEC = "exec_command"
BAOSTOCK_NET = "net_access:<*.baostock.com>"
CACHE_SCOPE = "local_read:<data/cache/**>"
FAKE_COMMAND = "python"

STUB_TOOLS = [{
    "name": "echo_symbol",
    "description": "回显证券代码",
    "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
}]


# ───────────────────────── 离线替身 ─────────────────────────


class FakeTransport(McpTransport):
    """离线替身传输：握手 + ``tools/list`` 可控，不碰子进程与网络。"""

    def __init__(self, *, tools=(), server_name: str = "fake-mcp") -> None:
        super().__init__()
        self.config_tools = list(tools)
        self.config_server_name = server_name

    def _open(self) -> None:  # noqa: D102 - 由基类语义定义
        pass

    def _close(self) -> None:
        pass

    def _write(self, payload: str) -> None:
        pass

    def _read(self, request_id: int, timeout_ms: int) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": request_id,
                           "result": {"tools": self.config_tools}})


class Probe:
    """探测替身：按预设序列回答，并记录调用次数（GWT-2 的「重试次数」证据）。"""

    def __init__(self, answers) -> None:
        self._answers = list(answers)
        self.calls = 0

    def __call__(self, _server_id: str) -> bool:
        answer = self._answers[min(self.calls, len(self._answers) - 1)]
        self.calls += 1
        return bool(answer)


class FakeSandbox:
    """沙箱替身：只实现降级候选需要的 ``is_disabled``。"""

    def __init__(self, disabled=()) -> None:
        self._disabled = set(disabled)

    def is_disabled(self, skill_id: str) -> bool:
        return skill_id in self._disabled


class Clock:
    """严格递增的确定性时钟（留痕可按时间稳定排序，避免同微秒并列）。"""

    def __init__(self) -> None:
        self._base = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
        self._n = 0

    def __call__(self) -> datetime:
        self._n += 1
        return self._base + timedelta(microseconds=self._n)


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def make_registry(store: Store, *, gateway=None) -> McpServerRegistry:
    """注册表（注入替身传输工厂：连接测试不碰真实子进程/网络）。"""
    return McpServerRegistry(
        store, gateway=gateway,
        transport_factory=lambda _record: FakeTransport(tools=STUB_TOOLS),
    )


def add_stdio(registry: McpServerRegistry, sid: str = "local-stub", *, permissions=()):
    return registry.add_server(
        sid, display_name="本地替身", transport="stdio",
        command=FAKE_COMMAND, permissions=permissions,
    )


def add_remote(registry: McpServerRegistry, sid: str = "remote-one", *, permissions=()):
    return registry.add_server(
        sid, display_name="远程替身", transport="remote",
        url=URL, remote_acknowledged=True, permissions=permissions,
    )


def make_hub(store: Store, registry: McpServerRegistry, **kwargs) -> McpHubStateMachine:
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("now", Clock())
    return McpHubStateMachine(store, servers=registry, **kwargs)


# ───────────────────────── GWT-1 状态机 + 留痕 ─────────────────────────


def test_state_defaults_to_disconnected_and_legal_chain_is_logged(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)

    assert hub.state_of("local-stub") == "disconnected"
    assert hub.transitions("local-stub") == ()

    hub.transition("local-stub", "connected", trace_id="tr-1", reason="探测成功")
    hub.transition("local-stub", "reconnecting", trace_id="tr-1")
    hub.transition("local-stub", "connected", trace_id="tr-1")
    hub.transition("local-stub", "disabled", trace_id="tr-2")
    hub.transition("local-stub", "disconnected", trace_id="tr-2", reason="用户启用")

    logged = hub.transitions("local-stub")
    assert [(t.from_state, t.to_state) for t in logged] == [
        ("disconnected", "connected"),
        ("connected", "reconnecting"),
        ("reconnecting", "connected"),
        ("connected", "disabled"),
        ("disabled", "disconnected"),
    ]
    assert all(t.occurred_at.tzinfo is not None for t in logged)
    assert all(t.reason.strip() for t in logged)
    assert [t.trace_id for t in logged] == ["tr-1", "tr-1", "tr-1", "tr-2", "tr-2"]
    assert hub.state_of("local-stub") == "disconnected"


def test_illegal_transition_is_rejected_and_state_unchanged(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)

    hub.disable("local-stub")
    with pytest.raises(McpStateTransitionError) as excinfo:
        hub.transition("local-stub", "connected")
    assert "非法状态转移" in str(excinfo.value)
    assert hub.state_of("local-stub") == "disabled"
    # 只有 disable 记的那一条；被拒的转移不留痕
    assert len(hub.transitions("local-stub")) == 1


def test_invalid_state_value_is_rejected(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    with pytest.raises(McpValidationError):
        hub.transition("local-stub", "sleeping")


def test_same_state_transition_is_idempotent_without_log(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)

    record = hub.transition("local-stub", "disconnected")
    assert record.state == "disconnected"
    assert hub.transitions("local-stub") == ()
    assert f"{HUB_STATE_PREFIX}local-stub.json" in store.list_files("config")


def test_state_of_rejects_unregistered_server(store):
    registry = make_registry(store)
    hub = make_hub(store, registry)
    with pytest.raises(McpServerNotFoundError):
        hub.state_of("nobody")


def test_permission_pending_is_derived_from_permission_book(store):
    registry = make_registry(store)
    add_stdio(registry, permissions=(EXEC, BAOSTOCK_NET))
    hub = make_hub(store, registry)

    assert hub.state_of("local-stub") == "permission_pending"
    record = hub.sync_from_registry("local-stub", trace_id="tr-p")
    assert record.state == "permission_pending"
    assert [(t.from_state, t.to_state) for t in hub.transitions("local-stub")] == [
        ("disconnected", "permission_pending"),
    ]

    registry.permissions.approve("local-stub", EXEC)
    registry.permissions.approve("local-stub", BAOSTOCK_NET)
    # 权限全部批准后不停留 pending（推导态，不粘住）
    assert hub.state_of("local-stub") == "disconnected"
    assert hub.sync_from_registry("local-stub").state == "disconnected"


def test_state_record_and_history_live_in_separate_partitions(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)

    hub.transition("local-stub", "connected")
    assert f"{HUB_STATE_PREFIX}local-stub.json" in store.list_files("config")
    log_names = store.list_files("execution_log")
    assert any(n.startswith(TRANSITION_PREFIX) for n in log_names)
    assert not any(n.startswith(TRANSITION_PREFIX) for n in store.list_files("config"))


# ───────────────────────── GWT-2 崩溃 → 提示 + 可配重连 ─────────────────────────


def test_on_disconnect_recovers_within_configured_retries(store):
    registry = make_registry(store)
    add_stdio(registry)
    slept: list[float] = []
    probe = Probe([False, False, True])
    hub = make_hub(store, registry, probe=probe, sleep=slept.append)
    hub.config.set(max_retries=3, interval_ms=250)

    hub.transition("local-stub", "connected")
    notices = hub.on_disconnect("local-stub", reason="对端关闭", trace_id="tr-9")

    assert [n.kind for n in notices] == ["server-disconnected", "reconnect-recovered"]
    assert all(n.occurred_at.tzinfo is not None for n in notices)
    assert hub.state_of("local-stub") == "connected"
    assert probe.calls == 3
    assert slept == [0.25, 0.25]
    chain = [(t.from_state, t.to_state) for t in hub.transitions("local-stub")]
    assert ("connected", "reconnecting") in chain
    assert ("reconnecting", "connected") in chain
    assert hub.notices("local-stub") == notices


def test_on_disconnect_exhausts_then_exposes_candidates(store):
    registry = make_registry(store)
    add_stdio(registry)
    skills = SkillRegistry(store)
    ensure_official_pack(skills)
    probe = Probe([False])
    hub = make_hub(store, registry, skills=skills, probe=probe)
    hub.config.set(max_retries=2, interval_ms=0)

    hub.transition("local-stub", "connected")
    notices = hub.on_disconnect("local-stub")

    assert [n.kind for n in notices] == ["server-disconnected", "reconnect-exhausted"]
    assert hub.state_of("local-stub") == "disconnected"
    assert probe.calls == 2
    assert "degrade_candidates" in notices[-1].message
    assert hub.degrade_candidates("local-stub")  # 耗尽后候选可查


def test_on_disconnect_refuses_a_disabled_server(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    hub.disable("local-stub")
    with pytest.raises(McpStateError):
        hub.on_disconnect("local-stub")


def test_reconnect_primitive_requires_reconnecting_state(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry, probe=Probe([True]))
    with pytest.raises(McpStateError):
        hub.reconnect("local-stub")


def test_zero_retries_stops_without_probing(store):
    registry = make_registry(store)
    add_stdio(registry)
    probe = Probe([True])
    hub = make_hub(store, registry, probe=probe)
    hub.config.set(max_retries=0)
    hub.transition("local-stub", "connected")

    notices = hub.on_disconnect("local-stub")
    assert [n.kind for n in notices] == ["server-disconnected", "reconnect-exhausted"]
    assert probe.calls == 0
    assert hub.state_of("local-stub") == "disconnected"


def test_reconnect_settings_are_config_entries_with_change_ids(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)

    entries = hub.config.register_defaults()
    assert len(entries) == 2
    for entry in entries:
        assert isinstance(entry, ConfigEntry)
        assert len(ConfigEntry.model_fields) == 8  # 01 §7 七字段（config_id/display_name 同列一行）
        assert entry.scope == "global"
        assert entry.change_policy.record_history is True
        assert entry.panel_form_spec.help_text.strip()
        assert entry.description_for_chat.strip()
    assert hub.config.get() == ReconnectSettings(
        max_retries=3, interval_ms=1000)

    changes = hub.config.set(max_retries=5, trace_id="tr-cfg")
    assert len(changes) == 1
    change = changes[0]
    assert change.config_id == MAX_RETRIES_CONFIG_ID
    assert (change.old_value, change.new_value) == (3, 5)
    assert change.change_id.startswith("chg_")
    assert change.trace_ref == "tr-cfg"
    assert hub.config.get().max_retries == 5
    assert hub.config.changes() == changes
    assert f"{HUB_CHANGE_PREFIX}{change.change_id}.json" in store.list_files("execution_log")

    reread = {e.config_id: e for e in hub.config.list_entries()}
    assert reread[MAX_RETRIES_CONFIG_ID].default == 5
    assert reread[INTERVAL_CONFIG_ID].default == 1000

    # 同值再设不留痕；非法取值显式拒
    assert hub.config.set(max_retries=5) == ()
    with pytest.raises(McpValidationError):
        hub.config.set(interval_ms=-1)


def test_settings_are_registered_per_option_not_compounded(store):
    """A6：次数与间隔各自独立条目（一条目只有一个 panel_form_spec）。"""
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    hub.config.register_defaults()
    ids = {e.config_id for e in hub.config.list_entries()}
    assert ids == {MAX_RETRIES_CONFIG_ID, INTERVAL_CONFIG_ID}
    assert HUB_CHANGE_PREFIX.endswith("/") and not MAX_RETRIES_CONFIG_ID.endswith(".json")
    assert isinstance(hub.config, McpHubConfig)


# ───────────────────────── GWT-3 降级候选并列 ─────────────────────────


def _seed_skills(store: Store) -> SkillRegistry:
    skills = SkillRegistry(store)
    skills.register("sk_alpha", name="行情快照", description="公共行情快照",
                    source="official", offline_level="full")
    skills.register("sk_beta", name="财务汇总", description="财务指标汇总",
                    source="user-built", offline_level="degraded")
    skills.register("sk_gamma", name="联网检索项", description="需联网执行",
                    source="official", offline_level="none")
    skills.register("sk_mapped", name="映射来的项", description="来自某 MCP Server",
                    source="mcp-mapped", offline_level="none")
    return skills


def test_degrade_candidates_list_platform_builtins_in_parallel(store):
    registry = make_registry(store)
    add_stdio(registry)
    skills = _seed_skills(store)
    hub = make_hub(store, registry, skills=skills)

    before = store.list_files("config")
    candidates = hub.degrade_candidates("local-stub")

    assert [c.skill_id for c in candidates] == ["sk_alpha_v1.0", "sk_beta_v1.0", "sk_gamma_v1.0"]
    assert "mcp-mapped" not in {c.source for c in candidates}
    assert [c.available for c in candidates] == [True, True, False]
    assert "离线完整可用" in candidates[0].detail
    assert "降级" in candidates[1].detail
    assert "离线不可用" in candidates[2].detail
    # 并列陈列：不带推荐语/排序权重
    assert all("建议" not in c.detail for c in candidates)
    # 纯读：不写盘、不留痕、不改状态
    assert store.list_files("config") == before
    assert hub.transitions("local-stub") == ()
    assert hub.notices("local-stub") == ()
    assert hub.state_of("local-stub") == "disconnected"


def test_degrade_candidates_reflect_sandbox_disabled(store):
    registry = make_registry(store)
    add_stdio(registry)
    skills = _seed_skills(store)
    sandbox = FakeSandbox(disabled=["sk_alpha_v1.0"])
    hub = make_hub(store, registry, skills=skills, sandbox=sandbox)

    candidates = {c.skill_id: c for c in hub.degrade_candidates("local-stub")}
    assert candidates["sk_alpha_v1.0"].available is False
    assert "禁用" in candidates["sk_alpha_v1.0"].detail
    assert candidates["sk_beta_v1.0"].available is True


def test_degrade_candidates_cover_the_official_pack(store):
    registry = make_registry(store)
    add_stdio(registry)
    skills = SkillRegistry(store)
    ensure_official_pack(skills)
    hub = make_hub(store, registry, skills=skills)

    candidates = hub.degrade_candidates("local-stub")
    assert len(candidates) == 12
    assert {c.source for c in candidates} == {"official"}
    assert [c.skill_id for c in candidates] == sorted(c.skill_id for c in candidates)


def test_degrade_candidates_empty_without_skill_registry(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    assert hub.degrade_candidates("local-stub") == ()


# ───────────────────────── GWT-4 禁用 → 中止进行中调用 ─────────────────────────


def test_cancel_inflight_only_signals_without_changing_state(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    hub.transition("local-stub", "connected")

    with hub.begin_call("local-stub") as first, hub.begin_call("local-stub") as second:
        assert hub.inflight("local-stub") == (first, second)
        assert hub.cancel_inflight("local-stub") == 2
        assert first.cancelled and second.cancelled
        assert hub.state_of("local-stub") == "connected"
    assert hub.inflight("local-stub") == ()


def test_begin_call_refuses_a_disabled_server(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    hub.disable("local-stub")
    with pytest.raises(McpStateError):
        hub.begin_call("local-stub")


def test_cancel_signal_aborts_inflight_remote_call_end_to_end(store):
    """GWT-4 中止链路：真实 ``HttpSseTransport`` → 网关 → ``cancelled`` 审计 + 显式失败。

    本用例的信号走不写盘的 ``cancel_inflight``，故并发窗口内只有工作线程写盘——这只是
    该场景的天然形态，不再是规避手段：``Store`` 的同分区并发写自 ``T-L0-008`` 起已由分区级
    互斥锁串行化（原「清单缓存无写并发保护」的 L0 遗留册 E1 随之关闭，回归用例见
    ``tests/l0/test_store_concurrency.py``）。
    """
    gateway = EgressGateway(store)
    registry = make_registry(store, gateway=gateway)
    add_remote(registry, permissions=(EXEC,))
    registry.permissions.approve("remote-one", EXEC)
    hub = make_hub(store, registry)

    entered = threading.Event()
    outcome: dict[str, object] = {}

    def blocking_post(_url: str, _body: str, _timeout_ms: int) -> str:
        """替身发包：进入后阻塞，直到取消信号置位（模拟「正在进行中」）。"""
        entered.set()
        holder.cancel.wait(5)
        return "{}"

    with hub.begin_call("remote-one", trace_id="tr-4") as holder:
        transport = HttpSseTransport(
            URL, gateway, "remote-one", post=blocking_post, cancel=holder.cancel,
        )
        client = McpClient(transport)

        def worker() -> None:
            try:
                client.call_tool("echo_symbol", {"symbol": "sh.600000"})
                outcome["error"] = None
            except McpConnectionError as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(5), "替身发包未进入"
        signalled = hub.cancel_inflight("remote-one")
        thread.join(5)

    assert signalled == 1
    assert holder.cancelled is True
    # 结果走显式失败分支（不留「成功」假象）
    assert isinstance(outcome["error"], McpConnectionError)
    assert "取消" in str(outcome["error"])

    events = gateway.query(kind="remote_mcp", initiator="remote-one")
    assert [e.status for e in events] == ["cancelled"]
    assert events[0].detail
    # 审计不含请求体内容（零明文）
    payload = json.dumps(events[0].model_dump(mode="json"), ensure_ascii=False)
    assert "symbol" not in payload and "echo_symbol" not in payload


def test_disable_marks_state_notice_and_signals_inflight(store):
    """GWT-4 状态面（串行）：禁用 → 状态转 ``disabled`` + 提示 + 在途调用被信号化。"""
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    hub.transition("local-stub", "connected")

    with hub.begin_call("local-stub", trace_id="tr-4") as holder:
        record = hub.disable("local-stub", trace_id="tr-4")
        assert holder.cancelled is True

    assert record.state == "disabled"
    assert hub.state_of("local-stub") == "disabled"
    assert [(t.from_state, t.to_state) for t in hub.transitions("local-stub")][-1] == (
        "connected", "disabled")
    assert [n.kind for n in hub.notices("local-stub")] == ["call-cancelled"]


def test_disabled_server_cannot_be_reconnected_through_transition(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry)
    hub.disable("local-stub")
    with pytest.raises(McpStateTransitionError):
        hub.transition("local-stub", "reconnecting")
    # 启用后回基态，由探测决定能否 connected
    assert hub.enable("local-stub").state == "disconnected"
    assert hub.state_of("local-stub") == "disconnected"


# ───────────────────────── GWT-5 出网全登记 + 审计不可注销 ─────────────────────────


def _echo_post(_url: str, body: str, _timeout_ms: int) -> str:
    """替身发包：按请求 id 回一条合法 JSON-RPC 响应（不碰真实网络）。"""
    request = json.loads(body)
    return json.dumps({"jsonrpc": "2.0", "id": request.get("id"),
                       "result": {"tools": []}})


def test_remote_egress_is_fully_audited_and_survives_removal(store):
    gateway = EgressGateway(store)
    registry = make_registry(store, gateway=gateway)
    add_remote(registry)
    hub = make_hub(store, registry)

    transport = HttpSseTransport(URL, gateway, "remote-one", post=_echo_post)
    transport.request("tools/list")
    transport.request("tools/list")

    events = gateway.query(kind="remote_mcp", initiator="remote-one")
    assert len(events) == 2
    assert [e.status for e in events] == ["ok", "ok"]
    assert all(e.purpose.strip() for e in events)
    assert all(e.target_host == "mcp.example.com" for e in events)
    assert all(e.bytes_out > 0 and e.bytes_in > 0 for e in events)

    registry.remove_server("remote-one")
    # 审计不可注销：注册记录没了，历史出网仍可查
    assert len(gateway.query(kind="remote_mcp", initiator="remote-one")) == 2
    with pytest.raises(McpServerNotFoundError):
        hub.state_of("remote-one")


def test_audited_egress_carries_no_payload_plaintext(store):
    gateway = EgressGateway(store)
    registry = make_registry(store, gateway=gateway)
    add_remote(registry)
    _ = make_hub(store, registry)
    transport = HttpSseTransport(URL, gateway, "remote-one", post=_echo_post)
    transport.request("tools/call")

    events = gateway.query(kind="remote_mcp", initiator="remote-one")
    assert len(events) == 1
    payload = json.dumps(events[0].model_dump(mode="json"), ensure_ascii=False)
    assert "tools/call" not in payload
    # 审计记录只在 execution_log 的 net/ 前缀下（唯一出口口径）
    assert any(n.startswith("net/remote_mcp/") for n in store.list_files("execution_log"))


def test_notices_partition_is_append_only(store):
    registry = make_registry(store)
    add_stdio(registry)
    hub = make_hub(store, registry, probe=Probe([False]))
    hub.config.set(max_retries=1)
    hub.transition("local-stub", "connected")
    hub.on_disconnect("local-stub")

    names = [n for n in store.list_files("execution_log") if n.startswith(NOTICE_PREFIX)]
    assert len(names) == 2
    assert len({Path(n).name for n in names}) == 2
