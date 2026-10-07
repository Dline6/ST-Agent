"""演进面的表现层入口在**真装配**下端到端（[`T-UI-004.3`]）。

`tests/ui/test_ui_evolution.py` 用替身钉信封与描述形态；本组用 M4 关卡的真组合根经**真回环面**
走一遍：变更历史读得到、一键回滚真的回放了旧值、档位切换真的经配置门面生效并留痕、
出厂重置的三次确认是硬门。顺带钉住与 [`.2`](../ui/) 的提案处置面的**互锁**（先有变更，才有可回滚项）。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m4 import seeded_m4

from st_agent.l6 import TIER_CONFIG_ID
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

PROPOSAL = {
    "config_id": TIER_CONFIG_ID,
    "current": "collaborative",
    "suggested": "manual",
    "reason": "先把档位收一档，观察一周再放",
    "trace_ref": "",
}


@pytest.fixture()
def running(tmp_path: Path):
    rig = seeded_m4(tmp_path / ROOT_NAME)
    with serve(dev=False, reflection=rig.m4.reflection) as ui:
        yield ui


def _call(running, method: str, path: str, body: dict | None = None):  # noqa: ANN001, ANN201
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    headers = {TOKEN_HEADER: running.token}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def _make_a_change(running) -> str:  # noqa: ANN001
    """先经提案处置面产生一条变更（两个面互锁：没有变更就没有可回滚项）。"""
    status, payload = _call(running, "POST", "/api/reflection/proposals/decide",
                            {"action": "accept", "proposal": PROPOSAL})
    assert status == 200 and payload["status"] == "ok"
    assert payload["data"]["applied"] is True
    return payload["data"]["change"]["change_id"]


def test_changes_endpoint_is_empty_until_something_changed(running) -> None:
    _, payload = _call(running, "GET", "/api/evolution/changes")
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "change_timeline"
    assert payload["data"]["slots"]["changes"] == []


def test_rollback_endpoint_replays_the_previous_value(running) -> None:
    change_id = _make_a_change(running)

    _, before = _call(running, "GET", "/api/evolution/changes")
    listed = before["data"]["slots"]["changes"]
    assert [item["change_id"] for item in listed] == [change_id]
    assert listed[0]["actions"] == ["rollback"]
    assert listed[0]["new_value"] == "manual"

    status, rolled = _call(running, "POST", "/api/evolution/changes/rollback",
                           {"change_id": change_id})
    assert status == 200 and rolled["status"] == "ok"
    assert rolled["data"]["applied"] is True

    _, after = _call(running, "GET", "/api/evolution/changes")
    listed = after["data"]["slots"]["changes"]
    assert len(listed) == 2, "回滚本身也是一次变更（新 change_id、原记录保留）"
    assert listed[0]["rolled_back"] is True and listed[0]["actions"] == []
    assert listed[-1]["config_id"] == TIER_CONFIG_ID

    _, auth = _call(running, "GET", "/api/evolution/authorization")
    tier_entry = auth["data"]["slots"]["entries"][0]
    assert tier_entry["current"] == "collaborative"          # 取值真的回到了变更前


def test_authorization_panel_switches_the_tier_through_the_registry(running) -> None:
    _, read = _call(running, "GET", "/api/evolution/authorization")
    assert read["data"]["component_type"] == "setting_panel"
    tier_entry, grading_entry = read["data"]["slots"]["entries"]
    assert tier_entry["current"] == "collaborative"
    assert tier_entry["actions"] == ["set"]
    assert grading_entry["actions"] == []                    # 对象类条目只呈现

    status, applied = _call(
        running, "POST", "/api/evolution/authorization",
        {"config_id": TIER_CONFIG_ID, "value": "autonomous"},
    )
    assert status == 200 and applied["status"] == "ok"
    assert applied["data"]["changed"] is True

    _, again = _call(running, "GET", "/api/evolution/authorization")
    assert again["data"]["slots"]["entries"][0]["current"] == "autonomous"


def test_unknown_setting_entry_is_refused(running) -> None:
    status, payload = _call(
        running, "POST", "/api/evolution/authorization",
        {"config_id": "no.such-entry", "value": 1},
    )
    assert status == 200
    assert payload["status"] == "validation_failed"
    assert "不认这个条目" in payload["reason"]


def test_factory_reset_requires_three_confirmations(running) -> None:
    _make_a_change(running)

    _, refused = _call(running, "POST", "/api/evolution/factory-reset", {"confirmations": 2})
    assert refused["status"] == "validation_failed"
    assert "3 次确认" in refused["reason"]
    _, still_there = _call(running, "GET", "/api/evolution/changes")
    assert len(still_there["data"]["slots"]["changes"]) == 1, "不足三次 ⇒ 一步都不做"

    status, reset = _call(running, "POST", "/api/evolution/factory-reset", {"confirmations": 3})
    assert status == 200 and reset["status"] == "ok"
    assert reset["data"]["performed"] is True
    assert len(reset["data"]["replayed"]) == 1, "重置＝逆序回放演进变更（逐条留痕）"


def test_missing_confirmation_count_is_refused(running) -> None:
    _, payload = _call(running, "POST", "/api/evolution/factory-reset", {})
    assert payload["status"] == "validation_failed"


def test_evolution_endpoints_require_the_token(running) -> None:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request("GET", "/api/evolution/changes")
        assert conn.getresponse().status == 401
    finally:
        conn.close()
