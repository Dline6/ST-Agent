"""触达区在**真 M5 组合根 + 真回环服务**上的取数与写面（[`T-UI-017`] 三叶）。

与 `tests/ui/test_ui_delivery.py`（替身钉形状与失败态）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 造出的 [`DeliveryFacade`] 走一遍真链路——

1. **配置面板**出真预算矩阵（当前情境的格位逐条带来源标注）与真渠道链；
2. **配置落值**经 L5 自有写面**真写进 `config` 分区**并产生 `change_id` 留痕；取值未变不复落值；
3. **日报**「生成 → 读回」往返：写同一路径、四段 + 投出态；
4. **推送历史**出真投递留痕（逐条 `delivery_id` 可寻址）；
5. **五动作反馈**以一个真 `delivery_id` 为靶（写面仍是既有反馈通道）；
6. **疲劳**「连续忽略 → 发问 → 答复」闭环：答复经 L3 采集面铸 `FeedbackEvent` → 总线
   `FeedbackRecorded` → L5 `consume` 真的落了值（[`T-UI-017.3`] 假设 A1 的同一路径）。

离线可跑：真 Store / 真 L1–L6，渠道那一跳由记录型替身接管（`rig_m3`）。
"""

from __future__ import annotations

import http.client
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m3 import RecordingChannel
from rig_m5 import seeded_m5

from st_agent.contracts.identifiers import digest_id
from st_agent.l5.signal import SignalContent, SignalLensStance, signal_event
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

RULES: list[tuple[str, object]] = []

_DEDUP = "l1:test-manual:sh.600000:用户自定规则"
"""手工投递的信号用的去重键（与盯盘种子不同键，便于把留痕按键筛出来）。"""


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
    """真 M5 组合根 + 一个**可记录**的桌面渠道（升级链与结算都走真路径）。"""
    return seeded_m5(
        tmp_path / ROOT_NAME, rules=RULES,
        channels={"desktop": RecordingChannel("desktop")},
    )


def _publish(runtime, *, level: str = "emergency", dedup_key: str = _DEDUP):
    """像上游那样往总线上投一条合契的 `SignalEmitted`（`signal_id` 由 L5 采纳时铸造，不在负载内）。"""
    event = signal_event(
        level=level,
        content_ref=SignalContent(
            conclusion="sh.600000 触发异动条件：涨跌幅 +7.50%（上游投递的信号）",
            lens_stances=(SignalLensStance(
                lens_id=digest_id("lens", "l1:test-observer"),
                stance="neutral", summary="观察：该标的当日涨跌幅超过盯盘阈值",
            ),),
        ),
        evidence_refs=("run_abcdef0123456789abcd",),
        dedup_key=dedup_key,
        source_trace_id="tr_00000000000000000001",
        occurred_at=datetime.now().astimezone(),
    )
    runtime.events.publish(event)
    return event


# ─────────────────────────── 配置面板与落值 ───────────────────────────


def test_config_panel_shows_the_real_matrix_and_channel_chains(m5) -> None:
    """GWT-1：矩阵格位逐条带来源标注；渠道链出缺省三级（`desktop,email,im_webhook | 5,15`）。"""
    runtime = m5.m5
    with serve(dev=False, delivery=runtime.delivery) as running:
        payload = _get(running, "/api/delivery/config")
        assert payload["status"] == "ok", payload.get("reason")
        slots = payload["data"]["slots"]
        assert slots["surface"] == "delivery-config"
        entries = slots["entries"]
        mode = next(e for e in entries if e["actions"] == ["switch-mode"])
        assert mode["current"] == "workday"
        emergency = next(
            e for e in entries
            if e["actions"] == ["set-policy"] and e["params"]["level"] == "emergency"
        )
        assert emergency["current"] == "desktop,email,im_webhook | 5,15"
        # 缺省格位如实标「缺省」——不把缺省冒充成用户配过的
        cell = next(e for e in entries if e["identifier"].startswith("盘中 · 短提示 · 允许级别"))
        assert cell["current"] == "emergency"
        assert "缺省" in cell["description"]


def test_config_writes_land_in_the_real_store_with_a_change_record(m5) -> None:
    """GWT-1 / GWT-2：改值经 L5 自有写面**真落盘**并产生 `change_id`；取值未变不复落值。"""
    runtime = m5.m5
    l5 = runtime.m3.l5
    with serve(dev=False, delivery=runtime.delivery) as running:
        switched = _post(
            running, "/api/delivery/config", {"action": "switch-mode", "value": "weekend"},
        )
        assert switched["status"] == "ok", switched.get("reason")
        assert switched["data"]["changed"] is True
        assert switched["data"]["change_id"], "落值必须产生变更留痕（[01 §7]）"
        assert l5.budget.mode == "weekend"
        again = _post(
            running, "/api/delivery/config", {"action": "switch-mode", "value": "weekend"},
        )
        assert again["data"]["changed"] is False, "取值未变不留痕（各 owner 的既有取向）"

        cell = _post(running, "/api/delivery/config", {
            "action": "set-cell", "mode": "weekend", "slot_id": "afternoon",
            "content_type": "brief", "field": "levels",
            "value": "emergency,important",
        })
        assert cell["status"] == "ok", cell.get("reason")
        assert cell["data"]["changed"] is True
        view = l5.budget.map().view(next(
            v.key for v in l5.budget.map().cells
            if v.key.mode == "weekend" and v.key.slot_id == "afternoon"
            and v.key.content_type == "brief"
        ))
        assert view is not None and view.source == "configured"
        assert set(view.allowed_levels) == {"emergency", "important"}

        policy = _post(running, "/api/delivery/config", {
            "action": "set-policy", "level": "routine", "value": "desktop,email | 10",
        })
        assert policy["status"] == "ok", policy.get("reason")
        assert policy["data"]["changed"] is True
        assert l5.policies.get("routine").channels == ("desktop", "email")

        bad_chain = _post(running, "/api/delivery/config", {
            "action": "set-policy", "level": "routine", "value": "desktop,email,tts",
        })
        assert bad_chain["status"] == "validation_failed", "链长变了却不给等待段 ⇒ 显式拒"
        assert "未读等待" in bad_chain["reason"]

        when = _post(
            running, "/api/delivery/config", {"action": "set-report-time", "value": "07:30"},
        )
        assert when["data"]["changed"] is True
        assert l5.reports.report_time().strftime("%H:%M") == "07:30"

        threshold = _post(running, "/api/delivery/config", {
            "action": "set-fatigue-threshold", "value": "3",
        })
        assert threshold["data"]["changed"] is True
        assert l5.fatigue.threshold() == 3

        resend = _post(running, "/api/delivery/config", {"action": "resend-off"})
        assert resend["status"] == "ok"
        assert l5.policies.resend_on_reconnect is False


# ─────────────────────────── 日报与推送历史 ───────────────────────────


def test_report_build_then_read_round_trips(m5) -> None:
    """GWT-2：未生成 ⇒ `empty` + 原因；「生成」后读回四段 + 投出态；清单里出现该日。"""
    runtime = m5.m5
    with serve(dev=False, delivery=runtime.delivery) as running:
        day = "2026-01-05"
        before = _get(running, f"/api/delivery/report?day={day}")
        assert before["status"] == "empty"
        assert "尚无报告" in before["reason"]

        built = _post(running, "/api/delivery/report/build", {"day": day})
        assert built["status"] == "ok", built.get("reason")
        assert built["data"]["day"] == day
        assert built["data"]["sections"] == 4

        after = _get(running, f"/api/delivery/report?day={day}")
        assert after["status"] == "ok", after.get("reason")
        titles = [section["title"] for section in after["data"]["slots"]["sections"]]
        # 该日无留痕 ⇒ 四段皆空 ⇒ 首段即「今日无内容」的正文；四段仍逐段并列在册
        assert titles[0] == "今日无内容"
        assert [t for t in titles if t in (
            "昨日回顾", "今日关注", "多视角摘要", "Memory 更新摘要",
        )] == ["昨日回顾", "今日关注", "多视角摘要", "Memory 更新摘要"]
        assert titles[-1] == "投出"
        assert after["data"]["slots"]["sections"][0]["lines"] == ["今天没有需要打扰你的事"]

        listing = _get(running, "/api/delivery/reports")
        assert listing["status"] == "ok"
        assert day in [row["day"] for row in listing["data"]["slots"]["rows"]]

        bad = _get(running, "/api/delivery/report?day=05/01/2026")
        assert bad["status"] == "validation_failed"
        assert "YYYY-MM-DD" in bad["reason"]


def test_history_lists_real_delivery_records(m5) -> None:
    """GWT-3：无留痕 ⇒ `empty`；投一条信号后逐条出真留痕（`delivery_id` 可寻址）。"""
    runtime = m5.m5
    with serve(dev=False, delivery=runtime.delivery) as running:
        assert _get(running, "/api/delivery/history")["status"] == "empty"

        _publish(runtime)
        payload = _get(running, "/api/delivery/history?limit=5")
        assert payload["status"] == "ok", payload.get("reason")
        entry = payload["data"]["slots"]["entries"][0]
        assert entry["kind"] == "desktop"
        assert entry["state"] == "ok"
        assert "异动提示" in entry["content"] or "触发异动条件" in entry["content"]
        assert entry["trace_ref"] in {r.delivery_id for r in runtime.m3.l5.delivery.ledger()}

        assert _get(running, "/api/delivery/history?limit=0")["status"] == "validation_failed"


# ─────────────────────────── 五动作反馈 ───────────────────────────


def test_feedback_dialog_targets_a_real_delivery_and_the_write_face_accepts_it(m5) -> None:
    """GWT-1：对话框以真 `delivery_id` 为靶；五动作经**既有**反馈通道真的送达。"""
    runtime = m5.m5
    _publish(runtime)
    delivery_id = runtime.m3.l5.delivery.ledger()[-1].delivery_id
    with serve(dev=False, delivery=runtime.delivery, reflection=runtime.reflection) as running:
        dialog = _get(running, f"/api/delivery/feedback?delivery={delivery_id}")
        assert dialog["status"] == "ok", dialog.get("reason")
        slots = dialog["data"]["slots"]
        assert slots["target"] == {"kind": "delivery", "ref": delivery_id}
        assert slots["actions"] == ["adopted", "ignored", "rejected", "queried", "liked"]
        assert slots["reason_required"] == ["rejected"]

        # 否定类缺原因 ⇒ 采集面拒（display 侧的必填入口由渲染件守，硬门在采集面）
        missing = _post(running, "/api/reflection/feedback", {
            "target": slots["target"], "action": "rejected",
        })
        assert missing["status"] == "validation_failed"

        recorded = _post(running, "/api/reflection/feedback", {
            "target": slots["target"], "action": "adopted",
        })
        assert recorded["status"] == "ok", recorded.get("reason")
        assert recorded["data"]["delivery"]["delivered"] is True, "已接总线，反馈应真的送达"


# ─────────────────────────── 疲劳：发问 → 答复闭环 ───────────────────────────


def test_fatigue_inquiry_and_answer_settle_on_the_real_face(m5) -> None:
    """GWT-2 / GWT-3：连续忽略 ⇒ 发问（`delivery_id` 可寻址）；答复「关闭」经事件面真落进静音清单。"""
    runtime = m5.m5
    l5 = runtime.m3.l5
    # 单级链 ⇒ 链尽即结算「未确认」（一次忽略），不必等升级等待；阈值 1 便于一轮达阈。
    l5.policies.set_policy("emergency", ["desktop"], waits_minutes=[])
    l5.fatigue.set_threshold(1)
    _publish(runtime)
    record = l5.delivery.ledger()[-1]
    moment = record.created_at + timedelta(minutes=1)
    l5.delivery.advance(now=moment)
    (inquiry,) = l5.fatigue.sweep(moment)

    with serve(dev=False, delivery=runtime.delivery) as running:
        fatigue = _get(running, "/api/delivery/fatigue")
        assert fatigue["status"] == "ok", fatigue.get("reason")
        state_lines = fatigue["data"]["slots"]["sections"][1]["lines"]
        assert any("连续忽略 1 次" in line and "有未答复的调整询问" in line for line in state_lines)

        inquiries = _get(running, "/api/delivery/inquiries")
        assert inquiries["status"] == "ok", inquiries.get("reason")
        (row,) = inquiries["data"]["slots"]["rows"]
        assert row["delivery_id"] == inquiry.delivery_id
        # 问句取自该询问推送的留痕正文（中性问句，过 [01 §6]）
        assert "已连续 1 次未被确认" in row["question"]
        assert "是否减少" in row["question"]

        answered = _post(running, "/api/delivery/fatigue/answer", {
            "delivery_id": inquiry.delivery_id, "choice": "disable",
        })
        assert answered["status"] == "ok", answered.get("reason")
        assert answered["data"]["delivered"] is True, "答复须经总线真的送达 L5 与 L6"
        assert l5.fatigue.is_muted(inquiry.dedup_key) is True, "「关闭」真的落进了静音清单"

        assert _get(running, "/api/delivery/inquiries")["status"] == "empty", "答复后不再待答"


def test_fatigue_answer_refuses_a_push_that_never_asked(m5) -> None:
    """答复别的推送 ⇒ 显式拒（不让它静默落进一个不存在的询问）。"""
    runtime = m5.m5
    _publish(runtime)
    delivery_id = runtime.m3.l5.delivery.ledger()[-1].delivery_id
    with serve(dev=False, delivery=runtime.delivery) as running:
        payload = _post(running, "/api/delivery/fatigue/answer", {
            "delivery_id": delivery_id, "choice": "keep",
        })
        assert payload["status"] == "validation_failed"
        assert "不是待答复的调整询问" in payload["reason"]
