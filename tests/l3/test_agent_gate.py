"""T-AGT-005.2 测试：逐步闸门与终止交还（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT-6/7/8 逐条，外加闸门三态透传、四种 fail-closed 情形与**跨层键空间/词表**的相乘性钉法。
执行面一律用**真** ``SkillRunner`` + 真 ``Store`` + 真官方 Pack，**判据面**在跨层用例里用
**真** [`RuntimeAuthorization`](../../src/st_agent/l6/runtime_authorization.py)（只有协议端是替身）——
故「闸门真的在链上」「交还不伪装完成」「不进变更流」三条是在真流水线与真判据上验的。

`tests/` 是不受 [铁律 7](../../项目管理/工程宪法.md) 约束的跨层装配位，故本件可 import ``st_agent.l6``
（与 [`test_agent_report.py`](test_agent_report.py) 同口径）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from l3_helpers import NOW
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.registry import ConfigRegistryFacade
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import (
    GATE_VERDICTS,
    SKILL_ACTION_PREFIX,
    ActionGate,
    GateDecision,
    TurnResult,
    action_id_of,
    conclude_agent_run,
    run_agent_loop,
)
from st_agent.l6 import (
    ACTION_VERDICTS,
    EvolutionAuthorization,
    EvolutionChangeFlow,
    EvolutionRiskRule,
    RuntimeAuthorization,
    agent_family,
    skill_action_id,
)

EP = "ep_test"
TASK = "查证 sh.600000 的公告与舆情"

SK_WATCH = "sk_stock_watch"          # 缺省清单：`skill.` ⇒ 须协作
SK_A = "sk_data_aggregate"
SK_B = "sk_sector_heatmap"

COLLABORATIVE_REASON_FRAGMENT = "须协作"


# ───────────────────────── 夹具：真执行面 ─────────────────────────


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(
        f"{SK_WATCH}_v1.0", lambda ctx, params: ResultEnvelope.ok({"watch": dict(params)}),
    )
    out.register_executor(
        f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.ok({"cards": []}),
    )
    out.register_executor(
        f"{SK_B}_v1.0", lambda ctx, params: ResultEnvelope.ok({"top_n": params.get("top_n")}),
    )
    return out


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


# ───────────────────────── 夹具：真 L6 判据面 ─────────────────────────


def faces(store, *, rules=None, tier: str = "collaborative"):
    """真演进授权面（清单 owner）+ 真运行期授权面（档位 owner），共享同一 ``Store``。"""
    authz = EvolutionAuthorization(store=store, now=lambda: NOW)
    if rules is not None:
        authz.set_grading(rules)
    runtime = RuntimeAuthorization(store=store, grading=authz, now=lambda: NOW)
    if tier != "collaborative":
        runtime.set_tier(tier)
    return authz, runtime


def approved_rule(base: str, risk_class: str = "autonomous-ok") -> EvolutionRiskRule:
    """把单个技能摘出来标风险类（放在 `skill.` 通则**之前**，验首个命中前缀）。"""
    return EvolutionRiskRule(prefix=skill_action_id(base), risk_class=risk_class)


# ───────────────────────── 替身：协议端口 ─────────────────────────


class ScriptedProtocol:
    """脚本化协议替身（同接口；脚本用尽即给「结束」）。"""

    def __init__(self, turns=(), *, supported: bool = True) -> None:
        self._turns = list(turns)
        self._supported = supported

    def tool_calls_supported(self) -> bool:
        return self._supported

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        return self._turns.pop(0) if self._turns else TurnResult(text="（脚本已尽）")


def call(name: str, arguments: dict | None = None) -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id="call_1", name=name, arguments=arguments or {}),
    ))


def finish(text: str = "证据已收集完毕") -> TurnResult:
    return TurnResult(text=text)


def drive(runner, tools, protocol, gate):
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools, gate=gate,
    )


# ───────────────────────── GWT-6 · 遇需协作即终止交还 ─────────────────────────


class TestGwt6HandsBackWhenNotAutonomous:
    """首步即「须协作」⇒ 一步不执行、终止并**显式**交还待确认的那一步与已收集证据。"""

    def test_first_step_needing_collaboration_executes_nothing(self, store, runner, tools):
        _authz, runtime = faces(store)                       # 缺省档 collaborative + 缺省清单
        gate = ActionGate(runtime)
        outcome = drive(runner, tools, ScriptedProtocol([call(SK_WATCH)]), gate)

        assert outcome.termination == "needs_confirmation"
        assert outcome.steps == ()
        assert outcome.pending is not None and outcome.pending.name == SK_WATCH
        assert not any(
            name.startswith("skill-run/") for name in store.list_files("execution_log")
        ), "闸门拦下时执行面不该被触碰"

    def test_handback_names_the_step_the_reason_and_the_empty_evidence(self, store, runner, tools):
        _authz, runtime = faces(store)
        outcome = drive(runner, tools, ScriptedProtocol([call(SK_WATCH, {"code": "sh.600000"})]),
                        ActionGate(runtime))
        # 先取产出前的留痕判据：一步未执行 ⇒ 产出装出来的是交还说明（非证据包）
        reason = conclude_agent_run(store, outcome).envelope.reason
        assert "下一步需确认" in reason
        assert SK_WATCH in reason
        assert "sh.600000" in reason                          # 待确认那一步的参数一并交还
        assert "闸门成因" in reason and COLLABORATIVE_REASON_FRAGMENT in reason
        assert "已收集证据 0 步" in reason

    def test_red_line_action_terminates_as_denied(self, store, runner, tools):
        _authz, runtime = faces(store, rules=(
            approved_rule(SK_WATCH, "never-autonomous"),
            EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
        ))
        runtime.set_tier("autonomous")                        # 档位拉到最宽也越不过红线
        outcome = drive(runner, tools, ScriptedProtocol([call(SK_WATCH)]), ActionGate(runtime))
        assert outcome.termination == "denied"
        assert outcome.steps == ()
        assert "never-autonomous" in outcome.gate_reason

    def test_same_script_runs_once_the_gate_permits(self, store, runner, tools):
        """闸门**真的在链上**——同一脚本在放行档位下照常执行（不是脚本本身跑不动）。"""
        _authz, runtime = faces(store, rules=(
            approved_rule(SK_WATCH),
            EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
        ), tier="autonomous")
        outcome = drive(runner, tools, ScriptedProtocol([call(SK_WATCH), finish()]),
                        ActionGate(runtime))
        assert outcome.termination == "finished"
        assert [step.tool_name for step in outcome.steps] == [SK_WATCH]
        assert outcome.steps[0].envelope.status == "ok"


# ───────────────────────── GWT-7 · 交还不伪装完成 ─────────────────────────


class TestGwt7HandbackIsNotPresentedAsFinished:
    """交还的产出**状态非 `ok`**，且被拦下的那一步**不落**「已执行动作」。"""

    def test_envelope_is_not_ok_and_record_keeps_the_step_out(self, store, runner, tools):
        _authz, runtime = faces(store)
        outcome = drive(runner, tools, ScriptedProtocol([call(SK_WATCH)]), ActionGate(runtime))
        report = conclude_agent_run(store, outcome)

        assert report.envelope.status != "ok"
        assert report.envelope.status == "empty"
        assert report.envelope.data is None                   # 不伪造一份「证据包」
        assert report.record.termination == "needs_confirmation"
        assert report.record.steps == ()
        assert [step.tool_name for step in outcome.steps] == []   # pending 不在已执行里

    def test_mid_loop_handback_keeps_the_executed_steps_as_evidence(self, store, runner, tools):
        """跑到第二步才被拦下时，第一步的证据**照实保留**、终止原因标注为交还。"""
        _authz, runtime = faces(store, rules=(
            approved_rule(SK_A),
            EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
        ), tier="autonomous")
        outcome = drive(
            runner, tools,
            ScriptedProtocol([call(SK_A), call(SK_WATCH), finish()]),
            ActionGate(runtime),
        )
        report = conclude_agent_run(store, outcome)
        assert outcome.termination == "needs_confirmation"
        assert [step.tool_name for step in outcome.steps] == [SK_A]
        assert report.envelope.status == "ok"                 # 有证据 ⇒ 证据包仍交付
        assert report.envelope.data.slots["pending"]["tool_name"] == SK_WATCH
        assert report.record.termination == "needs_confirmation"


# ───────────────────────── GWT-8 · 不淹变更历史 ─────────────────────────


class TestGwt8RuntimeCallsLeaveNoChangeRecord:
    """N 步自主循环跑完（真执行 + 真判据），**变更历史两处都无新增**。"""

    def test_multi_step_autonomous_run_writes_no_change_record(self, store, runner, tools):
        authz, runtime = faces(store, rules=(
            approved_rule(SK_A), approved_rule(SK_B),
            EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
        ), tier="autonomous")
        facade = ConfigRegistryFacade(store)
        facade.register_family(agent_family(runtime))
        flow = EvolutionChangeFlow(
            store=store, registry=facade, authorization=authz, now=lambda: NOW,
        )
        # 基线取在装配**之后**——写清单与切档位本身就是两次**配置**变更（那是该留的痕），
        # 本条要钉的是「工具调用不产生 ChangeRecord」，故只比较运行前后的差。
        baseline = (facade.changes(), flow.history(), runtime.changes())
        outcome = drive(
            runner, tools,
            ScriptedProtocol([call(SK_A), call(SK_B), finish()]),
            ActionGate(runtime),
        )

        report = conclude_agent_run(store, outcome)
        assert [step.tool_name for step in outcome.steps] == [SK_A, SK_B]
        assert report.envelope.status == "ok"
        assert (facade.changes(), flow.history(), runtime.changes()) == baseline
        assert any(name.startswith("skill-run/") for name in store.list_files("execution_log"))
        assert any(name.startswith("agent-run/") for name in store.list_files("execution_log"))


# ───────────────────────── 闸门本体：三态与 fail-closed ─────────────────────────


class FakeDecisions:
    """判据端口的替身（**鸭子类型**：只有 ``decide``；返回对象只带 ``verdict``/``reason``）。"""

    def __init__(self, verdict: str = "autonomous-ok", reason: str = "", *, raises=None) -> None:
        self._verdict = verdict
        self._reason = reason
        self._raises = raises
        self.seen: list[str] = []

    def decide(self, action_id: str):
        self.seen.append(action_id)
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(verdict=self._verdict, reason=self._reason)


class TestGateUnitBehavior:
    """闸门本体：三态透传 + 四种 fail-closed + 纯判断（不落任何留痕）。"""

    def test_verdicts_pass_through_with_their_reason(self):
        for verdict in GATE_VERDICTS:
            gate = ActionGate(FakeDecisions(verdict, f"{verdict} 的成因"))
            decision = gate.authorize(skill_id="sk_x_v1.0", tool_name="sk_x", arguments={})
            assert decision == GateDecision(verdict=verdict, reason=f"{verdict} 的成因")

    def test_action_id_is_built_from_the_tool_name_not_the_version(self):
        decisions = FakeDecisions()
        ActionGate(decisions).authorize(
            skill_id="sk_x_v2.3", tool_name="sk_x", arguments={"a": 1},
        )
        assert decisions.seen == ["skill.sk_x"]

    def test_absent_port_is_denied(self):
        decision = ActionGate().authorize(skill_id="sk_x_v1.0", tool_name="sk_x", arguments={})
        assert decision.verdict == "denied"
        assert "未注入" in decision.reason and "fail-closed" in decision.reason

    def test_port_without_the_method_is_denied(self):
        decision = ActionGate(SimpleNamespace()).authorize(
            skill_id="sk_x_v1.0", tool_name="sk_x", arguments={},
        )
        assert decision.verdict == "denied"
        assert "decide" in decision.reason

    def test_port_failure_is_denied_with_its_cause(self):
        gate = ActionGate(FakeDecisions(raises=RuntimeError("清单读不出")))
        decision = gate.authorize(skill_id="sk_x_v1.0", tool_name="sk_x", arguments={})
        assert decision.verdict == "denied"
        assert "清单读不出" in decision.reason

    def test_unrecognized_return_is_denied(self):
        gate = ActionGate(SimpleNamespace(decide=lambda action_id: 42))
        decision = gate.authorize(skill_id="sk_x_v1.0", tool_name="sk_x", arguments={})
        assert decision.verdict == "denied"
        assert "不可识别" in decision.reason

    def test_gate_writes_nothing(self, store):
        _authz, runtime = faces(store)
        gate = ActionGate(runtime)
        for _ in range(3):
            gate.authorize(skill_id=f"{SK_WATCH}_v1.0", tool_name=SK_WATCH, arguments={})
        assert store.list_files("execution_log") == ()
        assert store.list_files("config") == ()


# ───────────────────────── 跨层：键空间与词表的相乘性 ─────────────────────────


class TestCrossLayerPins:
    """L3 的键构造与 L6 的键构造、两层的结论词表相乘——**不靠 import，靠用例钉住**。"""

    def test_action_id_matches_the_l6_constructor(self):
        for base in ("sk_stock_watch", "sk_data_aggregate"):
            assert action_id_of(base) == skill_action_id(base) == f"{SKILL_ACTION_PREFIX}{base}"

    def test_verdict_vocabulary_matches_the_loop(self):
        assert ACTION_VERDICTS == GATE_VERDICTS

    def test_real_l6_face_answers_through_the_gate(self, store):
        _authz, runtime = faces(store, rules=(
            approved_rule(SK_WATCH),
            EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
        ))
        gate = ActionGate(runtime)
        held = gate.authorize(skill_id=f"{SK_WATCH}_v1.0", tool_name=SK_WATCH, arguments={})
        assert held.verdict == "needs-confirmation"

        runtime.set_tier("autonomous")
        allowed = gate.authorize(skill_id=f"{SK_WATCH}_v1.0", tool_name=SK_WATCH, arguments={})
        assert allowed.verdict == "autonomous-ok"
        assert allowed.reason == runtime.decide(skill_action_id(SK_WATCH)).reason
