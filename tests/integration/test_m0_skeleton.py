"""T-INT-001 · M0 集成关卡：骨架打通冒烟（GWT-1..5）。

锚点：[00-架构总览 §5](../../../docs/技术架构-v2/00-架构总览.md) 端到端数据流 +
各层接口约定。**不重复**单任务已覆盖的单元行为——只测装配关系与跨层数据流。

两处口径按**已交付实现**改判（原措辞与实现相抵，见任务 `## 假设与前提` A2/A6）：

- **GWT-5**：``execution_log``（SkillRun / 出网审计 / 越界留痕的落点）**永不进
  归档**（02 §8.1 分区表），故「此前的留痕可查」不成立——改判为「``config`` 面
  用户数据可查 + 留痕**不随备份走**是设计而非静默丢失」。
- **GWT-2 的「网关审计有记录」**：官方执行器只经**注入**的取数面读本地缓存、
  不出网（02 §6 唯一出口 = 出网才记），故官方那一次**必须零审计**；审计面在同一
  装配上由**声明 + 批准 ``net_access``** 的样例 Skill 经 ``ctx.gateway`` 兑现。

用例全离线（``sender`` 注入），无 ``live`` 标记。
"""

from __future__ import annotations

import re
from pathlib import Path

from rig import PASS, Rig, assemble
from st_agent.contracts.registry_types import SemVer
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.backup import (
    WIPE_CONFIRM_TOKENS,
    create_backup,
    restore_backup,
    wipe_all,
)
from st_agent.l1.runner.models import RUN_PREFIX, SkillRun
from st_agent.l1.skills.ids import base_of
from st_agent.l1.skills.official.install import uncovered_bases
from st_agent.l1.workflow.models import ParamBinding, WorkflowDAG, WorkflowNode

INITIATOR = "m0-gate"
PURPOSE = "M0 集成关卡冒烟"

ST_SYNC = "sk_st_list_sync_v1.0"
RISK_SCAN = "sk_delisting_risk_scan_v1.0"
AGGREGATE = "sk_data_aggregate_v1.0"
FLOW_ID = "wf_m0_gate_v1.0"

OFFICIAL_BASE_COUNT = 12

EGRESS_BASE = "sk_gate_egress"
EGRESS_ID = "sk_gate_egress_v1.0"
EGRESS_PERM = "net_access:<*.declared.example>"
IN_SCOPE_HOST = "data.declared.example"

PROBE_BASE = "sk_gate_probe"
PROBE_ID = "sk_gate_probe_v1.0"
PROBE_PERMS = ("net_access:<*.declared.example>", "local_read:<data/declared/**>")
OUT_OF_SCOPE_HOST = "evil.example.com"
OUT_OF_SCOPE_PATH = "data/outside/secret.txt"

SKILL_RUN_RE = re.compile(r"snap_[0-9a-f]{20}\Z")


# ───────────────────────── 通用读取件 ─────────────────────────


def _skill_runs(rig: Rig) -> tuple[SkillRun, ...]:
    """全部 SkillRun 留痕（按落盘序；执行日志是 ``execution_log`` 分区）。"""
    store = rig.runtime.store
    return tuple(
        SkillRun.model_validate_json(
            store.get("execution_log", name).decode("utf-8"))
        for name in store.list_files("execution_log")
        if name.startswith(RUN_PREFIX) and name.endswith(".json")
    )


def _skill_run(rig: Rig, skill_run_id: str) -> SkillRun:
    """按 id 取一条 SkillRun（不存在即用例失败，不静默）。"""
    found = [r for r in _skill_runs(rig) if r.skill_run_id == skill_run_id]
    assert len(found) == 1, f"期望恰一条 {skill_run_id!r}，实得 {len(found)} 条"
    return found[0]


# ═════════════════════ GWT-1 · 装配即用 ═════════════════════


class TestGwt1AssemblyIsUsable:
    """Given 全新空目录 + 口令，When 经组合根装配，Then 装配成功且官方 Pack 可列出。"""

    def test_fresh_root_assembles_with_every_l0_face(self, bare_rig: Rig):
        rt = bare_rig.runtime
        # L0 面（T-L1-006.1）：四条出口都在，且都挂在同一个 Store 上
        assert rt.store is not None
        assert rt.endpoints.list_endpoints() == ()
        assert rt.vault.list_credentials() == ()
        assert rt.gateway.online is True
        assert rt.llm is not None
        # L1 能力面（T-L1-006.2）：注册表 / 沙箱 / 流水线 / 复用 / 工作流库
        assert all(f is not None for f in (
            rt.skills, rt.sandbox, rt.runner, rt.outputs, rt.workflows,
            rt.scheduler, rt.mcp_servers, rt.skill_permissions,
        ))

    def test_official_pack_is_fully_listed(self, bare_rig: Rig):
        descriptors = bare_rig.runtime.skills.list_all()
        assert len(descriptors) == OFFICIAL_BASE_COUNT
        assert len({base_of(d.skill_id) for d in descriptors}) == OFFICIAL_BASE_COUNT
        # 播种完整性：12 个 base 全部有执行器（无「列得出但跑不了」的悬空项）
        assert uncovered_bases() == ()

    def test_sandbox_carries_endpoints_and_provider_hosts(self, bare_rig: Rig):
        """沙箱缺任一面即 LLM 出口 fail-closed——装配必须把两面都接上。"""
        rt = bare_rig.runtime
        rt.endpoints.register("local-main", kind="local", provider="local-llama",
                              capability={"max_context_tokens": 4096,
                                          "supports_structured_output": False})
        rt.provider_hosts.register("local-llama", IN_SCOPE_HOST)
        desc = rt.skills.get(AGGREGATE)
        session = rt.sandbox.session(desc, desc.permissions, trace_id="tr_m0_gwt1")
        assert session.resolve_llm_host("local-main") == IN_SCOPE_HOST
        # 未登记映射的提供方 → 主机无法解析 → fail-closed（不臆测）
        assert session.resolve_llm_host("missing-endpoint") is None


# ═════════════════════ GWT-2 · 一次执行的完整留痕 ═════════════════════


class TestGwt2FullTrace:
    """Given 已装配运行时 + 官方 Skill（离线数据），When 执行，Then 留痕齐全。"""

    def test_official_run_over_real_cache_persists_everything(self, seeded_rig: Rig):
        rt = seeded_rig.runtime
        out = rt.runner.run(
            RISK_SCAN, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=rt.skills.get(RISK_SCAN).permissions,
        )

        # 信封：ok + 时间锚点 + 契约形态的快照证据引用（01 §5 / §8）
        assert out.envelope.status == "ok"
        assert out.envelope.as_of is not None and out.envelope.as_of.tzinfo is not None
        kinds = [ref.kind for ref in out.envelope.evidence_refs]
        assert kinds == ["dataset_snapshot_id"]
        assert SKILL_RUN_RE.match(out.envelope.evidence_refs[0].ref), (
            f"快照引用不合 01 §1 形态：{out.envelope.evidence_refs[0].ref!r}")

        # SkillRun 落盘可回放：输入快照 / 输出 / 耗时 / 依赖链
        record = _skill_run(seeded_rig, out.skill_run_id)
        assert record.skill_id == RISK_SCAN
        assert record.envelope.status == "ok"
        assert record.duration_ms >= 0
        assert record.started_at.tzinfo is not None
        assert record.upstream == (ST_SYNC,), "依赖链须记到实际执行的上游"
        assert set(record.params) == {"threshold"}

        # 依赖链上的**上游**那一次也各自留痕（拓扑序内每跳都有一条）
        upstream = [r for r in _skill_runs(seeded_rig) if r.skill_id == ST_SYNC]
        assert len(upstream) == 1

        # Trace 含对应步骤（本次 + 上游各一步，ref 指向各自的 skill_run_id）
        refs = [step.ref for step in out.trace.steps]
        assert refs[-1] == out.skill_run_id
        assert all(step.step_type == "skill_run" for step in out.trace.steps)
        assert upstream[0].skill_run_id in refs

        # 输出登记（复用面）
        assert rt.outputs.is_registered(out.skill_run_id)

    def test_cache_only_run_leaves_no_egress_audit(self, seeded_rig: Rig):
        """读本地缓存**不出网**（02 §6 唯一出口 = 出网才记）——显式钉住。"""
        seeded_rig.runtime.runner.run(
            ST_SYNC, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=seeded_rig.runtime.skills.get(ST_SYNC).permissions,
        )
        assert seeded_rig.runtime.gateway.query() == ()
        assert seeded_rig.sender.calls == []

    def test_declared_egress_run_is_audited(self, seeded_rig: Rig):
        """审计面由**声明 + 批准 net_access** 的 Skill 经 ctx.gateway 兑现。"""
        rt = seeded_rig.runtime
        rt.skills.register(
            EGRESS_BASE, version="1.0", name="声明出网的样例",
            description="声明一条出网权限并按声明目标出网的样例",
            permissions=(EGRESS_PERM,), offline_level="none",
        )

        def _egress(ctx, params):
            return ctx.gateway.execute(
                "data_fetch", IN_SCOPE_HOST,
                initiator=EGRESS_ID, purpose="集成关卡出网冒烟",
            )

        rt.runner.register_executor(EGRESS_ID, _egress)
        out = rt.runner.run(
            EGRESS_ID, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=(EGRESS_PERM,),
        )

        assert out.envelope.status == "ok"
        assert seeded_rig.sender.calls == [("data_fetch", IN_SCOPE_HOST, 60_000)]
        events = rt.gateway.query(kind="data_fetch")
        assert len(events) == 1
        assert events[0].initiator == EGRESS_ID
        assert events[0].status == "ok"
        assert events[0].target_host == IN_SCOPE_HOST

        # 留痕与审计并存于同一运行时的 execution_log 分区
        names = rt.store.list_files("execution_log")
        assert any(n.startswith(RUN_PREFIX) for n in names)
        assert any(n.startswith("net/") for n in names)


# ═════════════════════ GWT-3 · 失败路径不编造 ═════════════════════


class TestGwt3FailureIsExplicit:
    """Given 数据源不可用 / 上游失败，Then 显式化，不得用错误数据继续。"""

    def test_missing_data_surface_is_unavailable_with_last_update(self, bare_rig: Rig):
        out = bare_rig.runtime.runner.run(
            ST_SYNC, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=bare_rig.runtime.skills.get(ST_SYNC).permissions,
        )
        assert out.envelope.status == "unavailable"
        assert out.envelope.last_updated_at is not None
        assert out.envelope.last_updated_at.tzinfo is not None
        assert out.envelope.as_of is not None
        # 失败也留痕（不静默）
        assert _skill_run(bare_rig, out.skill_run_id).envelope.status == "unavailable"

    def test_upstream_failure_blocks_downstream(self, bare_rig: Rig):
        out = bare_rig.runtime.runner.run(
            RISK_SCAN, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=bare_rig.runtime.skills.get(RISK_SCAN).permissions,
        )
        assert out.envelope.status == "dependency_failed"
        assert ST_SYNC in (out.envelope.reason or "")
        assert out.envelope.data is None, "下游不得携带任何载荷（不用错误数据继续）"

        # 下游与上游**各自**留痕，上游是 unavailable 而非被跳过
        record = _skill_run(bare_rig, out.skill_run_id)
        assert record.upstream == (ST_SYNC,)
        assert record.envelope.status == "dependency_failed"
        upstream = [r for r in _skill_runs(bare_rig) if r.skill_id == ST_SYNC]
        assert len(upstream) == 1 and upstream[0].envelope.status == "unavailable"


# ═════════════════════ GWT-4 · 越界拦截端到端 ═════════════════════


def _register_probe(rig: Rig, seen: list, touched: list) -> None:
    """注册一个「声明内已批准、目标越界」的探查 Skill（官方 Pack 不声明权限）。"""
    rig.runtime.skills.register(
        PROBE_BASE, version="1.0", name="越界探查",
        description="断言沙箱三类出口边界的探查样例",
        permissions=PROBE_PERMS, offline_level="none",
    )

    def _probe(ctx, params):
        # 取 verdict 是为了断言 01 §11 的 BehaviorViolation 事件（拦截路径的
        # 公开观测面只有 GuardVerdict；信封里只剩文案）。
        seen.append(("net_verdict", ctx.sandbox.check_net(OUT_OF_SCOPE_HOST)))
        seen.append(("net_call", ctx.gateway.execute(
            "data_fetch", OUT_OF_SCOPE_HOST,
            initiator=PROBE_ID, purpose="越界探查")))
        seen.append(("local_verdict", ctx.sandbox.check_file(OUT_OF_SCOPE_PATH)))
        seen.append(("local_call", ctx.sandbox.read_file(
            OUT_OF_SCOPE_PATH, lambda p: touched.append(p) or "不应被读到")))
        return ResultEnvelope.validation_failed("越界已拦截（见沙箱留痕与事件）")

    rig.runtime.runner.register_executor(PROBE_ID, _probe)


class TestGwt4SandboxBoundary:
    """Given 已装配 runner + 沙箱，When 越界出网 / 越界读文件，Then 拦截 + 留痕 + 底层未触碰。"""

    def test_out_of_scope_targets_are_blocked_and_base_untouched(self, seeded_rig: Rig):
        seen: list = []
        touched: list = []
        _register_probe(seeded_rig, seen, touched)

        out = seeded_rig.runtime.runner.run(
            PROBE_ID, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=PROBE_PERMS,
        )
        assert out.envelope.status == "validation_failed"

        by_key = dict(seen)
        assert set(by_key) == {"net_verdict", "net_call", "local_verdict", "local_call"}

        # 两个 verdict：拒绝 + BehaviorViolation 事件（01 §11，携关联 trace）
        for key in ("net_verdict", "local_verdict"):
            verdict = by_key[key]
            assert verdict.allowed is False
            assert verdict.event is not None
            assert verdict.event.event == "BehaviorViolation"
            assert verdict.event.trace_id == out.trace.trace_id.value
            # A7 事件最小载荷：只含 skill_id + 越界类别，**不含**被访问资源串
            assert set(verdict.event.payload) == {"skill_id", "violation"}
            assert verdict.event.payload["skill_id"] == PROBE_ID

        # 两个实操作：拦截成信封（不抛裸异常）——信封的 reason 面向人，含目标串；
        # 事件载荷才是最小化的那一面（见上）。
        for key in ("net_call", "local_call"):
            env = by_key[key]
            assert env.status == "validation_failed"
            assert "越界留痕" in (env.reason or "")

        # 越界留痕落盘（execution_log/sandbox-violation/**）
        kinds = {v.kind for v in seeded_rig.runtime.sandbox.violations()}
        assert kinds == {"net_access", "local_read"}

        # 底层未被触碰：发包实现零调用、读取器零调用、网关零审计
        assert seeded_rig.sender.calls == []
        assert touched == []
        assert seeded_rig.runtime.gateway.query() == ()


# ═════════════════════ GWT-5 · 数据闭环 ═════════════════════


class TestGwt5BackupLoop:
    """Given 已产生执行留痕与用户配置，When 备份→清空→恢复→重装配，Then 运行时可用。"""

    def test_backup_wipe_restore_reassemble(self, seeded_rig: Rig, tmp_path: Path):
        rt = seeded_rig.runtime
        root = seeded_rig.root

        # ── 1) 造出用户可见的 config 面状态 + 一条执行留痕 ──
        rt.skills.set_parameters(AGGREGATE, {"format": "trend"})
        dag = WorkflowDAG(
            flow_id=FLOW_ID,
            name="M0 关卡装配自检流",
            description="集成关卡用的工作流定义",
            version=SemVer.parse("1.0"),
            nodes=(WorkflowNode(
                node_id="agg", skill_id=AGGREGATE,
                params={"format": ParamBinding(kind="literal", value="简报")}),),
        )
        rt.workflows.save(dag)
        rt.skill_permissions.declare(PROBE_BASE, PROBE_PERMS)
        rt.skill_permissions.approve(PROBE_BASE, PROBE_PERMS[0])
        before = rt.runner.run(
            ST_SYNC, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=rt.skills.get(ST_SYNC).permissions,
        )
        assert before.envelope.status == "ok"

        # ── 2) 备份（含可选分区 data_cache，随归档一并迁移） ──
        archive = create_backup(
            rt.store, PASS, tmp_path / "m0-gate-backup.json", include_data_cache=True)

        # ── 3) 完全清空（三枚确认令牌按序集齐） ──
        wipe_all(root, WIPE_CONFIRM_TOKENS)
        assert not (root / "keyfile.json").exists()
        assert archive.exists(), "归档在存储根之外，清空不触碰"

        # ── 4) 恢复 + 重新装配（复核：装配入口可重复使用） ──
        report = restore_backup(archive, PASS, root)
        assert report.file_count > 0
        again = assemble(root)
        rt2 = again.runtime

        # ── 5) 断言：运行时可用 + config 面用户数据可查 ──
        assert len(rt2.skills.list_all()) == OFFICIAL_BASE_COUNT
        formats = {p.default for p in rt2.skills.get(AGGREGATE).parameters
                   if p.name == "format"}
        assert formats == {"trend"}
        assert [d.flow_id for d in rt2.workflows.list_all()] == [FLOW_ID]
        assert PROBE_PERMS[0] in rt2.skill_permissions.approved_permissions(PROBE_BASE)

        # ── 6) 留痕**不随备份走**：是设计（execution_log 永不进归档），非静默丢失 ──
        assert "execution_log" not in report.restored_partitions
        assert rt2.store.list_files("execution_log") == ()
        # …且恢复后新执行可正常留痕（流水线未被留痕缺口破坏）
        fresh = rt2.runner.run(
            ST_SYNC, initiator=INITIATOR, purpose=PURPOSE,
            approved_permissions=rt2.skills.get(ST_SYNC).permissions,
        )
        assert fresh.envelope.status == "ok"
        assert _skill_run(again, fresh.skill_run_id).envelope.status == "ok"
