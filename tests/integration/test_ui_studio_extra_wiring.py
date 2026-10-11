"""Studio 附加面在**真 M5 组合根**上的端到端（[`T-UI-015.3`]）。

与 `tests/ui/test_ui_studio_extra.py`（替身钉缺口分流 / 段形状 / 路由）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 走的四段真链路——

1. **模板库与 fork**——真 `TemplateLibrary` 的官方种子；fork 经**真** `DraftIntake.receive`
   落成画布会话（与主动提案**同一条**路径），`StudioHandoff.opened()` 里能看到它；
2. **接受即落盘**——真 `WorkflowStore.save`：工作流进 `config` 分区，清单与属性面板读回；
3. **真试跑**——真 `TrialRunner` + 真 `SkillRunner.for_trials()`（**不带输出复用登记**）：
   起跑 / 单步 / 跑到结束，逐节点快照与试跑历史都读得到；
4. **版本动作**——激活 / 回滚经真 `WorkflowStore` 的激活指针。

离线可跑：真 Store / 真官方 Pack 与官方模板 / 真 L1–L6，只有 LLM 出网那一跳是脚本替身
（`rig_m5`）；模板里前两个 Skill 在本组补装确定性执行件（`rig_m5` 已装后两个）。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

RULES: list[tuple[str, object]] = []

TEMPLATE_ID = "daily_st_brief"
"""官方模板「每日 ST 简报」（4 节点、3 连线、1 分组、cron 调度）。"""

SESSION_KEY = f"tpl:{TEMPLATE_ID}"
"""模板 fork 交画布会话的键（与提案来源共用 `StudioHandoff` 的会话表）。"""


def _register_missing_executors(m5) -> None:
    """给模板里 `rig_m5` 未装执行件的两个官方 base 补上确定性执行件（真 L1 流水线）。"""
    runner = m5.m5.m1.runtime.runner
    for base in ("sk_st_list_sync", "sk_delisting_risk_scan"):
        runner.register_executor(
            f"{base}_v1.0", lambda ctx, params: ResultEnvelope.ok({"step": "ok"})
        )


def _request(running, method: str, path: str, body: dict | None = None) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=60)
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
    rig = seeded_m5(tmp_path / ROOT_NAME, rules=RULES)
    _register_missing_executors(rig)
    return rig


def _serving(m5):
    """起回环面：Studio 面 ＋ 反思面（fork 出的会话要经后者列出）。"""
    return serve(dev=False, studio=m5.m5.studio, reflection=m5.m5.reflection)


def _sections(envelope: dict) -> dict[str, list[str]]:
    return {item["title"]: item["lines"] for item in envelope["data"]["slots"]["sections"]}


def test_the_template_library_lists_the_official_seeds(m5) -> None:
    """GWT-1：模板库出真种子；空状态出**后端给的**一句话入口（不是前端硬编码）。"""
    with _serving(m5) as running:
        payload = _get(running, "/api/studio/templates")
    assert payload["home"]["status"] == "ok", payload["home"].get("reason")
    home = _sections(payload["home"])
    assert home["工作流"] == ["尚未创建任何工作流"]
    assert home["一句话入口"] == ["用一句话描述你想要的工作流"]

    assert payload["templates"]["status"] == "ok"
    rows = payload["templates"]["data"]["slots"]["rows"]
    assert TEMPLATE_ID in [row["template_id"] for row in rows]


def test_the_preview_carries_the_unpublished_identity(m5) -> None:
    with _serving(m5) as running:
        payload = _get(running, f"/api/studio/template?id={TEMPLATE_ID}")
    assert payload["status"] == "ok"
    sections = _sections(payload)
    assert any("v0.0" in line for line in sections["基本信息"])
    assert len(sections["节点"]) == 4
    assert len(sections["连线"]) == 3


def test_fork_opens_a_real_canvas_session_and_accepting_lands_it(m5) -> None:
    """GWT-1 / [03 §4]：fork 只在「接受」落盘——fork 后**先**不落盘，接受后才进工作流清单。"""
    with _serving(m5) as running:
        forked = _post(running, "/api/studio/template/fork", {"template_id": TEMPLATE_ID})
        assert forked["status"] == "ok", forked.get("reason")
        assert forked["data"]["session_key"] == SESSION_KEY
        assert forked["data"]["node_count"] == 4

        # fork 只是**开了会话**：工作流还没落盘（[03 §4] 编辑期一律不落盘）
        listed = _get(running, "/api/studio/workflows")
        assert listed["status"] == "empty"

        # 会话真在交接面里（与主动提案同一张表）
        sessions = _get(running, "/api/studio/sessions")
        assert sessions["status"] == "ok"
        assert SESSION_KEY in [item["proposal_id"] for item in sessions["data"]["sessions"]]

        # 「接受」才是落盘点：经真 DraftIntake → WorkflowStore.save
        accepted = m5.m5.l6.studio.accept(SESSION_KEY)
        assert accepted.change_id

        after = _get(running, "/api/studio/workflows")
        assert after["status"] == "ok"
        rows = after["data"]["slots"]["rows"]
        assert [row["flow_id"] for row in rows] == [accepted.flow_id]
        assert rows[0]["node_count"] == 4
        assert "wf_daily_st_brief" in accepted.flow_id


def test_the_property_panel_and_versions_read_the_landed_workflow(m5) -> None:
    with _serving(m5) as running:
        forked = _post(running, "/api/studio/template/fork", {"template_id": TEMPLATE_ID})
        flow_id = m5.m5.l6.studio.accept(forked["data"]["session_key"]).flow_id

        detail = _get(running, f"/api/studio/workflow?flow={flow_id}")
        assert detail["status"] == "ok", detail.get("reason")
        sections = _sections(detail)
        assert sections["调度"] == ["cron：0 8 * * *"]
        assert len(sections["节点"]) == 4
        # 接受落盘即激活首版（[03 §4]）——属性面板如实标出
        assert f"当前生效：{flow_id}" in sections["基本信息"]

        versions = _get(running, f"/api/studio/versions?flow={flow_id}")
        assert versions["versions"]["status"] == "ok"
        entries = versions["versions"]["data"]["slots"]["entries"]
        assert [item["id"] for item in entries] == [flow_id]
        assert "当前生效" in entries[0]["content"]
        assert versions["diff"]["status"] == "empty"


def test_a_real_trial_runs_step_by_step_and_leaves_history(m5) -> None:
    """GWT-2 / GWT-3：真试跑——单步 → 跑到结束，逐节点快照带**值来源**，历史可读回。"""
    with _serving(m5) as running:
        forked = _post(running, "/api/studio/template/fork", {"template_id": TEMPLATE_ID})
        flow_id = m5.m5.l6.studio.accept(forked["data"]["session_key"]).flow_id

        started = _post(running, "/api/studio/trial", {"action": "start", "flow_id": flow_id})
        trial_id = started["trial_id"]
        assert trial_id
        assert started["summary"]["status"] == "ok"
        assert "运行中" in " ".join(_sections(started["summary"])["试跑"])
        assert started["steps"]["data"]["slots"]["rows"] == []

        stepped = _post(running, "/api/studio/trial", {"action": "step", "trial_id": trial_id})
        rows = stepped["steps"]["data"]["slots"]["rows"]
        assert len(rows) == 1
        assert rows[0]["node_id"] == "list_sync"
        assert rows[0]["status"] == "完成"
        # 逐参数标注值来源（[03 §4]）——本节点参数是默认值
        assert rows[0]["sources"]

        finished = _post(
            running, "/api/studio/trial", {"action": "run_to_end", "trial_id": trial_id}
        )
        assert finished["summary"]["status"] == "ok"
        assert "已完成" in " ".join(_sections(finished["summary"])["试跑"])
        assert len(finished["steps"]["data"]["slots"]["rows"]) == 4
        # 完整链经真 `describe_trace` 出（非 ok 也如实出）
        assert finished["trace"]["status"] in ("ok", "empty")

        history = _get(running, f"/api/studio/trials?flow={flow_id}")
        assert history["trials"]["status"] == "ok"
        assert [row["trial_id"] for row in history["trials"]["data"]["slots"]["rows"]] == [trial_id]


def test_a_trial_session_does_not_survive_a_new_runtime(m5, tmp_path) -> None:
    """会话表是**进程内**的：换一个组合根实例，同一 `trial_id` 无可续跑的活对象。"""
    with _serving(m5) as running:
        forked = _post(running, "/api/studio/template/fork", {"template_id": TEMPLATE_ID})
        flow_id = m5.m5.l6.studio.accept(forked["data"]["session_key"]).flow_id
        trial_id = _post(
            running, "/api/studio/trial", {"action": "start", "flow_id": flow_id}
        )["trial_id"]

        other = seeded_m5(tmp_path / "second", rules=RULES)
        with serve(dev=False, studio=other.m5.studio) as second:
            payload = _post(
                second, "/api/studio/trial", {"action": "step", "trial_id": trial_id}
            )
        assert payload["summary"]["status"] == "unavailable"
        assert "会话表" in payload["summary"]["reason"]


def test_version_actions_move_the_active_pointer(m5) -> None:
    """GWT-4：激活经真 `WorkflowStore` 的激活指针；未知流标识即拒（不是 500）。"""
    with _serving(m5) as running:
        forked = _post(running, "/api/studio/template/fork", {"template_id": TEMPLATE_ID})
        flow_id = m5.m5.l6.studio.accept(forked["data"]["session_key"]).flow_id

        activated = _post(
            running, "/api/studio/workflow/version",
            {"flow_id": flow_id, "action": "activate"},
        )
        assert activated["status"] == "ok", activated.get("reason")
        assert activated["data"]["flow_id"] == flow_id

        detail = _get(running, f"/api/studio/workflow?flow={flow_id}")
        assert f"当前生效：{flow_id}" in _sections(detail)["基本信息"]

        unknown = _get(running, "/api/studio/workflow?flow=wf_absent_v1.0")
        assert unknown["status"] in ("validation_failed", "unavailable")
        assert unknown["reason"]
