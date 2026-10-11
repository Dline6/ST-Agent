"""Skill 库（`/skills`）在**真 M5 组合根**上的端到端（[`T-UI-015.1`]）。

与 `tests/ui/test_ui_skills.py`（替身钉缺口分流 / 取值面 / 路由）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 造出的 [`SkillsFacade`] 走一遍真链路——

1. **清单读的是真注册表**——官方 Pack 的 12 个 Skill 版本逐条出表（[01 §2] 工具目录＝
   描述体投影，不新造第二份登记）；
2. **详情 / 参数面板读的是真描述体**——参数项逐条来自 `parameters` 声明；
3. **参数落值真落盘**——经 `SkillRegistry.set_parameters` 改默认值，再读回**同一值**
   （[03 §1.1]：描述体即参数的唯一真相源，无第二处）；
4. **执行日志读的是真留痕**——经真 [`SkillRunner`] 跑一次 Skill，`execution_log` 分区
   `skill-run/` 下真多出一条记录，日志面**按同一 `skill_run_id` 读回**（[03 §1.2] 步骤 5）。

离线可跑：真 Store / 真官方 Pack / 真 L1–L6，只有 LLM 出网那一跳是脚本替身（`rig_m5`）。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m5 import SK_A, seeded_m5

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

RULES: list[tuple[str, object]] = []

AGGREGATE = f"{SK_A}_v1.0"
"""真装配里装了确定性执行件的官方 base（`sk_data_aggregate_v1.0`，见 `rig_m5`）。"""

RISK_ALERT = "sk_risk_alert_v1.0"
"""未装执行件、但**已注册**的官方 base——供「注册着但没执行过」一类用例。"""


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
    return seeded_m5(tmp_path / ROOT_NAME, rules=RULES)


def _serving(m5, **extra):
    return serve(dev=False, skills=m5.m5.skills, **extra)


def _param_entry(payload: dict, name: str) -> dict:
    entries = payload["data"]["slots"]["entries"]
    return next(item for item in entries if item["identifier"] == name)


def test_the_catalog_lists_the_real_official_pack(m5) -> None:
    """GWT-1：清单出的是**真注册表**的内容（官方 Pack 12 个 base 各一版）。"""
    with _serving(m5) as running:
        payload = _get(running, "/api/skills/catalog")
    assert payload["status"] == "ok", payload.get("reason")
    assert payload["data"]["component_type"] == "table"
    rows = payload["data"]["slots"]["rows"]
    ids = {row["skill_id"] for row in rows}
    assert AGGREGATE in ids and RISK_ALERT in ids
    assert len(ids) >= 12
    aggregate = next(row for row in rows if row["skill_id"] == AGGREGATE)
    assert aggregate["source"] == "官方预置"
    assert aggregate["offline_level"] == "完全离线可用"
    assert aggregate["parameter_count"] == 1


def test_the_detail_reads_the_real_descriptor(m5) -> None:
    """GWT-1：详情五段取自真描述体；官方 Pack 的 `permissions=()` **如实**出一段空集。"""
    with _serving(m5) as running:
        payload = _get(running, f"/api/skills/detail?skill={AGGREGATE}")
    assert payload["status"] == "ok", payload.get("reason")
    sections = {item["title"]: item["lines"] for item in payload["data"]["slots"]["sections"]}
    assert sections["权限声明"] == ["（无权限声明）"]
    assert sections["依赖"] == ["（无依赖）"]
    assert any("format" in line for line in sections["可配参数"])
    assert sections["输入 / 输出契约"][0] == "输入：query"


def test_a_real_default_is_changed_and_read_back(m5) -> None:
    """GWT-2：参数改值经唯一写口真落盘，**再读回同一值**（刷新后仍为新值）。"""
    with _serving(m5) as running:
        before = _get(running, f"/api/skills/params?skill={AGGREGATE}")
        assert before["status"] == "ok"
        assert _param_entry(before, "format")["current"] == "brief"
        assert _param_entry(before, "format")["options"] == [
            {"value": "table"}, {"value": "trend"}, {"value": "brief"},
        ]

        applied = _post(
            running, "/api/skills/params",
            {"skill_id": AGGREGATE, "name": "format", "value": "trend"},
        )
        assert applied["status"] == "ok", applied.get("reason")
        assert applied["data"] == {"applied": True}

        after = _get(running, f"/api/skills/params?skill={AGGREGATE}")
        assert _param_entry(after, "format")["current"] == "trend"


def test_an_out_of_range_value_is_refused_and_the_default_is_untouched(m5) -> None:
    """GWT-2 的边界：超范围 ⇒ `validation_failed`（不允许保存），落值面逐字不变。"""
    with _serving(m5) as running:
        bad = _post(
            running, "/api/skills/params",
            {"skill_id": RISK_ALERT, "name": "lookahead_days", "value": "9999"},
        )
        assert bad["status"] == "validation_failed"
        assert bad["reason"]

        now = _get(running, f"/api/skills/params?skill={RISK_ALERT}")
        assert _param_entry(now, "lookahead_days")["current"] == "30"


def test_the_log_reads_back_a_real_run(m5) -> None:
    """GWT-3：真跑一次 Skill ⇒ `skill-run/` 真多一条，日志面按同一 `skill_run_id` 读回。"""
    outcome = m5.m5.m1.runtime.runner.run(AGGREGATE)
    assert outcome.skill_run_id

    with _serving(m5) as running:
        payload = _get(running, f"/api/skills/logs?skill={AGGREGATE}")
    assert payload["status"] == "ok", payload.get("reason")
    rows = payload["data"]["slots"]["rows"]
    assert outcome.skill_run_id in [row["skill_run_id"] for row in rows]
    row = next(item for item in rows if item["skill_run_id"] == outcome.skill_run_id)
    assert row["status"] == "完成"
    assert "format" in row["params"]


def test_a_registered_skill_that_never_ran_reads_as_empty(m5) -> None:
    """GWT-3 的空态：注册着但没执行过 ⇒ `empty` ＋ 原因（不是 `ok` 空表、不是 `unavailable`）。"""
    with _serving(m5) as running:
        payload = _get(running, f"/api/skills/logs?skill={RISK_ALERT}")
        assert payload["status"] == "empty"
        assert payload["reason"]
        # 同一时刻，面本身是通的——`empty` 说的是「没有留痕」，不是「面没接线」
        assert _get(running, "/api/skills/status")["status"] == "ok"


def test_unknown_and_malformed_identifiers_do_not_impersonate_each_other(m5) -> None:
    """GWT-4：形态非法 ⇒ `validation_failed`；形态合法但未注册 ⇒ `unavailable` ＋ 点名。"""
    with _serving(m5) as running:
        malformed = _get(running, "/api/skills/detail?skill=nope")
        assert malformed["status"] == "validation_failed"
        unknown = _get(running, "/api/skills/detail?skill=sk_absent_v1.0")
        assert unknown["status"] == "unavailable"
        assert unknown["reason"]
