"""T-AGT-004.2 测试：循环驱动、协议端口与上界（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT-1..7 逐条；执行面一律用**真** ``SkillRunner`` + 真 ``Store`` + 真官方 Pack（只有
LLM 端与闸门是注入的替身）——故「不绕过 L1 流水线」「指针指向真实存在的记录」两条是
在真流水线上验的，不是替身自证。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime import (
    AGENT_GATE_METHOD,
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_STEPS,
    GateDecision,
    ToolCallProtocol,
    TurnResult,
    run_agent_loop,
)
from st_agent.l0.llm import ToolCall

EP = "ep_test"
TASK = "查证 sh.600000 的公告与舆情"

SK_A = "sk_data_aggregate"
SK_B = "sk_sector_heatmap"
SK_RANGE = "sk_delisting_risk_scan"
SK_PERM = "sk_agent_probe"
PERM = "net_access:<example.com>"

BIG_NOTE = "x" * 3000


# ───────────────────────── 夹具：真执行面 ─────────────────────────


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    reg.register(
        SK_PERM, name="探针能力", description="供循环用例的最小能力",
        permissions=(PERM,),
    )
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.ok({"cards": []}))
    out.register_executor(f"{SK_B}_v1.0", lambda ctx, params: ResultEnvelope.ok(
        {"heatmap": {"top_n": params.get("top_n")}}))
    out.register_executor(f"{SK_PERM}_v1.0", lambda ctx, params: ResultEnvelope.ok({"probe": True}))
    return out


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


# ───────────────────────── 替身：协议端口与授权端口 ─────────────────────────


class FakeProtocol:
    """脚本化协议替身（同接口；脚本项可以是 :class:`TurnResult` 或「按上下文作答」的函数）。

    ``repeat_last=True`` 即脚本用尽后**反复重放末项**——用于构造「模型永不给出结束」。
    """

    def __init__(self, script=(), *, supported: bool = True, repeat_last: bool = False) -> None:
        self._script = list(script)
        self._supported = supported
        self._repeat_last = repeat_last
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return self._supported

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        self.contexts.append(context)
        if not self._script:
            item: TurnResult = TurnResult(text="（脚本已尽）")
        elif len(self._script) == 1 and self._repeat_last:
            item = self._script[0]
        else:
            item = self._script.pop(0)
        return item(context) if callable(item) else item


class ScriptedProtocol:
    """与 :class:`FakeProtocol` **同接口、独立实现**的另一个替身（GWT-7 的「换端口」）。"""

    def __init__(self, replies) -> None:
        self._replies = list(replies)

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        return self._replies.pop(0) if self._replies else TurnResult(text="（脚本已尽）")


class FakeGate:
    def __init__(self, verdict: str = "autonomous-ok", reason: str = "") -> None:
        self._verdict = verdict
        self._reason = reason
        self.seen: list[dict] = []

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        self.seen.append(
            {"skill_id": skill_id, "tool_name": tool_name, "arguments": dict(arguments)}
        )
        return GateDecision(verdict=self._verdict, reason=self._reason)


def call(name: str, arguments: dict | None = None, *, call_id: str = "call_1") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=name, arguments=arguments or {}),
    ))


def finish(text: str = "证据已收集完毕") -> TurnResult:
    return TurnResult(text=text)


def drive(runner, tools, protocol, *, gate=None, **kwargs):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=gate if gate is not None else FakeGate(), **kwargs,
    )


# ───────────────────────── GWT-1 · 多步闭环 ─────────────────────────


def test_gwt1_multi_step_and_second_action_depends_on_first_result(runner, tools):
    """「看结果再决定下一步」是本能力的核心：第二轮的上下文里必须已有第一步的结果。"""
    observed: dict = {}

    def second(context):
        observed["names"] = [s.tool_name for s in context.steps]
        observed["payload"] = context.steps[0].envelope.data
        return call(SK_B, {"top_n": 5}, call_id="call_2")

    outcome = drive(runner, tools, FakeProtocol([
        call(SK_A, {}, call_id="call_1"), second, finish(),
    ]))

    assert outcome.termination == "finished"
    assert [s.tool_name for s in outcome.steps] == [SK_A, SK_B]
    assert [s.envelope.status for s in outcome.steps] == ["ok", "ok"]
    assert observed["names"] == [SK_A]
    assert observed["payload"] == {"cards": []}
    assert outcome.answer == "证据已收集完毕"
    assert outcome.llm_calls == 3


# ───────────────────────── GWT-2 · 决策不入步、动作入步 ─────────────────────────


def test_gwt2_trace_holds_only_skill_run_steps(runner, tools):
    outcome = drive(runner, tools, FakeProtocol([call(SK_A), call(SK_B, {"top_n": 3}), finish()]))
    assert [s.step_type for s in outcome.trace.steps] == ["skill_run", "skill_run"]
    assert [s.ref for s in outcome.trace.steps] == [s.skill_run_id for s in outcome.steps]
    # 无「模型决策」类步骤：链的步数恒等于动作数
    assert len(outcome.trace.steps) == len(outcome.steps) == 2


# ───────────────────────── GWT-3 · 缺省闸门 fail-closed ─────────────────────────


def test_gwt3_missing_gate_executes_nothing(runner, tools, store):
    """未注入授权端口 → 一步都不执行，且**没有任何执行留痕**（不静默放行）。"""
    protocol = FakeProtocol([call(SK_A)])
    outcome = run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools, gate=None,
    )
    assert outcome.termination == "gate_absent"
    assert outcome.steps == ()
    assert "fail-closed" in outcome.reason
    assert store.list_files("execution_log") == ()          # 执行面完全未被触碰
    assert outcome.trace.steps == ()                         # 也没留下任何动作步


def test_gwt3_gate_without_authorize_method_is_absent(runner, tools):
    class NotAGate:
        pass

    outcome = run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=FakeProtocol([call(SK_A)]),
        runner=runner, tools=tools, gate=NotAGate(),
    )
    assert outcome.termination == "gate_absent"
    assert AGENT_GATE_METHOD == "authorize"                  # 端口名就是注入契约


# ───────────────────────── GWT-4 · 双上界到界即停 ─────────────────────────


def forever(_context):
    return call(SK_A, {}, call_id="call_x")


def test_gwt4_step_limit_terminates_and_labels_bound(runner, tools):
    outcome = drive(runner, tools, FakeProtocol([forever], repeat_last=True), max_steps=2)
    assert outcome.termination == "step_limit"
    assert outcome.at_bound is True
    assert len(outcome.steps) == 2
    assert "上界" in outcome.reason and "2" in outcome.reason
    assert outcome.max_steps == 2


def test_gwt4_llm_call_limit_terminates_first_when_tighter(runner, tools):
    outcome = drive(runner, tools, FakeProtocol([forever], repeat_last=True),
                    max_steps=10, max_llm_calls=3)
    assert outcome.termination == "llm_call_limit"
    assert outcome.at_bound is True
    assert outcome.llm_calls == 3
    assert len(outcome.steps) == 3


def test_gwt4_defaults_are_injectable_not_hardcoded(runner, tools):
    """上界是**参数**（带缺省），不是模块常量——装配方可选。"""
    outcome = drive(runner, tools, FakeProtocol([forever], repeat_last=True))
    assert (outcome.max_steps, outcome.max_llm_calls) == (DEFAULT_MAX_STEPS, DEFAULT_MAX_LLM_CALLS)
    assert outcome.max_steps != 2                            # 缺省确实生效
    tight = drive(runner, tools, FakeProtocol([forever], repeat_last=True),
                  max_steps=1, max_llm_calls=9)
    assert tight.termination == "step_limit"


# ───────────────────────── GWT-5 · 跑在上界的边界上 ─────────────────────────


def test_gwt5_finishing_exactly_at_step_bound_is_normal_completion(runner, tools):
    outcome = drive(
        runner, tools,
        FakeProtocol([call(SK_A), call(SK_B, {"top_n": 3}), finish()]),
        max_steps=2,
    )
    assert outcome.termination == "finished"
    assert outcome.at_bound is False
    assert len(outcome.steps) == 2                           # 恰在第 N 步给出结束


def test_gwt5_finishing_without_any_step_is_normal_completion(runner, tools):
    outcome = drive(runner, tools, FakeProtocol([finish("无需调用能力即可作答")]), max_steps=1)
    assert outcome.termination == "finished"
    assert outcome.steps == ()
    assert outcome.answer == "无需调用能力即可作答"


# ───────────────────────── GWT-6 · 不绕过 L1 流水线 ─────────────────────────


def test_gwt6_param_violation_is_judged_by_l1(runner, tools):
    """参数越界由 L1 判（循环不自判）；得 L1 的 ``validation_failed`` 信封。"""
    outcome = drive(runner, tools, FakeProtocol([
        call(SK_RANGE, {"threshold": 5.0}), finish(),
    ]))
    first = outcome.steps[0]
    assert first.envelope.status == "validation_failed"
    assert "参数校验失败" in first.envelope.reason
    assert outcome.termination == "finished"                 # 单步失败不终止循环


def test_gwt6_missing_permission_is_judged_by_l1(runner, tools):
    without = drive(runner, tools, FakeProtocol([call(SK_PERM), finish()]))
    assert without.steps[0].envelope.status == "validation_failed"
    assert "缺少已批准权限" in without.steps[0].envelope.reason

    with_perm = drive(
        runner, tools, FakeProtocol([call(SK_PERM), finish()]), approved_permissions=(PERM,),
    )
    assert with_perm.steps[0].envelope.status == "ok"


# ───────────────────────── GWT-7 · 协议端口可替换 ─────────────────────────


def test_gwt7_swapping_protocol_implementation_keeps_behaviour(runner, tools):
    script = [call(SK_A), call(SK_B, {"top_n": 3}), finish()]
    one = drive(runner, tools, FakeProtocol(list(script)))
    other = drive(runner, tools, ScriptedProtocol(list(script)))
    assert one.termination == other.termination == "finished"
    assert [s.tool_name for s in one.steps] == [s.tool_name for s in other.steps]
    assert [s.envelope.status for s in one.steps] == [s.envelope.status for s in other.steps]


def test_gwt7_native_protocol_satisfies_the_port_interface():
    """T1 实现与替身共用同一接口（结构性检查，避免端口被悄悄改宽）。"""
    assert hasattr(ToolCallProtocol, "tool_calls_supported")
    assert hasattr(ToolCallProtocol, "invoke_turn")


# ───────────────────────── 闸门三态与交还 ─────────────────────────


def test_gate_needs_confirmation_terminates_and_returns_pending_step(runner, tools, store):
    gate = FakeGate("needs-confirmation", "该能力访问外部主机，需逐项确认")
    outcome = drive(runner, tools, FakeProtocol([call(SK_A), finish()]), gate=gate)
    assert outcome.termination == "needs_confirmation"
    assert outcome.steps == ()
    assert outcome.pending is not None and outcome.pending.name == SK_A
    assert outcome.gate_reason == "该能力访问外部主机，需逐项确认"
    assert store.list_files("execution_log") == ()
    assert gate.seen[0]["skill_id"] == f"{SK_A}_v1.0"         # 闸门拿到的执行目标，不是工具名


def test_gate_denied_and_garbage_both_fail_closed(runner, tools):
    denied = drive(runner, tools, FakeProtocol([call(SK_A)]), gate=FakeGate("denied"))
    assert denied.termination == "denied"
    assert denied.steps == ()

    class BrokenGate:
        def authorize(self, **kwargs):
            return None                                        # 不可识别的结论

    broken = drive(runner, tools, FakeProtocol([call(SK_A)]), gate=BrokenGate())
    assert broken.termination == "denied"
    assert broken.steps == ()
    assert "fail-closed" in broken.gate_reason


# ───────────────────────── 端点能力与目录外调用 ─────────────────────────


def test_endpoint_without_tool_support_never_calls_llm(runner, tools):
    protocol = FakeProtocol([call(SK_A)], supported=False)
    outcome = drive(runner, tools, protocol)
    assert outcome.termination == "endpoint_unavailable"
    assert outcome.steps == () and outcome.llm_calls == 0
    assert protocol.contexts == []                            # 一次都没问
    assert "工具调用" in outcome.reason


def test_unknown_tool_terminates_without_execution(runner, tools, store):
    outcome = drive(runner, tools, FakeProtocol([call("sk_not_in_catalog")]))
    assert outcome.termination == "unknown_tool"
    assert outcome.steps == ()
    assert "不在工具目录内" in outcome.reason
    assert store.list_files("execution_log") == ()


def test_llm_failure_is_carried_through(runner, tools):
    failure = ResultEnvelope.failed("端点不可达", log_ref="llm-run/x.json")
    outcome = drive(runner, tools, FakeProtocol([TurnResult(envelope=failure)]))
    assert outcome.termination == "llm_failed"
    assert outcome.failure is failure
    assert outcome.steps == ()


# ───────────────────────── 留痕指针真的存在（.1 GWT-3 的端到端证据） ─────────────────────────


def test_step_pointer_points_to_real_execution_log_records(runner, tools, store):
    """每一步的 ``log_ref`` 都能在 ``execution_log`` 里取到真记录（可点击复核）。"""
    outcome = drive(runner, tools, FakeProtocol([call(SK_A), call(SK_B, {"top_n": 3}), finish()]))
    for step in outcome.steps:
        raw = store.get("execution_log", step.log_ref)
        assert step.skill_run_id.encode() in raw
        assert step.log_ref == f"skill-run/{step.skill_run_id}.json"


def test_truncated_result_keeps_readable_pointer(runner, tools, store):
    """结果被截断时，指针仍指向原记录——上下文里丢的是体积，不是可追溯性。"""
    runner.register_executor(
        f"{SK_B}_v1.0", lambda ctx, params: ResultEnvelope.ok({"heatmap": {"note": BIG_NOTE}}),
    )
    seen: dict = {}

    def second(context):
        step = context.steps[0]
        seen["truncated"] = step.truncated
        seen["log_ref"] = step.envelope.data.log_ref
        return finish()

    outcome = drive(
        runner, tools, FakeProtocol([call(SK_B, {"top_n": 3}), second]),
        max_result_chars=200,
    )
    assert seen["truncated"] is True
    assert store.get("execution_log", seen["log_ref"])       # 指针可读
    assert outcome.steps[0].truncated is False               # 循环里的原件未被改写


# ───────────────────────── 非法入参 ─────────────────────────


@pytest.mark.parametrize("kwargs", [{"max_steps": 0}, {"max_llm_calls": 0}, {"max_steps": -1}])
def test_non_positive_bounds_rejected(runner, tools, kwargs):
    with pytest.raises(AgentRuntimeValidationError, match="须为 ≥1 的整数"):
        drive(runner, tools, FakeProtocol([finish()]), **kwargs)


def test_runner_is_required(tools):
    with pytest.raises(AgentRuntimeValidationError, match="循环需要执行面"):
        run_agent_loop(
            task=TASK, endpoint_id=EP, protocol=FakeProtocol([finish()]),
            runner=None, tools=tools,
        )
