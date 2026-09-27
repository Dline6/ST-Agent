"""T-L2-001.1 GWT-1 ~ GWT-3：节点/边本体（04 §1 / §2）。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from memory_helpers import ANNOUNCEMENT_ID, NOW, STOCK_ID, TRACE_ID, edge, node, node_types
from st_agent.contracts.identifiers import MemoryNodeId
from st_agent.l2.memory import (
    NODE_TYPES,
    EdgeTypeName,
    MemoryValidationError,
    Provenance,
    checked_edge,
    checked_node,
    edge_key,
    new_node_id,
    parse_node,
)

GENERAL_FIELDS = (
    "memory_node_id", "confidence", "source", "provenance",
    "privacy_level", "revision_history", "created_at", "updated_at",
)

#: §1 表逐类专属字段（GWT-1 的判据）。
SPEC_OWN_FIELDS: dict[str, set[str]] = {
    "identity": {"risk_preference", "capital_scale", "investing_years",
                 "cognitive_bias_self_eval"},
    "attention": {"holdings", "watchlist", "sector_preferences", "theme_interests"},
    "thesis": {"subject", "subject_kind", "view", "stated_at"},
    "history": {"event", "feedback_id", "outcome"},
    "pattern": {"pattern", "triggered_by"},
    "evolution": {"dimension", "trajectory"},
}

#: 把某类的**全部**专属字段清空（验「空节点无意义」）。
EMPTY_OWN: dict[str, dict] = {
    "identity": dict.fromkeys(SPEC_OWN_FIELDS["identity"]),
    "attention": {f: () for f in SPEC_OWN_FIELDS["attention"]},
    "thesis": dict.fromkeys(SPEC_OWN_FIELDS["thesis"]),
    "history": dict.fromkeys(SPEC_OWN_FIELDS["history"]),
    "pattern": {"pattern": "   ", "triggered_by": ()},
    "evolution": {"dimension": None, "trajectory": ()},
}


# ───────────────────────── GWT-1：六类节点齐备 ─────────────────────────


@pytest.mark.parametrize("kind", node_types())
def test_gwt1_every_type_has_spec_fields_and_general_fields(kind: str) -> None:
    built = node(kind)
    assert built.type == kind
    fields = set(type(built).model_fields)
    assert SPEC_OWN_FIELDS[kind] <= fields, f"{kind} 缺专属字段"
    assert set(GENERAL_FIELDS) <= fields, f"{kind} 缺通用字段"


def test_gwt1_six_types_exactly() -> None:
    assert NODE_TYPES == ("identity", "attention", "thesis", "history",
                          "pattern", "evolution")


@pytest.mark.parametrize("kind", node_types())
def test_gwt1_empty_own_fields_rejected(kind: str) -> None:
    """空节点（只有通用字段）无意义——「某维度无记忆」表达为没有该节点（§7）。"""
    with pytest.raises(MemoryValidationError):
        node(kind, **EMPTY_OWN[kind])


# ───────────────────────── GWT-2：通用字段不变量 ─────────────────────────


def test_gwt2_node_id_format_and_roundtrip() -> None:
    built = node("thesis")
    assert MemoryNodeId.of(built.memory_node_id).value == built.memory_node_id
    assert parse_node(built.model_dump_json().encode("utf-8")) == built


def test_gwt2_bad_node_id_rejected() -> None:
    with pytest.raises(MemoryValidationError):
        node("thesis", memory_node_id="mn_zzz")


def test_gwt2_inferred_requires_trace() -> None:
    with pytest.raises(MemoryValidationError):
        node("thesis", source="inferred")
    ok = node("thesis", source="inferred", provenance=Provenance(trace_id=TRACE_ID))
    assert ok.provenance is not None and ok.provenance.trace_id == TRACE_ID


def test_gwt2_user_stated_must_not_carry_provenance() -> None:
    with pytest.raises(MemoryValidationError):
        node("thesis", provenance=Provenance(trace_id=TRACE_ID))


@pytest.mark.parametrize("bad", [
    {"privacy_level": "secret"},
    {"confidence": 1.5},
    {"confidence": -0.1},
    {"created_at": datetime(2026, 9, 27, 12, 0)},        # 无时区
    {"updated_at": NOW - timedelta(days=1)},             # 早于 created_at
])
def test_gwt2_shape_violations_rejected(bad: dict) -> None:
    with pytest.raises(MemoryValidationError):
        node("thesis", **bad)


def test_gwt2_unknown_type_rejected() -> None:
    with pytest.raises(MemoryValidationError):
        checked_node(type="mood", memory_node_id=new_node_id(), confidence=0.5,
                     source="user_stated", privacy_level="public",
                     created_at=NOW, updated_at=NOW, risk_preference="x")


def test_gwt2_id_typed_own_fields_are_shape_checked() -> None:
    """§1 里以 ID 形态出现的专属字段，同样受契约形态约束。"""
    with pytest.raises(MemoryValidationError):
        node("history", feedback_id="not-a-feedback-id")
    ok = node("history", feedback_id="fb_" + "a" * 20)
    assert ok.feedback_id == "fb_" + "a" * 20
    with pytest.raises(MemoryValidationError):
        node("attention", holdings=("600000",))
    with pytest.raises(MemoryValidationError):
        node("pattern", triggered_by=("not-a-node-id",))


# ───────────────────────── GWT-3：四类边齐备 ─────────────────────────


def test_gwt3_internal_edges_take_node_ids_both_ends() -> None:
    a, b = new_node_id(), new_node_id()
    for kind in ("related_to", "evolves_from", "derived_from"):
        built = edge(kind, a, b)
        assert built.edge_type == kind
        assert edge_key(built) == f"{a}__{kind}__{b}"


def test_gwt3_refers_to_accepts_external_ids() -> None:
    src = new_node_id()
    for target in (STOCK_ID, ANNOUNCEMENT_ID, new_node_id()):
        built = checked_edge(edge_type="refers_to", source_id=src, target_id=target,
                             created_at=NOW)
        assert built.target_id == target


@pytest.mark.parametrize("bad", [
    {"edge_type": "depends_on"},
    {"source_id": "not-an-id"},
    {"target_id": "not-an-id"},                       # related_to 内端非法
])
def test_gwt3_edge_shape_violations_rejected(bad: dict) -> None:
    fields = {"edge_type": "related_to", "source_id": new_node_id(),
              "target_id": new_node_id(), "created_at": NOW, **bad}
    with pytest.raises(MemoryValidationError):
        checked_edge(**fields)


def test_gwt3_refers_to_rejects_unknown_target() -> None:
    with pytest.raises(MemoryValidationError):
        checked_edge(edge_type="refers_to", source_id=new_node_id(),
                     target_id="not-a-known-id", created_at=NOW)


def test_gwt3_edge_key_is_deterministic_and_direction_sensitive() -> None:
    a, b = new_node_id(), new_node_id()
    fwd = edge("related_to", a, b)
    rev = edge("related_to", b, a)
    assert edge_key(fwd) == edge_key(fwd)
    assert edge_key(fwd) != edge_key(rev)


def test_edge_type_literal_matches_spec() -> None:
    """§2 四类边的机器可读口径（防枚举悄悄漂移）。"""
    assert set(EdgeTypeName.__args__) == {"related_to", "evolves_from",
                                          "derived_from", "refers_to"}


def test_checked_node_wraps_pydantic_error() -> None:
    """非法构造抛本层错误类型，不冒裸 pydantic 异常。"""
    with pytest.raises(MemoryValidationError):
        checked_node(type="thesis", memory_node_id=new_node_id())
