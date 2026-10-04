"""T-L4-004.1 测试：分歧图视图投影 + 一致性语义（06 §5–§6）。

GWT 对照（任务文件 5 条）：
- GWT-1 矩阵行：全部参与视角 × 立场 / 信心度 / 链锚（含 `insufficient-data` 者照常成行）
- GWT-2 四字段透传 + Mini Debate 段带稳定键（`dfv-conflict-<n>`）
- GWT-3 一致性语义：全有结论且立场唯一 → 提示；含数据不足或立场不一 → 不提示；证据面不受影响
- GWT-4 无合并结论（`hasattr` + `model_dump` 双向钉）
- GWT-5 生成文案过 §6；视角名取阵容、缺省退回 `lens_id`
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from st_agent.contracts.capability_types import LensOpinion
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.divergence_view import (
    CONFLICT_KEY_PREFIX,
    UNANIMITY_NOTICE,
    ConflictEntry,
    DivergenceView,
    DivergenceViewer,
    LensRow,
)
from st_agent.l4.lens import JudgingCriteria, Lens

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)

RUN_A = "run_" + "a" * 20
RUN_B = "run_" + "b" * 20
MN_C = "mn_" + "c" * 20

SK_OPP = "sk_opportunity_mine_v1.0"
SK_RISK = "sk_risk_alert_v1.0"

LID_A = "lens_aaaaaa"
LID_B = "lens_bbbbbb"
LID_C = "lens_cccccc"


# ───────────────────────── 替身 ─────────────────────────


def _op(lens_id: str, stance: str, refs: tuple[str, ...] = (),
        *, confidence: str = "medium", trace_id: str = "tr" + "0" * 22) -> LensOpinion:
    return LensOpinion(
        lens_id=lens_id, stance=stance,
        key_reasons=(f"视角 {lens_id} 的中性陈述理由",),
        evidence_refs=refs, confidence=confidence, skills_triggered=(), trace_id=trace_id,
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


class BoomRoster:
    """寻址一律失败——验「取名不可得时退回 lens_id，不臆造、不报错」（任务 A3）。"""

    def get(self, lens_id: str) -> Lens:
        raise RuntimeError("roster down")


def _result(topic: str, ops: list[LensOpinion]):
    return SimpleNamespace(topic=topic, opinions=tuple(ops),
                           envelope=SimpleNamespace(as_of=_NOW))


def _map(topic: str, ops: list[LensOpinion], *, roster=None):
    return CrossExaminer(roster=roster, now=lambda: _NOW).cross_examine(
        _result(topic, ops))


def _view(topic: str, ops: list[LensOpinion], *, roster=None):
    result = _result(topic, ops)
    return DivergenceViewer(roster=roster).build(result, _map(topic, ops, roster=roster))


# ───────────────────────── GWT-1 · 矩阵行 ─────────────────────────


class TestMatrixRows:
    def test_every_participating_lens_is_a_row_in_order(self):
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_B, "negative", (RUN_B,))]
        view = _view("某标的", ops)
        assert [r.lens_id for r in view.rows] == [LID_A, LID_B]

    def test_row_carries_stance_confidence_and_trace_anchor(self):
        ops = [_op(LID_A, "positive", (RUN_A,), confidence="high",
                   trace_id="tr" + "1" * 22)]
        row = _view("某标的", ops).rows[0]
        assert (row.stance, row.confidence) == ("positive", "high")
        assert row.trace_id == "tr" + "1" * 22  # 06 §5 追问接口的取值面

    def test_insufficient_data_lens_still_has_a_row(self):
        """失败隔离是「不阻塞他者」，不是「从图里消失」（06 §2.3）。"""
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_C, "insufficient-data")]
        view = _view("某标的", ops)
        assert [r.lens_id for r in view.rows] == [LID_A, LID_C]
        assert view.rows[1].stance == "insufficient-data"

    def test_name_comes_from_roster(self):
        opp = _lens("机会视角", (SK_OPP,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,))]
        view = _view("某标的", ops, roster=FakeRoster([opp]))
        assert view.rows[0].name == "机会视角"

    def test_name_falls_back_to_lens_id_without_roster(self):
        ops = [_op(LID_A, "positive", (RUN_A,))]
        assert _view("某标的", ops).rows[0].name == LID_A

    def test_name_falls_back_when_roster_lookup_fails(self):
        """阵容寻址失败＝取名不可得 → 退回 lens_id（中性且不臆造，任务 A3）。"""
        ops = [_op(LID_A, "positive", (RUN_A,))]
        assert _view("某标的", ops, roster=BoomRoster()).rows[0].name == LID_A

    def test_no_opinions_yields_no_rows(self):
        assert _view("某标的", []).rows == ()


# ───────────────────────── GWT-2 · 四字段透传 + Mini Debate 段 ─────────────────────────


class TestCarriedFields:
    def test_four_fields_are_carried_verbatim(self):
        opp, risk = _lens("机会视角", (SK_OPP,)), _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A, MN_C)),
               _op(risk.lens_id, "negative", (RUN_A, RUN_B))]
        m = _map("某标的", ops, roster=FakeRoster([opp, risk]))
        view = DivergenceViewer(roster=FakeRoster([opp, risk])).build(
            _result("某标的", ops), m)
        assert view.agreements == m.agreements
        assert view.disagreements == m.disagreements
        assert view.blind_spots == m.blind_spots
        assert view.evidence_network == m.evidence_network
        assert view.notes == m.notes
        assert view.map_id == m.map_id and view.map_id
        assert view.as_of == _NOW

    def test_network_view_nodes_and_edges_are_exposed(self):
        ops = [_op(LID_A, "positive", (RUN_A, MN_C)), _op(LID_B, "negative", (RUN_B,))]
        net = _view("某标的", ops).evidence_network
        assert {n.ref for n in net.nodes} == {RUN_A, MN_C, RUN_B}
        assert len(net.edges) == 3

    def test_conflicts_are_keyed_for_parallel_text_slots(self):
        """键由本层生成一次，`.2` 的生成文案槽按同一键并联（任务 A2）。"""
        opp, risk = _lens("机会视角", (SK_OPP,)), _lens("风险视角", (SK_RISK,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_A,))]
        m = _map("某标的", ops, roster=FakeRoster([opp, risk]))
        view = DivergenceViewer(roster=FakeRoster([opp, risk])).build(
            _result("某标的", ops), m)
        assert [c.key for c in view.conflicts] == [f"{CONFLICT_KEY_PREFIX}-0"]
        assert [c.analysis for c in view.conflicts] == list(m.conflicts)

    def test_conflict_keys_are_stable_and_indexed(self):
        opp = _lens("机会视角", (SK_OPP,))
        risk = _lens("风险视角", (SK_RISK,))
        # 与 opp 共享 SK_OPP ⇒ 第二个方向冲突对也有「前提相关」触发条件（06 §4）
        third = _lens("基本面视角", (SK_OPP,))
        ops = [_op(opp.lens_id, "positive", (RUN_A,)),
               _op(risk.lens_id, "negative", (RUN_A,)),
               _op(third.lens_id, "negative", (RUN_B,))]
        roster = FakeRoster([opp, risk, third])
        m = _map("某标的", ops, roster=roster)
        view = DivergenceViewer(roster=roster).build(_result("某标的", ops), m)
        assert [c.key for c in view.conflicts] == [
            f"{CONFLICT_KEY_PREFIX}-{i}" for i in range(len(view.conflicts))
        ] and len(view.conflicts) == 2


# ───────────────────────── GWT-3 · 一致性语义（§6） ─────────────────────────


class TestUnanimity:
    def test_all_same_stance_yields_notice(self):
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_B, "positive", (RUN_B,))]
        view = _view("某标的", ops)
        assert view.unanimous is True
        assert view.uniform_stance == "positive"
        assert view.unanimity_notice == UNANIMITY_NOTICE

    def test_insufficient_data_lens_blocks_unanimity(self):
        """数据缺失不是共识（任务 A1）——把「没有立场」算进一致＝误报高度一致。"""
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_C, "insufficient-data")]
        view = _view("某标的", ops)
        assert view.unanimous is False
        assert view.uniform_stance is None
        assert view.unanimity_notice == ""

    def test_differing_stances_are_not_unanimous(self):
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_B, "negative", (RUN_B,))]
        view = _view("某标的", ops)
        assert view.unanimous is False and view.unanimity_notice == ""

    def test_single_opinion_is_not_unanimous(self):
        """单视角无「一致」可言。"""
        view = _view("某标的", [_op(LID_A, "positive", (RUN_A,))])
        assert view.unanimous is False and view.unanimity_notice == ""

    def test_only_insufficient_opinions_is_not_unanimous(self):
        ops = [_op(LID_A, "insufficient-data"), _op(LID_B, "insufficient-data")]
        view = _view("某标的", ops)
        assert view.unanimous is False and view.unanimity_notice == ""

    def test_evidence_face_is_complete_under_unanimity(self):
        """06 §6：提示一致的同时**仍完整展示证据**——不因一致裁掉证据面。"""
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_B, "positive", (RUN_A, RUN_B))]
        view = _view("某标的", ops)
        assert view.unanimous is True
        assert [a.ref for a in view.agreements] == [RUN_A]
        assert {n.ref for n in view.evidence_network.nodes} == {RUN_A, RUN_B}
        assert len(view.evidence_network.edges) == 3
        assert view.rows  # 矩阵行也在

    def test_notice_is_gated_by_neutrality_check(self, monkeypatch):
        """提示文案过 §6 是**送达路径上的门**，不是装饰——门失效即不送达。"""
        monkeypatch.setattr(
            "st_agent.l4.divergence_view._OUTPUT_CHECK", _AlwaysFailGuard())
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_B, "positive", (RUN_B,))]
        view = _view("某标的", ops)
        assert view.unanimous is True
        assert view.unanimity_notice == ""


class _AlwaysFailGuard:
    """把所有文案判为违规的替身守卫（验 §6 门确在送达路径上）。"""

    def check_output(self, text: str):
        return SimpleNamespace(passed=False, findings=())


# ───────────────────────── GWT-4 · 无合并结论 ─────────────────────────


_MERGED_FIELDS = ("verdict", "recommendation", "summary", "advice", "conclusion")


class TestNoMergedConclusion:
    @pytest.mark.parametrize("model", [DivergenceView, LensRow, ConflictEntry])
    def test_no_merged_conclusion_field(self, model):
        """铁律 4 / 06 架构级红线：分歧图不得产出合并后的统一建议。"""
        for name in _MERGED_FIELDS:
            assert not hasattr(model, name), f"{model.__name__} 不得有 {name} 字段"
            assert name not in model.model_fields, f"{model.__name__} 不得有 {name} 字段"

    def test_serialized_payload_has_no_merged_conclusion(self):
        ops = [_op(LID_A, "positive", (RUN_A,)), _op(LID_B, "negative", (RUN_B,))]
        payload = _view("某标的", ops).model_dump(mode="json")
        for name in _MERGED_FIELDS:
            assert name not in payload


# ───────────────────────── GWT-5 · 生成文案与层界 ─────────────────────────


class TestNeutralityAndLayering:
    def test_unanimity_notice_passes_output_check(self):
        from st_agent.contracts import NeutralityGuard
        assert NeutralityGuard().check_output(UNANIMITY_NOTICE).passed

    def test_view_does_not_touch_llm_egress(self):
        """同 T-L4-003 A3：视图投影不 import 任何 LLM / 出网面。"""
        path = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l4" / "divergence_view.py"
        text = path.read_text(encoding="utf-8")
        assert "l0.llm" not in text and "LlmClient" not in text
