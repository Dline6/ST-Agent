"""T-L2-003 · 记忆删除与审计（04 §6；GWT-1..5）。"""

from __future__ import annotations

from datetime import datetime

import pytest
from memory_helpers import NOW, edge, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    DELETE_PREFIX,
    MemoryDeleter,
    MemoryGraph,
    MemoryNotFoundError,
    MemoryValidationError,
    MemoryWriter,
)

#: 既有 execution_log 留痕（删除不得动它，§6）。
EXISTING_LOG = "skill-run/run_old.json"
EXISTING_BYTES = b'{"skill_id": "stock-watch", "status": "ok"}'


def derived(child: str, parent: str):
    """`derived_from` 边（派生方 → 依据方；见任务 A1）。"""
    return edge("derived_from", child, parent)


def snapshot(graph: MemoryGraph) -> tuple[frozenset[str], frozenset[str]]:
    """图谱当前态（节点 id 集 + 边键集），用于「零变更 / 不误伤」比对。"""
    return (
        frozenset(n.memory_node_id for n in graph.nodes()),
        frozenset(f"{e.source_id}__{e.edge_type}__{e.target_id}" for e in graph.edges()),
    )


class TestGwt1CascadeRemovesDerivedChain:
    """GWT-1：删根 → 派生它的下游一并移除，相接的边不留悬空。"""

    def test_history_and_its_pattern_go_together(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history = node("history")
        pattern = node("pattern")
        for n in (history, pattern):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))

        outcome = deleter.delete(history.memory_node_id, confirmed_by="user")

        assert {n.type for n in outcome.nodes} == {"history", "pattern"}
        assert graph.nodes() == ()
        assert graph.edges() == ()

    def test_no_dangling_edge_is_left_behind(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history, pattern, thesis = node("history"), node("pattern"), node("thesis")
        for n in (history, pattern, thesis):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))
        graph.add_edge(edge("related_to", thesis.memory_node_id, history.memory_node_id))

        deleter.delete(history.memory_node_id, confirmed_by="user")

        live = set(n.memory_node_id for n in graph.nodes())
        assert all(e.source_id in live for e in graph.edges())
        assert all(e.target_id in live for e in graph.edges())

    def test_edge_between_two_survivors_stays(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history, pattern, thesis, attention = (
            node("history"), node("pattern"), node("thesis"), node("attention"))
        for n in (history, pattern, thesis, attention):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))
        kept = graph.add_edge(edge("related_to", thesis.memory_node_id,
                                   attention.memory_node_id))

        deleter.delete(history.memory_node_id, confirmed_by="user")

        assert graph.edges() == (kept,)


class TestGwt2UnrelatedEntitiesAreUntouched:
    """GWT-2：删除范围限于被删节点与其相接边，无关者逐条原样保留。"""

    def test_survivors_are_byte_identical(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history, pattern = node("history"), node("pattern")
        other_a, other_b = node("thesis"), node("attention")
        for n in (history, pattern, other_a, other_b):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))
        unrelated = graph.add_edge(edge("related_to", other_a.memory_node_id,
                                        other_b.memory_node_id))

        outcome = deleter.delete(history.memory_node_id, confirmed_by="user")

        assert {n.memory_node_id for n in graph.nodes()} == {
            other_a.memory_node_id, other_b.memory_node_id}
        assert graph.edges() == (unrelated,)
        assert {n.memory_node_id for n in outcome.nodes} == {
            history.memory_node_id, pattern.memory_node_id}


class TestGwt3AuditTrailKeepsExistingLogs:
    """GWT-3：既有 execution_log 记录逐字节不变，删除动作另留一条可查记录。"""

    @pytest.fixture()
    def seeded(self, store: Store) -> Store:
        store.put("execution_log", EXISTING_LOG, EXISTING_BYTES)
        return store

    def test_existing_record_is_not_touched(
        self, store: Store, graph: MemoryGraph, deleter: MemoryDeleter, seeded: Store
    ) -> None:
        history = node("history")
        graph.put_node(history)

        deleter.delete(history.memory_node_id, confirmed_by="user")

        assert store.get("execution_log", EXISTING_LOG) == EXISTING_BYTES

    def test_deletion_record_carries_snapshots_and_reason(
        self, store: Store, graph: MemoryGraph, deleter: MemoryDeleter, seeded: Store
    ) -> None:
        history, pattern = node("history"), node("pattern")
        for n in (history, pattern):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))

        outcome = deleter.delete(history.memory_node_id, confirmed_by="user",
                                 reason="用户要求删除这条历史")

        record = outcome.record
        assert f"{DELETE_PREFIX}{record.delete_id}.json" in store.list_files("execution_log")
        assert record.root_node_id == history.memory_node_id
        assert set(record.node_ids) == {history.memory_node_id, pattern.memory_node_id}
        assert [e["edge_type"] for e in record.edges] == ["derived_from"]
        assert record.deleted_at == NOW
        assert record.reason == "用户要求删除这条历史"

    def test_deletion_is_lookupable_by_node(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history, pattern, bystander = node("history"), node("pattern"), node("thesis")
        for n in (history, pattern, bystander):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))

        deleter.delete(history.memory_node_id, confirmed_by="user")

        records = deleter.records()
        assert len(records) == 1
        # 根与级联成员都可被查到；无关节点查不到
        assert deleter.record_for(history.memory_node_id) == records
        assert deleter.record_for(pattern.memory_node_id) == records
        assert deleter.record_for(bystander.memory_node_id) == ()


class TestGwt4FailureIsExplicitAndChangesNothing:
    """GWT-4：未声明用户确认 / 目标不存在 / 入参非法 → 显式抛错且图谱零变更。"""

    def test_requires_user_confirmation(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history = node("history")
        graph.put_node(history)
        before = snapshot(graph)

        with pytest.raises(MemoryValidationError):
            deleter.delete(history.memory_node_id, confirmed_by="agent")

        assert snapshot(graph) == before
        assert deleter.records() == ()

    def test_missing_node_raises(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history, pattern = node("history"), node("pattern")
        for n in (history, pattern):
            graph.put_node(n)
        graph.add_edge(derived(pattern.memory_node_id, history.memory_node_id))
        before = snapshot(graph)

        with pytest.raises(MemoryNotFoundError):
            deleter.delete(node("thesis").memory_node_id, confirmed_by="user")

        assert snapshot(graph) == before
        assert deleter.records() == ()

    def test_malformed_id_fails_with_layer_error(
        self, deleter: MemoryDeleter
    ) -> None:
        with pytest.raises(MemoryValidationError):
            deleter.delete("../../etc/passwd", confirmed_by="user")

    def test_naive_moment_is_rejected(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history = node("history")
        graph.put_node(history)

        with pytest.raises(MemoryValidationError):
            deleter.delete(history.memory_node_id, confirmed_by="user",
                           now=datetime(2026, 9, 27, 12, 0))

        assert graph.has_node(history.memory_node_id) is True


class TestGwt5RecursiveAndSharedDerivation:
    """GWT-5：多层链递归删净；菱形共享派生每节点只删一次、不死循环。"""

    def test_deep_chain_is_removed(self, graph: MemoryGraph, deleter: MemoryDeleter) -> None:
        history, mid, tail = node("history"), node("pattern"), node("pattern")
        for n in (history, mid, tail):
            graph.put_node(n)
        graph.add_edge(derived(mid.memory_node_id, history.memory_node_id))
        graph.add_edge(derived(tail.memory_node_id, mid.memory_node_id))

        outcome = deleter.delete(history.memory_node_id, confirmed_by="user")

        assert len(outcome.nodes) == 3
        assert graph.nodes() == ()

    def test_diamond_shaped_derivation_deletes_each_node_once(
        self, graph: MemoryGraph, deleter: MemoryDeleter
    ) -> None:
        history = node("history")
        left, right, top = node("pattern"), node("pattern"), node("pattern")
        for n in (history, left, right, top):
            graph.put_node(n)
        graph.add_edge(derived(left.memory_node_id, history.memory_node_id))
        graph.add_edge(derived(right.memory_node_id, history.memory_node_id))
        graph.add_edge(derived(top.memory_node_id, left.memory_node_id))
        graph.add_edge(derived(top.memory_node_id, right.memory_node_id))

        outcome = deleter.delete(history.memory_node_id, confirmed_by="user")

        ids = outcome.record.node_ids
        assert len(ids) == len(set(ids)) == 4
        assert graph.nodes() == ()


class TestGraphDeletePrimitives:
    """存储层原语：只删记录、不存在即错（语义入口是 MemoryDeleter）。"""

    def test_delete_missing_node_raises(self, graph: MemoryGraph) -> None:
        with pytest.raises(MemoryNotFoundError):
            graph.delete_node(node("thesis").memory_node_id)

    def test_delete_missing_edge_raises(self, graph: MemoryGraph) -> None:
        a, b = node("thesis"), node("attention")
        for n in (a, b):
            graph.put_node(n)
        removed = edge("related_to", a.memory_node_id, b.memory_node_id)

        with pytest.raises(MemoryNotFoundError):
            graph.delete_edge(removed)

    def test_delete_edge_then_node(self, graph: MemoryGraph) -> None:
        a, b = node("thesis"), node("attention")
        for n in (a, b):
            graph.put_node(n)
        graph.add_edge(edge("related_to", a.memory_node_id, b.memory_node_id))

        graph.delete_edge(edge("related_to", a.memory_node_id, b.memory_node_id))
        graph.delete_node(a.memory_node_id)

        assert graph.has_node(a.memory_node_id) is False
        assert graph.edges() == ()


class TestRedLineShapes:
    """与 edit_node / resolve 同款：删除是「用户显式」路径，副驾无自主删除入口。"""

    def test_writer_and_deleter_agree_on_the_gate(
        self, graph: MemoryGraph, writer: MemoryWriter, deleter: MemoryDeleter
    ) -> None:
        history = node("history")
        writer.add_node(history)

        for call in (
            lambda: writer.edit_node(history.memory_node_id, changes={"event": "改"},
                                     confirmed_by="agent"),
            lambda: deleter.delete(history.memory_node_id, confirmed_by="agent"),
        ):
            with pytest.raises(MemoryValidationError):
                call()

        assert graph.has_node(history.memory_node_id) is True
