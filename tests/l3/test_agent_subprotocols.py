"""T-AGT-030 测试：五个子协议动作在循环内的承接（[05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

覆盖两叶的 GWT：配置草稿卡 / 裁决卡 / 分歧图 / 训练复述 / 当前链展开；以及
「只产卡不落值」「失败回灌不终止」「闸门仍逐步生效」三条边界。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from l3_helpers import NOW

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.builtin import install_builtins
from st_agent.l1.builtin.subprotocols import SUB_PROTOCOL_PRIMITIVES
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.config.draft import ConfigDraft, ConfigDraftProtocol
from st_agent.l3.conflict.adjudication import (
    ADJUDICATION_HEADER,
    ADJUDICATION_QUESTION,
    ConflictAdjudication,
    ConflictSide,
)
from st_agent.l3.runtime import (
    SUBPROTOCOL_TOOLS,
    GateDecision,
    LoopOutcome,
    SubprotocolPorts,
    TurnResult,
    run_agent_loop,
)
from st_agent.l6.training import TrainingSession, TrainingTurn

EP = "ep_subprotocols"
TASK = "把一次配置 / 记忆 / 分析 / 训练 / 追问请求变成循环动作"


# ───────────────────────── 夹具 ─────────────────────────


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def runner(store, skills: SkillRegistry) -> SkillRunner:
    out = SkillRunner(store, skills)
    install_builtins(
        skills, out, primitives=SUB_PROTOCOL_PRIMITIVES,
    )
    return out


@pytest.fixture()
def tools(skills: SkillRegistry, runner: SkillRunner):
    # 依赖 `runner` 是**有意**的：内置描述体在 runner fixture 里安装，
    # 工具目录须在安装之后取（否则五个动作不在目录，循环按未知工具处理）。
    return skills.tool_catalog()


class ScriptedProtocol:
    def __init__(self, replies) -> None:
        self._replies = list(replies)
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str, cancel=None) -> TurnResult:
        self.contexts.append(context)
        return self._replies.pop(0) if self._replies else TurnResult(text="（脚本已尽）")


class FakeGate:
    def __init__(self, verdict: str = "autonomous-ok") -> None:
        self._verdict = verdict
        self.seen: list[dict] = []

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        self.seen.append({"skill_id": skill_id, "tool_name": tool_name})
        return GateDecision(verdict=self._verdict)


def call(name: str, arguments: dict | None = None) -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id="call_1", name=name, arguments=arguments or {}),
    ))


def finish(text: str = "已处理") -> TurnResult:
    return TurnResult(text=text)


def run(tools, protocol, runner, *, ports=None, gate=None) -> LoopOutcome:
    return run_agent_loop(
        task=TASK,
        endpoint_id=EP,
        protocol=protocol,
        runner=runner,
        tools=tools,
        gate=gate or FakeGate(),
        max_steps=8,
        max_llm_calls=8,
        subprotocols=ports,
    )


def card_of(outcome: LoopOutcome) -> dict:
    assert len(outcome.cards) == 1
    return outcome.cards[0]


# ───────────────────────── GWT：配置草稿卡（.1） ─────────────────────────


def test_configure_action_produces_a_draft_card_and_hands_back(tools, runner):
    protocol = ScriptedProtocol([
        call("sk_configure", {"target": "sk_data_aggregate", "values": {"top_n": 3}}),
        finish(),
    ])
    ports = SubprotocolPorts(configs=ConfigDraftProtocol(descriptors=None))
    outcome = run(tools, protocol, runner, ports=ports)

    assert outcome.termination == "card_ready"
    card = card_of(outcome)
    assert card.component_type == "config_draft_card"
    assert card.slots["target"] == "sk_data_aggregate"
    assert card.slots["values"] == {"top_n": 3}
    # 只产卡：不执行任何 L1 能力、不占步数
    assert outcome.steps == ()
    assert outcome.executed_steps == 0


def test_configure_compose_rejects_unknown_parameters(store):
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    protocol = ConfigDraftProtocol(descriptors=reg)
    envelope = protocol.compose(
        target="sk_data_aggregate", values={"no_such_parameter": 1},
    )
    assert envelope.status == "validation_failed"
    assert "no_such_parameter" in (envelope.reason or "")


def test_configure_compose_builds_the_draft_without_a_preconfirmed_card(store):
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    protocol = ConfigDraftProtocol(descriptors=reg)
    envelope = protocol.compose(target="sk_data_aggregate", values={"format": "table"})
    assert envelope.status == "ok"
    assert isinstance(envelope.data, ConfigDraft)
    assert envelope.data.target == "sk_data_aggregate"
    assert envelope.data.parameter_draft == {"format": "table"}
    assert "sk_data_aggregate" in envelope.data.understanding_summary


# ───────────────────────── GWT：裁决卡（.1） ─────────────────────────


def _card() -> ConflictAdjudication:
    return ConflictAdjudication(
        conflict_id="cf_test",
        kind="contradiction",
        dimension="thesis",
        trace_id="tr_test",
        proposed=ConflictSide(dimension="thesis", fields={"view": "新提案"}),
        existing=ConflictSide(
            node_id="mem_existing", dimension="thesis", fields={"view": "既有记录"},
        ),
        header=ADJUDICATION_HEADER,
        question=ADJUDICATION_QUESTION,
    )


class FakeAdjudicator:
    def __init__(self, cards=()) -> None:
        self._cards = tuple(cards)
        self.calls: list[str] = []

    def cards(self) -> ResultEnvelope:
        self.calls.append("cards")
        return ResultEnvelope.ok(self._cards) if self._cards else ResultEnvelope.empty("无")

    def card_for(self, conflict_id: str) -> ResultEnvelope:
        self.calls.append(conflict_id)
        return ResultEnvelope.ok(self._cards[0]) if self._cards else ResultEnvelope.empty("无")


def test_memory_op_produces_an_adjudication_card_and_does_not_write(tools, runner):
    protocol = ScriptedProtocol([
        call("sk_memory_op"), finish(),
    ])
    adjudicator = FakeAdjudicator([_card()])
    outcome = run(tools, protocol, runner, ports=SubprotocolPorts(adjudicator=adjudicator))

    assert outcome.termination == "card_ready"
    card = card_of(outcome)
    assert card.component_type == "conflict_adjudication_card"
    assert card.slots["sides"][1]["fields"] == {"view": "新提案"}
    # 只读：动作只列卡，不调用裁决面的 submit（红线写入不在动作面）
    assert adjudicator.calls == ["cards"]
    assert not hasattr(adjudicator, "submitted")


def test_memory_op_multiple_cards_are_all_returned(tools, runner):
    """多张待裁决卡：全部进 `LoopOutcome.cards`，交还说明显式给卡数。"""
    second = _card().model_copy(update={"conflict_id": "cf_test_2"})
    protocol = ScriptedProtocol([call("sk_memory_op"), finish()])
    adjudicator = FakeAdjudicator([_card(), second])
    outcome = run(tools, protocol, runner, ports=SubprotocolPorts(adjudicator=adjudicator))

    assert outcome.termination == "card_ready"
    assert len(outcome.cards) == 2
    assert "待裁决卡 2 张" in outcome.reason


# ───────────────────────── GWT：分歧图（.2） ─────────────────────────


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def analyze(self, confirmation, *, values=None, now=None):
        self.calls.append(dict(confirmation.values))
        view = SimpleNamespace(
            topic="sh.600000",
            map_id="map_test",
            rows=[SimpleNamespace(
                lens_id="lens_a", name="视角 A", stance="neutral",
                confidence=0.5, trace_id="tr_a",
            )],
            agreements=(), disagreements=(), blind_spots=(),
            evidence_network=_Model(), conflicts=(),
            notes=(), unanimous=False, uniform_stance=None,
            unanimity_notice="", as_of=None,
        )
        return SimpleNamespace(envelope=ResultEnvelope.ok({"view": True}), view=view)


class _Model:
    """描述件的 `_json` 只认 ``model_dump``；用最小鸭子面承载空的证据网络。"""

    def model_dump(self, mode: str = "json") -> dict:
        return {}


def test_analyze_triggers_l4_and_produces_a_divergence_map(tools, runner):
    protocol = ScriptedProtocol([
        call("sk_analyze", {"topic": "sh.600000", "mode": "quick"}), finish(),
    ])
    analyzer = FakeAnalyzer()
    outcome = run(tools, protocol, runner, ports=SubprotocolPorts(deliberations=analyzer))

    assert outcome.termination == "card_ready"
    card = card_of(outcome)
    assert card.component_type == "divergence_map"
    assert card.slots["topic"] == "sh.600000"
    assert analyzer.calls == [{"topic": "sh.600000", "mode": "quick"}]


# ───────────────────────── GWT：训练复述（.2） ─────────────────────────


class FakeTrainer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start(self, confirmation, *, values=None, now=None):
        self.calls.append(dict(confirmation.values))
        session = TrainingSession(
            training_id="train_test",
            correction="不要追涨",
            restatement="在涨幅过高时不追入",
            pattern="用户倾向回避追高",
            created_at=NOW,
        )
        return TrainingTurn(envelope=ResultEnvelope.ok(session), session=session)


def test_train_returns_the_session_and_continues(tools, runner):
    protocol = ScriptedProtocol([
        call("sk_train", {"correction": "不要追涨"}),
        finish("训练复述已给出，等待确认"),
    ])
    trainer = FakeTrainer()
    outcome = run(tools, protocol, runner, ports=SubprotocolPorts(trainings=trainer))

    assert outcome.termination == "finished"
    assert outcome.cards == ()
    assert outcome.steps == ()
    assert trainer.calls == [{"correction": "不要追涨"}]
    # 复述回灌进下一轮工作上下文（动作不进 steps，故以本段回灌）
    assert "训练协议已产出未确认会话" in protocol.contexts[1].render_prompt()


# ───────────────────────── GWT：当前链展开（.2） ─────────────────────────


def test_explain_after_a_real_step_produces_a_trace_card(store):
    """在真执行面上先跑一步，再 explain → trace_timeline 卡。"""
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    out = SkillRunner(store, reg)
    out.register_executor(
        "sk_data_aggregate_v1.0",
        lambda _ctx, _params: ResultEnvelope.ok({"cards": []}),
    )
    install_builtins(reg, out, primitives=SUB_PROTOCOL_PRIMITIVES)
    protocol = ScriptedProtocol([
        call("sk_data_aggregate"),
        call("sk_explain"),
        finish(),
    ])
    outcome = run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=out,
        tools=reg.tool_catalog(), gate=FakeGate(),
        max_steps=8, max_llm_calls=8,
        subprotocols=SubprotocolPorts(),
    )
    assert outcome.termination == "card_ready"
    assert len(outcome.steps) == 1
    card = card_of(outcome)
    assert card.component_type == "trace_timeline"
    assert card.slots["steps"]


# ───────────────────────── 边界：失败回灌 / 闸门 / 目录 ─────────────────────────


def test_missing_port_is_fed_back_and_the_loop_continues(tools, runner):
    protocol = ScriptedProtocol([
        call("sk_configure", {"target": "sk_data_aggregate"}),
        finish("配置草稿未生成"),
    ])
    outcome = run(tools, protocol, runner, ports=SubprotocolPorts(configs=None))
    assert outcome.termination == "finished"
    assert outcome.cards == ()
    assert "未注入配置草稿生成面" in protocol.contexts[1].render_prompt()


def test_gate_still_applies_to_subprotocol_actions(tools, runner):
    protocol = ScriptedProtocol([
        call("sk_configure", {"target": "sk_data_aggregate"}),
        finish(),
    ])
    outcome = run(
        tools, protocol, runner,
        ports=SubprotocolPorts(configs=ConfigDraftProtocol()),
        gate=FakeGate("needs-confirmation"),
    )
    assert outcome.termination == "needs_confirmation"
    assert outcome.cards == ()
    assert outcome.pending is not None and outcome.pending.name == "sk_configure"


def test_unknown_subprotocol_tool_is_rejected_by_the_adapter():
    from st_agent.l3.runtime.subprotocols import handle

    result = handle("sk_not_a_subprotocol", {}, ports=SubprotocolPorts())
    assert result.envelope.status == "validation_failed"
    assert result.cards == ()


def test_the_five_actions_are_always_in_the_catalog(tools):
    names = {entry.name for entry in tools}
    assert SUBPROTOCOL_TOOLS <= names
