"""T-L2-001.2 GWT-1 ~ GWT-4：MemoryWriter 写入接口（04 §3.2）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from memory_helpers import NOW, TRACE_ID, node
from st_agent.l2.memory import (
    MemoryConflictError,
    MemoryGraph,
    MemoryNode,
    MemoryValidationError,
    MemoryWriter,
    Provenance,
)

LATER = NOW + timedelta(hours=1)


# ───────────────────────── GWT-1：用户显式路径直写 ─────────────────────────


def test_gwt1_user_stated_written_directly(graph: MemoryGraph,
                                           writer: MemoryWriter) -> None:
    stated = node("identity", source="user_stated", risk_preference="稳健型")
    writer.add_node(stated)
    stored = graph.get_node(stated.memory_node_id)
    assert stored == stated
    assert stored.source == "user_stated"
    assert stored.provenance is None


# ───────────────────────── GWT-2：系统推断路径留据 ─────────────────────────


def test_gwt2_inferred_written_with_trace(graph: MemoryGraph,
                                          writer: MemoryWriter) -> None:
    inferred = node("pattern", source="inferred", provenance=Provenance(trace_id=TRACE_ID))
    writer.add_node(inferred)
    stored = graph.get_node(inferred.memory_node_id)
    assert stored.source == "inferred"
    assert stored.provenance is not None
    assert stored.provenance.trace_id == TRACE_ID


def test_gwt2_inferred_without_trace_is_unrepresentable() -> None:
    """§1 不变量：无 trace 的推断节点根本构造不出来（写入面因此无从接收）。"""
    with pytest.raises(MemoryValidationError):
        node("pattern", source="inferred")


# ───────────────────────── GWT-3：编辑必留修正历史 ─────────────────────────


def test_gwt3_edit_appends_revision_history(graph: MemoryGraph,
                                            writer: MemoryWriter) -> None:
    original = node("thesis", view="看好反转", confidence=0.8)
    writer.add_node(original)

    updated = writer.edit_node(
        original.memory_node_id,
        changes={"view": "观点转为中性", "confidence": 0.5},
        confirmed_by="user",
        reason="用户更正",
        now=LATER,
    )

    assert updated.view == "观点转为中性"
    assert updated.confidence == 0.5
    assert updated.updated_at == LATER
    assert updated.created_at == original.created_at          # 创建时刻不变
    assert updated.memory_node_id == original.memory_node_id  # ID 不变（01 §1）
    assert len(updated.revision_history) == 1
    entry = updated.revision_history[0]
    assert entry.previous["view"] == "看好反转"                # 旧值保留供审计
    assert entry.previous["confidence"] == 0.8
    assert entry.replaced_at == LATER
    assert entry.reason == "用户更正"
    # 落盘的是改后版本
    assert graph.get_node(original.memory_node_id) == updated


def test_gwt3_second_edit_stacks_history(graph: MemoryGraph,
                                         writer: MemoryWriter) -> None:
    n = node("identity", risk_preference="稳健型")
    writer.add_node(n)
    writer.edit_node(n.memory_node_id, changes={"risk_preference": "平衡型"},
                     confirmed_by="user", now=LATER)
    twice = writer.edit_node(n.memory_node_id, changes={"risk_preference": "进取型"},
                             confirmed_by="user", now=LATER + timedelta(hours=1))
    assert [e.previous["risk_preference"] for e in twice.revision_history] == ["稳健型", "平衡型"]
    assert twice.risk_preference == "进取型"


# ───────────────────────── GWT-4：红线（推断面不得改既有节点） ─────────────────────────


def test_gwt4_add_node_cannot_overwrite_existing(writer: MemoryWriter) -> None:
    """推断类写入的唯一动词是新增，而新增撞既有 id 即冲突——无法「借新增改既有」。"""
    n = node("thesis")
    writer.add_node(n)
    with pytest.raises(MemoryConflictError):
        writer.add_node(node("thesis", memory_node_id=n.memory_node_id, view="改过的观点"))


def test_gwt4_edit_requires_user_confirmation(writer: MemoryWriter, graph: MemoryGraph) -> None:
    n = node("thesis")
    writer.add_node(n)
    for bad in ("system", "inferred", "", "User"):
        with pytest.raises(MemoryValidationError):
            writer.edit_node(n.memory_node_id, changes={"view": "x"}, confirmed_by=bad)
    assert graph.get_node(n.memory_node_id).view == "看好反转"    # 未被改动


@pytest.mark.parametrize("field", ["memory_node_id", "type", "created_at",
                                   "updated_at", "revision_history"])
def test_gwt4_immutable_fields_rejected(writer: MemoryWriter, field: str) -> None:
    n = node("thesis")
    writer.add_node(n)
    with pytest.raises(MemoryValidationError):
        writer.edit_node(n.memory_node_id, changes={field: "x"}, confirmed_by="user")


def test_gwt4_unknown_field_rejected(writer: MemoryWriter) -> None:
    """未知名段一律拒绝，不静默丢弃（静默丢弃会让人以为改成了）。"""
    n = node("thesis")
    writer.add_node(n)
    with pytest.raises(MemoryValidationError):
        writer.edit_node(n.memory_node_id, changes={"deep_thought": 42}, confirmed_by="user")


def test_gwt4_public_write_surface_is_exactly_three_verbs() -> None:
    """API 形状即红线：写入面只有「新增 / 编辑（须用户确认）/ 加边」三个动词。

    这条钉子防的是**悄悄长出一个推断类可调用的修改入口**——一旦新增公开方法，
    本用例失败，迫使改动者重新面对 §3.2 红线。
    """
    public = {n for n in dir(MemoryWriter) if not n.startswith("_")}
    assert public == {"add_node", "add_edge", "edit_node"}


def test_gwt4_writer_returns_persisted_node_type(writer: MemoryWriter) -> None:
    assert isinstance(writer.add_node(node("history")), MemoryNode)
