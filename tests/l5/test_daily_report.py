"""L5 每日报告本体与模板配置（07 §5）的验收用例。

对齐任务文件 [`T-L5-003.1`](../项目管理/tasks/T-L5-003.1-每日报告本体与模板配置.md) 的
GWT-1..5——四段齐备与多视角并列、模板两通道同源且可订阅到任一渠道、空态显式、
完整 Trace 与 01 §6 中性化、生成幂等与记忆面缺失的显式空态。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from l5_helpers import ANN_ID, MN_ID, NOW, PASS, RUN_ID, event
from st_agent.l0.storage import Store
from st_agent.l5.budget import AttentionBudget
from st_agent.l5.channel_policy import ChannelPolicies
from st_agent.l5.channels import ChannelDispatcher, ChannelPayload, ChannelResult
from st_agent.l5.daily_report import (
    NO_CONTENT_NOTE,
    SECTION_NAMES,
    DailyReportBuilder,
    ReportTemplate,
)
from st_agent.l5.daily_report_store import REPORT_PREFIX, TIME_CONFIG_ID, TEMPLATE_CONFIG_ID
from st_agent.l5.delivery import DeliveryOrchestrator
from st_agent.l5.errors import DailyReportValidationError
from st_agent.l5.frequency import FrequencyController
from st_agent.l5.signal import adopt_signal

CST = timezone(timedelta(hours=8))
DAY = date(2026, 10, 6)
MIDDAY = datetime(2026, 10, 6, 11, 0, tzinfo=CST)


class RecordingChannel:
    """记录投递载荷的渠道替身。"""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.payloads: list[ChannelPayload] = []

    def deliver(self, payload: ChannelPayload) -> ChannelResult:
        self.payloads.append(payload)
        return ChannelResult(channel=self.channel, status="ok")

    def health(self):
        raise NotImplementedError

    def degrade(self, reason: str) -> ChannelResult:
        return ChannelResult(channel=self.channel, status="unavailable", detail=reason)


class FailingGuard:
    """总判不合规的中性化守卫替身（用于钉住「命中即抛错」）。"""

    def check_output(self, text: str):
        return SimpleNamespace(
            passed=False,
            findings=[SimpleNamespace(kind="first_person", matched="我")],
        )


def routine_signal(conclusion: str = "该标的近两日公告密度高于其 30 日均值"):
    """一条 `routine` 信号（缺省情境下走日报路由 ⇒ 成为待汇总项）。"""
    return adopt_signal(event(level="routine", content_ref={
        "conclusion": conclusion,
        "lens_stances": [
            {"lens_id": "lens_" + "a" * 20, "stance": "positive", "summary": "资金面显示净流入"},
            {"lens_id": "lens_" + "b" * 20, "stance": "negative", "summary": "基本面指标走弱"},
        ],
    }))


def reader(*, confidence: float = 0.8, slices: bool = True):
    """L2 `MemoryReader` 的鸭子替身。"""
    items = ()
    if slices:
        items = (SimpleNamespace(
            node=SimpleNamespace(
                memory_node_id=MN_ID, type="attention",
                updated_at=datetime(2026, 10, 5, 9, 0, tzinfo=CST),
            ),
            confidence=confidence, source="user_stated",
        ),)
    return SimpleNamespace(query=lambda spec: SimpleNamespace(slices=items, as_of=NOW))


def build(tmp_path, *, store=None, reader_=None, dispatcher=None, budget=None, guard=None, frequency=None):
    store = store if store is not None else Store.create(tmp_path / "root", PASS)
    policies = ChannelPolicies(store=store, now=lambda: NOW)
    dispatcher = dispatcher if dispatcher is not None else ChannelDispatcher({})
    orch = DeliveryOrchestrator(
        dispatcher=dispatcher, policies=policies,
        budget=budget if budget is not None else AttentionBudget(), store=store, now=lambda: NOW,
    )
    reports = DailyReportBuilder(
        store=store, orchestrator=orch, reader=reader_,
        frequency=frequency if frequency is not None else FrequencyController(store=store, now=lambda: NOW),
        dispatcher=dispatcher, guard=guard, now=lambda: NOW,
    )
    return orch, reports, store


class TestGwt1FourSections:
    """GWT-1：四段齐备；多视角摘要逐视角并列、不合并分歧。"""

    def test_four_sections_with_content(self, tmp_path):
        orch, reports, _store = build(tmp_path, reader_=reader())
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        orch.dispatch(routine_signal("昨日的一条"), content_type="brief", now=NOW - timedelta(days=1))
        report = reports.build(DAY, now=NOW)
        assert [s.name for s in report.sections] == list(SECTION_NAMES)
        assert not report.empty
        by_name = {s.name: s for s in report.sections}
        assert by_name["yesterday_review"].lines          # 昨日有留痕 ⇒ 有回顾陈述
        assert any("sh.600000" in line for line in by_name["today_focus"].lines)
        digest = "\n".join(by_name["lens_digest"].lines)
        assert "[positive] 资金面显示净流入" in digest     # 逐视角摘要并列保留
        assert "[negative] 基本面指标走弱" in digest
        assert any("attention" in line for line in by_name["memory_update"].lines)

    def test_memory_section_without_reader_is_explicit_empty(self, tmp_path):
        orch, reports, _store = build(tmp_path)
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        report = reports.build(DAY, now=NOW)
        section = next(s for s in report.sections if s.name == "memory_update")
        assert section.empty and "未接入记忆读取面" in section.empty_note


class TestGwt2Template:
    """GWT-2：模板两通道读到同一份值、可订阅到任一渠道、未配置走内置缺省。"""

    def test_default_template_is_the_four_sections_on_desktop(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        template = reports.template()
        assert template.sections == SECTION_NAMES
        assert template.channels == ("desktop",)

    def test_both_channels_read_the_same_value(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        reports.set_template(
            ReportTemplate(sections=("today_focus", "lens_digest"), channels=("email", "tts")),
            trace_ref=None,
        )
        entry = reports.entry(TEMPLATE_CONFIG_ID)
        assert entry.default["sections"] == ["today_focus", "lens_digest"]
        assert entry.default["channels"] == ["email", "tts"]
        assert reports.template().channels == ("email", "tts")
        assert reports.entry(TEMPLATE_CONFIG_ID).default == entry.default   # 读面同源

    def test_time_is_a_writable_scalar_entry(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        change = reports.set_time("07:30")
        assert change is not None and change.config_id == TIME_CONFIG_ID
        assert reports.report_time().strftime("%H:%M") == "07:30"
        assert reports.entry(TIME_CONFIG_ID).default == "07:30"

    def test_bad_template_and_time_are_rejected(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        with pytest.raises(DailyReportValidationError):
            reports.set_template({"sections": ["nope"], "channels": ["desktop"]})
        with pytest.raises(DailyReportValidationError):
            reports.set_template({"sections": [], "channels": ["desktop"]})
        with pytest.raises(DailyReportValidationError):
            reports.set_template({"sections": ["today_focus"], "channels": ["carrier_pigeon"]})
        with pytest.raises(DailyReportValidationError):
            reports.set_time("25:00")

    def test_disabled_sections_are_not_rendered(self, tmp_path):
        orch, reports, _store = build(tmp_path, reader_=reader())
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        reports.set_template({"sections": ["today_focus"], "channels": ["desktop"]})
        report = reports.build(DAY, now=NOW)
        assert [s.name for s in report.sections] == ["today_focus"]


class TestGwt3EmptyState:
    """GWT-3：四段皆空写「今天没有需要打扰你的事」；段级空态各给说明。"""

    def test_nothing_to_report_is_explicit(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        report = reports.build(DAY, now=NOW)
        assert report.empty
        assert report.body == NO_CONTENT_NOTE

    def test_partial_empty_gets_a_section_note(self, tmp_path):
        orch, reports, _store = build(tmp_path)
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        report = reports.build(DAY, now=NOW)
        assert not report.empty
        review = next(s for s in report.sections if s.name == "yesterday_review")
        assert review.empty and "2026-10-05" in review.empty_note


class TestGwt4TraceAndNeutrality:
    """GWT-4：完整 Trace 锚点；整段文案过 01 §6（命中即抛错）。"""

    def test_trace_anchors_are_recorded(self, tmp_path):
        orch, reports, _store = build(tmp_path, reader_=reader())
        result = orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        report = reports.build(DAY, now=NOW)
        assert result.record.signal_id in report.trace.signal_ids
        assert result.record.delivery_id in report.trace.delivery_ids
        assert MN_ID in report.trace.memory_node_ids

    def test_neutrality_hit_raises(self, tmp_path):
        orch, reports, _store = build(tmp_path, guard=FailingGuard())
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        with pytest.raises(DailyReportValidationError):
            reports.build(DAY, now=NOW)


class TestGwt5IdempotentAndPersisted:
    """GWT-5：同一 `(day, 留痕状态)` 恒同结论、写同一路径。"""

    def test_rebuild_is_identical_and_writes_one_path(self, tmp_path):
        orch, reports, store = build(tmp_path, reader_=reader())
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        first = reports.build(DAY, now=NOW)
        second = reports.build(DAY, now=NOW + timedelta(hours=1))
        assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})
        assert store.get("execution_log", f"{REPORT_PREFIX}{DAY.isoformat()}.json") is not None

    def test_due_is_a_pure_time_comparison(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        assert reports.due(datetime(2026, 10, 6, 7, 59, tzinfo=CST)) is False
        assert reports.due(datetime(2026, 10, 6, 8, 0, tzinfo=CST)) is True
        reports.set_time("09:30")
        assert reports.due(datetime(2026, 10, 6, 9, 0, tzinfo=CST)) is False


class TestDelivery:
    """「可订阅到任一渠道」：日报经既有渠道面投出，不另造投递路径。"""

    def test_report_is_delivered_through_the_channel_face(self, tmp_path):
        email = RecordingChannel("email")
        orch, reports, _store = build(
            tmp_path, reader_=reader(), dispatcher=ChannelDispatcher({"email": email}),
        )
        orch.dispatch(routine_signal(), content_type="brief", now=MIDDAY)
        reports.set_template({"sections": ["today_focus"], "channels": ["email"]})
        delivered = reports.deliver(reports.build(DAY, now=NOW))
        assert delivered.delivered_channel == "email"
        assert len(email.payloads) == 1
        assert email.payloads[0].body == delivered.body

    def test_without_dispatcher_nothing_is_claimed(self, tmp_path):
        _orch, reports, _store = build(tmp_path)
        assert reports.deliver(reports.build(DAY, now=NOW)).delivered_channel == ""
