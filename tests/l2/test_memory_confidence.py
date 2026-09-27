"""T-L2-002.3 · 置信度模型（04 §5；GWT-1..6）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from memory_helpers import NOW, edge, inferred, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    BASELINE_CONFIG_ID,
    DYNAMICS_CONFIG_ID,
    ConfidenceBaseline,
    ConfidenceDynamics,
    ConfidenceModel,
    MemoryGraph,
    MemoryReader,
    MemoryValidationError,
    MemoryWriter,
    SliceQuery,
    checked_baseline,
    checked_dynamics,
)

BASELINE_PATH = "memory-policy/confidence-baseline.json"
DYNAMICS_PATH = "memory-policy/confidence-dynamics.json"


class TestGwt1BaselineOrdering:
    """GWT-1：``user_stated`` 基线严格高于 ``inferred``，且来自条目而非硬编码。"""

    def test_user_stated_baseline_exceeds_inferred(self, confidence: ConfidenceModel) -> None:
        assert confidence.baseline("user_stated") > confidence.baseline("inferred")

    def test_equal_stored_confidence_keeps_the_source_order(
        self, confidence: ConfidenceModel
    ) -> None:
        stated = node("thesis", confidence=0.8)
        guessed = inferred("thesis", confidence=0.8)
        assert confidence.effective(stated) > confidence.effective(guessed)

    def test_baseline_is_read_from_the_config_entry(self, confidence: ConfidenceModel) -> None:
        confidence.set_baseline(ConfidenceBaseline(user_stated=0.6, inferred=0.2))
        assert confidence.baseline("user_stated") == 0.6
        assert confidence.baseline("inferred") == 0.2

    def test_unknown_source_is_rejected(self, confidence: ConfidenceModel) -> None:
        with pytest.raises(MemoryValidationError, match="未知的记忆来源"):
            confidence.baseline("hearsay")


class TestGwt2ThesisDecaysOverTime:
    """GWT-2：thesis 有效置信度随龄期单调不增；半衰期可配。"""

    def test_decay_is_monotone_non_increasing(self, confidence: ConfidenceModel) -> None:
        thesis = node("thesis", confidence=0.9)
        values = [confidence.effective(thesis, now=NOW + timedelta(days=d))
                  for d in (0, 30, 90, 180, 365, 730)]
        assert all(a >= b for a, b in zip(values, values[1:]))
        assert values[0] > values[-1]

    def test_half_life_halves_at_the_named_age(self, confidence: ConfidenceModel) -> None:
        thesis = node("thesis", confidence=0.9)
        assert confidence.effective(thesis, now=NOW + timedelta(days=180)) == pytest.approx(0.45)

    def test_half_life_is_configurable(self, confidence: ConfidenceModel) -> None:
        thesis = node("thesis", confidence=0.9)
        at_180 = confidence.effective(thesis, now=NOW + timedelta(days=180))
        confidence.set_dynamics(ConfidenceDynamics(half_life_days=90.0))
        assert confidence.effective(thesis, now=NOW + timedelta(days=180)) < at_180


class TestGwt3NonDecayingTypesAreUnaffected:
    """GWT-3：§5 只把衰减写在 thesis 上。"""

    @pytest.mark.parametrize("kind", ["identity", "attention", "history", "pattern", "evolution"])
    def test_other_types_ignore_age(self, confidence: ConfidenceModel, kind: str) -> None:
        fresh = node(kind, confidence=0.8)
        aged = confidence.effective(fresh, now=NOW + timedelta(days=5000))
        assert aged == pytest.approx(confidence.effective(fresh, now=NOW))

    def test_decayed_types_are_configurable(self, confidence: ConfidenceModel) -> None:
        identity = node("identity", confidence=0.8)
        confidence.set_dynamics(ConfidenceDynamics(decayed_types=("identity",),
                                                   half_life_days=180.0))
        assert confidence.effective(identity, now=NOW + timedelta(days=180)) == pytest.approx(0.4)


class TestGwt4ReEstimationOnReference:
    """GWT-4：被新事件引用时重估（与衰减结合，且不回写存量节点）。"""

    def test_reference_raises_the_decayed_value(self, graph: MemoryGraph, writer: MemoryWriter,
                                                confidence: ConfidenceModel) -> None:
        old = node("thesis", confidence=0.9)
        writer.add_node(old)
        later = NOW + timedelta(days=360)
        decayed = confidence.effective(old, now=later)

        citer = node("thesis", subject="sz.000001", subject_kind="stock")
        writer.add_node(citer)
        writer.add_edge(edge("related_to", citer.memory_node_id, old.memory_node_id, at=later))

        restored = confidence.effective(old, now=later)
        assert restored > decayed
        assert restored <= confidence.baseline("user_stated")

    def test_recovery_never_rewrites_the_stored_node(self, graph: MemoryGraph,
                                                     writer: MemoryWriter,
                                                     confidence: ConfidenceModel) -> None:
        old = node("thesis", confidence=0.9)
        writer.add_node(old)
        later = NOW + timedelta(days=900)
        confidence.effective(old, now=later)
        assert graph.get_node(old.memory_node_id).confidence == 0.9

    def test_edges_older_than_the_node_do_not_restore(self, graph: MemoryGraph,
                                                      writer: MemoryWriter,
                                                      confidence: ConfidenceModel) -> None:
        other = node("thesis", subject="sz.000001", subject_kind="stock")
        old = node("thesis", confidence=0.9,
                   updated_at=NOW + timedelta(days=400))
        writer.add_node(other)
        writer.add_node(old)
        writer.add_edge(edge("related_to", other.memory_node_id, old.memory_node_id, at=NOW))
        later = NOW + timedelta(days=580)                     # 龄期 180 天 → 恰好半衰
        assert confidence.effective(old, now=later) == pytest.approx(0.45)


class TestGwt5ParametersAreConfigurableAndRecorded:
    """GWT-5：参数可配、改动即生效、每次变动留痕（可回滚单位）。"""

    def test_baseline_change_records_and_applies(self, store: Store,
                                                 confidence: ConfidenceModel) -> None:
        change = confidence.set_baseline(ConfidenceBaseline(user_stated=0.7, inferred=0.3))
        assert change is not None
        assert change.config_id == BASELINE_CONFIG_ID
        assert change.old_value == {"user_stated": 0.9, "inferred": 0.5}
        assert change.new_value == {"user_stated": 0.7, "inferred": 0.3}
        assert f"memory-policy-change/{change.change_id}.json" in store.list_files("execution_log")
        assert BASELINE_PATH in store.list_files("config")
        assert confidence.baseline("user_stated") == 0.7

    def test_dynamics_change_records_and_applies(self, store: Store,
                                                 confidence: ConfidenceModel) -> None:
        change = confidence.set_dynamics(ConfidenceDynamics(half_life_days=30.0))
        assert change is not None
        assert change.config_id == DYNAMICS_CONFIG_ID
        assert DYNAMICS_PATH in store.list_files("config")
        assert confidence.dynamics().half_life_days == 30.0

    def test_unchanged_value_leaves_no_trace(self, confidence: ConfidenceModel) -> None:
        assert confidence.set_dynamics(ConfidenceDynamics()) is None
        assert confidence.changes() == ()

    def test_baseline_order_is_enforced(self) -> None:
        with pytest.raises(MemoryValidationError, match="user_stated > inferred"):
            checked_baseline(user_stated=0.3, inferred=0.5)

    def test_unknown_decayed_type_is_rejected(self) -> None:
        with pytest.raises(MemoryValidationError, match="未知的记忆维度"):
            checked_dynamics(decayed_types=("mood",))

    def test_corrupt_entry_falls_back_to_default(self, store: Store,
                                                 confidence: ConfidenceModel) -> None:
        store.put("config", BASELINE_PATH, b"{ not json")
        assert confidence.baseline("user_stated") == pytest.approx(
            ConfidenceBaseline().user_stated)


class TestGwt6SlicesCarryEffectiveAndStoredConfidence:
    """GWT-6：切片携有效置信度，并另携节点存储值（衰减可见、可追溯）。"""

    def test_slice_confidence_is_effective(self, graph: MemoryGraph, writer: MemoryWriter) -> None:
        old = node("thesis", confidence=0.9)
        writer.add_node(old)
        reader = MemoryReader(graph, now=NOW + timedelta(days=180))

        result = reader.query(SliceQuery(task_type="chat", view="list", token_budget=None))

        assert result.envelope.status == "ok"
        slice_ = next(s for s in result.slices if s.node.memory_node_id == old.memory_node_id)
        assert slice_.base_confidence == 0.9
        assert slice_.confidence == pytest.approx(0.45)

    def test_slice_of_a_fresh_node_shows_no_decay(self, graph: MemoryGraph,
                                                  writer: MemoryWriter) -> None:
        old = node("thesis", confidence=0.9)
        writer.add_node(old)
        reader = MemoryReader(graph, now=NOW)
        result = reader.query(SliceQuery(view="list", token_budget=None))
        slice_ = next(s for s in result.slices if s.node.memory_node_id == old.memory_node_id)
        assert slice_.confidence == slice_.base_confidence == 0.9
