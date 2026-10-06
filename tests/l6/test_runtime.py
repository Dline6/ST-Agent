"""L6 装配缝与报告投出（[`runtime.py`](../../src/st_agent/l6/runtime.py)）的验收用例。

对齐任务文件 [`T-L6-001.3`](../项目管理/tasks/T-L6-001.3-L6装配缝与报告投出.md) 的 GWT-1..5——
经 L5 渠道面投出与「不假装送达」、「一周一次」从留痕读（重启安全）、总线订阅让反馈自动落池、
各可选面缺省的显式降级、01 §7 族按条目取值形态分流。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from l6_helpers import (
    FB_ADOPTED,
    NOW,
    PASS,
    WEEK,
    delivery_record,
    feedback_event,
    ledger,
    recording_channel,
)
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l0.storage import Store
from st_agent.l1.events import EventBus
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l5.channels import ChannelDispatcher
from st_agent.l6 import (
    DAY_CONFIG_ID,
    TEMPLATE_CONFIG_ID,
    TIME_CONFIG_ID,
    FeedbackPool,
    WeeklyReportBuilder,
    build_l6,
    weekly_report_family,
)
from st_agent.l6.registry_adapter import TEMPLATE_WRITE_OWNER

OWN_MODE = "attention-budget.current-mode"


def stack(tmp_path, *, channel_status="ok", **kw):
    store = Store.create(tmp_path / "root", PASS)
    adapter = recording_channel("desktop", status=channel_status)
    dispatcher = ChannelDispatcher({"desktop": adapter})
    built = build_l6(
        store, dispatcher=dispatcher, orchestrator=ledger(delivery_record()), **kw,
    )
    return built, adapter, store


class TestGwt1Delivery:
    """GWT-1：经 L5 渠道面投出并回写渠道；未接投出面 / 渠道不可用 ⇒ 不假装送达。"""

    def test_report_is_delivered_through_the_channel_face(self, tmp_path):
        built, adapter, store = stack(tmp_path)
        report = built.reports.build(WEEK, now=NOW)
        delivered = built.reports.deliver(report, now=NOW)
        assert delivered.delivered_channel == "desktop"
        assert adapter.payloads and adapter.payloads[0].title == report.title
        assert built.reports.stored(WEEK).delivered_channel == "desktop"   # 回写留痕

    def test_without_a_dispatcher_nothing_is_pretended(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        reports = WeeklyReportBuilder(store=store, now=lambda: NOW)
        report = reports.build(WEEK, now=NOW)
        assert reports.deliver(report, now=NOW).delivered_channel == ""
        assert reports.stored(WEEK).delivered_channel == ""

    def test_unavailable_channels_are_not_reported_as_delivered(self, tmp_path):
        built, adapter, _store = stack(tmp_path, channel_status="unavailable")
        report = built.reports.build(WEEK, now=NOW)
        delivered = built.reports.deliver(report, now=NOW)
        assert delivered.delivered_channel == ""
        assert adapter.payloads                                        # 试过了，只是没成
        assert built.reports.stored(WEEK).delivered_channel == ""


class TestGwt2OncePerWeekFromTheLedger:
    """GWT-2：「一周一次」判据取**盘上留痕**，重启后依然成立。"""

    def test_publish_delivers_once(self, tmp_path):
        built, adapter, _store = stack(tmp_path)
        first = built.reports.publish(WEEK, now=NOW)
        assert first is not None and first.delivered_channel == "desktop"
        assert built.reports.publish(WEEK, now=NOW) is None            # 第二轮回 None
        assert len(adapter.payloads) == 1

    def test_a_fresh_builder_still_knows_it_was_delivered(self, tmp_path):
        """**重启安全**：换一个构造器续读既有留痕，仍不重投。"""
        built, adapter, store = stack(tmp_path)
        built.reports.publish(WEEK, now=NOW)
        reborn = build_l6(
            store, dispatcher=ChannelDispatcher({"desktop": adapter}),
            orchestrator=ledger(delivery_record()), now=lambda: NOW,
        )
        assert reborn.reports.publish(WEEK, now=NOW) is None
        assert len(adapter.payloads) == 1

    def test_a_stored_but_undelivered_report_is_retried_not_regenerated(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        plain = WeeklyReportBuilder(store=store, now=lambda: NOW)
        plain.build(WEEK, now=NOW)                                     # 生成但未投出
        adapter = recording_channel("desktop")
        with_dispatcher = WeeklyReportBuilder(
            store=store, dispatcher=ChannelDispatcher({"desktop": adapter}), now=lambda: NOW,
        )
        assert with_dispatcher.publish(WEEK, now=NOW).delivered_channel == "desktop"
        assert plain.stored(WEEK).generated_at == with_dispatcher.stored(WEEK).generated_at


class TestGwt3AssemblyAndSubscription:
    """GWT-3：总线订阅让反馈**一跳落池**；未注入总线则**不假装已接线**。"""

    def test_feedback_lands_in_the_pool_via_the_bus(self, tmp_path):
        bus = EventBus()
        built, _adapter, _store = stack(tmp_path, events=bus)
        assert built.feedback_subscription is not None
        assert bus.subscribers("FeedbackRecorded") == ("l6:feedback-pool",)
        result = bus.publish(feedback_event(feedback_id=FB_ADOPTED))
        assert result.delivered
        assert built.pool.get(FB_ADOPTED) is not None

    def test_malformed_feedback_is_recorded_per_subscriber_and_not_pooled(self, tmp_path):
        bus = EventBus()
        built, _adapter, _store = stack(tmp_path, events=bus)
        bad = PlatformEvent(
            event="FeedbackRecorded",
            payload={"feedback_id": FB_ADOPTED, "target": {"kind": "delivery", "ref": "bogus"},
                     "action": "adopted"},
            occurred_at=NOW,
        )
        result = bus.publish(bad)
        assert not result.delivered and result.failed                   # 记因、不吞（01 §11）
        assert result.failed[0].subscriber_id == "l6:feedback-pool"
        assert built.pool.counts().total == 0                           # 拒收 ⇒ 不落盘

    def test_without_a_bus_there_is_no_subscription(self, tmp_path):
        built, _adapter, _store = stack(tmp_path)
        assert built.feedback_subscription is None
        assert built.pool.counts().total == 0                           # 不假装已接线

    def test_registry_receives_the_family(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        facade = ConfigRegistryFacade(store)
        built = build_l6(store, registry=facade, now=lambda: NOW)
        listed = {e.config_id for e in facade.list(scope="global")}
        assert {DAY_CONFIG_ID, TIME_CONFIG_ID, TEMPLATE_CONFIG_ID} <= listed
        assert built.registry is facade


class TestGwt4ExplicitDegradation:
    """GWT-4：任一可选面缺省都走**显式降级**，没有一处静默。"""

    def test_bare_assembly_still_works(self, tmp_path):
        built = build_l6(now=lambda: NOW)
        assert built.pool.counts().total == 0
        report = built.reports.build(WEEK, now=NOW)
        assert report.empty and report.delivered_channel == ""
        assert built.reports.entry(DAY_CONFIG_ID).config_id == DAY_CONFIG_ID

    def test_pool_and_reports_can_be_overridden(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        pool = FeedbackPool(store=store, now=lambda: NOW)
        reports = WeeklyReportBuilder(store=store, pool=pool, now=lambda: NOW)
        built = build_l6(store, pool=pool, reports=reports, now=lambda: NOW)
        assert built.pool is pool and built.reports is reports


class TestGwt5FamilyAdapter:
    """GWT-5：读面全接；标量可写、对象类 fail-closed 并点名写面。"""

    def build_family(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        reports = WeeklyReportBuilder(store=store, now=lambda: NOW)
        return ConfigRegistryFacade(store), weekly_report_family(reports), reports

    def test_scalar_entries_go_through_the_face(self, tmp_path):
        facade, _family, reports = self.build_family(tmp_path)
        facade.register_family(weekly_report_family(reports))
        change = facade.set(TIME_CONFIG_ID, "07:30")
        assert change is not None and change.config_id == TIME_CONFIG_ID
        assert reports.report_time().strftime("%H:%M") == "07:30"

    def test_object_entry_fails_closed_and_names_the_owner(self, tmp_path):
        _facade, family, _reports = self.build_family(tmp_path)
        with pytest.raises(RegistryValidationError) as exc:
            family.apply(TEMPLATE_CONFIG_ID, {"sections": ["week_digest"], "channels": ["desktop"]})
        assert TEMPLATE_WRITE_OWNER in str(exc.value)

    def test_unknown_and_foreign_ids_are_rejected(self, tmp_path):
        _facade, family, _reports = self.build_family(tmp_path)
        with pytest.raises(RegistryValidationError):
            family.apply("weekly-report.nope", 1)
        with pytest.raises(RegistryValidationError):
            family.apply(OWN_MODE, "workday")
        assert family.entry("daily-report.time") is None

    def test_bad_scalar_value_raises_the_registry_vocabulary(self, tmp_path):
        _facade, family, _reports = self.build_family(tmp_path)
        with pytest.raises(RegistryValidationError):
            family.apply(TIME_CONFIG_ID, "25:00")


def test_no_timers_or_threads_in_l6(monkeypatch, tmp_path):
    """A3：本层不起定时器 / 线程——装配面只做注入、订阅与接线。"""
    import st_agent.l6.runtime as runtime

    started: list[str] = []
    monkeypatch.setattr("threading.Thread.start", lambda self: started.append("thread"), raising=False)
    build_l6(Store.create(tmp_path / "root", PASS), events=EventBus(), now=lambda: NOW)
    assert started == []
    assert not hasattr(runtime, "Timer") and "schedule" not in dir(runtime)


def test_l6_never_imports_the_composition_root():
    """A1：层不得反向 import 组合根。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6"
    for path in sorted(src.glob("*.py")):
        assert "st_agent.app" not in path.read_text(encoding="utf-8"), path.name


def test_delivery_timestamp_is_not_in_the_future(tmp_path):
    """投出用的时刻由调用方按次传入，不读系统时钟以外的第二处时间源。"""
    built, _adapter, _store = stack(tmp_path)
    report = built.reports.publish(WEEK, now=NOW)
    assert isinstance(report.generated_at, datetime)
    assert report.generated_at <= NOW + timedelta(seconds=1)
