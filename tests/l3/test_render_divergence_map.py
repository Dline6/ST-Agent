"""T-L4-004.2 测试：``divergence_map`` 描述件（[06 §5–§6](../../docs/技术架构-v2/06-L4-多视角推理.md)；[01 §12](../../docs/技术架构-v2/01-平台共享契约.md)）。

GWT 对照（任务文件 5 条）：
- GWT-1 两视图槽齐备（矩阵 + 证据网络），裹在 ``ok`` 信封内
- GWT-2 追问接口：矩阵每行携 ``trace_id``（展开链复用 ``trace_timeline``，本件不自造）
- GWT-3 一致性提示 + 证据面完整（不因一致裁证据）
- GWT-4 逐槽分栏 / 降级三分支（unavailable / empty / validation_failed）+ 两端注册表一致
- GWT-5 无合并结论槽

本件消费的是 **L4 的视图投影**（真链路构建，不手抄槽结构）；`.1` 的用例另在
``tests/l4/test_divergence_view.py``。层界（描述件不 import ``st_agent.l4``）在此同钉。
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.capability_types import LensOpinion
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription
from st_agent.l3.render import (
    DIVERGENCE_ABSENT_REASON,
    DIVERGENCE_EMPTY_REASON,
    DIVERGENCE_TITLE,
    STANCE_LABELS,
    describe_divergence_map,
)
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.deliberation import DeliberationResult
from st_agent.l4.divergence_view import UNANIMITY_NOTICE, DivergenceViewer
from st_agent.l4.lens import JudgingCriteria, Lens
from st_agent.ui.neutrality_gate import NeutralityGate
from st_agent.ui.registry import slot_gaps

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone(timedelta(hours=8)))

RUN_A = "run_" + "a" * 20
RUN_B = "run_" + "b" * 20
ANN_C = "ann_" + "c" * 20
SNAP_D = "snap_" + "d" * 20


def _lens(name: str, bundle: tuple[str, ...]) -> Lens:
    return Lens(
        lens_id="lens_" + name.encode("utf-8").hex()[:16],
        name=name, description=f"关注{name}维度", skill_bundle=bundle,
        judging_criteria=JudgingCriteria(natural="以中性指标为准"),
        kind="builtin", enabled=True,
    )


class _Roster:
    def __init__(self, lenses: tuple[Lens, ...]) -> None:
        self._by_id = {lens.lens_id: lens for lens in lenses}

    def get(self, lens_id: str) -> Lens:
        if lens_id not in self._by_id:
            raise KeyError(lens_id)
        return self._by_id[lens_id]


def _opinion(lens: Lens, stance: str, refs: tuple[str, ...], index: int) -> LensOpinion:
    return LensOpinion(
        lens_id=lens.lens_id, stance=stance,
        key_reasons=(f"视角「{lens.name}」基于 {len(refs)} 条有效证据形成观点",),
        evidence_refs=refs, confidence="medium",
        skills_triggered=(), trace_id=f"tr_{index:020d}",
    )


def _view(*, unanimous: bool):
    """经**真** L4 链路（对照 → 视图投影）产出视图，本件只描述它。"""
    opp = _lens("机会视角", ("sk_opportunity_mine_v1.0",))
    sentiment = _lens("情绪视角", ("sk_sentiment_flow_analysis_v1.0",))
    risk = _lens("风险视角", ("sk_risk_alert_v1.0",))
    fundamental = _lens("基本面视角", ("sk_fundamental_screening_v1.0",))

    if unanimous:
        lenses, opinions = (opp, sentiment, fundamental), (
            _opinion(opp, "positive", (RUN_A, ANN_C), 1),
            _opinion(sentiment, "positive", (ANN_C,), 2),
            _opinion(fundamental, "positive", (ANN_C,), 3),
        )
    else:
        lenses, opinions = (opp, sentiment, risk), (
            _opinion(opp, "positive", (RUN_A, ANN_C), 1),
            _opinion(sentiment, "positive", (ANN_C,), 2),
            _opinion(risk, "negative", (RUN_A, SNAP_D, RUN_B), 3),
        )
    roster = _Roster(lenses)
    result = DeliberationResult(
        topic="是否关注 sh.600000", mode="deep",
        envelope=ResultEnvelope.ok([l.lens_id for l in lenses], as_of=NOW),
        opinions=opinions, lens_ids=tuple(l.lens_id for l in lenses),
    )
    examined = CrossExaminer(roster=roster, now=lambda: NOW).cross_examine(result)
    return DivergenceViewer(roster=roster).build(result, examined)


# ───────────────────────── GWT-1 · 两视图槽齐备 ─────────────────────────


class TestTwoViews:
    def test_ok_envelope_and_component_type(self) -> None:
        envelope = describe_divergence_map(_view(unanimous=False))
        assert envelope.status == "ok"
        description = envelope.data
        assert isinstance(description, UiDescription)
        assert description.component_type == "divergence_map"
        assert description.title == DIVERGENCE_TITLE
        assert description.description_id.startswith("desc_")

    def test_matrix_row_per_participating_lens(self) -> None:
        """主视图：视角 × 结论，逐行给出立场与信心度。"""
        matrix = describe_divergence_map(_view(unanimous=False)).data.slots["matrix"]
        assert [row["name"] for row in matrix] == ["机会视角", "情绪视角", "风险视角"]
        assert [row["stance"] for row in matrix] == ["positive", "positive", "negative"]
        assert all(row["confidence"] == "medium" for row in matrix)

    def test_network_slot_carries_nodes_and_edges(self) -> None:
        """辅视图：证据节点 + 引用边。"""
        network = describe_divergence_map(_view(unanimous=False)).data.slots["network"]
        assert {n["ref"] for n in network["nodes"]} == {RUN_A, ANN_C, SNAP_D, RUN_B}
        assert len(network["edges"]) == 6

    def test_required_slots_are_filled(self) -> None:
        """必填槽（服务端 ``REQUIRED_SLOTS``）在真产物里齐备——否则会被判缺槽。"""
        description = describe_divergence_map(_view(unanimous=False)).data
        assert slot_gaps(description) == ()

    def test_summaries_are_carried(self) -> None:
        slots = describe_divergence_map(_view(unanimous=False)).data.slots
        assert [a["ref"] for a in slots["agreements"]] == [ANN_C]  # 机会 × 情绪 同向引用
        assert len(slots["disagreements"]) == 2                     # 机会 / 情绪 各与风险成对
        assert slots["topic"] == "是否关注 sh.600000"
        assert slots["map_id"]


# ───────────────────────── GWT-2 · 追问接口 ─────────────────────────


class TestFollowUp:
    def test_every_row_carries_its_trace_anchor(self) -> None:
        """06 §5 追问：点击某视角 → 展开该视角完整 Trace（复用 05 §7 的渲染）。"""
        matrix = describe_divergence_map(_view(unanimous=False)).data.slots["matrix"]
        assert [row["trace_id"] for row in matrix] == [
            f"tr_{i:020d}" for i in (1, 2, 3)
        ]


# ───────────────────────── GWT-3 · 一致性提示 + 证据完整 ─────────────────────────


class TestUnanimity:
    def test_unanimous_view_carries_the_notice(self) -> None:
        slots = describe_divergence_map(_view(unanimous=True)).data.slots
        assert slots["unanimous"] is True
        assert slots["uniform_stance"] == "positive"
        assert slots["unanimity_notice"] == UNANIMITY_NOTICE

    def test_not_unanimous_view_has_an_empty_notice(self) -> None:
        slots = describe_divergence_map(_view(unanimous=False)).data.slots
        assert slots["unanimous"] is False
        assert slots["uniform_stance"] is None
        assert slots["unanimity_notice"] == ""

    def test_evidence_face_is_complete_under_unanimity(self) -> None:
        """06 §6：提示一致**且仍完整展示证据**——四个证据面一个不少。"""
        slots = describe_divergence_map(_view(unanimous=True)).data.slots
        assert slots["agreements"] and slots["network"]["nodes"]
        assert slots["disagreements"] == [] and slots["blind_spots"] == []
        assert slots["matrix"]


# ───────────────────────── GWT-4 · 分栏与降级 ─────────────────────────


class TestSlotsAndDegradation:
    def test_text_kinds_covers_every_slot_exactly(self) -> None:
        description = describe_divergence_map(_view(unanimous=False)).data
        assert set(description.text_kinds) == set(description.slots)

    def test_only_own_prose_is_generated(self) -> None:
        """生成文案槽＝本层自有文案；数据（视角名 / 证据引用 / 词表之外的结构）入 data 槽。"""
        description = describe_divergence_map(_view(unanimous=False)).data
        generated = {k for k, v in description.text_kinds.items() if v == "generated"}
        assert generated == {"conflict_texts", "notes", "unanimity_notice", "stance_labels"}

    def test_conflict_structure_and_prose_are_split_by_slot(self) -> None:
        """Mini Debate 段：结构化字段在 `conflicts`，生成文案在 `conflict_texts`（按 key 并联）。"""
        slots = describe_divergence_map(_view(unanimous=False)).data.slots
        assert slots["conflicts"], "共享证据的方向冲突应触发 Mini Debate"
        keys = [c["key"] for c in slots["conflicts"]]
        assert set(slots["conflict_texts"]) == set(keys)
        for conflict in slots["conflicts"]:
            assert "reasons" not in conflict          # 生成文案不在数据槽
            assert conflict["reviews"]                # 重评估的结构字段在数据槽
            assert all("note" not in review for review in conflict["reviews"])
        assert any(slots["conflict_texts"][k]["reasons"] for k in keys)

    def test_real_description_passes_the_outbound_gate(self) -> None:
        verdict = NeutralityGate().check(describe_divergence_map(_view(unanimous=True)).data)
        assert verdict.passed is True, verdict.reason

    def test_stance_labels_are_neutral(self) -> None:
        from st_agent.contracts.neutrality import NeutralityGuard
        guard = NeutralityGuard()
        assert all(guard.check_output(text).passed for text in STANCE_LABELS.values())

    def test_missing_view_is_unavailable(self) -> None:
        envelope = describe_divergence_map(None, now=NOW)
        assert envelope.status == "unavailable"
        assert envelope.reason == DIVERGENCE_ABSENT_REASON
        assert envelope.last_updated_at == NOW
        assert envelope.data is None

    def test_view_without_rows_is_empty(self) -> None:
        """视图存在但无参与视角 → `empty` + 原因，**不返回一张残缺的卡**。"""
        opp = _lens("机会视角", ("sk_opportunity_mine_v1.0",))
        roster = _Roster((opp,))
        result = DeliberationResult(
            topic="是否关注 sh.600000", mode="deep",
            envelope=ResultEnvelope.ok([], as_of=NOW), opinions=(),
        )
        examined = CrossExaminer(roster=roster, now=lambda: NOW).cross_examine(result)
        view = DivergenceViewer(roster=roster).build(result, examined)
        envelope = describe_divergence_map(view, now=NOW)
        assert envelope.status == "empty"
        assert envelope.reason == DIVERGENCE_EMPTY_REASON
        assert envelope.data is None

    def test_wrong_shape_is_validation_failed(self) -> None:
        envelope = describe_divergence_map(object(), now=NOW)
        assert envelope.status == "validation_failed"
        assert envelope.data is None
        assert "视图形状不合" in envelope.reason


# ───────────────────────── GWT-5 · 无合并结论 + 层界 ─────────────────────────


_MERGED_SLOTS = ("verdict", "recommendation", "summary", "advice")


class TestNoMergedConclusion:
    def test_no_merged_conclusion_slot(self) -> None:
        """铁律 4：分歧图是并列呈现面，不是结论面。"""
        description = describe_divergence_map(_view(unanimous=False)).data
        for name in _MERGED_SLOTS:
            assert name not in description.slots
        assert not any(name in str(k) for k in description.slots for name in _MERGED_SLOTS)


def test_describer_does_not_import_l4() -> None:
    """层界（铁律 7 / LAYER_ORDER `l3 < l4`）：描述件按属性取值，不 import `st_agent.l4`。"""
    path = (
        Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l3" / "render" / "describe.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not [m for m in imported if m.startswith("st_agent.l4")], imported
