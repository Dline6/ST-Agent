"""T-L1-003.3 测试：03 §4 工作流试跑（调试协议）。

GWT 对照（任务文件 5 条）：
- GWT-1 拓扑执行与失败短路：逐节点产出「输入快照 + 输出信封 + 耗时」；
  非 ok/empty 节点的**下游**不执行并标 ``dependency_failed``（旁支照常执行）
- GWT-2 单步：``step`` 每调用只推进一步，步间可读该节点输入/输出快照
- GWT-3 输入注入：注入值**覆盖** ``literal`` 绑定且快照标注来源为注入；
  ``ref`` 绑定与未声明参数名显式拒绝（不静默丢弃）
- GWT-4 暂停 / 继续 / 中止：暂停不推进、继续接着跑、中止后后续节点一律不执行
- GWT-5 历史对比：两次试跑按 ``node_id`` 对齐，逐节点列出输出差异

另覆盖落地口径面（03 §4 / [D-020]）：试跑门禁粒度、输出复用面零登记、
记录落 ``workflow-trial/`` 与 ``skill-run/`` 前缀可分辨、共用一条推理链、``trial_id`` 形态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.identifiers import TrialId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.reuse import OutputRegistry
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.workflow import (
    PARTITION,
    TRIAL_PREFIX,
    ParamBinding,
    TrialHistory,
    TrialNotFoundError,
    TrialRunner,
    TrialStateError,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowNode,
    WorkflowValidationError,
)
from st_agent.contracts.registry_types import SemVer

PASS = "correct horse battery staple"
FLOW = "wf_demo_v1.0"
FLOW_FAIL = "wf_fail_v1.0"

SK_A, SK_B, SK_C = "sk_a_v1.0", "sk_b_v1.0", "sk_c_v1.0"
SK_FAIL, SK_ISO, SK_MISSING = "sk_fail_v1.0", "sk_iso_v1.0", "sk_missing_v1.0"


# ───────────────────────── 夹具与构件 ─────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    reg.register("sk_a", name="结果产出", description="产出待传递的字段",
                 input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                 output_schema={"type": "object", "properties": {"y": {"type": "string"}}},
                 parameters=(dict(name="p_a", type="string", default="d_a",
                                  description="产出口径"),
                             dict(name="extra", type="number", default=7,
                                  description="补充数值")))
    reg.register("sk_b", name="二次加工", description="把上游字段加工为自有字段",
                 input_schema={"type": "object", "required": ["y"],
                               "properties": {"y": {"type": "string"}}},
                 output_schema={"type": "object", "properties": {"z": {"type": "string"}}},
                 parameters=(dict(name="y", type="string", default="",
                                  description="上游产出的字段"),))
    reg.register("sk_c", name="结果汇总", description="把上游字段汇总为结论字段",
                 input_schema={"type": "object", "required": ["z"],
                               "properties": {"z": {"type": "string"}}},
                 output_schema={"type": "object", "properties": {"w": {"type": "string"}}},
                 parameters=(dict(name="z", type="string", default="",
                                  description="上游产出的字段"),))
    reg.register("sk_fail", name="故障节点", description="固定返回失败的节点",
                 input_schema={"type": "object", "properties": {}},
                 output_schema={"type": "object", "properties": {"y": {"type": "string"}}})
    reg.register("sk_iso", name="旁支节点", description="与主链无关的旁支节点",
                 input_schema={"type": "object", "properties": {}},
                 output_schema={"type": "object", "properties": {"iso": {"type": "string"}}},
                 parameters=(dict(name="p", type="string", default="iso",
                                  description="旁支口径"),))
    return reg


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry) -> SkillRunner:
    return SkillRunner(store, registry)


@pytest.fixture()
def calls() -> dict[str, int]:
    return {}


def wire_echo(runner: SkillRunner, calls: dict[str, int]) -> None:
    """注册各 Skill 的执行器（echo 语义：输出取自输入，便于断言注入是否生效）。"""

    def _count(key: str) -> None:
        calls[key] = calls.get(key, 0) + 1

    def a(ctx, params):
        _count("a")
        return ResultEnvelope.ok({"y": params["p_a"]})

    def b(ctx, params):
        _count("b")
        return ResultEnvelope.ok({"z": params["y"]})

    def c(ctx, params):
        _count("c")
        return ResultEnvelope.ok({"w": params["z"]})

    def fail(ctx, params):
        _count("fail")
        return ResultEnvelope.failed("扫描阶段不可用", log_ref="skill-run/failed.json")

    def iso(ctx, params):
        _count("iso")
        return ResultEnvelope.ok({"iso": params["p"]})

    runner.register_executor(SK_A, a)
    runner.register_executor(SK_B, b)
    runner.register_executor(SK_C, c)
    runner.register_executor(SK_FAIL, fail)
    runner.register_executor(SK_ISO, iso)


def lit(value: object) -> ParamBinding:
    return ParamBinding(kind="literal", value=value)


def ref(from_node: str, path: str | None = None) -> ParamBinding:
    return ParamBinding(kind="ref", from_node=from_node, path=path)


def node(nid: str, skill_id: str, **params: ParamBinding) -> WorkflowNode:
    return WorkflowNode(node_id=nid, skill_id=skill_id, params=params)


def dag_of(flow_id: str, nodes, edges=()) -> WorkflowDAG:
    return WorkflowDAG(
        flow_id=flow_id, name="演示工作流", description="面向试跑演示的工作流",
        version=SemVer.parse(flow_id.rsplit("_v", 1)[1]),
        nodes=tuple(nodes), edges=tuple(edges),
    )


def main_flow() -> WorkflowDAG:
    """a → b → c 的三节点链（b/c 的入参由上游引用喂入）。"""
    return dag_of(FLOW, [
        node("a", SK_A, p_a=lit("L1")),
        node("b", SK_B, y=ref("a", "y")),
        node("c", SK_C, z=ref("b", "z")),
    ], [WorkflowEdge(edge_id="e_ab", from_node="a", to_node="b"),
        WorkflowEdge(edge_id="e_bc", from_node="b", to_node="c")])


def fail_flow() -> WorkflowDAG:
    """f 失败 → b 短路；旁支 iso 与 f 无关，照常执行。"""
    return dag_of(FLOW_FAIL, [
        node("f", SK_FAIL),
        node("b", SK_B, y=ref("f", "y")),
        node("iso", SK_ISO, p=lit("I1")),
    ], [WorkflowEdge(edge_id="e_fb", from_node="f", to_node="b")])


# ───────────────────────── GWT-1 拓扑执行与失败短路 ─────────────────────────


class TestGwt1TopologicalAndShortCircuit:
    def test_runs_in_topological_order_with_full_trace(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        record = trial.run_to_end()
        assert [s.node_id for s in record.steps] == ["a", "b", "c"]
        assert [s.order for s in record.steps] == [0, 1, 2]
        assert all(s.skill_run_id for s in record.steps)
        assert all(isinstance(s.duration_ms, int) and s.duration_ms >= 0
                   for s in record.steps)
        assert [s.envelope.status for s in record.steps] == ["ok", "ok", "ok"]
        assert record.status == "completed"
        assert calls == {"a": 1, "b": 1, "c": 1}

    def test_step_snapshot_is_effective_input(self, store, registry, runner, calls):
        """输入快照 = 实际生效参数（引用 / 字面量 + Skill 默认值）。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        record = trial.run_to_end()
        a_step, b_step = record.steps[0], record.steps[1]
        assert a_step.params == {"p_a": "L1", "extra": 7}
        assert a_step.value_sources == {"p_a": "literal", "extra": "default"}
        assert b_step.params == {"y": "L1"}
        assert b_step.value_sources == {"y": "upstream"}

    def test_downstream_of_failed_node_is_skipped(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(fail_flow())
        record = trial.run_to_end()
        f_step = record.step_of("f")
        b_step = record.step_of("b")
        assert f_step.envelope.status == "failed"
        assert b_step.skipped is True
        assert b_step.envelope.status == "dependency_failed"
        assert "f" in (b_step.envelope.reason or "")
        assert b_step.params == {} and b_step.skill_run_id is None

    def test_independent_branch_still_runs(self, store, registry, runner, calls):
        """失败短路只作用于下游——与失败节点无关的旁支照常执行。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(fail_flow())
        record = trial.run_to_end()
        assert record.step_of("iso").envelope.status == "ok"
        assert calls == {"fail": 1, "iso": 1}          # b 从未被执行
        assert record.status == "failed"

    def test_record_lands_in_trial_prefix(self, store, registry, runner, calls):
        """试跑记录落 ``workflow-trial/``（与 ``skill-run/`` 同分区不同前缀）。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.run_to_end()
        names = store.list_files(PARTITION)
        trials = [n for n in names if n.startswith(TRIAL_PREFIX)]
        runs = [n for n in names if n.startswith("skill-run/")]
        assert len(trials) == 1 and len(runs) == 3
        assert not any(n.startswith(TRIAL_PREFIX) for n in runs)
        assert trials[0] == f"{TRIAL_PREFIX}{trial.trial_id}.json"

    def test_shared_trace_chain(self, store, registry, runner, calls):
        """整条试跑共用一条推理链，逐节点追加 ``skill_run`` 步。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        record = trial.run_to_end()
        assert len(record.trace.steps) == 3
        assert {s.step_type for s in record.trace.steps} == {"skill_run"}
        refs = [s.ref for s in record.trace.steps]
        assert refs == [s.skill_run_id for s in record.steps]

    def test_trial_id_is_contract_id(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        assert TrialId.of(trial.trial_id).value == trial.trial_id

    def test_empty_workflow_completes_on_run_to_end(self, store, registry, runner, calls):
        """零节点工作流：无步可推，``run_to_end`` 当场完成（不留「永远运行中」的记录）。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(dag_of(FLOW, []))
        assert trial.snapshots() == ()
        record = trial.run_to_end()
        assert record.status == "completed" and record.steps == ()
        with pytest.raises(TrialStateError, match="无法继续推进"):
            trial.step()


# ───────────────────────── GWT-2 单步 ─────────────────────────


class TestGwt2SingleStep:
    def test_step_advances_exactly_one_node(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        assert trial.snapshots() == ()
        first = trial.step()
        assert first.node_id == "a"
        assert len(trial.snapshots()) == 1
        assert trial.status == "running"
        second = trial.step()
        assert second.node_id == "b"
        third = trial.step()
        assert third.node_id == "c"
        assert trial.status == "completed"

    def test_inspect_snapshots_between_steps(self, store, registry, runner, calls):
        """步间可检视该节点的输入快照与输出快照。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.step()
        snap = trial.snapshot_of("a")
        assert snap.params == {"p_a": "L1", "extra": 7}
        assert snap.envelope.data == {"y": "L1"}
        assert snap.duration_ms >= 0
        assert trial.snapshot_of("b") is None          # 尚未推进

    def test_step_after_finished_rejected(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.run_to_end()
        with pytest.raises(TrialStateError, match="无法继续推进"):
            trial.step()

    def test_step_allowed_while_paused(self, store, registry, runner, calls):
        """暂停只拦「自动跑到尾」，单步仍可推进（调试语义）。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.step()
        trial.pause()
        assert trial.step().node_id == "b"
        assert trial.status == "paused"


# ───────────────────────── GWT-3 输入注入 ─────────────────────────


class TestGwt3Injection:
    def test_injection_overrides_literal_at_start(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(
            main_flow(), {"a": {"p_a": "注入值"}})
        step = trial.step()
        assert step.params["p_a"] == "注入值"
        assert step.value_sources["p_a"] == "injected"
        assert step.envelope.data == {"y": "注入值"}

    def test_injection_during_session(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        flow = dag_of(FLOW, [node("a", SK_A, p_a=lit("L1")),
                             node("b", SK_ISO, p=lit("原文"))])
        trial = TrialRunner(store, registry, runner).start(flow)
        trial.step()                                    # a 已用字面量跑过
        trial.inject("b", {"p": "进行中注入"})
        step = trial.step()
        assert step.node_id == "b"
        assert step.params["p"] == "进行中注入"
        assert step.value_sources["p"] == "injected"
        assert step.envelope.data == {"iso": "进行中注入"}

    def test_injection_on_declared_unbound_param(self, store, registry, runner, calls):
        """节点已声明但未绑定的参数同样可注入（只拒 ``ref`` 绑定与未声明名）。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(
            main_flow(), {"a": {"extra": 42}})
        step = trial.step()
        assert step.params["extra"] == 42
        assert step.value_sources["extra"] == "injected"

    def test_injection_on_ref_binding_rejected(self, store, registry, runner, calls):
        """注入打在 ``ref`` 绑定上 → 该节点 ``validation_failed``（不静默丢弃）。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(
            main_flow(), {"b": {"y": "硬塞"}})
        trial.step()                                    # a ok
        step = trial.step()                             # b
        assert step.envelope.status == "validation_failed"
        assert "不接受注入" in (step.envelope.reason or "")
        assert calls.get("b", 0) == 0                   # 未执行

    def test_injection_of_undeclared_param_rejected(self, store, registry, runner, calls):
        """注入节点未声明的参数名 → 由流水线判 ``validation_failed``。"""
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(
            main_flow(), {"a": {"nope": 1}})
        step = trial.step()
        assert step.envelope.status == "validation_failed"
        assert "未知参数" in (step.envelope.reason or "")

    def test_injection_into_executed_node_rejected(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.step()
        with pytest.raises(TrialStateError, match="已执行"):
            trial.inject("a", {"p_a": "太晚"})

    def test_injection_into_unknown_node_rejected(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        with pytest.raises(WorkflowValidationError, match="不在"):
            trial.inject("nope", {"p_a": "x"})


# ───────────────────────── GWT-4 暂停 / 继续 / 中止 ─────────────────────────


class TestGwt4PauseResumeAbort:
    def test_pause_then_resume_completes(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.step()
        trial.pause()
        assert trial.status == "paused"
        trial.pause()                                   # 同态幂等
        assert trial.status == "paused"
        trial.resume()
        assert trial.status == "running"
        record = trial.run_to_end()
        assert [s.node_id for s in record.steps] == ["a", "b", "c"]

    def test_run_to_end_while_paused_rejected(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.pause()
        with pytest.raises(TrialStateError, match="resume"):
            trial.run_to_end()

    def test_pause_from_executor_stops_at_node_boundary(self, store, registry, runner, calls):
        """执行期置暂停 → ``run_to_end`` 在节点边界停下（非线程中断）。"""
        wire_echo(runner, calls)
        holder: dict = {}

        def pausing_a(ctx, params):
            calls["a"] = calls.get("a", 0) + 1
            holder["trial"].pause()
            return ResultEnvelope.ok({"y": params["p_a"]})

        runner.register_executor(SK_A, pausing_a)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        holder["trial"] = trial
        record = trial.run_to_end()
        assert record.status == "paused"
        assert [s.node_id for s in record.steps] == ["a"]
        trial.resume()
        assert [s.node_id for s in trial.run_to_end().steps] == ["a", "b", "c"]

    def test_abort_stops_remaining_nodes(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.step()
        trial.abort()
        assert trial.status == "aborted"
        trial.abort()                                   # 同态幂等
        assert trial.status == "aborted"
        assert len(trial.snapshots()) == 1
        assert calls == {"a": 1}                        # b / c 未执行
        with pytest.raises(TrialStateError, match="无法继续推进"):
            trial.step()
        assert TrialRunner(store, registry, runner).history.get(
            trial.trial_id).status == "aborted"

    def test_abort_from_executor(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        holder: dict = {}

        def aborting_a(ctx, params):
            calls["a"] = calls.get("a", 0) + 1
            holder["trial"].abort()
            return ResultEnvelope.ok({"y": params["p_a"]})

        runner.register_executor(SK_A, aborting_a)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        holder["trial"] = trial
        record = trial.run_to_end()
        assert record.status == "aborted"
        assert [s.node_id for s in record.steps] == ["a"]
        assert calls == {"a": 1}

    def test_terminal_session_rejects_control_calls(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        trial.run_to_end()
        for call in (trial.pause, trial.resume, trial.abort):
            with pytest.raises(TrialStateError):
                call()
        with pytest.raises(TrialStateError, match="注入"):
            trial.inject("a", {"p_a": "x"})


# ───────────────────────── GWT-5 试跑历史对比 ─────────────────────────


class TestGwt5HistoryCompare:
    def test_compare_lists_changed_nodes(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial_runner = TrialRunner(store, registry, runner)
        one = trial_runner.start(main_flow(), {"a": {"p_a": "第一次"}})
        one.run_to_end()
        two = trial_runner.start(main_flow(), {"a": {"p_a": "第二次"}})
        two.run_to_end()
        diff = trial_runner.history.compare(one.trial_id, two.trial_id)
        assert diff.changed_nodes == ("a", "b", "c")    # 链式传导：a 变则全链变
        assert diff.added_nodes == () and diff.removed_nodes == ()
        assert diff.is_empty() is False

    def test_compare_identical_runs_is_empty(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial_runner = TrialRunner(store, registry, runner)
        one = trial_runner.start(main_flow(), {"a": {"p_a": "同值"}})
        one.run_to_end()
        two = trial_runner.start(main_flow(), {"a": {"p_a": "同值"}})
        two.run_to_end()
        assert one.trial_id != two.trial_id
        assert trial_runner.history.compare(one.trial_id, two.trial_id).is_empty()

    def test_compare_different_flows_nodes_added_removed(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial_runner = TrialRunner(store, registry, runner)
        long_run = trial_runner.start(main_flow())
        long_run.run_to_end()
        short = trial_runner.start(dag_of(FLOW_FAIL, [
            node("a", SK_A, p_a=lit("L1")), node("b", SK_B, y=ref("a", "y")),
        ]))
        short.run_to_end()
        diff = trial_runner.history.compare(short.trial_id, long_run.trial_id)
        assert diff.added_nodes == ("c",)               # 长跑多一个节点
        diff_back = trial_runner.history.compare(long_run.trial_id, short.trial_id)
        assert diff_back.removed_nodes == ("c",)

    def test_list_for_flow_and_get(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        trial_runner = TrialRunner(store, registry, runner)
        first = trial_runner.start(main_flow())
        first.run_to_end()
        other = trial_runner.start(fail_flow())
        other.run_to_end()
        history = trial_runner.history
        assert [r.trial_id for r in history.list_for_flow(FLOW)] == [first.trial_id]
        assert [r.trial_id for r in history.list_for_flow(FLOW_FAIL)] == [other.trial_id]
        assert len(history.list_all()) == 2
        record = history.get(first.trial_id)
        assert record.flow_id == FLOW
        assert record.status == "completed"
        assert record.steps[0].node_id == "a"

    def test_get_missing_and_malformed(self, store, registry, runner, calls):
        history = TrialHistory(store)
        with pytest.raises(TrialNotFoundError):
            history.get("trial_0123456789abcdef0123")
        with pytest.raises(WorkflowValidationError):
            history.get("not-a-trial-id")


# ───────────────────────── 落地口径：门禁 / 复用面 ─────────────────────────


class TestGateAndReuseSurface:
    def test_cycle_rejected_before_start(self, store, registry, runner, calls):
        wire_echo(runner, calls)
        cyclic = dag_of(FLOW, [
            node("a", SK_A, p_a=lit("L1")), node("b", SK_B, y=ref("a", "y")),
        ], [WorkflowEdge(edge_id="e_ab", from_node="a", to_node="b"),
            WorkflowEdge(edge_id="e_ba", from_node="b", to_node="a")])
        with pytest.raises(WorkflowValidationError, match="不可执行"):
            TrialRunner(store, registry, runner).start(cyclic)
        assert store.list_files(PARTITION) == ()

    def test_unknown_skill_is_not_gated(self, store, registry, runner, calls):
        """未注册 Skill 不拦起跑——落到节点级以失败信封装载，下游随之短路。"""
        wire_echo(runner, calls)
        flow = dag_of(FLOW, [
            node("a", SK_MISSING), node("b", SK_B, y=ref("a", "y")),
        ], [WorkflowEdge(edge_id="e_ab", from_node="a", to_node="b")])
        record = TrialRunner(store, registry, runner).start(flow).run_to_end()
        assert record.steps[0].envelope.status == "validation_failed"
        assert "未注册" in (record.steps[0].envelope.reason or "")
        assert record.steps[1].skipped is True
        assert record.status == "failed"

    def test_missing_ref_path_fails_node(self, store, registry, runner, calls):
        """引用取值路径不存在 → 该节点 ``validation_failed``（不静默置空）。"""
        wire_echo(runner, calls)
        flow = dag_of(FLOW, [
            node("a", SK_A, p_a=lit("L1")), node("b", SK_B, y=ref("a", "nope")),
        ], [WorkflowEdge(edge_id="e_ab", from_node="a", to_node="b")])
        record = TrialRunner(store, registry, runner).start(flow).run_to_end()
        b_step = record.step_of("b")
        assert b_step.envelope.status == "validation_failed"
        assert "取值路径" in (b_step.envelope.reason or "")
        assert calls.get("b", 0) == 0

    def test_trial_does_not_register_reusable_outputs(self, store, registry, runner, calls):
        """试跑不登记输出复用：同一 ``OutputRegistry`` 实例看不到任何试跑产物。"""
        wire_echo(runner, calls)
        outputs = OutputRegistry(store)
        trial = TrialRunner(store, registry, runner).start(main_flow())
        record = trial.run_to_end()
        assert record.status == "completed"
        for run_id in (s.skill_run_id for s in record.steps):
            assert outputs.is_registered(run_id) is False
        assert not [n for n in store.list_files(PARTITION)
                    if n.startswith("skill-output/")]

    def test_runner_with_outputs_refused(self, store, registry, runner, calls):
        """口径固化为构造期断言：带复用登记的 SkillRunner 不得用于试跑。"""
        with pytest.raises(WorkflowValidationError, match="复用"):
            TrialRunner(store, registry,
                        SkillRunner(store, registry, outputs=OutputRegistry(store)))
