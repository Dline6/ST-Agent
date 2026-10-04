"""T-L4-003 测试：交叉对照 + Mini Debate（06 §3–§4）。

GWT 对照（任务文件 5 条）：
- GWT-1 一致点（≥2 视角同向引用同一证据）+ 证据网络 + 对照链锚
- GWT-2 分歧点（方向相反的两视角）+ 冲突证据差集；非方向性差异不臆造为分歧
- GWT-3 盲点：维度目录 − 参与视角覆盖；未注入 / 不可判 → 空 + 显式标注
- GWT-4 Mini Debate 触发（方向冲突 + 证据重叠或前提相关）与冲突原因分析
- GWT-5 证据重评估端口（缺省确定性不触网）+ 生成文案过 §6
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from st_agent.contracts.capability_types import LensOpinion
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.divergence import (
    Dimension,
    EvidenceReview,
    evidence_kind_of,
)
from st_agent.l4.lens import JudgingCriteria, Lens

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)

RUN_A = "run_" + "a" * 20
RUN_B = "run_" + "b" * 20
MN_C = "mn_" + "c" * 20
ANN_D = "ann_" + "d" * 20
SNAP_E = "snap_" + "e" * 20
FREE_F = "free-form-ref-0001"

SK_OPP = "sk_opportunity_mine_v1.0"
SK_RISK = "sk_risk_alert_v1.0"
SK_BOTH = "sk_shared_metric_v1.0"


# ───────────────────────── 替身 ─────────────────────────


def _op(lens_id: str, stance: str, refs: tuple[str, ...] = (),
        *, reasons: tuple[str, ...] | None = None) -> LensOpinion:
    return LensOpinion(
        lens_id=lens_id, stance=stance,
        key_reasons=reasons or (f"视角 {lens_id} 的中性陈述理由",),
        evidence_refs=refs, confidence="medium",
        skills_triggered=(), trace_id="tr" + "0" * 22,
    )


def _lens(name: str, bundle: tuple[str, ...]) -> Lens:
    return Lens(
        lens_id="lens_" + name.encode("utf-8").hex()[:16],
        name=name, description=f"关注{name}维度", skill_bundle=bundle,
        judging_criteria=JudgingCriteria(natural="以中性指标为准"),
        kind="builtin", enabled=True,
    )


class FakeRoster:
    def __init__(self, lenses: list[Lens]) -> None:
        self._by_id = {l.lens_id: l for l in lenses}

    def get(self, lens_id: str) -> Lens:
        if lens_id not in self._by_id:
            raise KeyError(lens_id)
        return self._by_id[lens_id]


class FakeCatalog:
    """按主题返回预置维度（替身；真实现归 T-INT-003 组合根）。"""

    def __init__(self, dims: tuple[Dimension, ...], *, boom: bool = False) -> None:
        self._dims = dims
        self._boom = boom
        self.topics: list[str] = []

    def dimensions_for(self, topic: str) -> tuple[Dimension, ...]:
        self.topics.append(topic)
        if self._boom:
            raise RuntimeError("catalog down")
        return self._dims


class RecordingReviewer:
    def __init__(self, note: str = "证据时效与权重已由注入端点评估") -> None:
        self._note = note
        self.seen: list[tuple[str, tuple[str, ...]]] = []

    def review(self, ref, *, lens_ids, as_of):
        self.seen.append((ref, tuple(lens_ids)))
        return EvidenceReview(
            ref=ref, validity="valid", freshness="fresh", weight=0.5, note=self._note,
        )


def _result(topic: str, ops: list[LensOpinion]):
    return SimpleNamespace(topic=topic, opinions=tuple(ops),
                           envelope=SimpleNamespace(as_of=_NOW))


def _examiner(*, roster=None, catalog=None, reviewer=None) -> CrossExaminer:
    return CrossExaminer(roster=roster, catalog=catalog, reviewer=reviewer, now=lambda: _NOW)


# ───────────────────────── GWT-1 · 一致点 + 证据网络 ─────────────────────────


class TestAgreementsAndNetwork:
    def test_shared_evidence_same_direction_is_agreement(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "positive", (RUN_A, RUN_B))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert len(m.agreements) == 1
        ag = m.agreements[0]
        assert ag.ref == RUN_A and ag.stance == "positive"
        assert ag.lens_ids == ("lens_aaaaaa", "lens_bbbbbb")

    def test_same_evidence_opposite_direction_is_not_agreement(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "negative", (RUN_A,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert m.agreements == ()

    def test_single_citation_is_not_agreement(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "positive", (RUN_B,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert m.agreements == ()

    def test_insufficient_data_lens_excluded_from_agreements(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "insufficient-data", (RUN_A,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert m.agreements == ()

    def test_evidence_network_nodes_and_edges(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A, MN_C)),
               _op("lens_bbbbbb", "negative", (ANN_D, SNAP_E, FREE_F))]
        m = _examiner().cross_examine(_result("某标的", ops))
        kinds = {n.ref: n.kind for n in m.evidence_network.nodes}
        assert kinds[RUN_A] == "skill_run_id"
        assert kinds[MN_C] == "memory_node_id"
        assert kinds[ANN_D] == "announcement_id"
        assert kinds[SNAP_E] == "dataset_snapshot_id"
        assert kinds[FREE_F] == "unknown"  # 认不出→如实 unknown，不猜
        assert len(m.evidence_network.edges) == 5
        assert {(e.lens_id, e.ref) for e in m.evidence_network.edges} == {
            ("lens_aaaaaa", RUN_A), ("lens_aaaaaa", MN_C),
            ("lens_bbbbbb", ANN_D), ("lens_bbbbbb", SNAP_E), ("lens_bbbbbb", FREE_F),
        }

    def test_trace_records_aggregation_and_conclusion(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "negative", (RUN_B,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert m.trace is not None
        assert m.trace.conclusion_ref is not None
        assert m.trace.conclusion_ref.kind == "divergence_map"
        assert m.trace.conclusion_ref.ref == m.map_id
        assert [s.step_type for s in m.trace.steps] == ["aggregation"]
        assert 3 <= len(m.map_id) <= 128  # 01 §1 evidence/conclusion ref 有界

    def test_empty_opinions_notes_and_no_trace(self):
        m = _examiner().cross_examine(_result("某标的", []))
        assert m.agreements == () and m.disagreements == () and m.trace is None
        assert m.notes and any("无任何视角观点" in n for n in m.notes)


# ───────────────────────── GWT-2 · 分歧点 ─────────────────────────


class TestDisagreements:
    def test_opposite_direction_pair_yields_disagreement(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "negative", (RUN_B,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert len(m.disagreements) == 1
        d = m.disagreements[0]
        assert d.topic == "某标的"  # 任务 A1：争议对象＝编排主题
        a, b = d.sides
        assert (a.lens_id, a.stance) == ("lens_aaaaaa", "positive")
        assert (b.lens_id, b.stance) == ("lens_bbbbbb", "negative")
        assert a.only_refs == (RUN_A,)   # 本方引用、对方未引用
        assert b.only_refs == (RUN_B,)

    def test_unanimous_stances_yield_no_disagreement(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "positive", (RUN_B,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert m.disagreements == ()

    def test_neutral_vs_positive_is_not_a_direction_conflict(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "neutral", (RUN_B,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert m.disagreements == ()

    def test_each_opposing_pair_is_a_separate_item(self):
        ops = [_op("lens_aaaaaa", "positive", (RUN_A,)),
               _op("lens_bbbbbb", "positive", (RUN_B,)),
               _op("lens_cccccc", "negative", (MN_C,))]
        m = _examiner().cross_examine(_result("某标的", ops))
        assert len(m.disagreements) == 2  # 两个 positive 各与 negative 成一对
        assert {d.sides[1].lens_id for d in m.disagreements} == {"lens_cccccc"}


# ───────────────────────── GWT-3 · 盲点 ─────────────────────────


class TestBlindSpots:
    def test_uncovered_dimension_is_a_blind_spot(self):
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        catalog = FakeCatalog((Dimension(key="turnover_rate", label="换手率",
                                         skill_ids=("sk_never_used_v1.0",)),))
        m = _examiner(roster=FakeRoster([opp, risk]), catalog=catalog).cross_examine(
            _result("某标的", [_op(opp.lens_id, "positive", (RUN_A,))]))
        assert [s.key for s in m.blind_spots] == ["turnover_rate"]
        assert m.blind_spots[0].label == "换手率"

    def test_covered_dimension_is_not_a_blind_spot(self):
        opp = _lens("机会视角", (SK_OPP,))
        catalog = FakeCatalog((Dimension(key="mom", label="动量",
                                         skill_ids=(SK_OPP,)),))
        m = _examiner(roster=FakeRoster([opp]), catalog=catalog).cross_examine(
            _result("某标的", [_op(opp.lens_id, "positive", (RUN_A,))]))
        assert m.blind_spots == ()

    def test_no_catalog_yields_empty_with_note(self):
        opp = _lens("机会视角", (SK_OPP,))
        m = _examiner(roster=FakeRoster([opp])).cross_examine(
            _result("某标的", [_op(opp.lens_id, "positive", (RUN_A,))]))
        assert m.blind_spots == ()
        assert any("未注入维度目录" in n and "T-INT-003" in n for n in m.notes)

    def test_dimension_without_producing_skill_is_not_judged(self):
        opp = _lens("机会视角", (SK_OPP,))
        catalog = FakeCatalog((Dimension(key="opaque", label="无产出方"),))
        m = _examiner(roster=FakeRoster([opp]), catalog=catalog).cross_examine(
            _result("某标的", [_op(opp.lens_id, "positive", (RUN_A,))]))
        assert m.blind_spots == ()
        assert any("覆盖不可判" in n for n in m.notes)

    def test_catalog_failure_is_surfaced_not_silent(self):
        opp = _lens("机会视角", (SK_OPP,))
        catalog = FakeCatalog((), boom=True)
        m = _examiner(roster=FakeRoster([opp]), catalog=catalog).cross_examine(
            _result("某标的", [_op(opp.lens_id, "positive", (RUN_A,))]))
        assert m.blind_spots == ()
        assert any("维度目录查询失败" in n for n in m.notes)


# ───────────────────────── GWT-4 · Mini Debate ─────────────────────────


class TestMiniDebate:
    def test_trigger_on_direction_conflict_with_shared_evidence(self):
        """共享证据即触发；无共享 skill → premise_related=False。"""
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_A, RUN_B))]
        m = _examiner(roster=FakeRoster([opp, risk])).cross_examine(_result("某标的", ops))
        assert len(m.conflicts) == 1
        c = m.conflicts[0]
        assert c.shared_refs == (RUN_A,)
        assert c.premise_related is False
        assert c.sides[0].only_refs == ()          # 被 positive 引用、被 negative 忽略的证据
        assert c.sides[1].only_refs == (RUN_B,)    # 反向

    def test_trigger_on_shared_premise_without_shared_evidence(self):
        """无共享证据但共享 skill_bundle（前提相关）→ 触发（06 §4）。"""
        opp = _lens("机会视角", (SK_OPP, SK_BOTH))
        risk = _lens("风险视角", (SK_RISK, SK_BOTH))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_B,))]
        m = _examiner(roster=FakeRoster([opp, risk])).cross_examine(_result("某标的", ops))
        assert len(m.conflicts) == 1
        c = m.conflicts[0]
        assert c.shared_refs == ()
        assert c.premise_related is True
        assert c.shared_skills == (SK_BOTH,)

    def test_no_trigger_when_no_overlap_and_no_shared_skill(self):
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_B,))]
        m = _examiner(roster=FakeRoster([opp, risk])).cross_examine(_result("某标的", ops))
        assert m.disagreements and m.conflicts == ()
        assert any("未触发 Mini Debate" in n for n in m.notes)  # 记明未触发原因

    def test_no_conflict_analysis_without_direction_conflict(self):
        opp = _lens("机会视角", (SK_OPP,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,))]
        m = _examiner(roster=FakeRoster([opp])).cross_examine(_result("某标的", ops))
        assert m.conflicts == ()

    def test_conflict_reasons_are_neutral(self):
        from st_agent.contracts import NeutralityGuard
        guard = NeutralityGuard()
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_A,))]
        m = _examiner(roster=FakeRoster([opp, risk])).cross_examine(_result("某标的", ops))
        reasons = m.conflicts[0].reasons
        assert reasons and all(guard.check_output(r).passed for r in reasons)


# ───────────────────────── GWT-5 · 重评估端口 + §6 门 ─────────────────────────


class TestEvidenceReview:
    def test_default_reviewer_leaves_unknown(self):
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_A,))]
        m = _examiner(roster=FakeRoster([opp, risk])).cross_examine(_result("某标的", ops))
        reviews = m.conflicts[0].reviews
        assert [r.ref for r in reviews] == [RUN_A]
        assert all(r.validity == "unknown" and r.freshness == "unknown"
                   and r.weight is None for r in reviews)

    def test_injected_reviewer_is_used_for_all_conflict_evidence(self):
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A, MN_C)),
               _op(risk.lens_id, "negative", (RUN_A, RUN_B))]
        reviewer = RecordingReviewer()
        m = _examiner(roster=FakeRoster([opp, risk]), reviewer=reviewer).cross_examine(
            _result("某标的", ops))
        # 双方引用面并集，逐条一次
        assert [r for r, _ in reviewer.seen] == [MN_C, RUN_A, RUN_B]
        assert all(r.validity == "valid" for r in m.conflicts[0].reviews)

    def test_review_note_failing_neutrality_is_downgraded(self):
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_A,))]
        reviewer = RecordingReviewer(note="我认为这条证据不可信")
        m = _examiner(roster=FakeRoster([opp, risk]), reviewer=reviewer).cross_examine(
            _result("某标的", ops))
        note = m.conflicts[0].reviews[0].note
        assert "我认为" not in note and "中性化校验" in note


# ───────────────────────── 契约面 ─────────────────────────


class TestContractSurface:
    @pytest.mark.parametrize("ref,kind", [
        ("ann_" + "0" * 20, "announcement_id"),
        ("snap_" + "0" * 20, "dataset_snapshot_id"),
        ("run_" + "0" * 20, "skill_run_id"),
        ("mn_" + "0" * 20, "memory_node_id"),
        ("whatever-else", "unknown"),
    ])
    def test_evidence_kind_mapping(self, ref: str, kind: str):
        assert evidence_kind_of(ref) == kind

    def test_engine_does_not_touch_llm_egress(self):
        """任务 A3：对照件不 import 任何 LLM / 出网面（不触发 live 强制项）。"""
        src = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l4"
        for name in ("crosscheck.py", "divergence.py"):
            text = (src / name).read_text(encoding="utf-8")
            assert "l0.llm" not in text
            assert "LlmClient" not in text
