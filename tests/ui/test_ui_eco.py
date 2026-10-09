"""生态面的表现层入口（[`T-UI-004.4`]）。

本组钉 ui 侧的形态与分流：描述的形态（导出清单 / 收件目录 / 导入校验四段 / 逐项批准面板 /
官方索引 / 来源追溯 / 越界警示）· 写面只转发并如实出站 · 面未注入即 fail-closed。
真链路的装配（真导出、真收件、真安装、真禁用）由 `tests/integration/test_ui_eco_wiring.py` 覆盖。
"""

from __future__ import annotations

import pytest

from st_agent.ui.server import serve

PERMISSION = "local_read:<data_cache/**>"
REVIEW = {
    "file_name": "shared-skill.stskill",
    "kind": "skill",
    "identity": "Skill 均线观察（sk_ma_watch）",
    "wants": ["Skill 均线观察（sk_ma_watch）"],
    "permissions": [
        {"permission": PERMISSION, "description": "读取行情缓存", "state": "pending", "decided_at": None}
    ],
    "missing": [{"skill_id": "sk_peer", "how_to_get": "官方 Pack 或外部下载"}],
    "provenance": {
        "sharer": "alice", "imported_at": "2026-10-07T18:00:00+08:00",
        "checksum": "abc123", "origin_chain": ["alice", "bob"],
    },
}


class FakeEco:
    """生态面适配面替身（输入类失败用抛 ``ValueError`` 表示）。"""

    def __init__(
        self,
        *,
        plan=None,
        export=None,
        inbox=(),
        review=REVIEW,
        decision=None,
        install=None,
        ledger=None,
        index=None,
        violations=None,
        disable=None,
    ) -> None:
        self._plan = plan if plan is not None else {
            "included": ["偏好演化节点 3 条", "关注标的 2 条"],
            "excluded_nodes": 5,
            "confirmed_by_required": "user",
            "exports_dir": "/root/exports",
        }
        self._export = export if export is not None else {
            "kind": "mem", "path": "/root/exports/mem-1.stmem", "checksum": "c0ffee",
            "card": {"title": "记忆片段", "file_name": "mem-1.stmem"},
        }
        self._inbox = list(inbox)
        self._review = review
        self._decision = decision if decision is not None else {"permission": PERMISSION, "decision": "approve"}
        self._install = install if install is not None else {"kind": "skill", "installed_id": "sk_ma_watch"}
        self._ledger = ledger if ledger is not None else {"records": [], "empty_text": "你的 Skill 库目前只有官方 Pack。"}
        self._index = index if index is not None else {
            "available": True,
            "entries": [{
                "kind": "official_pack", "name": "官方 Pack", "version": "1.2",
                "description": "官方能力集", "download_url": None, "checksum": None,
            }],
            "boundary": "不做中央商店",
        }
        self._violations = violations if violations is not None else {
            "available": True, "attached": True,
            "records": [{
                "skill_id": "sk_ma_watch", "violation": "local_read",
                "trace_id": "tr_1", "occurred_at": "2026-10-07T18:00:00+08:00",
            }],
            "disabled": [],
        }
        self._disable = disable if disable is not None else {"skill_id": "sk_ma_watch", "disabled": True}
        self.calls: list[tuple] = []

    def export_plan(self):
        return dict(self._plan)

    def export(self, *, kind, ref="", author="", confirmed_by=""):
        self.calls.append(("export", kind, ref, author, confirmed_by))
        if isinstance(self._export, Exception):
            raise self._export
        return dict(self._export)

    def inbox(self):
        return list(self._inbox)

    def review(self, *, file_name, received_from=""):
        self.calls.append(("review", file_name, received_from))
        if isinstance(self._review, Exception):
            raise self._review
        return dict(self._review)

    def decide_import(self, *, file_name, permission, decision="approve"):
        self.calls.append(("decide", file_name, permission, decision))
        if isinstance(self._decision, Exception):
            raise self._decision
        return dict(self._decision)

    def install(self, *, file_name, confirmed_by="", received_from=""):
        self.calls.append(("install", file_name, confirmed_by))
        if confirmed_by != "user":
            raise ValueError("安装须由用户确认（09 §3 第 4 段）")
        if isinstance(self._install, Exception):
            raise self._install
        return dict(self._install)

    def imports_ledger(self):
        return dict(self._ledger)

    def index(self):
        return dict(self._index)

    def violations(self):
        return dict(self._violations)

    def disable(self, *, skill_id):
        self.calls.append(("disable", skill_id))
        if isinstance(self._disable, Exception):
            raise self._disable
        return dict(self._disable)


@pytest.fixture
def eco_server():
    def _start(facade):
        return serve(dev=False, eco=facade)

    return _start


READ_PATHS = (
    "/api/eco/export/plan",
    "/api/eco/inbox",
    "/api/eco/index",
    "/api/eco/imports",
    "/api/eco/violations",
    "/api/eco/violations/history",
)
WRITE_PATHS = (
    ("/api/eco/export", {"kind": "mem", "author": "me", "confirmed_by": "user"}),
    ("/api/eco/import/review", {"file_name": "a.stskill"}),
    ("/api/eco/import/permissions", {"file_name": "a.stskill"}),
    ("/api/eco/import/decide", {"file_name": "a.stskill", "permission": PERMISSION, "action": "approve"}),
    ("/api/eco/import/install", {"file_name": "a.stskill", "confirmed_by": "user"}),
    ("/api/eco/violations/disable", {"skill_id": "sk_ma_watch"}),
)


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_endpoints_are_unavailable_without_the_face(ui_server, http_get, auth, path):
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable" and "未接入" in payload["reason"]


@pytest.mark.parametrize("path,body", WRITE_PATHS)
def test_write_endpoints_are_unavailable_without_the_face(ui_server, http_post, auth, path, body):
    payload = http_post(ui_server, path, body, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable" and "未接入" in payload["reason"]


# ── 导出 ──────────────────────────────────────────────────────────────────────
def test_export_plan_renders_the_public_information_list(eco_server, http_get, auth):
    with eco_server(FakeEco()) as running:
        payload = http_get(running, "/api/eco/export/plan", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "report_card"
    sections = payload["data"]["slots"]["sections"]
    assert sections[0]["title"] == "本次导出包含以下公开信息"
    assert sections[0]["lines"] == ["包含：偏好演化节点 3 条", "包含：关注标的 2 条"]
    assert "5" in sections[1]["lines"][0]                 # 被过滤的节点数单独报
    assert "/root/exports" in sections[2]["lines"][0]


def test_export_forwards_every_field(eco_server, http_post, auth):
    facade = FakeEco()
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/export",
            {"kind": "mem", "author": "me", "confirmed_by": "user"}, headers=auth(running),
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["path"] == "/root/exports/mem-1.stmem"
    assert facade.calls == [("export", "mem", "", "me", "user")]


def test_export_without_confirmation_is_refused(eco_server, http_post, auth):
    """`.stmem` 的确认门由适配面把关——未确认即 `validation_failed`（不生成文件）。"""
    facade = FakeEco(export=ValueError("导出记忆片段须由用户确认（三步的第 3 步，09 §2）"))
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/export", {"kind": "mem", "author": "me"}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed"
    assert "用户确认" in payload["reason"]


def test_export_without_kind_is_validation_failed(eco_server, http_post, auth):
    with eco_server(FakeEco()) as running:
        payload = http_post(running, "/api/eco/export", {}, headers=auth(running)).json()
    assert payload["status"] == "validation_failed" and "kind" in payload["reason"]


# ── 导入 ──────────────────────────────────────────────────────────────────────
def test_inbox_renders_a_table(eco_server, http_get, auth):
    facade = FakeEco(inbox=[{"file_name": "a.stskill", "size": 128}])
    with eco_server(facade) as running:
        payload = http_get(running, "/api/eco/inbox", headers=auth(running)).json()
    assert payload["data"]["component_type"] == "table"
    # 行是**键控**形状（列键 → 值）：渲染件按 `row[column.key]` 取值。
    # 位置形状（`[["a.stskill", 128]]`）曾同时存在于六处产出方，而渲染件读的是键控——
    # 那六张表在生产面上**全是空的**（2026-10-09 实机实测，[D-105] ①）。
    assert payload["data"]["slots"]["rows"] == [{"file_name": "a.stskill", "size": 128}]
    assert payload["data"]["slots"]["columns"] == [
        {"key": "file_name", "label": "文件名", "kind": "text"},
        {"key": "size", "label": "大小（字节）", "kind": "number", "digits": 0},
    ]


def test_review_renders_the_four_segments(eco_server, http_post, auth):
    with eco_server(FakeEco()) as running:
        payload = http_post(
            running, "/api/eco/import/review", {"file_name": "shared-skill.stskill"}, headers=auth(running)
        ).json()
    assert payload["status"] == "ok"
    sections = payload["data"]["slots"]["sections"]
    assert [section["title"] for section in sections] == [
        "能力", "权限申请（逐项批准，01 §10）", "依赖", "来源追溯",
    ]
    assert PERMISSION in sections[1]["lines"][0] and "pending" in sections[1]["lines"][0]
    assert "获取途径" not in sections[2]["lines"][0] and "sk_peer" in sections[2]["lines"][0]
    assert "alice" in sections[3]["lines"][0] and "bob" in sections[3]["lines"][0]


def test_permissions_panel_offers_approve_and_reject(eco_server, http_post, auth):
    with eco_server(FakeEco()) as running:
        payload = http_post(
            running, "/api/eco/import/permissions", {"file_name": "shared-skill.stskill"},
            headers=auth(running),
        ).json()
    assert payload["data"]["component_type"] == "setting_panel"
    slots = payload["data"]["slots"]
    assert slots["surface"] == "import"                    # 面键 ⇒ 渲染件在固定表里解析路由
    entry = slots["entries"][0]
    assert entry["identifier"] == PERMISSION and entry["current"] == "pending"
    assert entry["actions"] == ["approve", "reject"]
    assert entry["params"] == {"file_name": "shared-skill.stskill", "permission": PERMISSION}
    assert slots["labels"]["approve"] == "批准"


def test_decision_is_forwarded(eco_server, http_post, auth):
    facade = FakeEco()
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/import/decide",
            {"file_name": "a.stskill", "permission": PERMISSION, "action": "reject"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "ok"
    assert facade.calls == [("decide", "a.stskill", PERMISSION, "reject")]


def test_decision_without_a_permission_is_validation_failed(eco_server, http_post, auth):
    facade = FakeEco()
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/import/decide", {"file_name": "a.stskill", "action": "approve"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "validation_failed" and "permission" in payload["reason"]
    assert facade.calls == []


def test_install_requires_user_confirmation(eco_server, http_post, auth):
    with eco_server(FakeEco()) as running:
        refused = http_post(
            running, "/api/eco/import/install", {"file_name": "a.stskill"}, headers=auth(running)
        ).json()
        accepted = http_post(
            running, "/api/eco/import/install",
            {"file_name": "a.stskill", "confirmed_by": "user"}, headers=auth(running),
        ).json()
    assert refused["status"] == "validation_failed" and "用户确认" in refused["reason"]
    assert accepted["status"] == "ok" and accepted["data"]["installed_id"] == "sk_ma_watch"


def test_install_reports_the_import_pipeline_reason(eco_server, http_post, auth):
    """权限未全部批准时导入流水线的原话**原样出站**（显式拒，不吞）。"""
    facade = FakeEco(install=ValueError("权限未全部批准，不得安装：['local_read:x']（01 §10）"))
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/import/install",
            {"file_name": "a.stskill", "confirmed_by": "user"}, headers=auth(running),
        ).json()
    assert payload["status"] == "validation_failed"
    assert "不得安装" in payload["reason"]


# ── 官方索引与来源追溯 ────────────────────────────────────────────────────────
def test_index_renders_a_table(eco_server, http_get, auth):
    with eco_server(FakeEco()) as running:
        payload = http_get(running, "/api/eco/index", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "table"
    assert payload["data"]["slots"]["rows"][0]["name"] == "官方 Pack"


def test_index_is_unavailable_when_the_endpoint_is_missing(eco_server, http_get, auth):
    facade = FakeEco(index={"available": False, "reason": "官方 Skill 索引不可用：未配端点"})
    with eco_server(facade) as running:
        payload = http_get(running, "/api/eco/index", headers=auth(running)).json()
    assert payload["status"] == "unavailable"
    assert "未配端点" in payload["reason"]


def test_empty_ledger_is_an_empty_state_with_the_library_text(eco_server, http_get, auth):
    """没有留痕 ⇒ `empty`（**不是**一张空表）；文案沿用 L1 的中性文本（story-10 的空状态）。"""
    with eco_server(FakeEco()) as running:
        payload = http_get(running, "/api/eco/imports", headers=auth(running)).json()
    assert payload["status"] == "empty"
    assert payload["data"] is None
    assert payload["reason"] == "你的 Skill 库目前只有官方 Pack。"


def test_ledger_with_records_renders_a_table(eco_server, http_get, auth):
    facade = FakeEco(ledger={
        "records": [{
            "import_id": "imp_1", "kind": "skill", "installed_id": "sk_ma_v1.0",
            "origin": {"sharer": "alice", "imported_at": "2026-10-07T18:00:00+08:00"},
        }],
        "empty_text": "",
    })
    with eco_server(facade) as running:
        payload = http_get(running, "/api/eco/imports", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["slots"]["rows"] == [
        {
            "import_id": "imp_1",
            "kind": "skill",
            "installed_id": "sk_ma_v1.0",
            "sharer": "alice",
            "imported_at": "2026-10-07T18:00:00+08:00",
        }
    ]


# ── 越界行为警示 ──────────────────────────────────────────────────────────────
def test_violation_alert_carries_the_record_and_the_disable_action(eco_server, http_get, auth):
    with eco_server(FakeEco()) as running:
        payload = http_get(running, "/api/eco/violations", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "violation_alert"
    slots = payload["data"]["slots"]
    assert slots["record"]["skill_id"] == "sk_ma_watch" and slots["record"]["disabled"] is False
    assert "超出其声明的范围" in slots["labels"]["warning"]
    assert slots["labels"]["disable"] == "禁用该能力"


def test_violation_alert_marks_an_already_disabled_ability(eco_server, http_get, auth):
    facade = FakeEco(violations={
        "available": True, "attached": True,
        "records": [{"skill_id": "sk_ma_watch", "violation": "local_read", "trace_id": "t", "occurred_at": "x"}],
        "disabled": ["sk_ma_watch"],
    })
    with eco_server(facade) as running:
        payload = http_get(running, "/api/eco/violations", headers=auth(running)).json()
    assert payload["data"]["slots"]["record"]["disabled"] is True


def test_violations_are_empty_not_fabricated(eco_server, http_get, auth):
    facade = FakeEco(violations={"available": True, "attached": True, "records": [], "disabled": []})
    with eco_server(facade) as running:
        payload = http_get(running, "/api/eco/violations", headers=auth(running)).json()
    assert payload["status"] == "empty" and "尚无越界行为" in payload["reason"]


def test_violations_report_an_absent_subscription(eco_server, http_get, auth):
    facade = FakeEco(violations={"available": False, "reason": "未接入越界行为的订阅面（装配归组合根）"})
    with eco_server(facade) as running:
        payload = http_get(running, "/api/eco/violations", headers=auth(running)).json()
    assert payload["status"] == "unavailable" and "未接入越界行为" in payload["reason"]


def test_violation_history_renders_a_table(eco_server, http_get, auth):
    with eco_server(FakeEco()) as running:
        payload = http_get(running, "/api/eco/violations/history", headers=auth(running)).json()
    assert payload["data"]["component_type"] == "table"
    assert [
        (row["skill_id"], row["violation"]) for row in payload["data"]["slots"]["rows"]
    ][0] == ("sk_ma_watch", "local_read")


def test_disable_is_forwarded(eco_server, http_post, auth):
    facade = FakeEco()
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/violations/disable", {"skill_id": "sk_ma_watch"}, headers=auth(running)
        ).json()
    assert payload["status"] == "ok"
    assert facade.calls == [("disable", "sk_ma_watch")]


def test_disable_without_a_skill_id_is_validation_failed(eco_server, http_post, auth):
    facade = FakeEco()
    with eco_server(facade) as running:
        payload = http_post(
            running, "/api/eco/violations/disable", {}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed" and "skill_id" in payload["reason"]
    assert facade.calls == []
