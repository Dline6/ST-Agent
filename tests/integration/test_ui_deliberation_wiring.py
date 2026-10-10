"""推理区在**真 M5 组合根**上的取数与写面（[`T-UI-016.1`] / [`.2`] / [`.3`]）。

与 `tests/ui/test_ui_deliberation.py`（替身钉形状）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 造出的门面走一遍真链路——

1. **阵容读面**出真 `LensRoster` 的 7 内置视角（`setting_panel`、面键 `deliberation-lens`）；
2. **一次编排**（`deep`）出逐视角进度 + **真** `divergence_map`（矩阵 / 网络两视图 +
   服务端下发的立场词表），且矩阵行的链锚点 ⊆ 逐视角段的锚点、链里**真有步骤**；
3. **自定义视角**经真 `LensRoster` 落盘 → 视角定义读面出 `report_card`；
4. **决策沉淀**走真 `Deliberation.record_decision` 落 L2 `history` 节点，并经读面取回；
5. **`POST /api/chat` 的 `analyze` 去向**随返回携 `traces` 键（结论卡「推理链」入口的取值面）。

离线可跑：真 Store / 真 L1 技能目录 / 真 L2–L4 编排件，只有 LLM 出网那一跳是脚本替身（`rig_m5`）。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.l3.intent import IntentDraft
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

ANALYZE_QUERY = IntentDraft(
    intent="analyze", target=None, understood={"topic": "是否关注中际旭创"}
)
"""`analyze` 意图草案（无 target Skill 的意图——派发目标是整条 L4 流程，[05 §3.2]）。

**主题经 `understood` 流入确认卡取值**（同 `rig_m2` 的口径）：`AnalyzeService` 从确认卡的
`values["topic"]` 取主题，而 `analyze` 的意图级参数声明只有 `mode`——故主题须由理解产物带上。
"""

RULES = [("研判", ANALYZE_QUERY), ("多视角", ANALYZE_QUERY)]

_BUILTIN_COUNT = 7


def _request(running, method: str, path: str, body: dict | None = None) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=30)
    try:
        headers = {TOKEN_HEADER: running.token}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


def _get(running, path: str) -> dict:
    return _request(running, "GET", path)


def _post(running, path: str, body: dict) -> dict:
    return _request(running, "POST", path, body)


@pytest.fixture()
def m5(tmp_path):
    return seeded_m5(tmp_path / ROOT_NAME, rules=RULES)


def test_lens_roster_on_the_real_root(m5) -> None:
    """GWT-5：真阵容出 7 内置视角，内置只给停用动作（不摆必然失败的删除）。"""
    runtime = m5.m5
    with serve(dev=False, deliberation=runtime.deliberation) as running:
        payload = _get(running, "/api/deliberation/lenses")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "setting_panel"
    assert description["slots"]["surface"] == "deliberation-lens"
    entries = description["slots"]["entries"]
    assert len(entries) == _BUILTIN_COUNT
    assert all(entry["actions"] == ["disable"] for entry in entries)
    assert {entry["identifier"] for entry in entries} == {
        "机会视角", "风险视角", "基本面视角", "情绪视角", "流动性视角", "宏观视角", "合规视角",
    }


def test_deep_analysis_on_the_real_root(m5) -> None:
    """GWT-2：一次 `deep` 编排——逐视角进度 + 分歧图两视图，全部参与视角照常成行。"""
    runtime = m5.m5
    with serve(dev=False, deliberation=runtime.deliberation) as running:
        payload = _post(
            running, "/api/deliberation/analyze",
            {"topic": "是否关注中际旭创", "mode": "deep"},
        )

    assert payload["reply"]["status"] == "ok", payload["reply"]

    progress = payload["progress"]["data"]
    assert progress["component_type"] == "report_card"
    lines = "\n".join(
        line for section in progress["slots"]["sections"] for line in section["lines"]
    )
    assert "视角" in lines and "信心度" in lines
    assert len(progress["slots"]["sections"][0]["lines"]) >= _BUILTIN_COUNT, (
        "逐视角状态**并列**——每个参与视角至少一行（不合并）"
    )

    divergence = payload["divergence"]["data"]
    assert divergence["component_type"] == "divergence_map"
    slots = divergence["slots"]
    assert len(slots["matrix"]) == _BUILTIN_COUNT
    assert "network" in slots and "nodes" in slots["network"]
    assert slots["stance_labels"], "立场词表由服务端下发（前端不自带词表）"


def test_matrix_anchors_are_expandable_traces(m5) -> None:
    """GWT-4：矩阵行的链锚点 ⊆ 逐视角段的锚点，且链里**真有步骤**（追问接口的取值面）。"""
    runtime = m5.m5
    with serve(dev=False, deliberation=runtime.deliberation) as running:
        payload = _post(
            running, "/api/deliberation/analyze", {"topic": "是否关注中际旭创", "mode": "deep"}
        )
    matrix_anchors = {row["trace_id"] for row in payload["divergence"]["data"]["slots"]["matrix"]}
    by_anchor = {entry["trace_id"]: entry for entry in payload["lenses"]}
    assert matrix_anchors and matrix_anchors <= set(by_anchor)
    for anchor in matrix_anchors:
        entry = by_anchor[anchor]
        assert entry["opinion"]["data"]["component_type"] == "report_card"
        assert entry["trace"]["data"]["component_type"] == "trace_timeline"
        assert entry["trace"]["data"]["slots"]["steps"], "链须有步骤（真编排产出的链）"


def test_custom_lens_round_trip_on_the_real_root(m5) -> None:
    """GWT-4（`.1`）：自定义视角经真 `LensRoster` 落盘，且视角定义读面能取回。"""
    runtime = m5.m5
    with serve(dev=False, deliberation=runtime.deliberation) as running:
        created = _post(
            running, "/api/deliberation/lenses",
            {"name": "供给节奏视角", "description": "评估上游供给与交付节奏",
             "skill_bundle": ["sk_data_aggregate_v1.0"],
             "judging_criteria": {"natural": "以上游供给与交付节奏为准"},
             "confidence_policy": {"high_at": 0.8, "medium_at": 0.5}},
        )
        assert created["status"] == "ok", created
        lens_id = created["data"]["lens_id"]
        described = _get(running, f"/api/deliberation/lens?id={lens_id}")
        entries = _get(running, "/api/deliberation/lenses")["data"]["slots"]["entries"]

    assert described["status"] == "ok"
    assert described["data"]["component_type"] == "report_card"
    body = json.dumps(described["data"]["slots"]["sections"], ensure_ascii=False)
    assert "供给节奏视角" in body and "sk_data_aggregate_v1.0" in body
    assert any(entry["params"]["lens_id"] == lens_id for entry in entries)


def test_decision_is_written_to_memory_and_read_back(m5) -> None:
    """GWT-3 / GWT-4（`.3`）：决策经真 `record_decision` 落 L2 `history`，读面取回。"""
    runtime = m5.m5
    with serve(dev=False, deliberation=runtime.deliberation) as running:
        empty = _get(running, "/api/deliberation/decisions")
        written = _post(
            running, "/api/deliberation/decision",
            {"decision": "不关注", "reasoning": "风险信号偏多",
             "adopted_lens_ids": ["lens_x"], "ignored_lens_ids": ["lens_y"]},
        )
        listed = _get(running, "/api/deliberation/decisions")

    assert empty["status"] == "empty", "未留痕时如实报空（不静默留空）"
    assert written["status"] == "ok", written
    node_id = written["data"]["memory_node_id"]
    assert node_id

    assert listed["status"] == "ok"
    sections = listed["data"]["slots"]["sections"]
    body = json.dumps(sections, ensure_ascii=False)
    assert "不关注" in body and "风险信号偏多" in body
    assert "lens_x" in body and "lens_y" in body, "采纳 / 忽略视角**并列**留痕（未采纳者不静默丢弃）"


def test_chat_carries_the_lens_traces_for_the_trace_entry(m5) -> None:
    """GWT-1（`.3`）：`analyze` 去向的 `/api/chat` 返程携 `traces` 键（结论卡「推理链」入口）。"""
    runtime = m5.m5
    with serve(dev=False, chat=runtime.chat) as running:
        posted = _post(running, "/api/chat", {"action": "post", "text": "研判 是否关注中际旭创"})
        assert posted["status"] == "ok", posted
        assert posted["needs_confirmation"] is True
        assert posted["traces"] == [], "非 analyze 去向的返程不带链（键恒在、值为空表）"

        dispatched = _post(
            running, "/api/chat",
            {"action": "dispatch", "session_id": posted["session_id"]},
        )

    assert dispatched["status"] == "ok", dispatched
    # `description` 是**信封载荷**（`data` 里才是描述本体）——同 `POST /api/chat` 的既有口径。
    assert dispatched["description"]["data"]["component_type"] == "divergence_map"
    traces = dispatched["traces"]
    assert traces, "analyze 去向须随返回携各视角链"
    for entry in traces:
        assert entry["lens_id"] and entry["trace_id"]
        assert entry["description"]["data"]["component_type"] == "trace_timeline"
