"""`T-INT-006` · M5 集成关卡：受控自主闭环的跨层装配用例。

只测**装配关系与跨层数据流**（单任务行为已由各自套件覆盖）：本关卡的 GWT 锚在
[00 §5 端到端数据流](../../docs/技术架构-v2/00-架构总览.md) 的**反向流**（用户发起 →
意图 → 澄清 → 确认 → 派发 → 结果渲染 → 留痕）与 [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)
的九段契约上。

**全离线**：出网只换掉 HTTP 发送口（`llm_post`），`LlmClient` / transport / 出网网关 /
能力协商 / 启动探测**都是真的**；真端点路径在 ``tests/live/test_llm_tools_live.py``。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from rig import PASS, ROOT_NAME, MarketData, RecordingSender, seed_market_db
from rig_m1 import DeterministicUnderstander
from rig_m5 import (
    DEFAULT_RULES,
    ENV,
    SK_A,
    SK_B,
    ScriptedEndpoint,
    seeded_m5,
)

from st_agent.__main__ import build_ambient_runtime
from st_agent.app import M4Runtime, M5Runtime, TurnResult, build_m4_runtime
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runtime import LLM_CAPABILITY, LLM_ENDPOINT_ID
from st_agent.l3.intent import ConfirmationItem, IntentDraft, checked_confirmation
from st_agent.l3.runtime import load_agent_run
from st_agent.l6 import EvolutionRiskRule

TASK_TEXT = "查证 sh.600000 的 ST 状态与退市风险"
QUERY_RULE = ("退市", IntentDraft(intent="query", target=f"{SK_A}_v1.0"))

SRC = Path(__file__).resolve().parents[2] / "src"
RUNTIME_DIR = SRC / "st_agent" / "l3" / "runtime"


@pytest.fixture()
def rig(tmp_path: Path):
    return seeded_m5(tmp_path / ROOT_NAME)


# ───────────────────────────── 共用小工具 ─────────────────────────────


def _make_autonomous(runtime: M5Runtime) -> None:
    """把闸门调成「可自主」：清单把 `skill.` 判为可自主 + 档位切 `autonomous`。

    两步都必要——清单恒判 `skill.` 为 `collaborative-required`（缺省），而档位独立于清单
    （[D-090] ① 的两条独立条目）；这正是 [01 §7] 共享清单的设计用法。
    """
    runtime.l6.authorization.set_grading(
        [EvolutionRiskRule(prefix="skill.", risk_class="autonomous-ok")]
    )
    runtime.l6.agent_authorization.set_tier("autonomous")


def _card(intent: str, target: str | None = None):
    """一张**已确认**的确认卡（`explain` 去向这类无参号令的入口凭证）。"""
    return checked_confirmation(
        envelope=ResultEnvelope.ok({"intent": intent}),
        intent=intent, target=target,
        items=(ConfirmationItem(
            text="意图", value=target or "未指定", source="intent"),),
        values={}, confirmed=True,
    )


def _investigate(runtime: M5Runtime, text: str = TASK_TEXT):
    """走一次 `investigate` 全流程（意图 → 确认 → 派发），返回派发产出。"""
    turn = runtime.chat.post(text)
    assert isinstance(turn, TurnResult), turn
    assert turn.needs_confirmation, "investigate 意图应产出确认卡"
    return runtime.chat.confirm_and_dispatch(turn.session_id)


# ─────────────────────── GWT-1 · 端到端多步自主查证 ───────────────────────


class TestGwt1EndToEndInvestigate:
    def test_two_steps_both_run_through_l1_and_trace_holds_exactly_two_skill_runs(
        self, rig
    ):
        _make_autonomous(rig.m5)

        outcome = _investigate(rig.m5)

        assert outcome.envelope.status == "ok"
        record = outcome.investigation.report.record
        # 两步都由模型按**工具目录**选出，且都真的经 L1 执行成功
        assert [s.tool_name for s in record.steps] == [SK_A, SK_B]
        assert [s.skill_id for s in record.steps] == [f"{SK_A}_v1.0", f"{SK_B}_v1.0"]
        assert [s.status for s in record.steps] == ["ok", "ok"]
        # 链上**恰好两条** `skill_run` 步——决策不入步（01 §4）
        trace = outcome.investigation.trace
        assert [s.step_type for s in trace.steps] == ["skill_run", "skill_run"]
        # 每步的留痕指针指向 L1 真落下的 `skill-run/` 记录
        for step in record.steps:
            assert rig.m5.store.get("execution_log", step.log_ref)
        # 引用清单只收**成功**步
        assert {ref.ref for ref in outcome.envelope.evidence_refs} == {
            s.skill_run_id for s in record.steps
        }

    def test_second_action_is_chosen_after_seeing_the_first_result(self, rig):
        _make_autonomous(rig.m5)

        _investigate(rig.m5)

        # 三轮：出动作 A → 回填 → 出动作 B → 回填 → 结束
        assert len(rig.endpoint.loop_calls) == 3
        first, second, final = rig.endpoint.loop_calls
        # 目录经 `tools` 入参下发（01 §2 描述体投影），不并进 prompt 文本
        assert {SK_A, SK_B} <= set(first["tools"])
        assert "调用 sk_data_aggregate" not in first["prompt"]
        # 第一轮的上下文里**没有**任何执行结果；第二轮才有第一步的结果
        assert "cards" not in first["prompt"]
        assert "cards" in second["prompt"]
        assert final["prompt"].count("调用 sk_") == 2


# ───────────────────────── GWT-2 · 闸门真的在链上 ─────────────────────────


class TestGwt2GateIsOnTheChain:
    def test_default_tier_is_collaborative_and_executes_nothing(self, rig):
        outcome = _investigate(rig.m5)

        # 缺省档 `collaborative`：模型出了一步，但闸门**拦下**，一步都没执行
        assert len(rig.endpoint.loop_calls) == 1
        assert outcome.investigation.report.record.steps == ()
        # 交还**显式**：非 ok 状态 + 待确认的那一步 + 已收集证据数（为零也明说）
        assert outcome.envelope.status == "empty"
        assert "下一步需确认" in outcome.envelope.reason
        assert SK_A in outcome.envelope.reason
        assert "本次已收集证据 0 步" in outcome.envelope.reason

    def test_autonomous_tier_with_relaxed_grading_executes(self, rig):
        _make_autonomous(rig.m5)

        outcome = _investigate(rig.m5)

        assert outcome.envelope.status == "ok"
        assert len(outcome.investigation.report.record.steps) == 2

    def test_grading_and_tier_are_independent_entries(self, rig):
        """档位是独立条目 `agent.authorization`，清单仍只有 `evolution.risk-grading` 一张。"""
        ids = {entry.config_id for entry in rig.m5.l6.agent_authorization.entries()}
        assert ids == {"agent.authorization"}
        assert "evolution.risk-grading" not in ids
        assert rig.m5.l6.agent_authorization.grading_source is rig.m5.l6.authorization


# ───────────────────── GWT-3 · 跨层端口未反转（铁律 7） ─────────────────────


class TestGwt3NoLayerReversal:
    def test_runtime_package_and_l3_do_not_import_upward_layers(self):
        for path in sorted(RUNTIME_DIR.glob("*.py")):
            for module, lineno in _imports_of(path):
                assert not module.startswith("st_agent.l6"), (
                    f"{path.name}:{lineno} 依赖更上层 {module}——L6 授权面经鸭子端口注入"
                )
        for path in sorted((SRC / "st_agent" / "l3").rglob("*.py")):
            for module, lineno in _imports_of(path):
                assert not module.startswith("st_agent.l4"), (
                    f"{path.name}:{lineno} 依赖更上层 {module}（铁律 7）"
                )

    def test_authorization_face_is_injected_not_constructed(self, rig):
        """L6 判据面是**注入**的同一实例——循环端口不认识 L6 类型，只认 `decide` 方法。"""
        assert rig.m5.investigations._authorization is rig.m5.l6.agent_authorization
        assert hasattr(rig.m5.investigations._authorization, "decide")


# ─────────────────── GWT-4 · 能力档来自探测而非硬编码 ───────────────────


class TestGwt4CapabilityComesFromTheProbe:
    def test_unprobed_endpoint_is_unavailable_not_run(self, tmp_path: Path):
        rig = seeded_m5(tmp_path / ROOT_NAME, supported=False)

        # 探测真跑过一次，且判据取的是**探测结果**（端点静默忽略 tools ⇒ 不支持）
        assert len(rig.endpoint.probe_calls) == 1
        capability = rig.m5.m1.runtime.endpoints.get(LLM_ENDPOINT_ID).capability
        assert capability.supports_function_calling is False
        assert "supports_function_calling" not in LLM_CAPABILITY  # 不是那份缺省

        outcome = _investigate(rig.m5)

        assert outcome.envelope.status == "unavailable"
        assert "未声明支持工具调用" in outcome.envelope.reason
        assert rig.endpoint.loop_calls == []          # 一步都没执行（fail-closed）
        # 留痕**照实**记下这次未跑成的循环（零步 + 端点侧原因），不伪造、也不吞
        assert outcome.investigation.report.record.steps == ()
        assert outcome.investigation.report.record.termination == "endpoint_unavailable"

    def test_probed_supported_endpoint_runs(self, rig):
        assert len(rig.endpoint.probe_calls) == 1
        assert rig.m5.m1.runtime.endpoints.get(
            LLM_ENDPOINT_ID
        ).capability.supports_function_calling is True
        _make_autonomous(rig.m5)

        outcome = _investigate(rig.m5)

        assert outcome.envelope.status == "ok"


# ───────────────────── GWT-5 · 留痕端到端可查 ─────────────────────


class TestGwt5TrailIsQueryable:
    def test_record_holds_steps_termination_and_bounds(self, rig):
        _make_autonomous(rig.m5)

        outcome = _investigate(rig.m5)
        record = load_agent_run(rig.m5.store, outcome.investigation.agent_run_id)

        assert record.steps_count == 2
        assert record.termination == "finished"
        assert record.at_bound is False
        assert record.max_steps == rig.m5.m1.bounds.steps()
        assert record.max_llm_calls == rig.m5.m1.bounds.llm_calls()

    def test_explain_route_expands_the_same_trace(self, rig):
        _make_autonomous(rig.m5)
        outcome = _investigate(rig.m5)
        record = load_agent_run(rig.m5.store, outcome.investigation.agent_run_id)

        explained = rig.m5.m1.bus.dispatch(
            _card("explain"), trace=outcome.investigation.trace,
        )

        assert explained.envelope.status == "ok"
        assert [s.step_type for s in explained.envelope.data.steps] == [
            "skill_run", "skill_run",
        ]
        assert explained.envelope.data.trace_id.value == record.trace_id

    def test_bounds_are_read_from_the_config_entry(self, rig):
        """上界取自 [01 §7] 登记项（`investigate.max_steps`）——不是循环入口的缺省常量。"""
        rig.m5.m1.bounds.set_steps(3)
        _make_autonomous(rig.m5)

        outcome = _investigate(rig.m5)

        assert outcome.investigation.report.record.max_steps == 3


# ───────────────────── GWT-6 · 既有链路不回归 ─────────────────────


class TestGwt6NoRegression:
    def test_m4_root_still_fails_closed_for_investigate(self, tmp_path: Path):
        """**不改** M4 面：无循环装配的根上，`investigate` 照旧 `unavailable` + 点名。"""
        root = tmp_path / ROOT_NAME
        seed_market_db(root, PASS)
        m4 = build_m4_runtime(
            root, PASS, market_query=MarketData(), sender=RecordingSender(),
            llm_env={}, understander=DeterministicUnderstander(DEFAULT_RULES),
        )

        assert isinstance(m4, M4Runtime)
        outcome = _investigate(m4)

        assert outcome.envelope.status == "unavailable"
        assert "T-INT-006" in outcome.envelope.reason

    def test_existing_query_route_still_runs_on_the_m5_root(self, tmp_path: Path):
        """既有六类去向在 M5 根上照常：`query` 仍经 L1 执行面直取。"""
        rig = seeded_m5(tmp_path / ROOT_NAME, rules=[*DEFAULT_RULES, QUERY_RULE])
        _make_autonomous(rig.m5)

        turn = rig.m5.chat.post("退市风险扫描一下")
        outcome = rig.m5.chat.confirm_and_dispatch(turn.session_id)

        assert turn.confirmation.intent == "query"
        assert outcome.envelope.status == "ok"
        assert outcome.run.skill_id == f"{SK_A}_v1.0"

    def test_six_existing_routes_are_intact(self):
        from st_agent.l3.dispatch import ROUTE_BY_INTENT

        assert {spec.intent for spec in ROUTE_BY_INTENT.values()} == {
            "query", "configure", "analyze", "memory_op", "train", "explain",
            "investigate",
        }
        assert all(spec.wired for spec in ROUTE_BY_INTENT.values())


# ───────────────────── GWT-7 · 生产入口生效 ─────────────────────


class TestGwt7ProductionEntry:
    def test_default_root_is_the_m5_root_with_probe_and_loop_port(self, tmp_path: Path):
        root = tmp_path / ROOT_NAME
        seed_market_db(root, PASS)
        endpoint = ScriptedEndpoint()

        runtime = build_ambient_runtime(
            root, PASS, market_query=MarketData(), sender=RecordingSender(),
            llm_env=ENV, llm_post=endpoint,
            understander=DeterministicUnderstander(DEFAULT_RULES),
        )

        assert isinstance(runtime, M5Runtime)
        assert runtime.investigations is not None        # 循环端口已注入
        assert len(endpoint.probe_calls) == 1            # 探测已执行（不是只在测试装配里）
        assert runtime.m1.runtime.endpoints.get(
            LLM_ENDPOINT_ID
        ).capability.supports_function_calling is True

    def test_unconfigured_root_assembles_and_fails_closed(self, tmp_path: Path):
        """**未配 LLM 端点**的机器上：装配成功、端口已注入、`investigate` 回
        `unavailable`（不抛异常）—— 05 §10 的「端点不可用 → fail-closed + 显式降级告知」。"""
        root = tmp_path / ROOT_NAME
        seed_market_db(root, PASS)

        runtime = build_ambient_runtime(
            root, PASS, market_query=MarketData(), sender=RecordingSender(),
            llm_env={}, understander=DeterministicUnderstander(DEFAULT_RULES),
        )

        assert isinstance(runtime, M5Runtime)
        assert runtime.investigations is not None
        assert runtime.m1.runtime.endpoints.list_endpoints() == ()

        outcome = _investigate(runtime)

        assert outcome.envelope.status == "unavailable"
        assert "未声明支持工具调用" in outcome.envelope.reason


# ───────────────────────────── 内部 ─────────────────────────────


def _imports_of(path: Path):
    """产出 ``(模块名, 行号)``（同 `tests/test_layering.py` 的口径）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                yield node.module, node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
