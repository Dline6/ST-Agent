"""T-L1-005 测试：定时调度（03 §6）。

T-L1-005.1（判定与状态）GWT 对照：
- GWT-1 目标枚举：工作流 ∪ 声明 ``frequency_minutes`` 的 Skill，已禁用者不在其中，
  按标识升序
- GWT-2 到期判定与幂等：``interval`` 到点即到期；记账后同一时刻不再到期
- GWT-3 cron 求值：命中矩阵 + 跨月 / 备选日 / 闰日；非法表达式构造期即拒；
  已注册工作流携带非法 cron 时该目标被排除且经 ``invalid()`` 显式列出
- GWT-4 运行状态可查与跨实例落盘：状态落 ``config`` 分区、换实例可读回；
  ``manual`` 目标不进自动到期集
- GWT-5 无 ``Store`` 退化：仅进程内，行为逐字段一致

T-L1-005.2（触发执行）GWT 对照：
- GWT-1 触发即走完整流水线（到期与手动同一路径）：SkillRun 可回放、Trace 带本次
  步骤且 ``trace_id`` 由调度器创建、``initiator`` 标明来源
- GWT-2 传参取描述体当前默认值（双通道改参即生效）
- GWT-3 不应执行者显式不执行（复合 Skill 未物化 → ``gap`` / 已禁用 → ``skipped``）
- GWT-4 失败不静默：失败记账 + 原因可查 + ``last_success_at`` 不推进
- GWT-5 权限来自注入来源：有批准即执行、空集即被拦下并经信封显式化

另覆盖假设 A2（频率参数约定口径）、A4（cron 最小子集边界）、A5（event 不自动触发）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.scheduler import (
    STATE_PREFIX,
    CronSpec,
    CronSyntaxError,
    ScheduleTarget,
    Scheduler,
    SchedulerTargetNotFoundError,
    SchedulerValidationError,
)
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l1.workflow.models import WorkflowSchedule
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"
TZ = timezone(timedelta(hours=8))
STOCK_WATCH = "sk_stock_watch_v1.0"
FREQ_PARAM = "frequency_minutes"


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


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def scheduler(store: Store, skills: SkillRegistry) -> Scheduler:
    workflows = Workflows(
        wf("wf_morning_v1.0", mode="cron", cron="0 8 * * *"),
        wf("wf_daily_v1.0", mode="interval", interval_minutes=60),
        wf("wf_manual_v1.0", mode="manual"),
    )
    return Scheduler(store, workflows=workflows, skills=skills)


# ───────────────────────── GWT-1 目标枚举 ─────────────────────────


class TestGwt1Targets:
    def test_enumerates_workflows_and_frequency_skills(self, scheduler: Scheduler) -> None:
        ids = [t.target_id for t in scheduler.targets()]
        assert ids == ["sk_stock_watch_v1.0", "wf_daily_v1.0", "wf_manual_v1.0",
                       "wf_morning_v1.0"]

    def test_manual_workflow_is_a_target_but_never_due(self, scheduler: Scheduler) -> None:
        target = scheduler.target("wf_manual_v1.0")
        assert target.kind == "workflow"
        assert target.schedule.mode == "manual"
        due = [t.target_id for t in scheduler.due_targets(at(2026, 9, 27, 8, 0))]
        assert "wf_manual_v1.0" not in due

    def test_origin_is_explicit_per_source(self, scheduler: Scheduler) -> None:
        assert scheduler.target("wf_daily_v1.0").origin == "workflow-schedule"
        skill = scheduler.target(STOCK_WATCH)
        assert (skill.kind, skill.origin) == ("skill", "skill-frequency")
        assert skill.schedule.mode == "interval"
        assert skill.schedule.interval_minutes == 60       # pack.py 的声明默认值

    def test_event_workflow_is_a_target_but_never_due(self) -> None:
        scheduler = Scheduler(None, workflows=Workflows(
            wf("wf_on_event_v1.0", mode="event", event="SkillRunCompleted")))
        assert [t.target_id for t in scheduler.targets()] == ["wf_on_event_v1.0"]
        assert scheduler.due_targets(at(2026, 9, 27, 8, 0)) == ()

    def test_disabled_skill_leaves_the_target_set(self, store: Store,
                                                  skills: SkillRegistry) -> None:
        class Sandbox:
            def __init__(self, disabled: set[str]) -> None:
                self._disabled = disabled

            def is_disabled(self, skill_id: str) -> bool:
                return skill_id in self._disabled

        scheduler = Scheduler(store, skills=skills, sandbox=Sandbox({STOCK_WATCH}))
        assert scheduler.targets() == ()
        assert Scheduler(store, skills=skills).targets() != ()

    def test_skill_without_frequency_param_is_not_a_target(self, store: Store) -> None:
        reg = SkillRegistry(store)
        reg.register("sk_plain", name="无频率参数", description="不声明频率参数的 Skill",
                     input_schema={"type": "object", "properties": {}},
                     output_schema={"type": "object", "properties": {}})
        assert Scheduler(store, skills=reg).targets() == ()

    def test_skill_with_unusable_frequency_is_rejected_loudly(self, store: Store) -> None:
        reg = SkillRegistry(store)
        reg.register("sk_broken_freq", name="频率值不可用", description="声明了频率但缺省值不合用",
                     input_schema={"type": "object", "properties": {}},
                     output_schema={"type": "object", "properties": {}},
                     parameters=({"name": FREQ_PARAM, "type": "string", "default": "",
                                  "description": "频率参数"},))
        with pytest.raises(SchedulerValidationError, match=FREQ_PARAM):
            Scheduler(store, skills=reg).targets()

    def test_unknown_target_raises(self, scheduler: Scheduler) -> None:
        with pytest.raises(SchedulerTargetNotFoundError):
            scheduler.target("wf_nope_v1.0")

    def test_bad_target_id_is_rejected(self, scheduler: Scheduler) -> None:
        with pytest.raises(SchedulerValidationError):
            scheduler.state("../escape")


# ───────────────────────── GWT-2 到期判定与幂等 ─────────────────────────


class TestGwt2IntervalDue:
    def test_first_tick_is_due_without_history(self, scheduler: Scheduler) -> None:
        due = [t.target_id for t in scheduler.due_targets(at(2026, 9, 27, 8, 0))]
        assert due == ["sk_stock_watch_v1.0", "wf_daily_v1.0", "wf_morning_v1.0"]

    def test_same_instant_is_not_due_twice(self, scheduler: Scheduler) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        assert "wf_daily_v1.0" in [t.target_id for t in scheduler.due_targets(t0)]
        scheduler.record_run("wf_daily_v1.0", status="ok", now=t0)
        assert "wf_daily_v1.0" not in [t.target_id for t in scheduler.due_targets(t0)]
        assert "wf_daily_v1.0" not in [
            t.target_id for t in scheduler.due_targets(t0 + timedelta(seconds=59))]
        assert "wf_daily_v1.0" in [
            t.target_id for t in scheduler.due_targets(t0 + timedelta(minutes=60))]

    def test_failure_also_advances_the_clock(self, scheduler: Scheduler) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        scheduler.record_run("wf_daily_v1.0", status="failed", now=t0,
                             detail="上游不可用")
        assert "wf_daily_v1.0" not in [t.target_id for t in scheduler.due_targets(t0)]

    def test_next_due_at_reports_now_when_already_due(self, scheduler: Scheduler) -> None:
        t0 = at(2026, 9, 27, 8, 0, 30)
        assert scheduler.next_due_at("wf_daily_v1.0", now=t0) == at(2026, 9, 27, 8, 0)

    def test_next_due_at_after_a_run(self, scheduler: Scheduler) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        scheduler.record_run("wf_daily_v1.0", status="ok", now=t0)
        assert scheduler.next_due_at("wf_daily_v1.0", now=t0) == t0 + timedelta(minutes=60)

    def test_next_due_at_is_none_for_passive_modes(self, scheduler: Scheduler) -> None:
        assert scheduler.next_due_at("wf_manual_v1.0", now=at(2026, 9, 27, 8, 0)) is None

    def test_naive_clock_is_rejected(self, scheduler: Scheduler) -> None:
        with pytest.raises(SchedulerValidationError, match="时区"):
            scheduler.due_targets(datetime(2026, 9, 27, 8, 0))


# ───────────────────────── GWT-3 cron 求值 ─────────────────────────


class TestGwt3Cron:
    @pytest.mark.parametrize("expression, moment, hit", [
        ("0 8 * * *", (2026, 9, 27, 8, 0), True),
        ("0 8 * * *", (2026, 9, 27, 8, 1), False),
        ("0 8 * * *", (2026, 9, 27, 9, 0), False),
        ("*/15 * * * *", (2026, 9, 27, 8, 45), True),
        ("*/15 * * * *", (2026, 9, 27, 8, 46), False),
        ("0 9 * * 1-5", (2026, 9, 28, 9, 0), True),     # 周一
        ("0 9 * * 1-5", (2026, 9, 27, 9, 0), False),    # 周日
        ("0 0 * * 1", (2026, 9, 29, 0, 0), False),      # 周二：日段不受限 → 按「与」
        ("0 0 1 * 1", (2026, 9, 1, 0, 0), True),        # 两段都受限 → 按「或」
        ("0 0 1 * 1", (2026, 9, 28, 0, 0), True),       # 周一，非 1 号
        ("0 0 1 * 1", (2026, 9, 29, 0, 0), False),      # 周二且非 1 号
        ("0 0 * * 7", (2026, 9, 27, 0, 0), True),       # 7 = 周日别名
        ("0 0 1,15 * *", (2026, 9, 15, 0, 0), True),
        ("0 0 1-3/2 * *", (2026, 9, 3, 0, 0), True),    # 1-3 步长 2 → {1, 3}
        ("0 0 1-3/2 * *", (2026, 9, 2, 0, 0), False),
    ])
    def test_matches_matrix(self, expression: str, moment: tuple, hit: bool) -> None:
        assert CronSpec.parse(expression).matches(at(*moment)) is hit

    def test_next_after_within_the_day(self) -> None:
        spec = CronSpec.parse("30 8 * * *")
        assert spec.next_after(at(2026, 9, 27, 8, 0)) == at(2026, 9, 27, 8, 30)
        assert spec.next_after(at(2026, 9, 27, 8, 30)) == at(2026, 9, 28, 8, 30)

    def test_next_after_crosses_the_month(self) -> None:
        assert CronSpec.parse("0 0 1 * *").next_after(
            at(2026, 1, 31, 0, 0)) == at(2026, 2, 1, 0, 0)

    def test_next_after_handles_leap_day(self) -> None:
        assert CronSpec.parse("0 0 29 2 *").next_after(
            at(2026, 3, 1, 0, 0)) == at(2028, 2, 29, 0, 0)

    def test_seconds_do_not_participate(self) -> None:
        spec = CronSpec.parse("0 8 * * *")
        assert spec.matches(at(2026, 9, 27, 8, 0)) is True
        assert spec.next_after(at(2026, 9, 27, 8, 0, 30)) == at(2026, 9, 28, 8, 0)

    @pytest.mark.parametrize("expression", [
        "", "0 8 * *", "0 8 * * * *", "60 8 * * *", "0 24 * * *", "0 0 0 * *",
        "0 0 * 13 *", "0 0 * * 8", "0 8 * * abc", "*/0 * * * *", "0-? * * * *",
        "0 8 * * 1--2", "0,,1 * * * *", "0 9-3 * * *", "0 8 * * MON",
    ])
    def test_illegal_expressions_are_rejected_at_parse(self, expression: str) -> None:
        with pytest.raises(CronSyntaxError):
            CronSpec.parse(expression)

    def test_non_string_expression_is_rejected(self) -> None:
        with pytest.raises(CronSyntaxError):
            CronSpec.parse(None)                                 # type: ignore[arg-type]

    def test_registered_cron_target_runs_on_match_only(self) -> None:
        scheduler = Scheduler(None, workflows=Workflows(
            wf("wf_morning_v1.0", mode="cron", cron="0 8 * * *")))
        assert [t.target_id for t in scheduler.due_targets(at(2026, 9, 27, 7, 59))] == []
        assert [t.target_id for t in scheduler.due_targets(at(2026, 9, 27, 8, 0))] \
            == ["wf_morning_v1.0"]
        scheduler.record_run("wf_morning_v1.0", status="ok", now=at(2026, 9, 27, 8, 0))
        assert scheduler.due_targets(at(2026, 9, 27, 8, 0, 30)) == ()
        assert scheduler.next_due_at(
            "wf_morning_v1.0", now=at(2026, 9, 27, 8, 0)) == at(2026, 9, 28, 8, 0)

    def test_illegal_registered_cron_is_surfaced_not_silent(self) -> None:
        scheduler = Scheduler(None, workflows=Workflows(
            wf("wf_bad_v1.0", mode="cron", cron="0 8 * *"),
            wf("wf_good_v1.0", mode="interval", interval_minutes=30)))
        assert [t.target_id for t in scheduler.due_targets(at(2026, 9, 27, 8, 0))] \
            == ["wf_good_v1.0"]
        (target_id, expression, reason), = scheduler.invalid()
        assert (target_id, expression) == ("wf_bad_v1.0", "0 8 * *")
        assert "5 段" in reason

    def test_invalid_is_empty_when_all_expressions_parse(self, scheduler: Scheduler) -> None:
        assert scheduler.invalid() == ()


# ───────────────────────── GWT-4 运行状态与跨实例落盘 ─────────────────────────


class TestGwt4State:
    def test_never_run_state_is_empty(self, scheduler: Scheduler) -> None:
        state = scheduler.state("wf_daily_v1.0")
        assert (state.last_run_at, state.last_status, state.updated_at) == (None, None, None)
        assert scheduler.states() == ()

    def test_state_round_trips_through_a_fresh_instance(self, store: Store,
                                                        skills: SkillRegistry) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        scheduler = Scheduler(store, skills=skills)
        scheduler.record_run("wf_daily_v1.0", status="ok", now=t0,
                             detail="一轮盯盘完成", skill_run_id="run_abc")

        assert f"{STATE_PREFIX}wf_daily_v1.0.json" in store.list_files("config")
        reopened = Scheduler(store, skills=skills)
        state = reopened.state("wf_daily_v1.0")
        assert (state.last_run_at, state.last_status, state.updated_at) == (t0, "ok", t0)
        assert (state.last_detail, state.last_skill_run_id) == ("一轮盯盘完成", "run_abc")
        assert [s.target_id for s in reopened.states()] == ["wf_daily_v1.0"]

    def test_records_every_status_in_the_vocabulary(self, scheduler: Scheduler) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        for status in ("ok", "failed", "gap", "skipped", "deferred"):
            state = scheduler.record_run("wf_daily_v1.0", status=status, now=t0,
                                         detail=status)
            assert (state.last_status, state.last_detail) == (status, status)
            assert state.last_run_at == t0

    def test_unknown_status_is_rejected(self, scheduler: Scheduler) -> None:
        with pytest.raises(SchedulerValidationError, match="未知运行状态"):
            scheduler.record_run("wf_daily_v1.0", status="pending",
                                 now=at(2026, 9, 27, 8, 0))

    def test_corrupt_state_record_is_surfaced(self, store: Store, skills: SkillRegistry) -> None:
        store.put("config", f"{STATE_PREFIX}wf_daily_v1.0.json", b"{not json")
        with pytest.raises(SchedulerValidationError, match="损坏"):
            Scheduler(store, skills=skills).state("wf_daily_v1.0")

    def test_ran_at_defaults_to_now(self, scheduler: Scheduler) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        state = scheduler.record_run("wf_daily_v1.0", status="ok", now=t0)
        assert state.last_run_at == t0


# ───────────────────────── GWT-5 无 Store 退化 ─────────────────────────


class TestGwt5WithoutStore:
    def test_behaviour_matches_the_persisted_path(self, skills: SkillRegistry) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        scheduler = Scheduler(None, skills=skills)
        assert [t.target_id for t in scheduler.due_targets(t0)] == [STOCK_WATCH]
        state = scheduler.record_run(STOCK_WATCH, status="ok", now=t0)
        assert state.last_run_at == t0
        assert scheduler.state(STOCK_WATCH) == state
        assert scheduler.due_targets(t0) == ()

    def test_state_is_per_instance(self, skills: SkillRegistry) -> None:
        t0 = at(2026, 9, 27, 8, 0)
        first = Scheduler(None, skills=skills)
        first.record_run(STOCK_WATCH, status="ok", now=t0)
        assert Scheduler(None, skills=skills).state(STOCK_WATCH).last_status is None


# ───────────────────────── 目标模型形态 ─────────────────────────


class TestTargetShape:
    def test_skill_target_must_be_interval(self) -> None:
        with pytest.raises(ValidationError, match="interval"):
            ScheduleTarget(target_id=STOCK_WATCH, kind="skill",
                           origin="skill-frequency",
                           schedule=WorkflowSchedule(mode="cron", cron="0 8 * * *"))

    def test_origin_must_match_kind(self) -> None:
        with pytest.raises(ValidationError, match="origin"):
            ScheduleTarget(target_id="wf_x_v1.0", kind="workflow",
                           origin="skill-frequency",
                           schedule=WorkflowSchedule(mode="manual"))
