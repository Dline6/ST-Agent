"""T-AGT-018.1 测试：并行工具调用（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

执行面一律用**真** ``SkillRunner`` + 真 ``Store`` + 真官方 Pack（只有 LLM 端与闸门是
注入的替身）——故「逐个过闸门」「留痕不因并发而放宽」是在真流水线上验的，不是替身自证。

并发性用 ``threading.Barrier`` 证明：真并行时各执行器同时到达栅栏；串行回退时首个
调用等不到同伴、栅栏破裂，执行器据此记录是否有重叠。
"""

from __future__ import annotations

import threading

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import (
    GateDecision,
    TurnResult,
    run_agent_loop,
)

EP = "ep_parallel"
TASK = "并行查证三个板块"

SK_A = "sk_data_aggregate"
SK_B = "sk_sector_heatmap"
SK_C = "sk_agent_probe"


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    reg.register(SK_C, name="并行探针", description="并行调用用例的最小能力")
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.ok({"a": True}))
    out.register_executor(f"{SK_B}_v1.0", lambda ctx, params: ResultEnvelope.ok({"b": True}))
    out.register_executor(f"{SK_C}_v1.0", lambda ctx, params: ResultEnvelope.ok({"c": True}))
    return out


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


class FakeProtocol:
    def __init__(self, script=()) -> None:
        self._script = list(script)
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        self.contexts.append(context)
        return self._script.pop(0) if self._script else TurnResult(text="（脚本已尽）")


class FakeGate:
    def __init__(self, verdicts: dict[str, str] | None = None) -> None:
        self._verdicts = dict(verdicts or {})
        self.seen: list[str] = []

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        self.seen.append(tool_name)
        return GateDecision(verdict=self._verdicts.get(tool_name, "autonomous-ok"))


def batch(*names: str) -> TurnResult:
    return TurnResult(tool_calls=tuple(
        ToolCall(call_id=f"call_{index}", name=name, arguments={})
        for index, name in enumerate(names, start=1)
    ))


def finish(text: str = "证据已收集完毕") -> TurnResult:
    return TurnResult(text=text)


def drive(runner, tools, protocol, *, gate=None, **kwargs):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=gate if gate is not None else FakeGate(), **kwargs,
    )


def _barrier_executor(barrier: threading.Barrier, overlap: dict[str, bool], name: str):
    def execute(ctx, params):
        try:
            barrier.wait(timeout=1.0)
            overlap[name] = True
        except threading.BrokenBarrierError:
            overlap[name] = False
        return ResultEnvelope.ok({"tool": name})

    return execute


def test_gwt1_batch_runs_in_parallel_and_keeps_call_order(runner, tools, store):
    overlap: dict[str, bool] = {}
    barrier = threading.Barrier(3)
    for skill_id, name in (
        (f"{SK_A}_v1.0", SK_A), (f"{SK_B}_v1.0", SK_B), (f"{SK_C}_v1.0", SK_C),
    ):
        runner.register_executor(skill_id, _barrier_executor(barrier, overlap, name))

    outcome = drive(
        runner, tools,
        FakeProtocol([batch(SK_A, SK_B, SK_C), finish()]),
        parallel_safe=True,
    )

    assert outcome.termination == "finished"
    assert overlap == {SK_A: True, SK_B: True, SK_C: True}     # 真并发
    assert [step.tool_name for step in outcome.steps] == [SK_A, SK_B, SK_C]
    assert [step.step_type for step in outcome.trace.steps] == ["skill_run"] * 3
    assert [step.ref for step in outcome.trace.steps] == [
        step.skill_run_id for step in outcome.steps
    ]
    assert outcome.parallel_used is True
    assert "并行执行" in outcome.parallel_note
    assert outcome.executed_steps == 3
    assert outcome.llm_calls == 2                              # 批一次 + 收尾一次
    assert all(step.envelope.status == "ok" for step in outcome.steps)
    # 每条 skill_run 都在真 Store 里
    recorded = set(store.list_files("execution_log"))
    assert all(step.log_ref in recorded for step in outcome.steps)


def test_gwt1_gate_is_asked_for_every_call_before_any_execution(runner, tools):
    events: list[str] = []
    gate = FakeGate()

    def gate_proxy(**kwargs):
        events.append(f"gate:{kwargs['tool_name']}")
        return gate.authorize(**kwargs)

    for skill_id, name in (
        (f"{SK_A}_v1.0", SK_A), (f"{SK_B}_v1.0", SK_B),
    ):
        runner.register_executor(
            skill_id,
            lambda ctx, params, name=name: (
                events.append(f"run:{name}"), ResultEnvelope.ok({})
            )[1],
        )

    class ProxyGate(FakeGate):
        def authorize(self, **kwargs):
            return gate_proxy(**kwargs)

    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A, SK_B), finish()]),
        gate=ProxyGate(), parallel_safe=True,
    )

    assert outcome.termination == "finished"
    first_run = next(index for index, item in enumerate(events) if item.startswith("run:"))
    assert events[:first_run] == [f"gate:{SK_A}", f"gate:{SK_B}"]


def test_gwt2_any_rejected_aborts_the_whole_batch(runner, tools, store):
    mediator = FakeGate({SK_B: "needs-confirmation"})
    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A, SK_B, SK_C)]),
        gate=mediator, parallel_safe=True,
    )

    assert outcome.termination == "needs_confirmation"
    assert outcome.pending is not None and outcome.pending.name == SK_B
    assert outcome.steps == ()                                  # 整批未执行
    assert "其余 2 个调用未执行" in outcome.reason
    assert store.list_files("execution_log") == ()              # 零留痕


def test_gwt2_denied_aborts_the_whole_batch(runner, tools):
    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A, SK_C)]),
        gate=FakeGate({SK_C: "denied"}),
    )
    assert outcome.termination == "denied"
    assert outcome.pending.name == SK_C
    assert outcome.steps == ()


def test_gwt3_default_serial_fallback_is_labeled(runner, tools):
    overlap: dict[str, bool] = {}
    barrier = threading.Barrier(2)
    for skill_id, name in ((f"{SK_A}_v1.0", SK_A), (f"{SK_B}_v1.0", SK_B)):
        runner.register_executor(skill_id, _barrier_executor(barrier, overlap, name))

    outcome = drive(runner, tools, FakeProtocol([batch(SK_A, SK_B), finish()]))

    assert outcome.termination == "finished"
    assert overlap == {SK_A: False, SK_B: False}                # 串行回退
    assert [step.tool_name for step in outcome.steps] == [SK_A, SK_B]
    assert outcome.parallel_used is False
    assert "显式退回串行" in outcome.parallel_note


def test_single_call_is_unchanged_even_when_parallel_is_available(runner, tools):
    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A), finish()]), parallel_safe=True,
    )
    assert [step.tool_name for step in outcome.steps] == [SK_A]
    assert outcome.parallel_note == ""                          # 单调用不加标注
    assert outcome.parallel_used is False


def test_batch_over_step_budget_terminates_without_execution(runner, tools, store):
    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A, SK_B)]), max_steps=1,
        parallel_safe=True,
    )
    assert outcome.termination == "step_limit"
    assert outcome.steps == ()
    assert store.list_files("execution_log") == ()


def test_completion_order_does_not_reorder_steps_or_trace(runner, tools):
    import time

    def slow(ctx, params):
        time.sleep(0.15)
        return ResultEnvelope.ok({"slow": True})

    def fast(ctx, params):
        time.sleep(0.01)
        return ResultEnvelope.ok({"fast": True})

    runner.register_executor(f"{SK_A}_v1.0", slow)
    runner.register_executor(f"{SK_B}_v1.0", fast)
    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A, SK_B), finish()]), parallel_safe=True,
    )

    assert [step.tool_name for step in outcome.steps] == [SK_A, SK_B]
    assert [step.ref for step in outcome.trace.steps] == [
        step.skill_run_id for step in outcome.steps
    ]


def test_failed_step_in_a_batch_does_not_stop_the_batch(runner, tools):
    runner.register_executor(
        f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.failed("执行失败", log_ref="x")
    )
    outcome = drive(
        runner, tools, FakeProtocol([batch(SK_A, SK_B), finish()]), parallel_safe=True,
    )
    assert outcome.termination == "finished"
    assert [step.envelope.status for step in outcome.steps] == ["failed", "ok"]
