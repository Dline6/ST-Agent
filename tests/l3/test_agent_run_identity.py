"""T-AGT-022.1 测试：开跑铸号与活动 run 注册表。

GWT-1（开跑前铸号且收口同一标识）、GWT-2（可按 id 取句柄、结束移除）、
GWT-3（同会话再次启动显式拒绝）；另加注册表本体的三条守卫（重复标识 / 同会话冲突 /
结束后清理）。循环用最小替身驱动，不接 LLM、不接 L1 真执行面。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from st_agent.composition.agent import _AgentInvestigator
from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.trace import Trace
from st_agent.l1.skills.tool_catalog import ToolEntry
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime import (
    ActiveRunRegistry,
    LoopOutcome,
    RunAlreadyActiveError,
    TurnResult,
    agent_log_ref_of,
    conclude_agent_run,
    load_agent_run,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SESSION = "sess_00000000000000000001"
TOOL = ToolEntry(name="sk_demo", skill_id="sk_demo_v1.0", description="演示工具")


class _Skills:
    def tool_catalog(self) -> tuple[ToolEntry, ...]:
        return (TOOL,)


class _Bounds:
    def steps(self) -> int:
        return 8

    def llm_calls(self) -> int:
        return 12

    def budget_chars(self) -> int:
        return 4000


class _Card:
    """最小确认卡（`investigate` 只读 `values` 与 `target`）。"""

    values: dict[str, object] = {}
    target = "查证 sh.600000 的公告"


def _outcome() -> LoopOutcome:
    return LoopOutcome(
        task="查证 sh.600000 的公告", endpoint_id="ep_test",
        termination="finished", trace=Trace(trace_id=TraceId.generate()),
        max_steps=8, max_llm_calls=12,
    )


def _port(store, *, protocol, runs=None) -> _AgentInvestigator:
    return _AgentInvestigator(
        store=store, runner=object(), skills=_Skills(), bounds=_Bounds(),
        protocol=protocol, endpoint_id="ep_test", runs=runs,
    )


class _ProbeProtocol:
    """一轮即结束；在「模型轮」里探测注册表（此时循环已经开始）。"""

    def __init__(self, runs: ActiveRunRegistry, seen: list) -> None:
        self._runs = runs
        self._seen = seen

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        self._seen.append(
            tuple(
                (run.agent_run_id, run.session_id, run.started_at, run.cancel_requested)
                for run in (self._runs.get(i) for i in self._runs.active_ids())
                if run is not None
            )
        )
        return TurnResult(text="完成")


class _ExplodingProtocol:
    """若被调用即失败——用于断言「冲突时不跑循环」。"""

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        raise AssertionError("同会话冲突时不应进入循环")


class _CancellingProtocol:
    """在「模型轮」里经注册表句柄请求取消——验控制面与循环共享**同一事件**（T-AGT-024.1 A3）。"""

    def __init__(self, runs: ActiveRunRegistry) -> None:
        self._runs = runs

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        handle = self._runs.get(self._runs.active_ids()[0])
        assert cancel is handle.cancel_event          # 组合根把句柄的事件原样交给循环
        handle.request_cancel()
        return TurnResult(text="不应被当作模型给出的结束")


# ───────────────────────── GWT-1 / GWT-2 ─────────────────────────


def test_gwt1_gwt2_id_exists_before_loop_and_handle_is_removed_after(store):
    runs = ActiveRunRegistry()
    seen: list = []
    port = _port(store, protocol=_ProbeProtocol(runs, seen), runs=runs)

    result = port.investigate(_Card(), session_id=SESSION)

    assert result.report is not None
    assert result.envelope.status == "ok"
    # GWT-1：循环运行期间注册表里就是这个 id，收口留痕沿用它
    assert seen, "循环里应探测到活动 run"
    assert seen[0][0][0] == result.agent_run_id
    assert seen[0][0][1] == SESSION
    assert load_agent_run(store, result.agent_run_id).agent_run_id == result.agent_run_id
    assert agent_log_ref_of(result.agent_run_id) in store.list_files("execution_log")
    # GWT-2：结束后注册表移除该 run
    assert runs.get(result.agent_run_id) is None
    assert runs.active_for_session(SESSION) is None


def test_gwt2_handle_carries_identity_and_cancel_marker(store):
    runs = ActiveRunRegistry()
    handle = runs.register("agr_00000000000000000001", SESSION)
    assert handle.session_id == SESSION
    assert handle.started_at is not None
    assert handle.cancel_requested is False
    assert runs.get(handle.agent_run_id).cancel_event is handle.cancel_event  # 同一事件，不另存副本
    handle.request_cancel()
    assert runs.get(handle.agent_run_id).cancel_requested is True
    assert runs.finish(handle.agent_run_id) is True
    assert runs.get(handle.agent_run_id) is None


def test_cancel_request_reaches_the_running_loop(store):
    """控制面置位 → 循环按 ``cancelled`` 收口；一步未执行即如实空态。"""
    runs = ActiveRunRegistry()
    port = _port(store, protocol=_CancellingProtocol(runs), runs=runs)

    result = port.investigate(_Card(), session_id=SESSION)

    assert result.report is not None
    assert result.report.record.termination == "cancelled"
    assert result.envelope.status == "empty"
    assert runs.get(result.agent_run_id) is None    # 收口后照常移除


def test_stop_requests_cancel_on_active_run_only(store):
    """停止口：在册即置位并回真；不在册回假（no-op），不伪造一次停止。"""
    runs = ActiveRunRegistry()
    port = _port(store, protocol=_ProbeProtocol(runs, []), runs=runs)
    handle = runs.register("agr_00000000000000000003")

    assert port.stop(handle.agent_run_id) is True
    assert handle.cancel_requested is True
    assert port.stop("agr_missing") is False


# ───────────────────────── GWT-3 ─────────────────────────


def test_gwt3_same_session_second_start_is_rejected_without_running(store):
    runs = ActiveRunRegistry()
    runs.register("agr_00000000000000000002", SESSION)
    port = _port(store, protocol=_ExplodingProtocol(), runs=runs)

    result = port.investigate(_Card(), session_id=SESSION)

    assert result.report is None and result.trace is None
    assert result.envelope.status == "validation_failed"
    assert "已有活动 run" in (result.envelope.reason or "")
    assert runs.active_for_session(SESSION).agent_run_id == "agr_00000000000000000002"


def test_gwt3_sessionless_runs_do_not_conflict(store):
    """互斥只在有 `session_id` 时生效（D-154 B9 说的是「一个会话」）。"""
    runs = ActiveRunRegistry()
    first = _port(store, protocol=_ProbeProtocol(runs, []), runs=runs)
    second = _port(store, protocol=_ProbeProtocol(runs, []), runs=runs)

    one = first.investigate(_Card(), session_id=None)
    two = second.investigate(_Card(), session_id=None)

    assert one.envelope.status == "ok" and two.envelope.status == "ok"
    assert one.agent_run_id != two.agent_run_id


# ───────────────────────── 注册表守卫 ─────────────────────────


def test_registry_rejects_duplicate_id_and_same_session():
    runs = ActiveRunRegistry()
    runs.register("agr_a", SESSION)
    with pytest.raises(AgentRuntimeValidationError):
        runs.register("agr_a", None)
    with pytest.raises(RunAlreadyActiveError):
        runs.register("agr_b", SESSION)
    assert runs.finish("agr_a") is True
    assert runs.finish("agr_a") is False          # 幂等
    runs.register("agr_b", SESSION)               # 原会话释放后可再登记


def test_conclude_uses_provided_id(store):
    fixed = "agr_00000000000000000003"
    report = conclude_agent_run(store, _outcome(), agent_run_id=fixed)
    assert report.agent_run_id == fixed
    assert report.record.agent_run_id == fixed
    assert agent_log_ref_of(fixed) in store.list_files("execution_log")


# ───────────────────────── T-AGT-023.1：预铸标识与只读运行面 ─────────────────────────


def test_preassigned_run_id_is_used_and_readable_after_finish(store):
    """后台派发预铸的标识被沿用；结束后仍可按 id 回放事件（SSE 断线重连的底座）。"""
    runs = ActiveRunRegistry()
    port = _port(store, protocol=_ProbeProtocol(runs, []), runs=runs)
    fixed = "agr_00000000000000000004"

    result = port.investigate(_Card(), session_id=SESSION, agent_run_id=fixed)

    assert result.agent_run_id == fixed
    assert agent_log_ref_of(fixed) in store.list_files("execution_log")
    assert port.is_active(fixed) is False
    assert port.has_event_log(fixed) is True
    events = port.events(fixed)
    assert events[0].event == "run_started"
    assert events[-1].event == "run_finished"
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert port.new_run_id() != fixed


def test_duplicate_preassigned_id_is_rejected_without_running(store):
    runs = ActiveRunRegistry()
    fixed = "agr_00000000000000000005"
    runs.register(fixed, SESSION)
    port = _port(store, protocol=_ExplodingProtocol(), runs=runs)

    result = port.investigate(_Card(), session_id=SESSION, agent_run_id=fixed)

    assert result.report is None
    assert result.envelope.status == "validation_failed"
    assert runs.get(fixed) is not None


def test_wait_started_reports_timeout_for_unregistered_id(store):
    port = _port(store, protocol=_ProbeProtocol(ActiveRunRegistry(), []))
    assert port.wait_started("agr_never_registered", timeout=0.05) is False
