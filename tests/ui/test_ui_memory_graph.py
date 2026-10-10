"""记忆区**图谱面**端点的形状与 fail-closed（[`T-UI-014.1`]）。

与 `tests/integration/test_ui_memory_graph.py`（真组合根）分工：本组用**替身**钉形状——

1. 三视图各出**对应组件型**（`graph` → `graph_view`、`list` → `table`、`timeline` → `timeline_view`）；
2. 图谱视图的边 / 节点装进对应槽，`labels` 走生成文案槽（系统词表不由前端自带，[D-064]）；
3. 画像卡 ≤5 标签且**截断如实报**（不静默丢弃）；
4. 面未接线 ⇒ `unavailable` + 点名；视图名不认识 ⇒ `validation_failed`；
5. `/memory` 页面登记不变（本叶**不新增路径**——[11-sitemap §2.2] 页内视图切换）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"

_LABELS = {
    "types": {"identity": "身份画像", "thesis": "观点"},
    "sources": {"user_stated": "用户明确表达"},
    "privacy": {"private": "私有"},
    "relations": {"related_to": "一般关联"},
}

_SLICES = [
    {
        "id": "mem_0001", "type": "thesis", "label": "估值处于历史低位",
        "confidence": 0.9, "effective_confidence": 0.8, "source": "user_stated",
        "privacy": "private", "as_of": "2026-10-10T10:00:00+08:00",
        "updated_at": "2026-10-10T10:00:00+08:00", "edges": [],
    },
]


class _FakeGraph:
    """图谱面替身（鸭子端口）：**只**回已定形态的取数口。"""

    def __init__(self, *, browse: dict | None = None, profile: dict | None = None) -> None:
        self._browse = browse if browse is not None else {
            "view": "graph", "slices": _SLICES, "total": len(_SLICES),
            "labels": _LABELS, "edges": [{"from": "mem_0002", "to": "mem_0001", "relation": "related_to"}],
        }
        self._profile = profile if profile is not None else {
            "tags": [{"label": "偏稳健", "kind": "identity", "source": "mem_0009"}],
            "total": 1, "limit": 5, "truncated": False, "labels": _LABELS,
        }
        self.exported: dict | None = None
        self.submitted: dict | None = None

    def browse(self, *, view: str, topic: str = "", task_type: str = "chat") -> dict:
        payload = dict(self._browse)
        payload["view"] = view
        return payload

    def profile(self) -> dict:
        return dict(self._profile)

    def node(self, *, node_id: str) -> dict | None:
        if node_id != "mem_0001":
            return None
        return {
            "node_id": "mem_0001", "type": "thesis", "label": "估值处于历史低位",
            "source": "user_stated", "privacy": "private",
            "created_at": "2026-10-10T10:00:00+08:00", "updated_at": "2026-10-10T10:00:00+08:00",
            "confidence": 0.9, "effective": False,
            "fields": {"view": "估值处于历史低位", "subject": "sh.600000"},
            "edges": [{"edge": {"from": "mem_0001", "to": "mem_0002", "relation": "related_to"},
                       "other": "mem_0002"}],
            "labels": _LABELS,
        }

    def revisions(self, *, node_id: str) -> dict | None:
        if node_id != "mem_0001":
            return None
        return {
            "node_id": "mem_0001", "label": "估值处于历史低位",
            "current": {"memory_node_id": "mem_0001", "type": "thesis"},
            "revisions": [
                {"replaced_at": "2026-10-10T09:00:00+08:00", "reason": "目标价由 9.5 改为 8.8",
                 "previous": {"memory_node_id": "mem_0001", "view": "目标价 9.5"}},
            ],
            "labels": _LABELS,
        }

    def export_plan(self) -> dict:
        return {"included": ["公开节点 2 个（identity / thesis）"], "excluded_nodes": 3, "node_count": 2}

    def export_memory(self, *, confirmed_by: str) -> dict:
        if confirmed_by != "user":
            raise ValueError("导出须经用户确认")
        self.exported = {"confirmed_by": confirmed_by}
        return {"node_ids": ["mem_0001"], "node_count": 1, "edge_count": 0}

    def onboarding_state(self) -> dict:
        return {
            "questions": [
                {"id": "risk_preference", "prompt": "你的风险偏好是？", "field": "risk_preference",
                 "node_type": "identity"},
            ],
            "empty_dimensions": ["risk_preference"],
            "has_profile": False,
        }

    def submit_onboarding(self, *, answers: dict) -> dict:
        self.submitted = dict(answers)
        return {"created": ["mem_0010"], "created_count": 1}


@pytest.fixture
def graph_server():
    def _start(facade):
        return serve(dev=False, graph=facade)

    return _start


def _payload(graph_server, http_get, auth, facade, path):
    with graph_server(facade) as running:
        return http_get(running, path, headers=auth(running)).json()


def test_graph_view_endpoint(graph_server, http_get, auth) -> None:
    """GWT-1：`view=graph` 出 `graph_view`，节点 / 边 / labels 三槽齐。"""
    payload = _payload(graph_server, http_get, auth, _FakeGraph(), "/api/memory/browse?view=graph")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "graph_view"
    slots = description["slots"]
    assert slots["nodes"][0]["id"] == "mem_0001"
    assert slots["nodes"][0]["type"] == "thesis"
    assert slots["edges"] == [{"from": "mem_0002", "to": "mem_0001", "relation": "related_to"}]
    assert slots["labels"]["types"]["thesis"] == "观点"


def test_list_view_endpoint(graph_server, http_get, auth) -> None:
    """GWT-1：`view=list` 出 `table`，类型 / 来源在描述层按文案表解析。"""
    payload = _payload(graph_server, http_get, auth, _FakeGraph(), "/api/memory/browse?view=list")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "table"
    columns = [column["key"] for column in description["slots"]["columns"]]
    assert "effective_confidence" in columns and "confidence" in columns
    row = description["slots"]["rows"][0]
    assert row["type"] == "观点" and row["source"] == "用户明确表达"


def test_timeline_view_endpoint(graph_server, http_get, auth) -> None:
    """GWT-1：`view=timeline` 出 `timeline_view`。"""
    payload = _payload(graph_server, http_get, auth, _FakeGraph(), "/api/memory/browse?view=timeline")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "timeline_view"
    entry = description["slots"]["entries"][0]
    assert entry["id"] == "mem_0001" and entry["content"] == "估值处于历史低位"


def test_profile_card(graph_server, http_get, auth) -> None:
    """GWT-2：画像卡出 `report_card`。"""
    payload = _payload(graph_server, http_get, auth, _FakeGraph(), "/api/memory/profile")
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "report_card"


def test_view_name_is_validated(graph_server, http_get, auth) -> None:
    """GWT-4：不认识的视图名 ⇒ `validation_failed`（不 500、不静默给空表）。"""
    payload = _payload(graph_server, http_get, auth, _FakeGraph(), "/api/memory/browse?view=nope")
    assert payload["status"] == "validation_failed"


def test_absent_face_is_unavailable(graph_server, http_get, auth) -> None:
    """GWT-4：面未接线 ⇒ `unavailable` + 点名。"""
    payload = _payload(graph_server, http_get, auth, None, "/api/memory/browse?view=list")
    assert payload["status"] == "unavailable"
    assert "记忆图谱面" in payload["reason"]


def test_memory_path_is_unchanged() -> None:
    """本叶**不新增路径**——记忆区五条屏全在 `/memory` 一条路径（[11-sitemap §2.2]）。"""
    assert "/memory" in PAGE_PATHS
    assert not any(path.startswith("/memory/") for path in PAGE_PATHS)


def test_graph_page_is_wired_in_frontend() -> None:
    """前端登记表里 `/memory` 有渲染件，且页面源码引用了三视图端点。"""
    source = (_UI / "web" / "js" / "pages" / "memory.js").read_text(encoding="utf-8")
    assert "/api/memory/browse" in source
    assert "/api/memory/profile" in source
    assert "'timeline'" in source and "'graph'" in source


# ───────────────────── 节点详情 / 修正历史（[T-UI-014.2]） ─────────────────────


def test_node_detail_endpoint(graph_server, http_get, auth) -> None:
    """GWT-1：`?node=<id>` 出 `report_card`，置信度标注为存储值。"""
    payload = _payload(
        graph_server, http_get, auth, _FakeGraph(), "/api/memory/node?id=mem_0001"
    )
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "report_card"
    titles = [section["title"] for section in description["slots"]["sections"]]
    assert titles == ["基本信息", "专属字段", "相接关系"]
    basic = description["slots"]["sections"][0]["lines"]
    assert any("存储值·非有效值" in line for line in basic)


def test_node_detail_without_id_is_validation_failed(graph_server, http_get, auth) -> None:
    """缺 `id` ⇒ `validation_failed`（不 500）。"""
    payload = _payload(graph_server, http_get, auth, _FakeGraph(), "/api/memory/node")
    assert payload["status"] == "validation_failed"


def test_unknown_node_is_unavailable(graph_server, http_get, auth) -> None:
    """GWT-3：不存在的节点 ⇒ `unavailable` + 点名（不静默空页）。"""
    payload = _payload(
        graph_server, http_get, auth, _FakeGraph(), "/api/memory/node?id=mem_9999"
    )
    assert payload["status"] == "unavailable"
    assert "mem_9999" in payload["reason"]


def test_revisions_endpoint(graph_server, http_get, auth) -> None:
    """GWT-2：`?node=<id>&view=revisions` 出 `timeline_view`，旧值保留。"""
    payload = _payload(
        graph_server, http_get, auth, _FakeGraph(),
        "/api/memory/node/revisions?id=mem_0001",
    )
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "timeline_view"
    entry = description["slots"]["entries"][0]
    assert entry["kind"] == "correction"
    assert "目标价" in entry["content"]
    assert "previous" in entry


# ───────────────────── 导入导出与 Onboarding（[T-UI-014.3]） ─────────────────────


def test_export_plan_endpoint(graph_server, http_get, auth) -> None:
    """GWT-1：导出计划出 `report_card`，含过滤节点数与确认门文案。"""
    payload = _payload(
        graph_server, http_get, auth, _FakeGraph(), "/api/memory/export/plan"
    )
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "report_card"
    titles = [section["title"] for section in description["slots"]["sections"]]
    assert "本次导出包含以下公开信息" in titles
    assert "确认门" in titles


def test_export_requires_confirmation(graph_server, http_post, auth) -> None:
    """GWT-2：未经确认（`confirmed_by != user`）⇒ `validation_failed`（不静默成功）。"""
    facade = _FakeGraph()
    with graph_server(facade) as running:
        response = http_post(
            running, "/api/memory/export", body={"confirmed_by": "nope"}, headers=auth(running)
        )
    payload = json.loads(response.body)
    assert payload["status"] == "validation_failed"
    assert facade.exported is None


def test_export_with_confirmation(graph_server, http_post, auth) -> None:
    """GWT-2：确认后落片段载荷。"""
    facade = _FakeGraph()
    with graph_server(facade) as running:
        response = http_post(
            running, "/api/memory/export", body={"confirmed_by": "user"}, headers=auth(running)
        )
    payload = json.loads(response.body)
    assert payload["status"] == "ok"
    assert payload["data"]["node_count"] == 1
    assert facade.exported == {"confirmed_by": "user"}


def test_onboarding_state_and_submit(graph_server, http_get, http_post, auth) -> None:
    """GWT-3/4：状态出问题清单与画像判据；提交落初始画像。"""
    facade = _FakeGraph()
    with graph_server(facade) as running:
        state = json.loads(http_get(running, "/api/memory/onboarding", headers=auth(running)).body)
        assert state["status"] == "ok"
        assert state["data"]["component_type"] == "report_card"

        submitted = json.loads(
            http_post(
                running,
                "/api/memory/onboarding",
                body={"answers": {"risk_preference": "偏稳健"}},
                headers=auth(running),
            ).body
        )
    assert submitted["status"] == "ok"
    assert submitted["data"]["created_count"] == 1
    assert facade.submitted == {"risk_preference": "偏稳健"}


def test_onboarding_submit_requires_answers(graph_server, http_post, auth) -> None:
    """`answers` 非对象 ⇒ `validation_failed`。"""
    with graph_server(_FakeGraph()) as running:
        response = http_post(
            running, "/api/memory/onboarding", body={"answers": []}, headers=auth(running)
        )
    assert json.loads(response.body)["status"] == "validation_failed"
