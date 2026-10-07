"""演进面的表现层入口（[`T-UI-004.3`]）。

本组钉 ui 侧的四件事：变更时间线的描述形态（含「已回滚的条目不再给回滚动作」）· 授权设置
面板的形态（档位可改、风险分级只呈现）· 写面只转发并如实出站 · 面未注入即 fail-closed。
真链路的装配由 `tests/integration/test_ui_evolution_wiring.py` 覆盖。
"""

from __future__ import annotations

import pytest

from st_agent.ui.server import serve

TIER_ID = "evolution.authorization"
GRADING_ID = "evolution.risk-grading"

CHANGES = [
    {
        "change_id": "chg_" + "1" * 20, "seq": 1, "config_id": TIER_ID,
        "old_value": "collaborative", "new_value": "manual", "reason": "先收一档",
        "trace_ref": "", "source": "weekly-report", "tier": "collaborative",
        "applied_at": "2026-10-11T20:00:00+08:00", "rolled_back_at": None,
        "rollback_change_id": None,
    },
    {
        "change_id": "chg_" + "2" * 20, "seq": 2, "config_id": TIER_ID,
        "old_value": "manual", "new_value": "collaborative", "reason": "回滚",
        "trace_ref": "", "source": "rollback", "tier": "collaborative",
        "applied_at": "2026-10-11T21:00:00+08:00",
        "rolled_back_at": "2026-10-11T21:00:00+08:00",     # 这条本身已被回滚
        "rollback_change_id": "chg_" + "3" * 20,
    },
]

AUTH = {
    "available": True,
    "tier": "collaborative",
    "tiers": ["manual", "collaborative", "autonomous"],
    "grading": [{"prefix": "memory.", "risk_class": "never-autonomous"}],
    "risk_classes": ["autonomous-ok", "collaborative-required", "never-autonomous"],
    "default_tier": "collaborative",
    "tier_config_id": TIER_ID,
    "grading_config_id": GRADING_ID,
}


class FakeEvolution:
    """演进面适配面替身（`decide` 一类的输入类失败用抛 ``ValueError`` 表示）。"""

    def __init__(self, *, changes=CHANGES, auth=AUTH, rollback=None, applied=None, reset=None):
        self._changes = list(changes)
        self._auth = dict(auth)
        self._rollback = rollback if rollback is not None else {"applied": True, "note": "已回放"}
        self._applied = applied if applied is not None else {"available": True, "changed": True}
        self._reset = reset if reset is not None else {"performed": True, "replayed": 2}
        self.calls: list[tuple] = []

    def changes(self):
        return list(self._changes)

    def rollback(self, change_id):
        self.calls.append(("rollback", change_id))
        if isinstance(self._rollback, Exception):
            raise self._rollback
        return dict(self._rollback)

    def authorization(self):
        return dict(self._auth)

    def set_authorization(self, *, tier=None, grading=None):
        self.calls.append(("set", tier, grading))
        if isinstance(self._applied, Exception):
            raise self._applied
        return dict(self._applied)

    def factory_reset(self, confirmations):
        self.calls.append(("reset", confirmations))
        if confirmations != 3:
            raise ValueError("出厂重置需 3 次确认，收到 %r——不足即拒（08 §6）" % (confirmations,))
        if isinstance(self._reset, Exception):
            raise self._reset
        return dict(self._reset)


@pytest.fixture
def evolution_server():
    def _start(facade):
        return serve(dev=False, reflection=facade)

    return _start


READ_PATHS = ("/api/evolution/changes", "/api/evolution/authorization")
WRITE_PATHS = (
    ("/api/evolution/changes/rollback", {"change_id": "chg_x"}),
    ("/api/evolution/authorization", {"config_id": TIER_ID, "value": "manual"}),
    ("/api/evolution/factory-reset", {"confirmations": 3}),
)


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_endpoints_are_unavailable_without_the_face(ui_server, http_get, auth, path):
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable" and "未接入" in payload["reason"]


@pytest.mark.parametrize("path,body", WRITE_PATHS)
def test_write_endpoints_are_unavailable_without_the_face(ui_server, http_post, auth, path, body):
    payload = http_post(ui_server, path, body, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable" and "未接入" in payload["reason"]


# ── 变更历史时间线 ────────────────────────────────────────────────────────────
def test_change_timeline_lists_changes_and_hides_rollback_for_already_rolled_back(
    evolution_server, http_get, auth
):
    with evolution_server(FakeEvolution()) as running:
        payload = http_get(running, "/api/evolution/changes", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "change_timeline"
    items = payload["data"]["slots"]["changes"]
    assert [item["change_id"] for item in items] == ["chg_" + "1" * 20, "chg_" + "2" * 20]
    assert items[0]["actions"] == ["rollback"] and items[0]["rolled_back"] is False
    assert items[1]["actions"] == [] and items[1]["rolled_back"] is True   # 已回滚的不再给动作
    assert payload["data"]["slots"]["labels"] == {"rollback": "一键回滚"}


def test_rollback_forwards_the_change_id_and_reports_the_run(evolution_server, http_get, http_post, auth):
    facade = FakeEvolution(rollback={"applied": False, "note": "无变更前取值可回放"})
    with evolution_server(facade) as running:
        payload = http_post(
            running,
            "/api/evolution/changes/rollback",
            {"change_id": "chg_" + "1" * 20},
            headers=auth(running),
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["applied"] is False
    assert "无变更前取值" in payload["data"]["note"]
    assert facade.calls == [("rollback", "chg_" + "1" * 20)]


def test_rollback_without_a_change_id_is_validation_failed(evolution_server, http_post, auth):
    with evolution_server(FakeEvolution()) as running:
        payload = http_post(
            running, "/api/evolution/changes/rollback", {}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed"
    assert "change_id" in payload["reason"]


def test_input_shaped_failure_from_the_face_maps_to_validation_failed(evolution_server, http_post, auth):
    facade = FakeEvolution(rollback=ValueError("变更不存在：'chg_x'"))
    with evolution_server(facade) as running:
        payload = http_post(
            running, "/api/evolution/changes/rollback", {"change_id": "chg_x"}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed" and "变更不存在" in payload["reason"]


# ── 授权设置面板 ──────────────────────────────────────────────────────────────
def test_setting_panel_offers_the_tier_switch_and_only_displays_grading(
    evolution_server, http_get, auth
):
    with evolution_server(FakeEvolution()) as running:
        payload = http_get(
            running, "/api/evolution/authorization", headers=auth(running)
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "setting_panel"
    slots = payload["data"]["slots"]
    tier, grading = slots["entries"]
    assert tier["config_id"] == TIER_ID and tier["current"] == "collaborative"
    assert [option["value"] for option in tier["options"]] == ["manual", "collaborative", "autonomous"]
    assert tier["actions"] == ["set"]
    assert grading["config_id"] == GRADING_ID
    assert grading["actions"] == [] and grading["options"] == []      # 对象类条目：只呈现
    assert "只呈现" in grading["note"]
    assert slots["labels"]["autonomous"] == "自主" and slots["labels"]["set"] == "应用"


def test_setting_panel_is_unavailable_when_the_subface_is_not_wired(evolution_server, http_get, auth):
    facade = FakeEvolution(auth={"available": False, "reason": "未接入演进授权判据"})
    with evolution_server(facade) as running:
        payload = http_get(running, "/api/evolution/authorization", headers=auth(running)).json()
    assert payload["status"] == "unavailable"
    assert "未接入演进授权判据" in payload["reason"]


def test_applying_a_setting_routes_by_config_id(evolution_server, http_post, auth):
    facade = FakeEvolution()
    with evolution_server(facade) as running:
        payload = http_post(
            running,
            "/api/evolution/authorization",
            {"config_id": TIER_ID, "value": "autonomous"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "ok" and payload["data"]["changed"] is True
    assert facade.calls == [("set", "autonomous", None)]


def test_unknown_config_id_is_validation_failed(evolution_server, http_post, auth):
    facade = FakeEvolution()
    with evolution_server(facade) as running:
        payload = http_post(
            running,
            "/api/evolution/authorization",
            {"config_id": "no.such-entry", "value": 1},
            headers=auth(running),
        ).json()
    assert payload["status"] == "validation_failed"
    assert "不认这个条目" in payload["reason"]
    assert facade.calls == []                                          # 根本没调到面


# ── 出厂重置 ──────────────────────────────────────────────────────────────────
def test_factory_reset_passes_the_confirmation_count_through(evolution_server, http_post, auth):
    facade = FakeEvolution()
    with evolution_server(facade) as running:
        payload = http_post(
            running, "/api/evolution/factory-reset", {"confirmations": 3}, headers=auth(running)
        ).json()
    assert payload["status"] == "ok" and payload["data"]["performed"] is True
    assert facade.calls == [("reset", 3)]


def test_factory_reset_below_three_confirmations_is_refused(evolution_server, http_post, auth):
    """不足三次即拒（08 §6）——面抛输入类失败，端点回 `validation_failed` + 原话。"""
    with evolution_server(FakeEvolution()) as running:
        payload = http_post(
            running, "/api/evolution/factory-reset", {"confirmations": 2}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed"
    assert "3 次确认" in payload["reason"]
