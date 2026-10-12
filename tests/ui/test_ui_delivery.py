"""触达区端点：**面缺席即 fail-closed**、表单值还原与描述摆形（[`T-UI-017`]）。

与 `tests/integration/test_ui_delivery_wiring.py`（真组合根 + 真存储）分工：本组只钉
「没接线时会怎样」「取值怎么归一」「描述摆成什么形状」——都不需要真 L5 落盘。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l5.daily_report import DailyReport, ReportSection, ReportTrace, NO_CONTENT_NOTE
from st_agent.l5.delivery_store import DeliveryRecord, delivery_id_for
from st_agent.l5.runtime import build_l5
from st_agent.ui.app import (
    DELIVERY_CONFIG_SURFACE,
    _bounded_limit,
    _history_entry,
    _level_tokens,
    _parse_policy_value,
    _positive_int,
    _report_day,
    _report_sections,
    build_ui,
)

_GET_PATHS = (
    "/api/delivery/report",
    "/api/delivery/reports",
    "/api/delivery/history",
    "/api/delivery/config",
    "/api/delivery/feedback",
    "/api/delivery/fatigue",
    "/api/delivery/inquiries",
)

_POST_PATHS = (
    "/api/delivery/report/build",
    "/api/delivery/config",
    "/api/delivery/fatigue/answer",
)

_NOW = datetime(2026, 10, 12, 8, 30, tzinfo=timezone(timedelta(hours=8)))


@pytest.mark.parametrize("path", _GET_PATHS)
def test_read_endpoints_fail_closed_without_the_face(ui_server, http_get, auth, path: str) -> None:
    """面缺席 ⇒ `unavailable` + 点名（不 500、不装空表）。"""
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]
    assert payload["render"]["presentation"] == "delayed"


@pytest.mark.parametrize("path", _POST_PATHS)
def test_write_endpoints_fail_closed_without_the_face(
    ui_server, http_post, auth, path: str
) -> None:
    """写面同样 fail-closed——缺面时**不执行任何动作**，只回 `unavailable` + 点名。"""
    payload = http_post(ui_server, path, {}, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


# ─────────────────────────── 纯函数口径 ───────────────────────────


def test_report_day_defaults_to_today_and_rejects_garbage() -> None:
    assert _report_day("") == datetime.now().astimezone().date()
    assert _report_day("2026-10-12") == date(2026, 10, 12)
    with pytest.raises(ValueError):
        _report_day("12/10/2026")


def test_bounded_limit_rejects_and_caps_without_silent_clamping() -> None:
    assert _bounded_limit("", default=30, cap=30, what="条数") == 30
    assert _bounded_limit("5", default=30, cap=30, what="条数") == 5
    # 上限之上**夹取**（读取成本护栏），但非法取值一律拒——不把 abc 当缺省
    assert _bounded_limit("9999", default=30, cap=30, what="条数") == 30
    for bad in ("0", "-1", "abc"):
        with pytest.raises(ValueError):
            _bounded_limit(bad, default=30, cap=30, what="条数")


def test_level_tokens_always_admit_emergency() -> None:
    """预算格位的候选取值**恒含紧急**——预算是「哪些额外级别可单独触达」，不挡紧急信号。"""
    tokens = _level_tokens()
    assert len(tokens) == 4, "三个级别里含 emergency 的非空子集有 4 个"
    assert all("emergency" in token for token, _ in tokens)
    assert {label for _, label in tokens} == {"仅紧急", "紧急 + 重要", "紧急 + 日常", "全部级别"}


def test_policy_value_parsing_keeps_the_chain_and_wait_pair() -> None:
    assert _parse_policy_value("desktop,email | 5,15") == (["desktop", "email"], [5, 15])
    assert _parse_policy_value(" desktop ") == (["desktop"], None)
    with pytest.raises(ValueError):
        _parse_policy_value("desktop | abc")


def test_positive_int_rejects_non_positive() -> None:
    assert _positive_int("3", "阈值") == 3
    for bad in ("0", "-2", "x", ""):
        with pytest.raises(ValueError):
            _positive_int(bad, "阈值")


# ─────────────────────────── 描述摆形 ───────────────────────────


def _report(*, empty: bool, delivered: str = "") -> DailyReport:
    sections = tuple(
        ReportSection(
            name=name, label=label,
            lines=() if empty else (f"{label} 的一行",),
            empty_note="（本段无内容）",
        )
        for name, label in (
            ("yesterday_review", "昨日回顾"), ("today_focus", "今日关注"),
            ("lens_digest", "多视角摘要"), ("memory_update", "Memory 更新摘要"),
        )
    )
    return DailyReport(
        day=date(2026, 10, 12), generated_at=_NOW, sections=sections, empty=empty,
        title="日报", body=NO_CONTENT_NOTE if empty else "正文", trace=ReportTrace(),
        anchor="a", delivered_channel=delivered,
    )


def test_report_sections_surface_the_four_parts_and_the_no_content_note() -> None:
    """四段皆空 ⇒ 正文写「今天没有需要打扰你的事」；未投出时**不假装送达**。"""
    empty = _report_sections(_report(empty=True))
    assert empty[0] == {"title": "今日无内容", "lines": [NO_CONTENT_NOTE]}
    assert [section["title"] for section in empty][-1] == "投出"
    assert "尚未投出" in empty[-1]["lines"][0]

    full = _report_sections(_report(empty=False, delivered="desktop"))
    assert [section["title"] for section in full][:4] == [
        "昨日回顾", "今日关注", "多视角摘要", "Memory 更新摘要",
    ]
    assert full[-1]["lines"] == ["已投出到「桌面通知」"]


def _record(**kw) -> DeliveryRecord:
    base = dict(
        delivery_id=delivery_id_for("sig_test", "desktop", 0),
        signal_id="sig_test", dedup_key="l1:x:sh.600000:异动", level="emergency", step=0,
        route="separate", channel="desktop", status="delivered",
        title="异动提示", body="涨跌幅超过阈值", created_at=_NOW,
    )
    base.update(kw)
    return DeliveryRecord(**base)


def test_history_entry_maps_status_to_a_presentation_bucket() -> None:
    """留痕六态 → `timeline_view` 的四个呈现档；**精确状态写在正文里**（不靠呈现档硬分）。"""
    entry = _history_entry(_record(status="read", channel="email"))
    assert entry["state"] == "ok"
    assert entry["kind"] == "email"
    assert entry["content"].startswith("已读 · ")
    assert entry["trace_ref"] == delivery_id_for("sig_test", "desktop", 0)

    pending = _history_entry(_record(status="unavailable", pending_reconnect=True))
    assert pending["state"] == "delayed"
    assert "离线，暂无法发送" in pending["content"]

    queue = _history_entry(_record(status="queued", channel="daily_report", route="daily_report"))
    assert queue["state"] == "empty"


# ─────────────────────────── 触达配置面板 ───────────────────────────


class _Facade:
    """一个**进程内**真 L5 栈的薄门面（无 store ⇒ 判定照常、不落盘）。"""

    def __init__(self, **kw: object) -> None:
        stack = build_l5()
        self.budget = stack.budget
        self.policies = stack.policies
        self.delivery = stack.delivery
        self.reports = stack.reports
        self.fatigue = stack.fatigue
        self.feedback = None
        self.store = None
        self.__dict__.update(kw)


def _app(**kw: object):
    return build_ui(host="127.0.0.1", port=0, delivery=_Facade(**kw))


def test_config_panel_lists_every_touchpoint_on_one_surface_key() -> None:
    """情境 / 预算格位 / 渠道链 / 补发 / 日报时刻 / 疲劳阈值**共用一个面键与一条路由**。"""
    payload = _app().api_delivery_config()
    assert payload["status"] == "ok", payload.get("reason")
    description = payload["data"]
    assert description["component_type"] == "setting_panel"
    slots = description["slots"]
    assert slots["surface"] == DELIVERY_CONFIG_SURFACE
    entries = slots["entries"]
    mode = next(e for e in entries if e["actions"] == ["switch-mode"])
    assert mode["current"] == "workday"
    assert [option["value"] for option in mode["options"]] == [
        "workday", "weekend", "business_trip", "vacation", "urgent",
    ]
    # 每格两条（允许级别 + 每格上限）× 当前情境的 5 时段 × 4 内容类型
    cells = [e for e in entries if e["actions"] == ["set-cell"]]
    assert len(cells) == 40
    assert all(e["params"]["mode"] == "workday" for e in cells)
    assert len([e for e in entries if e["actions"] == ["set-policy"]]) == 3
    assert len([e for e in entries if e["actions"] == ["resend-on", "resend-off"]]) == 1
    assert len([e for e in entries if e["actions"] == ["set-report-time"]]) == 1
    assert len([e for e in entries if e["actions"] == ["set-fatigue-threshold"]]) == 1
    # 候选级别标签随 token 下发（生成文案槽，前端不自带词表）
    labels = slots["labels"]
    assert labels["emergency,important"] == "紧急 + 重要"


def test_config_panel_names_the_missing_handle() -> None:
    """面在但缺句柄 ⇒ `unavailable` **点名缺哪一支**——不半途抛成一条假的 failed。"""
    payload = _app(fatigue=None).api_delivery_config()
    assert payload["status"] == "unavailable"
    assert "fatigue" in payload["reason"]


def test_config_apply_rejects_unknown_action_and_bad_values() -> None:
    """未知动作 / 越界取值一律 `validation_failed`（**不静默吞**）。"""
    app = _app()
    for body, needle in (
        ({"action": "nope"}, "未知的触达配置动作"),
        ({"action": "switch-mode", "value": "someday"}, "情境模式"),
        ({"action": "set-cell", "mode": "workday", "slot_id": "nope",
          "content_type": "brief", "field": "max", "value": "2"}, "未知时段"),
        ({"action": "set-cell", "mode": "workday", "slot_id": "evening",
          "content_type": "brief", "field": "max", "value": "0"}, "正整数"),
        ({"action": "set-policy", "level": "routine", "value": "desktop,desktop | 5"}, "两次"),
        ({"action": "set-report-time", "value": "25:00"}, "HH:MM"),
        ({"action": "set-fatigue-threshold", "value": "x"}, "正整数"),
    ):
        payload = app.api_delivery_config_apply(body)
        assert payload["status"] == "validation_failed", body
        assert needle in payload["reason"], body


# ─────────────────────────── 疲劳面（替身钉形状） ───────────────────────────


class _StubFatigue:
    def __init__(self, states: tuple[object, ...], *, threshold: int = 5) -> None:
        self._states = states
        self._threshold = threshold

    def counts(self) -> tuple[object, ...]:
        return self._states

    def threshold(self) -> int:
        return self._threshold


def _state(**kw: object) -> SimpleNamespace:
    base = dict(
        dedup_key="l1:x:sh.600000:异动", ignored_streak=5, threshold=5, muted=False,
        last_answer=None, answered_at=None, asked_delivery_id="", as_of=_NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_fatigue_panel_is_empty_without_any_count() -> None:
    payload = _app(fatigue=_StubFatigue(())).api_delivery_fatigue()
    assert payload["status"] == "empty"
    assert "尚无疲劳计数" in payload["reason"]


def test_fatigue_panel_reports_counts_threshold_and_mute() -> None:
    app = _app(fatigue=_StubFatigue((_state(muted=True, last_answer="disable"),), threshold=3))
    payload = app.api_delivery_fatigue()
    assert payload["status"] == "ok"
    lines = payload["data"]["slots"]["sections"][1]["lines"]
    assert any("连续忽略 5 次（阈值 5）" in line and "已静音" in line for line in lines)
    assert any("最近答复：关闭" in line for line in lines)
    choices = payload["data"]["slots"]["sections"][2]["lines"]
    assert choices == ["reduce：减少", "disable：关闭", "keep：保持"]


def test_inquiries_table_names_the_delivery_id_for_the_answer_buttons() -> None:
    delivery_id = delivery_id_for("sig_test", "desktop", 0)
    orchestrator = SimpleNamespace(get=lambda _id: _record(body="这类推送对你有用吗？要不要减少？"))
    face = _Facade(fatigue=_StubFatigue((_state(asked_delivery_id=delivery_id),)),
                   delivery=orchestrator)
    payload = build_ui(host="127.0.0.1", port=0, delivery=face).api_delivery_inquiries()
    assert payload["status"] == "ok"
    row = payload["data"]["slots"]["rows"][0]
    assert row["delivery_id"] == delivery_id
    assert row["question"] == "这类推送对你有用吗？要不要减少？"


def test_inquiries_are_empty_when_nothing_is_awaiting_an_answer() -> None:
    payload = _app(fatigue=_StubFatigue((_state(),))).api_delivery_inquiries()
    assert payload["status"] == "empty"
    assert "没有待答复的调整询问" in payload["reason"]


# ─────────────────────────── 疲劳答复 ───────────────────────────


class _StubFeedback:
    """L3 采集面的鸭子替身：记账调用、回一支 `ok` 信封（`delivery.delivered=True`）。"""

    def __init__(self, *, delivered: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self._delivered = delivered

    def __call__(self, *, target: object, action: str, **kw: object) -> ResultEnvelope:
        self.calls.append({"target": target, "action": action, **kw})
        # 与 `FeedbackCollector` 的产出同形：`record.delivery.delivered` / `.note`。
        return ResultEnvelope.ok(SimpleNamespace(
            delivery=SimpleNamespace(
                delivered=self._delivered,
                note="" if self._delivered else "未注入事件接收面，本次反馈未送达",
            ),
        ))


def test_fatigue_answer_goes_through_the_feedback_face_with_the_choice_in_context() -> None:
    """答复经 L3 采集面铸事件（`action` 取合法固定值、语义载在 `context.fatigue_choice`）。"""
    delivery_id = delivery_id_for("sig_test", "desktop", 0)
    feedback = _StubFeedback()
    app = _app(fatigue=_StubFatigue((_state(asked_delivery_id=delivery_id),)), feedback=feedback)
    payload = app.api_delivery_fatigue_answer({"delivery_id": delivery_id, "choice": "disable"})
    assert payload["status"] == "ok", payload.get("reason")
    assert payload["data"]["delivered"] is True
    call = feedback.calls[0]
    assert call["target"] == {"kind": "delivery", "ref": delivery_id}
    assert call["action"] == "ignored"
    assert call["context"] == {"fatigue_choice": "disable"}


def test_fatigue_answer_refuses_a_push_that_is_not_awaiting_an_answer() -> None:
    """答复别的推送**不属本面**——显式拒，不让它静默落进一个不存在的询问。"""
    app = _app(fatigue=_StubFatigue((_state(),)), feedback=_StubFeedback())
    payload = app.api_delivery_fatigue_answer({
        "delivery_id": delivery_id_for("sig_test", "desktop", 0), "choice": "keep",
    })
    assert payload["status"] == "validation_failed"
    assert "不是待答复的调整询问" in payload["reason"]


def test_fatigue_answer_rejects_an_unknown_choice() -> None:
    app = _app(fatigue=_StubFatigue((_state(),)), feedback=_StubFeedback())
    payload = app.api_delivery_fatigue_answer({"delivery_id": "dlv_" + "0" * 20, "choice": "maybe"})
    assert payload["status"] == "validation_failed"
    assert "reduce / disable / keep" in payload["reason"]


def test_fatigue_answer_reports_when_the_event_never_reached_the_bus() -> None:
    """总线未接通 ⇒ 如实标注「未送达」，**不假装收到答复**。"""
    delivery_id = delivery_id_for("sig_test", "desktop", 0)
    app = _app(fatigue=_StubFatigue((_state(asked_delivery_id=delivery_id),)),
               feedback=_StubFeedback(delivered=False))
    payload = app.api_delivery_fatigue_answer({"delivery_id": delivery_id, "choice": "reduce"})
    assert payload["status"] == "ok"
    assert payload["data"]["delivered"] is False
    assert "未送达" in payload["data"]["applied"]


# ─────────────────────────── 反馈对话框 ───────────────────────────


def test_feedback_dialog_carries_the_five_actions_for_a_known_push() -> None:
    delivery_id = delivery_id_for("sig_test", "desktop", 0)
    face = _Facade(delivery=SimpleNamespace(get=lambda _id: _record(title="异动提示")))
    payload = build_ui(host="127.0.0.1", port=0, delivery=face).api_delivery_feedback(
        delivery_id=delivery_id
    )
    assert payload["status"] == "ok"
    slots = payload["data"]["slots"]
    assert slots["target"] == {"kind": "delivery", "ref": delivery_id}
    assert slots["actions"] == ["adopted", "ignored", "rejected", "queried", "liked"]
    assert slots["reason_required"] == ["rejected"]


def test_feedback_dialog_splits_missing_malformed_and_unknown_targets() -> None:
    """三种缺口互不冒充：缺标识 / 形态非法 ⇒ `validation_failed`；形态合法但无留痕 ⇒ `unavailable`。"""
    face = _Facade(delivery=SimpleNamespace(get=lambda _id: None))
    app = build_ui(host="127.0.0.1", port=0, delivery=face)
    assert app.api_delivery_feedback(delivery_id="")["status"] == "validation_failed"
    assert app.api_delivery_feedback(delivery_id="not-an-id")["status"] == "validation_failed"
    unknown = app.api_delivery_feedback(delivery_id=delivery_id_for("sig_test", "desktop", 0))
    assert unknown["status"] == "unavailable"
    assert "无此留痕" in unknown["reason"]
