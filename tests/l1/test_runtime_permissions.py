"""T-L1-009.2 测试：批准账本挂载与权限供给接线（01 §10 + 03 §3.2 / §6）。

GWT 对照（任务文件 6 条）：
- GWT-1 批准即生效（端到端：批准后定时执行放行）
- GWT-2 空账本仍 fail-closed（不自动放行）
- GWT-3 消费接缝不变（`PermissionSource` 协议与 `Scheduler` 签名零变更）
- GWT-4 复合 Skill 独立批准（成员批过 ≠ 复合体被批准）
- GWT-5 MCP 派生 Skill 委托 MCP 账本（同一事实不存两份）
- GWT-6 回归（由既有 L1 用例承载）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.registry_types import SemVer
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, SkillPermissionSource, build_l1_runtime
from st_agent.l1.scheduler import PermissionSource, ScheduleTarget
from st_agent.l1.scheduler.scheduler import skill_of
from st_agent.l1.skills.ids import base_of
from st_agent.l1.workflow.composite import composite_skill_id, save_as_composite_skill
from st_agent.l1.workflow.models import WorkflowDAG, WorkflowNode, WorkflowSchedule

PASS = "correct horse battery staple"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone(timedelta(hours=8)))

DECLARER = "sk_decl_probe"
DECLARER_ID = "sk_decl_probe_v1.0"
DECLARER_PERM = "local_read:<data/perm/**>"

MEMBER = "sk_perm_member"
MEMBER_ID = "sk_perm_member_v1.0"
FLOW = "wf_perm_flow_v1.0"

FREQ = dict(name="frequency_minutes", type="integer", default=30,
            min_value=5, max_value=1440, description="执行频率分钟数")


class _FakeMarketQuery:
    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        return ResultEnvelope.empty("测试数据面为空")


def _register_declarer(rt: L1Runtime, *, base: str = DECLARER, permission: str = DECLARER_PERM) -> str:
    """注册一个**声明了权限**且带频率参数的 Skill（可作调度目标）。"""
    rt.skills.register(
        base, version="1.0", name="声明权限的探查",
        description="声明了一条权限的探查 Skill",
        parameters=(FREQ,), permissions=(permission,), offline_level="full",
    )
    skill_id = f"{base}_v1.0"
    rt.runner.register_executor(skill_id, lambda ctx, params: ResultEnvelope.ok({"ran": True}))
    return skill_id


def _source(rt: L1Runtime) -> SkillPermissionSource:
    return SkillPermissionSource(
        skills=rt.skill_permissions, mcp=rt.mcp_permissions,
        mcp_servers=rt.mcp_servers, mcp_mapper=rt.mcp_mapper,
    )


def _workflow_target(flow_id: str) -> ScheduleTarget:
    return ScheduleTarget(
        target_id=flow_id, kind="workflow", origin="workflow-schedule",
        schedule=WorkflowSchedule(mode="interval", interval_minutes=60),
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def runtime(store: Store) -> L1Runtime:
    return build_l1_runtime(store, market_query=_FakeMarketQuery())


class TestGwt2EmptyLedgerIsFailClosed:
    def test_declared_permission_is_not_auto_granted(self, runtime: L1Runtime):
        _register_declarer(runtime)

        run = runtime.scheduler.trigger(DECLARER_ID, now=NOW)

        assert run.status == "failed"
        assert run.envelope is not None
        assert "缺少已批准权限" in (run.envelope.reason or "")

    def test_composite_of_declaring_member_is_also_blocked(self, runtime: L1Runtime):
        """官方/自建 Skill 一律不自动放行——空账本即空集。"""
        _register_declarer(runtime, base=MEMBER, permission=DECLARER_PERM)

        assert _source(runtime).approved_for(_workflow_target(FLOW)) == ()


class TestGwt1ApprovalTakesEffect:
    def test_approved_skill_runs_under_the_scheduler(self, runtime: L1Runtime):
        _register_declarer(runtime)
        runtime.skill_permissions.declare(DECLARER, (DECLARER_PERM,))
        runtime.skill_permissions.approve(DECLARER, DECLARER_PERM)

        run = runtime.scheduler.trigger(DECLARER_ID, now=NOW)

        assert run.status == "ok"
        assert run.envelope is not None and run.envelope.data == {"ran": True}

    def test_approval_survives_a_rebuilt_runtime(self, store: Store, runtime: L1Runtime):
        _register_declarer(runtime)
        runtime.skill_permissions.declare(DECLARER, (DECLARER_PERM,))
        runtime.skill_permissions.approve(DECLARER, DECLARER_PERM)

        again = build_l1_runtime(store, market_query=_FakeMarketQuery())

        assert again.skill_permissions.approved_permissions(DECLARER) == (DECLARER_PERM,)
        # 新运行时的 runner 没有该 Skill 的执行器，但**权限门已过**——
        # 失败原因不是「缺少已批准权限」，说明批准确实跨装配存活。
        run = again.scheduler.trigger(DECLARER_ID, now=NOW)
        assert "缺少已批准权限" not in (run.envelope.reason or "")


class TestGwt3SeamUnchanged:
    def test_adapter_satisfies_the_permission_source_protocol(self, runtime: L1Runtime):
        assert isinstance(_source(runtime), PermissionSource)

    def test_default_source_reads_the_ledger(self, runtime: L1Runtime):
        """组合根缺省接的是账本供给——不是空集来源，也不是「声明即批准」。"""
        _register_declarer(runtime)
        runtime.skill_permissions.declare(DECLARER, (DECLARER_PERM,))
        runtime.skill_permissions.approve(DECLARER, DECLARER_PERM)

        assert runtime.scheduler.trigger(DECLARER_ID, now=NOW).status == "ok"

    def test_injected_source_wins_over_the_ledger(self, store: Store):
        class _Everything:
            def approved_for(self, target) -> tuple[str, ...]:
                return (DECLARER_PERM,)

        rt = build_l1_runtime(store, market_query=_FakeMarketQuery(), permissions=_Everything())
        _register_declarer(rt)

        # 账本里毫无记录，但注入来源放了行——说明注入面仍是唯一切换点
        assert rt.scheduler.trigger(DECLARER_ID, now=NOW).status == "ok"

    def test_resolution_is_shared_with_the_execution_side(self):
        assert skill_of(_workflow_target(FLOW)) == composite_skill_id(FLOW)
        assert skill_of(ScheduleTarget(
            target_id=DECLARER_ID, kind="skill", origin="skill-frequency",
            schedule=WorkflowSchedule(mode="interval", interval_minutes=30),
        )) == DECLARER_ID


class TestGwt4CompositeIsApprovedIndependently:
    def _materialize(self, rt: L1Runtime) -> None:
        _register_declarer(rt, base=MEMBER, permission=DECLARER_PERM)
        dag = WorkflowDAG(
            flow_id=FLOW, name="权限流程", description="复合体独立批准的用例流程",
            version=SemVer(major=1, minor=0),
            nodes=(WorkflowNode(node_id="n1", skill_id=MEMBER_ID),),
            schedule=WorkflowSchedule(mode="interval", interval_minutes=60),
        )
        rt.workflows.save(dag)
        save_as_composite_skill(dag, rt.skills, name="权限流程复合", description="复合体")

    def test_member_approval_does_not_authorize_the_composite(self, runtime: L1Runtime):
        self._materialize(runtime)
        runtime.skill_permissions.declare(MEMBER, (DECLARER_PERM,))
        runtime.skill_permissions.approve(MEMBER, DECLARER_PERM)

        # 成员批过 ≠ 复合体被展示过：工作流目标读的是**复合体** base
        assert _source(runtime).approved_for(_workflow_target(FLOW)) == ()
        assert _source(runtime).approved_for(ScheduleTarget(
            target_id=MEMBER_ID, kind="skill", origin="skill-frequency",
            schedule=WorkflowSchedule(mode="interval", interval_minutes=30),
        )) == (DECLARER_PERM,)

    def test_workflow_target_blocked_until_composite_is_approved(self, runtime: L1Runtime):
        self._materialize(runtime)
        runtime.skill_permissions.declare(MEMBER, (DECLARER_PERM,))
        runtime.skill_permissions.approve(MEMBER, DECLARER_PERM)

        run = runtime.scheduler.trigger(FLOW, now=NOW)

        assert run.status == "failed"
        assert "缺少已批准权限" in (run.envelope.reason or "")

    def test_approving_the_composite_base_unblocks_the_target(self, runtime: L1Runtime):
        self._materialize(runtime)
        composite_base = base_of(composite_skill_id(FLOW))
        runtime.skill_permissions.declare(composite_base, (DECLARER_PERM,))
        runtime.skill_permissions.approve(composite_base, DECLARER_PERM)

        assert _source(runtime).approved_for(_workflow_target(FLOW)) == (DECLARER_PERM,)


class TestGwt5McpDerivedSkillDelegates:
    def _mount(self, rt: L1Runtime, *, permission: str) -> str:
        """挂一台声明了权限的 Server 并同步出派生 Skill（离线存根，不触网）。

        注意顺序：MCP 侧先「声明 → **批准**」才允许装载映射（`mapping._require_approved`，
        即 01 §10 在 MCP 通道上的落点），故批准在前。
        """
        import sys

        stub = Path(__file__).resolve().parent / "_mcp_stub_server.py"
        rt.mcp_servers.add_server(
            "perm_srv", display_name="带权限的存根",
            command=sys.executable, args=[str(stub)], permissions=(permission,),
        )
        rt.mcp_permissions.approve("perm_srv", permission)
        rt.mcp_mapper.sync_tools("perm_srv")
        return "perm_srv"

    def test_derived_skill_reads_the_mcp_book(self, runtime: L1Runtime):
        perm = "net_access:<*.example.com>"
        server = self._mount(runtime, permission=perm)
        mapping = runtime.mcp_mapper.mappings(server)[0]
        target = ScheduleTarget(
            target_id=mapping.skill_id, kind="skill", origin="skill-frequency",
            schedule=WorkflowSchedule(mode="interval", interval_minutes=30),
        )

        assert _source(runtime).approved_for(target) == (perm,)   # 委托 MCP 账本

        # 撤销批准后即失效——证明读的是 MCP 账本的**当前**状态
        runtime.mcp_permissions.reject(server, perm)
        assert _source(runtime).approved_for(target) == ()

        # 同一事实不存两份：Skill 账本对该 base 恒空
        assert runtime.skill_permissions.approved_permissions(
            base_of(mapping.skill_id)) == ()

    def test_derived_skill_declares_the_servers_permissions(self, runtime: L1Runtime):
        perm = "net_access:<*.example.com>"
        server = self._mount(runtime, permission=perm)
        mapping = runtime.mcp_mapper.mappings(server)[0]

        assert runtime.skills.get(mapping.skill_id).permissions == (perm,)
