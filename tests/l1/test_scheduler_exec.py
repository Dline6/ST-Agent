"""T-L1-005.2 测试：调度执行接线（03 §1.2 / §3.2）。

GWT 对照（任务文件 5 条）：
- GWT-1 触发即走完整流水线（到期与手动同一路径）：SkillRun 可回放、Trace 带本次
  步骤且由调度器**创建** ``trace_id``、``initiator`` 标明来源＝定时调度
- GWT-2 传参取描述体**当前**默认值（双通道改参即生效）
- GWT-3 不应执行者显式不执行：复合 Skill 未物化 → ``gap``；已禁用 → ``skipped``
  （两者都留痕，且也推进到期，免得每轮重刷同一条）
- GWT-4 失败不静默：失败记账 + 原因可查 + ``last_success_at`` 不推进
- GWT-5 权限来自**注入来源**：有批准即执行、无来源（空集）即被流水线拦下并经
  信封显式化

另覆盖假设 A1（工作流经复合 Skill 执行）、A3（权限注入面）、A5（目标级互斥由
到期推进承担）。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.scheduler import (
    FREQUENCY_PARAM,
    Scheduler,
    SchedulerTargetNotFoundError,
    SchedulerValidationError,
)
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.workflow.models import WorkflowSchedule
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"
TZ = timezone(timedelta(hours=8))

FLOW = "wf_demo_flow_v1.0"
DEMO = "sk_demo_flow_v1.0"
MANUAL_FLOW = "wf_manual_flow_v1.0"
MANUAL_DEMO = "sk_manual_flow_v1.0"


def at(*args: int) -> datetime:
    """构造带时区的时刻（``at(2026, 9, 27, 8, 0)``）。"""
    return datetime(*args, tzinfo=TZ)


@dataclass(frozen=True)
class Dag:
    """工作流注册面的最小替身（真身 ``WorkflowDAG`` 带 ``flow_id`` / ``schedule``）。"""

    flow_id: str
    schedule: WorkflowSchedule


class Workflows:
    def __init__(self, *dags: Dag) -> None:
        self._dags = dags

    def list_all(self) -> tuple[Dag, ...]:
        return self._dags


def wf(flow_id: str, **schedule) -> Dag:
    return Dag(flow_id=flow_id, schedule=WorkflowSchedule(**schedule))


class Recorder:
    """执行器替身：记录每次上下文与入参，按设定返回信封（缺省即成功）。"""

    def __init__(self, envelope: ResultEnvelope | None = None) -> None:
        self.calls: list = []
        self.envelope = envelope

    def __call__(self, ctx, params):
        self.calls.append((ctx, dict(params)))
        return self.envelope if self.envelope is not None else ResultEnvelope.ok({"rows": 1})


class Allower:
    """权限来源替身：按 ``grants`` 放行。"""

    def __init__(self, grants: tuple[str, ...]) -> None:
        self._grants = grants
        self.seen: list = []

    def approved_for(self, target):
        self.seen.append(target.target_id)
        return self._grants


def register_flow(registry: SkillRegistry, base: str) -> str:
    """注册一个可供工作流执行的复合 Skill 体（返回其 skill_id）。"""
    descriptor = registry.register(
        base, version="1.0", name=f"{base} 执行体",
        description="调度触发的工作流复合体",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"rows": {"type": "integer"}}},
        parameters=({"name": "window_days", "type": "integer", "default": 20,
                     "description": "窗口天数"},),
    )
    return descriptor.skill_id


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def demo(store: Store) -> SkillRegistry:
    registry = SkillRegistry(store)
    register_flow(registry, "sk_demo_flow")
    register_flow(registry, "sk_manual_flow")
    return registry


@pytest.fixture()
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture()
def runner(store: Store, demo: SkillRegistry, recorder: Recorder) -> SkillRunner:
    runner = SkillRunner(store, demo)
    runner.register_executor(DEMO, recorder)
    runner.register_executor(MANUAL_DEMO, recorder)
    return runner


@pytest.fixture()
def sched(store: Store, demo: SkillRegistry, runner: SkillRunner) -> Scheduler:
    return Scheduler(
        store, runner=runner, skills=demo,
        workflows=Workflows(
            wf(FLOW, mode="interval", interval_minutes=60),
            wf(MANUAL_FLOW, mode="manual"),
        ),
    )


# ───────────────────────── GWT-1 触发即走完整流水线 ─────────────────────────


class TestGwt1Trigger:
    def test_tick_runs_the_workflows_composite_skill(self, sched: Scheduler,
                                                     recorder: Recorder) -> None:
        assert [r.target_id for r in sched.tick(at(2026, 9, 27, 8, 0))] == [FLOW]
        assert recorder.calls[0][0].skill_id == DEMO

    def test_scheduler_creates_the_trace_and_carries_the_step(
            self, sched: Scheduler, recorder: Recorder) -> None:
        run = [r for r in sched.tick(at(2026, 9, 27, 8, 0)) if r.target_id == FLOW][0]
        assert (run.status, run.envelope.status) == ("ok", "ok")
        ctx = recorder.calls[0][0]
        assert ctx.trace_id == run.trace_id
        assert ctx.initiator == f"schedule:{FLOW}"
        assert ctx.purpose == "定时调度触发"
        assert run.trace is not None
        assert run.trace.trace_id.value == run.trace_id
        assert [step.step_type for step in run.trace.steps] == ["skill_run"]
        assert run.trace.steps[0].ref == run.skill_run_id

    def test_skill_run_is_persisted_for_replay(self, sched: Scheduler,
                                               store: Store) -> None:
        run = [r for r in sched.tick(at(2026, 9, 27, 8, 0)) if r.target_id == FLOW][0]
        path = f"{RUN_PREFIX}{run.skill_run_id}.json"
        assert path in store.list_files("execution_log")
        record = json.loads(store.get("execution_log", path).decode("utf-8"))
        assert (record["skill_id"], record["trace_id"]) == (DEMO, run.trace_id)
        assert record["envelope"]["status"] == "ok"
        assert record["params"] == {"window_days": 20}

    def test_manual_trigger_takes_the_same_path(self, sched: Scheduler,
                                                recorder: Recorder) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        assert MANUAL_FLOW not in [t.target_id for t in sched.due_targets(t0)]
        run = sched.trigger(MANUAL_FLOW, now=t0)
        assert (run.target_id, run.skill_id, run.status) == (MANUAL_FLOW, MANUAL_DEMO, "ok")
        assert recorder.calls[0][0].initiator == f"schedule:{MANUAL_FLOW}"
        assert sched.state(MANUAL_FLOW).last_run_at == t0

    def test_trigger_unknown_target_raises(self, sched: Scheduler) -> None:
        with pytest.raises(SchedulerTargetNotFoundError):
            sched.trigger("wf_nope_v1.0", now=at(2026, 9, 27, 8, 0))

    def test_tick_without_a_runner_is_refused(self, store: Store,
                                              demo: SkillRegistry) -> None:
        scheduler = Scheduler(store, skills=demo,
                              workflows=Workflows(wf(FLOW, mode="interval",
                                                     interval_minutes=60)))
        with pytest.raises(SchedulerValidationError, match="执行面"):
            scheduler.tick(at(2026, 9, 27, 8, 0))


# ───────────────────────── GWT-2 传参取当前默认值 ─────────────────────────


class TestGwt2RunParameters:
    def test_defaults_are_read_at_run_time(self, sched: Scheduler, demo: SkillRegistry,
                                           recorder: Recorder) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        sched.tick(t0)
        assert recorder.calls[0][1] == {"window_days": 20}
        demo.set_parameters(DEMO, {"window_days": 5})
        sched.trigger(FLOW, now=t0)
        assert recorder.calls[1][1] == {"window_days": 5}


# ───────────────────────── GWT-3 不该跑的不跑 ─────────────────────────


class TestGwt3NotSupposedToRun:
    def test_unmaterialised_workflow_is_a_gap(self, store: Store, demo: SkillRegistry,
                                              runner: SkillRunner,
                                              recorder: Recorder) -> None:
        scheduler = Scheduler(
            store, runner=runner, skills=demo,
            workflows=Workflows(wf("wf_ghost_v1.0", mode="interval", interval_minutes=10)))
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert (run.status, run.skill_id, run.envelope, run.trace) == ("gap", None, None, None)
        assert "未物化" in run.detail
        assert recorder.calls == []
        assert scheduler.state("wf_ghost_v1.0").last_status == "gap"

    def test_disabled_constituent_skill_is_skipped(self, store: Store, demo: SkillRegistry,
                                                   runner: SkillRunner,
                                                   recorder: Recorder) -> None:
        class Sandbox:
            @staticmethod
            def is_disabled(skill_id: str) -> bool:
                return skill_id == DEMO

        scheduler = Scheduler(
            store, runner=runner, skills=demo, sandbox=Sandbox(),
            workflows=Workflows(wf(FLOW, mode="interval", interval_minutes=60)))
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert (run.status, run.skill_id) == ("skipped", DEMO)
        assert "禁用" in run.detail
        assert recorder.calls == []

    def test_not_run_also_advances_the_clock(self, store: Store, demo: SkillRegistry,
                                              runner: SkillRunner) -> None:
        scheduler = Scheduler(
            store, runner=runner, skills=demo,
            workflows=Workflows(wf("wf_ghost_v1.0", mode="interval", interval_minutes=30)))
        t0 = at(2026, 9, 27, 8, 0)
        assert len(scheduler.tick(t0)) == 1
        assert scheduler.tick(t0 + timedelta(minutes=1)) == ()
        assert len(scheduler.tick(t0 + timedelta(minutes=30))) == 1


# ───────────────────────── GWT-4 失败不静默 ─────────────────────────


class TestGwt4FailureIsNotSilent:
    def _scheduler(self, store: Store, demo: SkillRegistry,
                   recorder: Recorder) -> Scheduler:
        runner = SkillRunner(store, demo)
        runner.register_executor(DEMO, recorder)
        return Scheduler(store, runner=runner, skills=demo,
                         workflows=Workflows(wf(FLOW, mode="interval", interval_minutes=60)))

    def test_failed_envelope_is_recorded_with_its_reason(self, store: Store,
                                                         demo: SkillRegistry) -> None:
        recorder = Recorder(ResultEnvelope.failed("数据源不可用",
                                                  log_ref="execution_log/x.json"))
        scheduler = self._scheduler(store, demo, recorder)
        t0 = at(2026, 9, 27, 8, 0)
        (run,) = scheduler.tick(t0)
        assert (run.status, run.envelope.status) == ("failed", "failed")
        assert run.detail == "数据源不可用"
        state = scheduler.state(FLOW)
        assert (state.last_status, state.last_run_at) == ("failed", t0)
        assert state.last_success_at is None
        assert state.last_skill_run_id == run.skill_run_id

    def test_success_then_failure_keeps_the_last_success(self, store: Store,
                                                         demo: SkillRegistry) -> None:
        recorder = Recorder()
        scheduler = self._scheduler(store, demo, recorder)
        first = at(2026, 9, 27, 8, 0)
        scheduler.tick(first)
        assert scheduler.state(FLOW).last_success_at == first

        recorder.envelope = ResultEnvelope.failed("抓取失败", log_ref="execution_log/x.json")
        second = first + timedelta(minutes=60)
        scheduler.tick(second)
        state = scheduler.state(FLOW)
        assert (state.last_status, state.last_run_at) == ("failed", second)
        assert state.last_success_at == first

    def test_executor_exception_becomes_a_failed_envelope(self, store: Store,
                                                         demo: SkillRegistry) -> None:
        runner = SkillRunner(store, demo)

        def explode(ctx, params):
            raise RuntimeError("执行器炸了")

        runner.register_executor(DEMO, explode)
        scheduler = Scheduler(store, runner=runner, skills=demo,
                              workflows=Workflows(wf(FLOW, mode="interval",
                                                     interval_minutes=60)))
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert (run.status, run.envelope.status) == ("failed", "failed")
        assert "执行器炸了" in run.detail


# ───────────────────────── GWT-5 权限来自注入来源 ─────────────────────────


class TestGwt5Permissions:
    def _registry(self, store: Store) -> tuple[SkillRegistry, str]:
        registry = SkillRegistry(store)
        descriptor = registry.register(
            "sk_perm_watch", version="1.0", name="需联网的盯盘",
            description="声明网络权限与频率参数的 Skill",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
            permissions=("net_access:<*.baostock.com>",),
            parameters=({"name": FREQUENCY_PARAM, "type": "integer", "default": 30,
                         "description": "频率参数"},))
        return registry, descriptor.skill_id

    def test_granted_permissions_let_the_run_through(self, store: Store) -> None:
        registry, target_id = self._registry(store)
        runner = SkillRunner(store, registry)
        runner.register_executor(target_id, Recorder())
        source = Allower(("net_access:<*.baostock.com>",))
        scheduler = Scheduler(store, skills=registry, runner=runner, permissions=source)
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert (run.status, run.envelope.status) == ("ok", "ok")
        assert source.seen == [target_id]

    def test_empty_source_is_blocked_and_surfaced(self, store: Store) -> None:
        registry, target_id = self._registry(store)
        runner = SkillRunner(store, registry)
        runner.register_executor(target_id, Recorder())
        scheduler = Scheduler(store, skills=registry, runner=runner)
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert run.envelope.status == "validation_failed"
        assert "权限" in run.detail
        assert scheduler.state(target_id).last_status == "failed"

    def test_bad_permission_source_is_refused(self, store: Store) -> None:
        with pytest.raises(SchedulerValidationError, match="权限来源"):
            Scheduler(store, permissions=object())


# ───────────────────────── 轮询循环 ─────────────────────────


class TestRunForever:
    def test_loop_ticks_until_stopped(self, store: Store, demo: SkillRegistry) -> None:
        recorder = Recorder()
        runner = SkillRunner(store, demo)
        runner.register_executor(DEMO, recorder)
        scheduler = Scheduler(store, runner=runner, skills=demo,
                              workflows=Workflows(wf(FLOW, mode="interval",
                                                     interval_minutes=60)))
        times = [at(2026, 9, 27, 8, 0), at(2026, 9, 27, 9, 0), at(2026, 9, 27, 10, 0)]
        stop = threading.Event()
        reads: list[int] = []

        def clock() -> datetime:
            if len(reads) >= len(times):
                stop.set()
                return times[-1]
            reads.append(len(reads))
            return times[len(reads) - 1]

        fired = scheduler.run_forever(poll_seconds=0, stop=stop, clock=clock)
        assert fired == 3                      # 三个到期点各跑一次
        assert stop.is_set()
