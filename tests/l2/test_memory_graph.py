"""T-L2-001.1 GWT-4 ~ GWT-5：图谱落盘与图不变量（04 §1 / §2）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_helpers import PASS, STOCK_ID, edge, node
from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    EDGE_PREFIX,
    NODE_PREFIX,
    MemoryConflictError,
    MemoryGraph,
    MemoryNotFoundError,
    MemoryValidationError,
)


# ───────────────────────── GWT-4：落盘往返 ─────────────────────────


def test_gwt4_node_and_edge_roundtrip_across_reopen(store: Store, store_root: Path) -> None:
    graph = MemoryGraph(store)
    thesis = node("thesis")
    attention = node("attention")
    graph.put_node(thesis)
    graph.put_node(attention)
    graph.add_edge(edge("related_to", thesis.memory_node_id, attention.memory_node_id))

    reopened = MemoryGraph(Store.open(store_root, PASS))
    assert reopened.get_node(thesis.memory_node_id) == thesis
    assert reopened.get_node(attention.memory_node_id) == attention
    assert reopened.edges() == graph.edges()
    assert reopened.edges_of(thesis.memory_node_id) == graph.edges()


def test_gwt4_memory_partition_holds_only_memory_payload(store: Store) -> None:
    """§1/§2 的落盘面：`memory` 分区内只有本层的节点 / 边文件。"""
    graph = MemoryGraph(store)
    a, b = node("thesis"), node("attention")
    graph.put_node(a)
    graph.put_node(b)
    graph.add_edge(edge("refers_to", a.memory_node_id, STOCK_ID))

    files = store.list_files("memory")
    assert files, "落盘后分区不应为空"
    assert all(p.startswith(NODE_PREFIX) or p.startswith(EDGE_PREFIX) for p in files)
    assert MemoryGraph.node_path(a.memory_node_id) in files
    assert any(p.startswith(EDGE_PREFIX) for p in files)


def test_gwt4_one_file_per_entity(store: Store) -> None:
    graph = MemoryGraph(store)
    ids = [node("thesis").memory_node_id for _ in range(3)]
    for nid in ids:
        graph.put_node(node("thesis", memory_node_id=nid))
    nodes = [p for p in store.list_files("memory") if p.startswith(NODE_PREFIX)]
    assert len(nodes) == 3


def test_gwt4_nodes_returned_in_id_order(store: Store) -> None:
    graph = MemoryGraph(store)
    ids = sorted(node("history").memory_node_id for _ in range(3))
    for nid in reversed(ids):
        graph.put_node(node("history", memory_node_id=nid))
    assert [n.memory_node_id for n in graph.nodes()] == ids


# ───────────────────────── GWT-5：图不变量 ─────────────────────────


def test_gwt5_edge_with_missing_source_rejected(store: Store) -> None:
    graph = MemoryGraph(store)
    orphan = node("thesis")
    with pytest.raises(MemoryNotFoundError):
        graph.add_edge(edge("related_to", orphan.memory_node_id, "mn_" + "0" * 20))


def test_gwt5_internal_edge_with_missing_target_rejected(store: Store) -> None:
    graph = MemoryGraph(store)
    src = node("thesis")
    graph.put_node(src)
    with pytest.raises(MemoryNotFoundError):
        graph.add_edge(edge("related_to", src.memory_node_id, "mn_" + "1" * 20))


def test_gwt5_refers_to_external_target_needs_no_node(store: Store) -> None:
    """外端是外部 ID，L2 不为外部实体建影子节点（任务 `A3`）。"""
    graph = MemoryGraph(store)
    src = node("thesis")
    graph.put_node(src)
    assert graph.add_edge(edge("refers_to", src.memory_node_id, STOCK_ID)).target_id == STOCK_ID
    assert len(graph.edges()) == 1


def test_gwt5_duplicate_edge_is_idempotent(store: Store) -> None:
    graph = MemoryGraph(store)
    a, b = node("thesis"), node("attention")
    graph.put_node(a)
    graph.put_node(b)
    e = edge("related_to", a.memory_node_id, b.memory_node_id)
    first = graph.add_edge(e)
    second = graph.add_edge(e)
    assert first == second
    assert len(graph.edges()) == 1


def test_gwt5_put_node_rejects_existing_id(store: Store) -> None:
    """既有节点不得经新增路径覆盖（04 §4「不存在任何静默覆盖」）。"""
    graph = MemoryGraph(store)
    n = node("thesis")
    graph.put_node(n)
    with pytest.raises(MemoryConflictError):
        graph.put_node(node("thesis", memory_node_id=n.memory_node_id))


def test_gwt5_replace_node_rejects_missing_id(store: Store) -> None:
    """编辑不得悄悄变成新增。"""
    graph = MemoryGraph(store)
    with pytest.raises(MemoryNotFoundError):
        graph.replace_node(node("thesis"))


def test_gwt5_get_node_missing_raises(store: Store) -> None:
    graph = MemoryGraph(store)
    with pytest.raises(MemoryNotFoundError):
        graph.get_node(node("thesis").memory_node_id)
    assert graph.has_node(node("thesis").memory_node_id) is False


def test_gwt5_edges_of_covers_both_directions(store: Store) -> None:
    graph = MemoryGraph(store)
    a, b, c = node("thesis"), node("attention"), node("history")
    for n in (a, b, c):
        graph.put_node(n)
    graph.add_edge(edge("related_to", a.memory_node_id, b.memory_node_id))
    graph.add_edge(edge("derived_from", c.memory_node_id, a.memory_node_id))
    assert len(graph.edges_of(a.memory_node_id)) == 2
    assert len(graph.edges_of(b.memory_node_id)) == 1


@pytest.mark.parametrize("bad", ["not-an-id", "../../etc/passwd", "mn_ZZ"])
def test_gwt5_malformed_id_fails_with_layer_error(store: Store, bad: str) -> None:
    """畸形 id 一律得本层错误类型——不把 Store 的路径校验错误漏到调用方。"""
    graph = MemoryGraph(store)
    with pytest.raises(MemoryValidationError):
        graph.get_node(bad)
    with pytest.raises(MemoryValidationError):
        graph.has_node(bad)
    with pytest.raises(MemoryValidationError):
        graph.edges_of(bad)
