"""T-L2-001.3 GWT-1 ~ GWT-4：MemoryReader 上下文切片查询（04 §3.1）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from memory_helpers import NOW, STOCK_ID, edge, node
from st_agent.l2.memory import MemoryGraph, MemoryReader, MemoryWriter, SliceQuery

DAY = timedelta(days=1)


def _seed(graph: MemoryGraph) -> dict[str, str]:
    """三个不同「相关度/时近度/连通度」的节点——用于让三种视图的序真正分岔。"""
    thesis = node("thesis", view="看好反转机会", confidence=0.9,
                  created_at=NOW - 30 * DAY, updated_at=NOW - 10 * DAY)
    attention = node("attention", holdings=(STOCK_ID,), confidence=0.5,
                     created_at=NOW - 30 * DAY, updated_at=NOW - 1 * DAY)
    history = node("history", event="上季度反转判断失误", confidence=0.7,
                   created_at=NOW - 30 * DAY, updated_at=NOW - 5 * DAY)
    for n in (thesis, attention, history):
        graph.put_node(n)
    graph.add_edge(edge("related_to", attention.memory_node_id, thesis.memory_node_id))
    graph.add_edge(edge("refers_to", attention.memory_node_id, STOCK_ID))
    return {"thesis": thesis.memory_node_id, "attention": attention.memory_node_id,
            "history": history.memory_node_id}


@pytest.fixture()
def seeded(graph: MemoryGraph) -> dict[str, str]:
    return _seed(graph)


@pytest.fixture()
def fixed_reader(graph: MemoryGraph, seeded: dict[str, str]) -> MemoryReader:
    """时刻可注入的 reader——时近度判据在用例里确定可复算。"""
    return MemoryReader(graph, now=NOW)


# ───────────────────────── GWT-1：切片查询 ─────────────────────────


def test_gwt1_returns_relevant_nodes_ranked(fixed_reader: MemoryReader) -> None:
    res = fixed_reader.query(SliceQuery(task_type="all", topic="反转", token_budget=None))
    assert res.envelope.status == "ok"
    assert len(res.slices) == 3
    # 主题命中 + 高置信的 thesis 排在未命中的 attention 之前
    order = [s.node.type for s in res.slices]
    assert order.index("thesis") < order.index("attention")


def test_gwt1_budget_truncates_without_exceeding(fixed_reader: MemoryReader) -> None:
    full = fixed_reader.query(SliceQuery(task_type="all", token_budget=None)).slices
    sizes = [len(s.model_dump_json()) for s in full]
    assert sum(sizes) > sizes[0] + 5, "用例前提：全部切片超出紧预算，截断才有意义"

    budget = sizes[0] + 5
    res = fixed_reader.query(SliceQuery(task_type="all", token_budget=budget))
    assert len(res.slices) == 1
    assert sum(len(s.model_dump_json()) for s in res.slices) <= budget


def test_gwt1_no_budget_returns_all(fixed_reader: MemoryReader) -> None:
    res = fixed_reader.query(SliceQuery(task_type="all", token_budget=None))
    assert len(res.slices) == 3


def test_gwt1_task_type_filters_candidates(fixed_reader: MemoryReader) -> None:
    # 本 rig 只有 thesis / attention / history：onboarding 亲和 identity+attention
    res = fixed_reader.query(SliceQuery(task_type="onboarding", token_budget=None))
    assert {s.node.type for s in res.slices} == {"attention"}
    only = fixed_reader.query(SliceQuery(task_type="reflection", token_budget=None))
    assert {s.node.type for s in only.slices} == {"history"}


# ───────────────────────── GWT-2：切片标注 ─────────────────────────


def test_gwt2_slice_carries_as_of_and_confidence(fixed_reader: MemoryReader,
                                                 graph: MemoryGraph,
                                                 seeded: dict[str, str]) -> None:
    res = fixed_reader.query(SliceQuery(task_type="all", token_budget=None))
    assert res.slices
    by_id = {s.node.memory_node_id: s for s in res.slices}
    for node_id, sl in by_id.items():
        stored = graph.get_node(node_id)
        assert sl.as_of == stored.updated_at
        assert sl.confidence == stored.confidence
        assert sl.source == stored.source
    assert res.as_of == max(s.as_of for s in res.slices)


def test_gwt2_slice_exposes_edges_for_graph_view(fixed_reader: MemoryReader,
                                                seeded: dict[str, str]) -> None:
    res = fixed_reader.query(SliceQuery(task_type="all", token_budget=None))
    by_id = {s.node.memory_node_id: s for s in res.slices}
    assert len(by_id[seeded["attention"]].edges) == 2
    assert by_id[seeded["history"]].edges == ()


# ───────────────────────── GWT-3：三视图同一查询层 ─────────────────────────


def test_gwt3_three_views_share_same_candidate_set(fixed_reader: MemoryReader) -> None:
    views = ["graph", "list", "timeline"]
    results = [fixed_reader.query(SliceQuery(task_type="all", topic="反转",
                                             token_budget=None, view=v))
               for v in views]
    sets = [{s.node.memory_node_id for s in r.slices} for r in results]
    assert sets[0] == sets[1] == sets[2], "三视图必须返回同一批节点（只是组织不同）"


def test_gwt3_views_order_differently(fixed_reader: MemoryReader,
                                      seeded: dict[str, str]) -> None:
    def ids(view: str) -> list[str]:
        return [s.node.memory_node_id
                for s in fixed_reader.query(SliceQuery(task_type="all", topic="反转",
                                                       token_budget=None, view=view)).slices]
    assert ids("list") != ids("timeline")
    assert ids("list") != ids("graph")
    assert ids("graph")[0] == seeded["attention"], "图谱视图按连通度优先"
    assert ids("timeline")[0] == seeded["attention"], "时间线按 updated_at 倒序"
    assert ids("list")[0] == seeded["thesis"], "列表按相关性优先"


# ───────────────────────── GWT-4：空结果语义 ─────────────────────────


def test_gwt4_empty_graph_returns_empty_envelope(reader: MemoryReader) -> None:
    res = reader.query(SliceQuery(task_type="all"))
    assert res.envelope.status == "empty"
    assert res.envelope.reason
    assert res.slices == ()
    assert res.as_of is None


def test_gwt4_no_affinity_match_returns_empty(graph: MemoryGraph,
                                             writer: MemoryWriter) -> None:
    """复盘域只看 history/pattern/evolution——图里只有 thesis/attention 即空，且带原因。"""
    writer.add_node(node("thesis"))
    writer.add_node(node("attention"))
    res = MemoryReader(graph, now=NOW).query(
        SliceQuery(task_type="reflection", token_budget=None))
    assert res.envelope.status == "empty"
    assert res.envelope.reason
    assert res.slices == ()


def test_gwt4_budget_too_small_returns_empty_with_reason(fixed_reader: MemoryReader) -> None:
    res = fixed_reader.query(SliceQuery(task_type="all", token_budget=1))
    assert res.envelope.status == "empty"
    assert "预算" in (res.envelope.reason or "")
    assert res.slices == ()


def test_gwt4_query_rejects_nonpositive_budget() -> None:
    with pytest.raises(ValidationError):
        SliceQuery(task_type="all", token_budget=0)
