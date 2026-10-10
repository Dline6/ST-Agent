"""记忆区图谱面在**真组合根**上的取数（[`T-UI-014.1`]）。

与 `tests/ui/test_ui_memory_graph.py`（替身钉形状）分工：本组用 [`build_m5_runtime`](../../src/st_agent/app.py)
造出的真门面走一遍真链路——真 `MemoryWriter` 落节点 → 真 `MemoryReader` 三视图 →
真回环面出描述。三条断言对应本叶三处要害：

1. **三视图共享同一查询层**（[04 §3.1]）：同 `topic` / `task_type` 下三视图的节点集合**相同**，
   只换排序；
2. **图谱视图去重边**：同一条边被两端的切片各带一次，出描述时按 `(source, target, relation)`
   去重；
3. **画像卡 ≤5 标签**：造 7 个 `identity` 节点，出 5 条 + 显式收窄提示（不静默丢弃）。

两枚端点均为**只读**。
"""

from __future__ import annotations

import http.client
import json
from datetime import datetime

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.l2.memory import MemoryEdge, checked_node, new_node_id
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

_EPOCH = "2026-10-10T10:00:00+08:00"
_NOW = datetime.fromisoformat(_EPOCH)


def _thesis(subject: str, view: str, *, confidence: float = 0.8):
    """一个 `thesis` 节点（`subject` 为个股 `stock_id`，`view` 为观点内容）。"""
    epoch = "2026-10-10T10:00:00+08:00"
    return checked_node(
        type="thesis",
        memory_node_id=new_node_id(),
        confidence=confidence,
        source="user_stated",
        privacy_level="private",
        created_at=epoch,
        updated_at=epoch,
        subject=subject,
        subject_kind="stock",
        view=view,
    )


def _identity(risk: str):
    epoch = "2026-10-10T10:00:00+08:00"
    return checked_node(
        type="identity",
        memory_node_id=new_node_id(),
        confidence=0.9,
        source="user_stated",
        privacy_level="private",
        created_at=epoch,
        updated_at=epoch,
        risk_preference=risk,
    )


def _get(running, path: str) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request("GET", path, headers={TOKEN_HEADER: running.token})
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


@pytest.fixture()
def m5(tmp_path):
    return seeded_m5(tmp_path / ROOT_NAME)


def _seed_two_theses(rig):
    a = _thesis("sh.600000", "估值处于历史低位")
    b = _thesis("sz.000001", "零售转型尚未验证", confidence=0.3)
    writer = rig.m5.m1.writer
    writer.add_node(a)
    writer.add_node(b)
    return a, b


def test_three_views_share_one_query_layer(m5) -> None:
    """三视图走同一条查询层——候选集相同、只换排序（[04 §3.1]）。"""
    _seed_two_theses(m5)
    with serve(dev=False, graph=m5.m5.graph) as running:
        ids = {}
        for view in ("graph", "list", "timeline"):
            payload = _get(running, f"/api/memory/browse?view={view}")
            assert payload["status"] == "ok"
            ids[view] = {node_id_of(entry) for entry in _node_ids(payload, view)}
    assert ids["graph"] == ids["list"] == ids["timeline"]
    assert len(ids["graph"]) == 2


def _node_ids(payload: dict, view: str) -> list[dict]:
    description = payload["data"]
    slots = description["slots"]
    if view == "graph":
        return slots["nodes"]
    if view == "timeline":
        return slots["entries"]
    return slots["rows"]


def node_id_of(entry: dict) -> str:
    return entry.get("id") or entry.get("label")


def test_graph_view_dedupes_edges(m5) -> None:
    """图谱视图的边按 `(from, to, relation)` 去重（同一条边被两端切片各带一次）。"""
    a, b = _seed_two_theses(m5)
    m5.m5.m1.writer.add_edge(
        MemoryEdge(
            source_id=a.memory_node_id,
            target_id=b.memory_node_id,
            edge_type="related_to",
            created_at=_NOW,
        )
    )
    with serve(dev=False, graph=m5.m5.graph) as running:
        payload = _get(running, "/api/memory/browse?view=graph")
    description = payload["data"]
    assert description["component_type"] == "graph_view"
    edges = description["slots"]["edges"]
    assert len(edges) == 1
    assert edges[0]["from"] == a.memory_node_id


def test_profile_card_caps_at_five_tags(m5) -> None:
    """画像卡 ≤5 标签；超出显式收窄（不静默丢弃）。"""
    writer = m5.m5.m1.writer
    for risk in ("偏稳健", "偏激进", "中性", "长期持有", "短线", "价值", "成长"):
        writer.add_node(_identity(risk))
    with serve(dev=False, graph=m5.m5.graph) as running:
        payload = _get(running, "/api/memory/profile")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "report_card"
    lines = description["slots"]["sections"][0]["lines"]
    assert len(lines) == 5
    hint = description["slots"]["sections"][1]["lines"][0]
    assert "未显示" in hint and "2" in hint


def test_empty_memory_is_empty_not_ok(m5) -> None:
    """空记忆给 `empty` + 原因，**不**静默留空（[01 §5]）。"""
    with serve(dev=False, graph=m5.m5.graph) as running:
        payload = _get(running, "/api/memory/browse?view=list")
    assert payload["status"] == "empty"
    assert payload["reason"]


def test_unknown_view_is_validation_failed(m5) -> None:
    """不认识的视图名给 `validation_failed`（不 500、不静默给空表）。"""
    with serve(dev=False, graph=m5.m5.graph) as running:
        payload = _get(running, "/api/memory/browse?view=nope")
    assert payload["status"] == "validation_failed"


def test_absent_face_is_unavailable(m5) -> None:
    """面未接线 ⇒ `unavailable` + 点名（与「接了但无数据」两回事）。"""
    with serve(dev=False) as running:
        payload = _get(running, "/api/memory/browse?view=list")
    assert payload["status"] == "unavailable"
    assert "记忆图谱面" in payload["reason"]


# ───────────────────── 节点详情 / 修正历史（[T-UI-014.2]） ─────────────────────


def _post(running, path: str, body: dict) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request(
            "POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={TOKEN_HEADER: running.token, "Content-Type": "application/json"},
        )
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


def test_node_detail_and_revisions_on_the_real_root(m5) -> None:
    """真修正历史：`edit_node` 落 RevisionEntry → 详情出 `report_card`、修正历史出 `timeline_view`。"""
    a, _b = _seed_two_theses(m5)
    m5.m5.m1.writer.edit_node(
        a.memory_node_id,
        changes={"view": "目标价由 9.5 改为 8.8"},
        confirmed_by="user",
        reason="依据最新财报",
        now=_NOW,
    )
    with serve(dev=False, graph=m5.m5.graph) as running:
        detail = _get(running, f"/api/memory/node?id={a.memory_node_id}")
        revisions = _get(running, f"/api/memory/node/revisions?id={a.memory_node_id}")
        # 格式合法但**不存在**的节点 id（`mn_` + 20 位十六进制；[04 §1] 的 id 形态）。
        missing = _get(running, "/api/memory/node?id=mn_00000000000000000000")
    assert detail["status"] == "ok"
    assert detail["data"]["component_type"] == "report_card"
    assert revisions["status"] == "ok"
    entries = revisions["data"]["slots"]["entries"]
    assert len(entries) == 1 and entries[0]["kind"] == "correction"
    assert entries[0]["at"] == "2026-10-10"
    assert "previous" in entries[0]
    assert missing["status"] == "unavailable"


# ───────────────────── 导入导出与 Onboarding（[T-UI-014.3]） ─────────────────────


def test_export_plan_and_confirmation_on_the_real_root(m5) -> None:
    """真 `MemoryShare`：计划出清单；未确认即拒、确认后产出。"""
    from st_agent.l2.memory import checked_node, new_node_id

    epoch = "2026-10-10T10:00:00+08:00"
    m5.m5.m1.writer.add_node(checked_node(
        type="thesis", memory_node_id=new_node_id(), confidence=0.8, source="user_stated",
        privacy_level="public", created_at=epoch, updated_at=epoch,
        subject="sh.600000", subject_kind="stock", view="可公开的观点",
    ))
    with serve(dev=False, graph=m5.m5.graph) as running:
        plan = _get(running, "/api/memory/export/plan")
        refused = _post(running, "/api/memory/export", {"confirmed_by": ""})
        exported = _post(running, "/api/memory/export", {"confirmed_by": "user"})
    assert plan["status"] == "ok"
    assert plan["data"]["component_type"] == "report_card"
    assert refused["status"] == "validation_failed"
    assert exported["status"] == "ok"
    assert exported["data"]["node_count"] == 1


def test_onboarding_on_the_real_root(m5) -> None:
    """真 `OnboardingProtocol`：空画像时出问题清单，提交后落初始画像。"""
    with serve(dev=False, graph=m5.m5.graph) as running:
        before = _get(running, "/api/memory/onboarding")
        submitted = _post(
            running,
            "/api/memory/onboarding",
            {"answers": {"risk_preference": "偏稳健", "investing_years": "10 年"}},
        )
        after = _get(running, "/api/memory/onboarding")
    assert before["status"] == "ok"
    assert before["data"]["component_type"] == "report_card"
    assert submitted["status"] == "ok"
    assert submitted["data"]["created_count"] >= 1
    assert after["status"] == "ok"
