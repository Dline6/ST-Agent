"""T-L1-006.4 测试：组合根的调度面装配与在线翻转补跑（03 §6 + 02 §7 + 01 §10）。

GWT 对照（任务文件 3 条）：
- GWT-1 调度装配可用（注入 Store / 工作流库 / 注册表 / 沙箱 / runner / 网关 / 权限来源）
- GWT-2 在线翻转触发补跑（离 → 在线这一跳；幂等；仍离线不触发）
- GWT-3 权限来源不臆测（缺省空集 → 声明权限的定时执行被拦，不自动放行）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, build_l1_runtime

PASS = "correct horse battery staple"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone(timedelta(hours=8)))
PROBE = "sk_net_probe_v1.0"
STOCK_WATCH = "sk_stock_watch_v1.0"


class _FakeMarketQuery:
    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        return ResultEnvelope.empty("测试数据面为空")


def _register_probe(rt: L1Runtime) -> None:
    """注册一个「只能联网跑」的探查 Skill（`offline_level=none` + 频率参数）。"""
    rt.skills.register(
        "sk_net_probe", version="1.0", name="联网探查",
        description="需要联网才能执行的探查",
        parameters=(dict(name="frequency_minutes", type="integer", default=5,
                         min_value=5, max_value=1440, description="执行频率分钟数"),),
        offline_level="none",
    )
    rt.runner.register_executor(PROBE, lambda ctx, params: ResultEnvelope.ok({"probed": True}))


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def runtime(store: Store) -> L1Runtime:
    rt = build_l1_runtime(store, market_query=_FakeMarketQuery())
    _register_probe(rt)
    return rt


@pytest.fixture()
def offline(runtime: L1Runtime) -> L1Runtime:
    runtime.set_online(False)
    return runtime


class TestGwt1SchedulerWired:
    def test_scheduler_is_ready_with_both_target_sources(self, runtime: L1Runtime):
        by_id = {t.target_id: t for t in runtime.scheduler.targets()}

        assert by_id[STOCK_WATCH].origin == "skill-frequency"     # Skill 频率参数
        assert by_id[STOCK_WATCH].schedule.mode == "interval"
        assert by_id[PROBE].schedule.interval_minutes == 5         # 描述体当前默认值

    def test_trigger_runs_through_the_assembled_runner(self, runtime: L1Runtime):
        run = runtime.scheduler.trigger(PROBE, now=NOW)

        assert run.status == "ok"
        assert run.envelope is not None and run.envelope.data == {"probed": True}
        assert runtime.outputs.is_registered(run.skill_run_id)

    def test_sandbox_disabled_skill_leaves_the_target_set(self, runtime: L1Runtime):
        runtime.sandbox.disable(PROBE)

        assert PROBE not in {t.target_id for t in runtime.scheduler.targets()}


class TestGwt2OnlineFlipCatchUp:
    def test_offline_due_target_is_deferred(self, offline: L1Runtime, store: Store):
        # 只留 probe 一个到期目标，使「零 SkillRun」断言精确（官方 Skill 会照跑）
        offline.sandbox.disable(STOCK_WATCH)

        offline.scheduler.tick(NOW)

        state = offline.scheduler.state(PROBE)
        assert state.pending
        assert state.last_status == "deferred"        # 记「待补」，可查
        assert state.last_success_at is None
        assert [n for n in store.list_files("execution_log")
                if n.startswith("skill-run/")] == []  # 且不产生失败 SkillRun

    def test_coming_back_online_runs_catch_up_once(self, offline: L1Runtime):
        offline.scheduler.tick(NOW)

        runs = offline.set_online(True, now=NOW)

        assert [r.status for r in runs] == ["ok"]
        assert offline.scheduler.state(PROBE).pending == ()

    def test_repeated_online_flip_does_not_double_run(self, offline: L1Runtime):
        offline.scheduler.tick(NOW)
        offline.set_online(True, now=NOW)

        assert offline.set_online(True, now=NOW) == ()

    def test_flipping_to_offline_does_not_trigger_catch_up(self, runtime: L1Runtime):
        assert runtime.set_online(False, now=NOW) == ()

    def test_gateway_is_the_single_source_of_online_state(self, offline: L1Runtime):
        assert offline.gateway.online is False

        offline.set_online(True, now=NOW)

        assert offline.gateway.online is True


class TestGwt3PermissionsNotAssumed:
    DECLARER = "sk_decl_gate_v1.0"
    DECLARER_BASE = "sk_decl_gate"
    DECLARER_PERM = "net_access:<*.declared.example>"

    def _declare(self, rt: L1Runtime) -> None:
        """自建一个**声明了权限**的定时目标。

        官方 Pack 自 `T-L1-009.3` 起 `permissions=()`（取数全经注入面），
        故本组用例不再能借它验证权限门。
        """
        rt.skills.register(
            self.DECLARER_BASE, version="1.0", name="声明权限的定时目标",
            description="声明了一条权限的定时目标",
            parameters=(dict(name="frequency_minutes", type="integer", default=5,
                             min_value=5, max_value=1440, description="执行频率分钟数"),),
            permissions=(self.DECLARER_PERM,), offline_level="none",
        )
        rt.runner.register_executor(self.DECLARER, lambda ctx, params: ResultEnvelope.ok({}))

    def test_declared_permissions_are_not_auto_granted(self, runtime: L1Runtime):
        """未提供权限来源 → 账本为空 → 声明了权限的定时执行被拦（01 §10）。"""
        self._declare(runtime)

        run = runtime.scheduler.trigger(self.DECLARER, now=NOW)

        assert run.status == "failed"
        assert run.envelope is not None
        assert run.envelope.status == "validation_failed"

    def test_injected_permission_source_unlocks_the_run(self, store: Store):
        class _Approving:
            """常量权限来源替身：把该目标声明的权限视为已批准。"""

            def approved_for(self, target) -> tuple[str, ...]:
                return (TestGwt3PermissionsNotAssumed.DECLARER_PERM,)

        rt = build_l1_runtime(store, market_query=_FakeMarketQuery(),
                              permissions=_Approving())
        self._declare(rt)

        run = rt.scheduler.trigger(self.DECLARER, now=NOW)

        assert run.status == "ok"
