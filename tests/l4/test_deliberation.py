"""T-L4-002 测试：多视角执行编排（06 §2）。

GWT 对照（任务文件 5 条）：
- GWT-1 深度模式：全部启用视角各产一条 LensOpinion；记忆切片注入并计入证据 / Trace
- GWT-2 快速模式默认组合 + 显式 lens_ids 覆盖
- GWT-3 失败隔离：某视角无有效数据 / 执行抛穿 → insufficient-data，他者照常
- GWT-4 信心度按充分度分档；默认合成器标 neutral；注入合成器可给方向；违规理由降级
- GWT-5 决策沉淀写 history 节点；未注入写入面 → unavailable 点名
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from st_agent.contracts import (
    EvidenceRef,
    LensOpinion,
    ResultEnvelope,
    Trace,
)
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l2.memory import (
    MemoryGraph,
    MemoryReader,
    MemorySlice,
    MemorySliceResult,
    SliceQuery,
    checked_node,
    new_node_id,
)
from st_agent.l4.builtin import BUILTIN_LENSES
from st_agent.l4.deliberation import (
    FAST_COMBO,
    Deliberation,
    DeliberationResult,
    SynthesizedView,
)
from st_agent.l4.lens import JudgingCriteria, Lens
from st_agent.l4.roster import LensRoster

PASS = "correct horse battery staple"


def _now() -> datetime:
    return datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


# ───────────────────────── 替身 ─────────────────────────


class FakeRunOutcome:
    """鸭子类型对齐 SkillRunner.RunOutcome（只取编排消费的四个字段）。"""

    def __init__(self, envelope: ResultEnvelope, trace: Trace, skill_run_id: str):
        self.envelope = envelope
        self.trace = trace
        self.skill_run_id = skill_run_id


class FakeRunner:
    """按 skill_id 派发预置结果的假 SkillRunner（不触 L0/L1 真实流水线）。

    - `raises` 集合内的 skill_id → run() 抛穿（验证编排层的视角异常隔离）
    - 其余 → 按 `plan`（skill_id → envelope），缺省回 ok 信封
    """

    def __init__(
        self,
        plan: dict[str, ResultEnvelope] | None = None,
        *,
        raises: frozenset[str] = frozenset(),
    ) -> None:
        self._plan = plan or {}
        self._raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, skill_id, values, *, trace=None, initiator="", purpose="",
            approved_permissions=(), **kw):
        self.calls.append((skill_id, dict(values)))
        if skill_id in self._raises:
            raise RuntimeError(f"boom:{skill_id}")
        chain = trace if trace is not None else Trace(trace_id="tr" + "0" * 22)
        env = self._plan.get(skill_id, ResultEnvelope.ok({"rows": 1}, as_of=_now()))
        return FakeRunOutcome(env, chain, f"sr_{skill_id}")


class FakeReader:
    def __init__(self, node_ids: tuple[str, ...] = ("mn_" + "a" * 20, "mn_" + "b" * 20)) -> None:
        self._ids = node_ids
        self.queries: list[SliceQuery] = []

    def query(self, spec: SliceQuery) -> MemorySliceResult:
        self.queries.append(spec)
        slices = tuple(
            MemorySlice(
                node=_attention_node(nid), as_of=_now(),
                confidence=0.9, base_confidence=0.9, source="user_stated",
            )
            for nid in self._ids
        )
        return MemorySliceResult(
            envelope=ResultEnvelope.ok([s.node.memory_node_id for s in slices], as_of=_now()),
            slices=slices, as_of=_now(),
        )


def _attention_node(node_id: str):
    """构造一个合法 attention 节点（供 MemorySlice.node；memory_node_id 为 20 位十六进制）。"""
    return checked_node(
        type="attention", memory_node_id=node_id, source="user_stated",
        privacy_level="private", confidence=0.9,
        watchlist=["000001"], created_at=_now(), updated_at=_now(),
    )


class RecordingWriter:
    def __init__(self) -> None:
        self.nodes: list[Any] = []

    def add_node(self, node):
        self.nodes.append(node)
        return node


class _InlineExecutor:
    """同线程执行器（测试确定性，不依赖线程调度顺序）。"""

    def map(self, fn, seq):
        return [fn(x) for x in seq]


class _PosSynthesizer:
    """注入合成器：给方向（positive）+ 中性理由。"""

    def synthesize(self, lens, ok_outputs, memory_slice_ids, sufficiency) -> SynthesizedView:
        return SynthesizedView(
            stance="positive",
            key_reasons=("上涨条件已具备，反转信号可观测",),
        )


class _RudeSynthesizer:
    """注入合成器：产出第一人称措辞（应被中性化门拦下并降级）。"""

    def synthesize(self, lens, ok_outputs, memory_slice_ids, sufficiency) -> SynthesizedView:
        return SynthesizedView(
            stance="neutral",
            key_reasons=("我认为这只票值得关注",),
        )


# ───────────────────────── 阵容夹具 ─────────────────────────


def _lens(name: str, bundle: tuple[str, ...], **policy) -> Lens:
    return Lens(
        lens_id="lens_" + name.encode("utf-8").hex()[:16],
        name=name, description=f"关注{name}维度",
        skill_bundle=bundle,
        judging_criteria=JudgingCriteria(natural="以中性指标为准"),
        confidence_policy=policy or {"high_at": 0.8, "medium_at": 0.5},
        kind="builtin", enabled=True,
    )


class FakeRoster:
    """按 lens_id 寻址的阵容替身（不依赖 Store）。"""

    def __init__(self, lenses: list[Lens]) -> None:
        self._by_id = {l.lens_id: l for l in lenses}
        self._by_name = {l.name: l for l in lenses}

    def list_enabled(self):
        return tuple(l for l in self._by_id.values() if l.enabled)

    def get(self, lens_id: str) -> Lens:
        if lens_id not in self._by_id:
            raise KeyError(lens_id)
        return self._by_id[lens_id]


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def roster(store: Store, skills: SkillRegistry) -> LensRoster:
    r = LensRoster(store, skills=skills)
    r.seed_builtin()
    return r


@pytest.fixture()
def three_lenses() -> list[Lens]:
    """三个可编排视角（覆盖机会/风险/基本面名，与 FAST_COMBO 对齐）。"""
    return [
        _lens("机会视角", ("sk_opportunity_mine_v1.0", "sk_unhat_eligibility_check_v1.0")),
        _lens("风险视角", ("sk_risk_alert_v1.0", "sk_delisting_risk_scan_v1.0"),
              high_at=0.7, medium_at=0.5),
        _lens("基本面视角", ("sk_fundamental_screening_v1.0",)),
    ]


def _delib(roster, runner, *, reader=None, writer=None, synthesizer=None,
           executor=None) -> Deliberation:
    return Deliberation(
        roster=roster, runner=runner, reader=reader, writer=writer,
        synthesizer=synthesizer, executor=executor or _InlineExecutor(), now=_now,
    )


# ───────────────────────── GWT-1 · 深度模式并行执行 ─────────────────────────

class TestDeepMode:
    def test_all_enabled_lenses_produce_opinions(self, three_lenses: list[Lens]):
        """深度模式：每个启用视角各一条 LensOpinion，七字段齐。"""
        runner = FakeRunner()
        d = _delib(FakeRoster(three_lenses), runner, executor=_InlineExecutor())
        res = d.deliberate("*ST 华源", mode="deep")

        assert res.envelope.status == "ok"
        assert len(res.opinions) == 3
        for op in res.opinions:
            assert op.lens_id and op.trace_id
            assert op.stance in ("positive", "negative", "neutral", "insufficient-data")
            assert op.confidence in ("high", "medium", "low")
            assert op.key_reasons and all(r.strip() for r in op.key_reasons)

    def test_opinions_carry_evidence_and_skill_runs(self, three_lenses: list[Lens]):
        runner = FakeRunner()
        res = _delib(FakeRoster(three_lenses), runner).deliberate("某标的", mode="deep")
        for op in res.opinions:
            assert op.skills_triggered  # 至少一个 skill_run_id
            assert op.evidence_refs
            for ref in op.evidence_refs:
                assert isinstance(ref, str) and len(ref) >= 3  # 01 §3：字符串形态 §1 ID

    def test_memory_slice_ids_reach_opinion_evidence_and_trace(
        self, three_lenses: list[Lens]
    ):
        """切片注入：记忆节点进 evidence_refs（memory_node_id）+ 视角链记 memory_read 步。"""
        reader = FakeReader(node_ids=("mn_" + "f" * 20,))
        res = _delib(FakeRoster(three_lenses), FakeRunner(), reader=reader).deliberate(
            "*ST 某某", mode="deep")
        assert reader.queries and reader.queries[0].task_type == "deliberation"
        for op in res.opinions:
            assert "mn_" + "f" * 20 in op.evidence_refs  # 记忆节点 ID（字符串形态）进证据面
        for tr in res.traces:
            assert any(s.step_type == "memory_read" for s in tr.steps)

    def test_neutral_output_gate_blocks_first_person(
        self, three_lenses: list[Lens]
    ):
        """默认合成器全程无拟人化措辞（过 check_output）。"""
        res = _delib(FakeRoster(three_lenses), FakeRunner()).deliberate("标的", mode="deep")
        assert res.opinions
        assert all(not ("我" in r or "我认为" in r) for op in res.opinions for r in op.key_reasons)

    def test_empty_topic_returns_validation_failed(self, three_lenses: list[Lens]):
        res = _delib(FakeRoster(three_lenses), FakeRunner()).deliberate("   ")
        assert res.envelope.status == "validation_failed"
        assert res.opinions == ()

    def test_no_runner_fails_closed(self, three_lenses: list[Lens]):
        res = Deliberation(roster=FakeRoster(three_lenses), runner=None).deliberate("x")
        assert res.envelope.status == "unavailable"
        assert "T-INT-003" in (res.envelope.reason or "")


# ───────────────────────── GWT-2 · 快速模式与显式覆盖 ─────────────────────────

class TestModeSelection:
    def test_quick_mode_defaults_to_fast_combo(self, three_lenses: list[Lens]):
        res = _delib(FakeRoster(three_lenses), FakeRunner()).deliberate("标的", mode="quick")
        chosen = {op.lens_id for op in res.opinions}
        expected = {l.lens_id for l in three_lenses if l.name in FAST_COMBO}
        assert chosen == expected
        assert len(chosen) == 3

    def test_explicit_lens_ids_overrides_mode(self, three_lenses: list[Lens]):
        target = three_lenses[2]  # 基本面视角
        res = _delib(FakeRoster(three_lenses), FakeRunner()).deliberate(
            "标的", mode="quick", lens_ids=[target.lens_id])
        assert [op.lens_id for op in res.opinions] == [target.lens_id]

    def test_disabled_lens_excluded_from_deep_mode(self, three_lenses: list[Lens]):
        three_lenses[0].model_copy(update={"enabled": False})
        # 直接换 enabled（frozen）——用 model_copy 后放回 roster 字典
        fresh = [l.model_copy(update={"enabled": False}) if l.name == "机会视角" else l
                 for l in three_lenses]
        res = _delib(FakeRoster(fresh), FakeRunner()).deliberate("标的", mode="deep")
        assert all(op.lens_id != fresh[0].lens_id for op in res.opinions)
        assert len(res.opinions) == 2

    def test_empty_roster_returns_empty_envelope(self):
        res = _delib(FakeRoster([]), FakeRunner()).deliberate("标的", mode="deep")
        assert res.envelope.status == "empty"
        assert res.opinions == ()


# ───────────────────────── GWT-3 · 失败隔离 ─────────────────────────

class TestFailureIsolation:
    def test_skill_failure_marks_lens_insufficient_and_isolates(self, three_lenses: list[Lens]):
        """某视角全非 ok → insufficient-data（low），其余照常（不抛穿、不阻塞他者）。"""
        failing = three_lenses[1]
        plan = {
            "sk_opportunity_mine_v1.0": ResultEnvelope.ok({"rows": 1}, as_of=_now()),
            "sk_unhat_eligibility_check_v1.0": ResultEnvelope.ok({"rows": 2}, as_of=_now()),
            "sk_risk_alert_v1.0": ResultEnvelope.failed("上游炸", log_ref="l/1"),
            "sk_delisting_risk_scan_v1.0": ResultEnvelope.dependency_failed("依赖失败"),
            "sk_fundamental_screening_v1.0": ResultEnvelope.ok({"score": 0.7}, as_of=_now()),
        }
        res = _delib(FakeRoster(three_lenses), FakeRunner(plan)).deliberate("标的", mode="deep")
        by_id = {op.lens_id: op for op in res.opinions}
        assert by_id[failing.lens_id].stance == "insufficient-data"
        assert by_id[failing.lens_id].confidence == "low"
        assert by_id[failing.lens_id].skills_triggered == ()
        assert by_id[three_lenses[0].lens_id].stance == "neutral"
        assert by_id[three_lenses[2].lens_id].stance == "neutral"

    def test_empty_bundle_lens_is_insufficient(self):
        lone = _lens("流动性视角", ())
        res = _delib(FakeRoster([lone]), FakeRunner()).deliberate("标的", mode="deep")
        assert res.opinions[0].stance == "insufficient-data"
        assert res.opinions[0].skills_triggered == ()

    def test_runner_exception_isolated_per_lens(self, three_lenses: list[Lens]):
        """runner.run 抛穿：该视角 insufficient-data + 降级步记入链，他者照常（任务 A4）。"""
        boom = "sk_risk_alert_v1.0"
        runner = FakeRunner(raises=frozenset({boom}))
        res = _delib(FakeRoster(three_lenses), runner).deliberate("标的", mode="deep")
        by_id = {op.lens_id: op for op in res.opinions}
        assert by_id[three_lenses[1].lens_id].stance == "insufficient-data"
        # 他者照常——但视角 1 的第二个 skill 也跑不到（异常先发生，其余 skill 不再调用）
        assert by_id[three_lenses[0].lens_id].stance == "neutral"

    def test_empty_envelope_is_insufficient_not_ok(self, three_lenses: list[Lens]):
        """合法 empty（reason 齐全）不算有效数据 → insufficient-data（任务 A4 + 01 §5）。"""
        empty_ok = ResultEnvelope.empty("今日无高危信号", as_of=_now())
        plan = {
            "sk_risk_alert_v1.0": empty_ok,
            "sk_delisting_risk_scan_v1.0": empty_ok,
        }
        res = _delib(FakeRoster(three_lenses), FakeRunner(plan)).deliberate("标的", mode="deep")
        by_id = {op.lens_id: op for op in res.opinions}
        assert by_id[three_lenses[1].lens_id].stance == "insufficient-data"
        assert by_id[three_lenses[0].lens_id].stance == "neutral"  # 未受影响

    def test_partial_ok_counts_toward_sufficiency(self):
        lone = _lens("宏观视角", ("sk_a_v1.0", "sk_b_v1.0"), high_at=1.0, medium_at=0.5)
        plan = {
            "sk_a_v1.0": ResultEnvelope.ok({"x": 1}, as_of=_now()),
            "sk_b_v1.0": ResultEnvelope.failed("上游炸", log_ref="l/2"),
        }
        res = _delib(FakeRoster([lone]), FakeRunner(plan)).deliberate("标的", mode="deep")
        op = res.opinions[0]
        assert op.stance == "neutral"
        assert op.confidence == "medium"  # 0.5 / 阈值：medium_at=0.5 命中
        assert len(op.skills_triggered) == 2


# ───────────────────────── GWT-4 · 信心度分档与合成 ─────────────────────────

class TestConfidenceAndSynthesis:
    def test_confidence_high_all_ok(self):
        lone = _lens("机会视角", ("sk_a_v1.0",), high_at=0.8, medium_at=0.5)
        res = _delib(FakeRoster([lone]), FakeRunner()).deliberate("标的", mode="deep")
        assert res.opinions[0].confidence == "high"

    def test_injected_synthesizer_can_give_direction(self, three_lenses: list[Lens]):
        res = _delib(FakeRoster(three_lenses), FakeRunner(),
                     synthesizer=_PosSynthesizer()).deliberate("标的", mode="deep")
        assert all(op.stance == "positive" for op in res.opinions)

    def test_rude_synthesizer_output_falls_back(self, three_lenses: list[Lens]):
        """合成器给第一人称 → check_output 拦下 → 降级 insufficient-data（不送达违规文案）。"""
        res = _delib(FakeRoster(three_lenses), FakeRunner(),
                     synthesizer=_RudeSynthesizer()).deliberate("标的", mode="deep")
        assert all(op.stance == "insufficient-data" for op in res.opinions)
        assert all("我" not in r for op in res.opinions for r in op.key_reasons)
        for tr in res.traces:
            assert any(s.degraded for s in tr.steps)

    def test_no_merged_verdict(self, three_lenses: list[Lens]):
        """铁律 4：DeliberationResult 无任何「合并后」字段。"""
        res = _delib(FakeRoster(three_lenses), FakeRunner()).deliberate("标的", mode="deep")
        assert isinstance(res, DeliberationResult)
        assert not hasattr(res, "verdict")
        assert not hasattr(res, "recommendation")
        assert not hasattr(res, "summary")
        dump = res.model_dump()
        for k in dump:
            assert k not in ("verdict", "recommendation", "summary")


# ───────────────────────── GWT-5 · 决策沉淀 ─────────────────────────

class TestDecisionRecording:
    def test_decision_written_to_history_node(self):
        writer = RecordingWriter()
        d = Deliberation(roster=FakeRoster([]), runner=None,
                         writer=writer, now=_now)
        res = d.record_decision(
            decision="加仓", reasoning="风险视角显示流动性改善",
            adopted_lens_ids=("lens_a",), ignored_lens_ids=("lens_b",))
        assert res.status == "ok"
        assert len(writer.nodes) == 1
        node = writer.nodes[0]
        assert node.type == "history"
        assert node.source == "user_stated"
        assert "加仓" in node.event
        assert "风险视角显示流动性改善" in node.event
        assert "lens_a" in node.event and "lens_b" in node.event

    def test_real_memory_writer_end_to_end(self, store: Store):
        graph = MemoryGraph(store)
        writer = RecordingWriter()
        # 用真实 graph → 真 MemoryWriter 兜住节点合法性
        from st_agent.l2.memory import MemoryWriter as RealWriter
        d = Deliberation(roster=FakeRoster([]), runner=None,
                         writer=RealWriter(graph), now=_now)
        res = d.record_decision(decision="减仓", reasoning="退市风险")
        assert res.status == "ok"
        node_id = res.data["memory_node_id"]
        assert graph.get_node(node_id).event.startswith("减仓")

    def test_missing_decision_text_fails(self):
        d = Deliberation(roster=FakeRoster([]), runner=None,
                         writer=RecordingWriter(), now=_now)
        res = d.record_decision(decision="   ")
        assert res.status == "validation_failed"

    def test_no_writer_fails_closed_and_names_owner(self):
        d = Deliberation(roster=FakeRoster([]), runner=None, now=_now)
        res = d.record_decision(decision="加仓")
        assert res.status == "unavailable"
        assert "T-INT-003" in (res.reason or "")


# ───────────────────────── 默认并行执行（真 ThreadPoolExecutor） ─────────────────────────

class TestParallelDefault:
    def test_default_parallel_path_runs(self, three_lenses: list[Lens]):
        """不注入 executor → 走 ThreadPoolExecutor 分支（06 §2「并行执行」）。"""
        runner = FakeRunner()
        d = Deliberation(roster=FakeRoster(three_lenses), runner=runner, now=_now)
        res = d.deliberate("标的", mode="deep")
        assert len(res.opinions) == 3
        assert len(runner.calls) == 5  # 2 + 2 + 1 个 skill

    def test_real_threadpool_executor_parallelism(self, three_lenses: list[Lens]):
        """真 ThreadPoolExecutor：并发不串行（结果与 inline 一致）。"""
        with ThreadPoolExecutor(max_workers=4) as pool:
            d = Deliberation(roster=FakeRoster(three_lenses), runner=FakeRunner(),
                             executor=pool, now=_now)
            res = d.deliberate("标的", mode="deep")
        assert len(res.opinions) == 3
        assert all(op.stance == "neutral" for op in res.opinions)
