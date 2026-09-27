"""T-L1-005.3 测试：离线到期处置（03 §6 第三句 + 02 §7 + 01 §7）。

GWT 对照（任务文件 5 条）：
- GWT-1 离线可执行者照跑并标注：``full`` / ``degraded`` 照常执行，说明里标注
  「离线执行、数据停留在最后快照」
- GWT-2 离线不可执行者记待补：``none`` 记 ``deferred`` 且不产生 SkillRun
- GWT-3 恢复后按配置补跑（幂等）：补跑一次并清空待补集，重复调用不再执行
- GWT-4 恢复后按配置跳过：不执行、记 ``skipped`` 可查，待补集清空
- GWT-5 配置面按 01 §7 登记：缺省值 / 改配置产生 ``change_id`` / 同值零留痕 /
  改后行为随之变化

另覆盖假设 A1（离线判定取网关单一源头）、A2（分级判据取描述体 ``offline_level``）、
A3（策略条目新登记、不动 01 契约）、A4（待补集语义）、A5（只交付数据面）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.scheduler import (
    DEFAULT_OFFLINE_CATCH_UP,
    OFFLINE_CATCH_UP_CONFIG_ID,
    Scheduler,
    SchedulerPolicy,
    SchedulerValidationError,
)
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.workflow.models import WorkflowSchedule
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"
TZ = timezone(timedelta(hours=8))

FLOW = "wf_demo_flow_v1.0"
DEMO = "sk_demo_flow_v1.0"


def at(*args: int) -> datetime:
    """构造带时区的时刻（``at(2026, 9, 27, 8, 0)``）。"""
    return datetime(*args, tzinfo=TZ)


@dataclass(frozen=True)
class Dag:
    flow_id: str
    schedule: WorkflowSchedule


class Workflows:
    def __init__(self, *dags: Dag) -> None:
        self._dags = dags

    def list_all(self) -> tuple[Dag, ...]:
        return self._dags


class Gateway:
    """出网网关替身（真身 ``EgressGateway.online`` 为只读属性）。"""

    def __init__(self, *, online: bool = True) -> None:
        self.online = online


class Recorder:
    def __init__(self, envelope: ResultEnvelope | None = None) -> None:
        self.calls: list = []
        self.envelope = envelope

    def __call__(self, ctx, params):
        self.calls.append((ctx, dict(params)))
        return self.envelope if self.envelope is not None else ResultEnvelope.ok({"rows": 1})


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture()
def gateway() -> Gateway:
    return Gateway()


def build(store: Store, recorder: Recorder, gateway: Gateway, *,
          level: str = "full", workflow: str = FLOW) -> Scheduler:
    """装配一个「工作流 → 复合 Skill」的调度器，复合体的 ``offline_level`` 可调。"""
    registry = SkillRegistry(store)
    registry.register(
        "sk_demo_flow", version="1.0", name="工作流执行体",
        description="调度触发的工作流复合体",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"rows": {"type": "integer"}}},
        offline_level=level,
    )
    runner = SkillRunner(store, registry)
    runner.register_executor(DEMO, recorder)
    return Scheduler(
        store, runner=runner, skills=registry, gateway=gateway,
        workflows=Workflows(Dag(flow_id=workflow, schedule=WorkflowSchedule(
            mode="interval", interval_minutes=60))),
    )


# ───────────────────────── GWT-1 离线可执行者照跑 ─────────────────────────


class TestGwt1OfflineRunnable:
    @pytest.mark.parametrize("level", ["full", "degraded"])
    def test_runs_offline_with_a_staleness_note(self, store: Store, recorder: Recorder,
                                                gateway: Gateway, level: str) -> None:
        scheduler = build(store, recorder, gateway, level=level)
        gateway.online = False
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert (run.status, run.envelope.status) == ("ok", "ok")
        assert "离线执行" in run.detail
        assert "最后快照" in run.detail
        assert level in run.detail
        assert len(recorder.calls) == 1
        assert scheduler.state(FLOW).pending == ()

    def test_online_run_carries_no_offline_note(self, store: Store, recorder: Recorder,
                                                gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway)
        (run,) = scheduler.tick(at(2026, 9, 27, 8, 0))
        assert run.status == "ok"
        assert run.detail == ""

    def test_no_gateway_means_online(self, store: Store, recorder: Recorder) -> None:
        scheduler = build(store, recorder, Gateway())
        assert scheduler.tick(at(2026, 9, 27, 8, 0))[0].status == "ok"


# ───────────────────────── GWT-2 离线不可执行者记待补 ─────────────────────────


class TestGwt2Deferred:
    def test_none_is_deferred_without_a_skill_run(self, store: Store, recorder: Recorder,
                                                  gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway, level="none")
        gateway.online = False
        t0 = at(2026, 9, 27, 8, 0)
        (run,) = scheduler.tick(t0)
        assert (run.status, run.envelope, run.skill_run_id) == ("deferred", None, None)
        assert "none" in run.detail and "必须联网" in run.detail
        assert recorder.calls == []
        assert not [n for n in store.list_files("execution_log")
                    if n.startswith(RUN_PREFIX)]

        state = scheduler.state(FLOW)
        assert (state.last_status, state.last_run_at, state.pending) == ("deferred", t0, (t0,))
        assert state.last_success_at is None

    def test_deferred_also_advances_the_clock(self, store: Store, recorder: Recorder,
                                              gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway, level="none")
        gateway.online = False
        t0 = at(2026, 9, 27, 8, 0)
        scheduler.tick(t0)
        assert scheduler.tick(t0 + timedelta(minutes=1)) == ()
        assert len(scheduler.tick(t0 + timedelta(minutes=60))) == 1

    def test_manual_trigger_offline_is_deferred_too(self, store: Store, recorder: Recorder,
                                                    gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway, level="none")
        gateway.online = False
        run = scheduler.trigger(FLOW, now=at(2026, 9, 27, 8, 0))
        assert run.status == "deferred"
        assert scheduler.state(FLOW).pending == (at(2026, 9, 27, 8, 0),)


# ───────────────────────── GWT-3 恢复后补跑 ─────────────────────────


class TestGwt3CatchUp:
    def _deferred_twice(self, store: Store, recorder: Recorder,
                        gateway: Gateway) -> tuple[Scheduler, datetime, datetime]:
        scheduler = build(store, recorder, gateway, level="none")
        gateway.online = False
        t0 = at(2026, 9, 27, 8, 0)
        t1 = t0 + timedelta(minutes=60)
        scheduler.tick(t0)
        scheduler.tick(t1)
        assert scheduler.state(FLOW).pending == (t0, t1)
        return scheduler, t0, t1

    def test_catch_up_runs_once_and_clears_pending(self, store: Store, recorder: Recorder,
                                                   gateway: Gateway) -> None:
        scheduler, _t0, t1 = self._deferred_twice(store, recorder, gateway)
        gateway.online = True
        (run,) = scheduler.catch_up(t1)
        assert (run.status, run.skill_id) == ("ok", DEMO)
        assert "错过 2 个到期点" in run.detail
        assert "补跑一次" in run.detail
        assert len(recorder.calls) == 1
        assert scheduler.state(FLOW).pending == ()

    def test_catch_up_is_idempotent(self, store: Store, recorder: Recorder,
                                    gateway: Gateway) -> None:
        scheduler, _t0, t1 = self._deferred_twice(store, recorder, gateway)
        gateway.online = True
        scheduler.catch_up(t1)
        assert scheduler.catch_up(t1) == ()
        assert scheduler.catch_up(t1 + timedelta(minutes=1)) == ()
        assert len(recorder.calls) == 1

    def test_catch_up_while_still_offline_keeps_the_missed_set(
            self, store: Store, recorder: Recorder, gateway: Gateway) -> None:
        scheduler, t0, t1 = self._deferred_twice(store, recorder, gateway)
        (run,) = scheduler.catch_up(t1)
        assert run.status == "deferred"
        assert scheduler.state(FLOW).pending == (t0, t1)
        assert recorder.calls == []

    def test_nothing_pending_means_no_action(self, store: Store, recorder: Recorder,
                                             gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway)
        assert scheduler.catch_up(at(2026, 9, 27, 8, 0)) == ()
        assert recorder.calls == []

    def test_pending_for_a_target_off_the_surface_is_closed_out(
            self, store: Store, recorder: Recorder, gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway, level="none")
        gateway.online = False
        t0 = at(2026, 9, 27, 8, 0)
        scheduler.tick(t0)
        gateway.online = True

        # 目标已离开调度面（离线期间被禁用 / 注销）：待补项显式收尾，不永久挂着
        orphan = Scheduler(store, gateway=gateway, workflows=Workflows())
        (run,) = orphan.catch_up(t0)
        assert (run.target_id, run.status) == (FLOW, "skipped")
        assert "不在调度面" in run.detail
        assert recorder.calls == []
        assert orphan.state(FLOW).pending == ()


# ───────────────────────── GWT-4 恢复后跳过 ─────────────────────────


class TestGwt4Skip:
    def test_skip_records_and_clears(self, store: Store, recorder: Recorder,
                                     gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway, level="none")
        SchedulerPolicy(store).set("skip")
        gateway.online = False
        t0 = at(2026, 9, 27, 8, 0)
        scheduler.tick(t0)
        gateway.online = True

        (run,) = scheduler.catch_up(t0)
        assert (run.status, run.skill_id, run.envelope) == ("skipped", None, None)
        assert "跳过" in run.detail
        assert recorder.calls == []
        state = scheduler.state(FLOW)
        assert (state.last_status, state.pending) == ("skipped", ())

    def test_skip_is_idempotent(self, store: Store, recorder: Recorder,
                                gateway: Gateway) -> None:
        scheduler = build(store, recorder, gateway, level="none")
        SchedulerPolicy(store).set("skip")
        gateway.online = False
        t0 = at(2026, 9, 27, 8, 0)
        scheduler.tick(t0)
        gateway.online = True
        scheduler.catch_up(t0)
        assert scheduler.catch_up(t0) == ()


# ───────────────────────── GWT-5 配置面（01 §7） ─────────────────────────


class TestGwt5Policy:
    def test_entry_shape_and_default(self, store: Store) -> None:
        policy = SchedulerPolicy(store, now=lambda: at(2026, 9, 27, 8, 0))
        entry = policy.entry()
        assert entry.config_id == OFFLINE_CATCH_UP_CONFIG_ID
        assert entry.scope == "global"
        assert entry.panel_form_spec.widget == "select"
        assert entry.panel_form_spec.choices == ("catch-up", "skip")
        assert entry.change_policy.requires_confirmation is True
        assert entry.default == DEFAULT_OFFLINE_CATCH_UP == "catch-up"
        assert policy.value() == "catch-up"

    def test_set_writes_a_change_record_and_persists(self, store: Store) -> None:
        policy = SchedulerPolicy(store, now=lambda: at(2026, 9, 27, 8, 0))
        change = policy.set("skip")
        assert change is not None
        assert change.change_id.startswith("chg_")
        assert change.config_id == OFFLINE_CATCH_UP_CONFIG_ID
        assert (change.old_value, change.new_value) == ("catch-up", "skip")
        assert change.applied_at.startswith("2026-09-27T08:00")
        assert f"scheduler-change/{change.change_id}.json" in store.list_files("execution_log")
        assert SchedulerPolicy(store).value() == "skip"
        assert SchedulerPolicy(store).changes() == (change,)

    def test_same_value_writes_nothing(self, store: Store) -> None:
        policy = SchedulerPolicy(store)
        assert policy.set("catch-up") is None
        assert policy.changes() == ()
        policy.set("skip")
        assert policy.set("skip") is None
        assert len(policy.changes()) == 1

    def test_illegal_value_is_refused(self, store: Store) -> None:
        with pytest.raises(SchedulerValidationError, match="非法离线处置取值"):
            SchedulerPolicy(store).set("maybe")

    def test_corrupt_entry_falls_back_to_default(self, store: Store) -> None:
        store.put("config", "scheduler-policy/offline-catch-up.json", b"{not json")
        assert SchedulerPolicy(store).value() == "catch-up"

    def test_policy_entry_is_not_mistaken_for_a_state(self, store: Store,
                                                      recorder: Recorder,
                                                      gateway: Gateway) -> None:
        SchedulerPolicy(store).set("skip")
        scheduler = build(store, recorder, gateway)
        assert scheduler.states() == ()
