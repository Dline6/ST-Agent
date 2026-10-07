"""每周反思报告本体（[08 §2](../../docs/技术架构-v2/08-L6-反思演进.md)）的验收用例。

对齐任务文件 [`T-L6-001.2`](../项目管理/tasks/T-L6-001.2-每周反思报告本体.md) 的 GWT-1..5——
四段齐备与候选承载、四项条目两通道同源、两处空态分开判定、完整 Trace 与中性化边界、
生成幂等。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from l6_helpers import (
    CHG_ID,
    DLV_ID,
    FB_ADOPTED,
    FB_IGNORED,
    FB_REJECTED,
    MN_ID,
    NOW,
    PASS,
    PREV_WEEK,
    PROPOSAL_REASON,
    REJECT_REASON,
    TRACE_ID,
    WEEK,
    advisor,
    delivery_record,
    fatigue,
    feedback_event,
    frequency,
    ledger,
    memory_reader,
)
from st_agent.l0.storage import Store
from st_agent.l6 import (
    DAY_CONFIG_ID,
    INSUFFICIENT_NOTE,
    MIN_FEEDBACK_CONFIG_ID,
    NO_ADVISOR_NOTE,
    NO_TOUCH_NOTE,
    TEMPLATE_CONFIG_ID,
    TIME_CONFIG_ID,
    WEEK_SECTIONS,
    FeedbackPool,
    ProposalCandidate,
    ReportTemplate,
    WeeklyReportBuilder,
    WeeklyReportError,
    week_key,
    week_window,
)

EARLIER = NOW - timedelta(days=3)


class FailingGuard:
    """总判不合规的中性化守卫替身（钉住「命中即抛错」）。"""

    def check_output(self, text: str):
        return SimpleNamespace(
            passed=False, findings=[SimpleNamespace(kind="first_person", matched="我")],
        )


def build(
    tmp_path, *, store=None, pool=None, reader_=None, advisor_=None, guard=None,
    orchestrator=None, frequency_=None, fatigue_=None, dispatcher=None,
    min_feedback=3,
):
    store = store if store is not None else Store.create(tmp_path / "root", PASS)
    pool = pool if pool is not None else FeedbackPool(store=store, now=lambda: NOW)
    reports = WeeklyReportBuilder(
        store=store, pool=pool,
        orchestrator=orchestrator if orchestrator is not None else ledger(delivery_record()),
        frequency=frequency_ if frequency_ is not None else frequency((("k", 3),)),
        fatigue=fatigue_ if fatigue_ is not None else fatigue((("k", 5, True),)),
        reader=reader_, advisor=advisor_, dispatcher=dispatcher, guard=guard,
        default_min_feedback=min_feedback, now=lambda: NOW,
    )
    return reports, pool, store


class TestGwt1FourSections:
    """GWT-1：四段齐备；④ 只承载候选（`config_id` + 现值 + 建议值 + 理由 + trace 依据）。"""

    def test_four_sections_with_content(self, tmp_path):
        reports, pool, _store = build(
            tmp_path, reader_=memory_reader(nodes=({"type": "evolution"},)),
            advisor_=advisor({
                "config_id": "attention-budget.current-mode", "current": "workday",
                "suggested": "weekend", "reason": PROPOSAL_REASON, "trace_ref": DLV_ID,
            }),
            min_feedback=2,
        )
        pool.consume(feedback_event(feedback_id=FB_ADOPTED, action="adopted"))
        pool.consume(feedback_event(feedback_id=FB_REJECTED, action="rejected",
                                    reason=REJECT_REASON))
        report = reports.build(WEEK, now=NOW)

        assert [s.name for s in report.sections] == list(WEEK_SECTIONS)
        by_name = {s.name: s for s in report.sections}
        assert not report.empty and not report.insufficient
        assert any("本周共记录投递 1 条" in line.text for line in by_name["week_digest"].lines)
        assert any("采纳 1 条" in line.text for line in by_name["week_digest"].lines)
        assert any("过滤噪音（当前计数）" in line.text for line in by_name["week_digest"].lines)
        assert any("evolution" not in line.text and "维度 关注面" in line.text
                   for line in by_name["preference_drift"].lines)
        errors = by_name["errors_understood"]
        assert any(REJECT_REASON in line.text and not line.generated for line in errors.lines)
        assert len(report.proposals) == 1
        assert report.proposals[0].config_id == "attention-budget.current-mode"
        assert report.proposals[0].trace_ref == DLV_ID

    def test_proposals_carry_no_change_id_of_any_kind(self, tmp_path):
        reports, _pool, _store = build(
            tmp_path, advisor_=advisor({
                "config_id": "c", "current": 1, "suggested": 2, "reason": PROPOSAL_REASON,
            }),
        )
        report = reports.build(WEEK, now=NOW)
        dumped = report.model_dump(mode="json")
        assert CHG_ID not in repr(dumped)
        assert "change_id" not in repr(dumped)

    def test_proposal_candidate_renders_neutrally(self):
        line = ProposalCandidate(
            config_id="c", current=1, suggested=2, reason=PROPOSAL_REASON,
        ).as_line()
        assert line.generated and "1 → 2" in line.text

    @pytest.mark.parametrize("item", [
        {"config_id": "c", "reason": PROPOSAL_REASON},
        ProposalCandidate(config_id="c", current=1, suggested=2, reason=PROPOSAL_REASON),
    ])
    def test_advisor_items_may_be_models_or_mappings(self, tmp_path, item):
        reports, _pool, _store = build(tmp_path, advisor_=advisor(item))
        report = reports.build(WEEK, now=NOW)
        assert len(report.proposals) == 1

    def test_bad_advisor_item_is_rejected(self, tmp_path):
        reports, _pool, _store = build(tmp_path, advisor_=advisor({"only": "junk"}))
        with pytest.raises(WeeklyReportError):
            reports.build(WEEK, now=NOW)

    def test_missing_faces_degrade_to_explicit_empty_sections(self, tmp_path):
        reports = WeeklyReportBuilder(
            store=Store.create(tmp_path / "root", PASS), now=lambda: NOW,
        )
        report = reports.build(WEEK, now=NOW)
        by_name = {s.name: s for s in report.sections}
        assert "未接入记忆读取面" in by_name["preference_drift"].empty_note
        assert NO_ADVISOR_NOTE in by_name["adjustment_proposals"].empty_note


class TestGwt2Entries:
    """GWT-2：四项条目两通道同源、可写、越界即拒；未配置走内置缺省。"""

    def test_defaults(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        assert reports.day_of_week() == 6            # 周日
        assert reports.report_time().strftime("%H:%M") == "20:00"
        assert reports.min_feedback() == 3
        assert reports.template().sections == WEEK_SECTIONS
        assert reports.template().channels == ("desktop",)

    @pytest.mark.parametrize("setter, value, config_id, read", [
        ("set_day_of_week", 0, DAY_CONFIG_ID, lambda r: r.day_of_week()),
        ("set_time", "07:30", TIME_CONFIG_ID, lambda r: r.report_time().strftime("%H:%M")),
        ("set_min_feedback", 5, MIN_FEEDBACK_CONFIG_ID, lambda r: r.min_feedback()),
    ])
    def test_scalar_entries_are_writable_and_same_on_both_channels(
        self, tmp_path, setter, value, config_id, read
    ):
        reports, _pool, _store = build(tmp_path)
        change = getattr(reports, setter)(value)
        assert change is not None and change.config_id == config_id
        assert read(reports) == value
        assert reports.entry(config_id).default == value      # 读面同源

    def test_template_entry_round_trips(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        reports.set_template(ReportTemplate(
            sections=("week_digest", "adjustment_proposals"), channels=("email", "tts"),
        ))
        assert reports.entry(TEMPLATE_CONFIG_ID).default["channels"] == ["email", "tts"]
        assert reports.template().channels == ("email", "tts")
        assert reports.entry("nope").__class__.__name__ == "NoneType"

    @pytest.mark.parametrize("call, value", [
        ("set_day_of_week", 7), ("set_day_of_week", "sunday"),
        ("set_time", "25:00"), ("set_time", "8:00"),
        ("set_min_feedback", -1), ("set_min_feedback", True),
    ])
    def test_out_of_range_values_are_rejected_without_persisting(self, tmp_path, call, value):
        reports, _pool, _store = build(tmp_path)
        with pytest.raises(WeeklyReportError):
            getattr(reports, call)(value)
        assert reports.changes() == ()

    @pytest.mark.parametrize("template", [
        {"sections": ["nope"], "channels": ["desktop"]},
        {"sections": [], "channels": ["desktop"]},
        {"sections": ["week_digest"], "channels": ["carrier_pigeon"]},
        {"sections": ["week_digest"], "channels": []},
        {"sections": ["week_digest", "week_digest"], "channels": ["desktop"]},
    ])
    def test_bad_templates_are_rejected(self, tmp_path, template):
        reports, _pool, _store = build(tmp_path)
        with pytest.raises(WeeklyReportError):
            reports.set_template(template)

    def test_disabled_sections_are_not_rendered(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        reports.set_template({"sections": ["week_digest"], "channels": ["desktop"]})
        report = reports.build(WEEK, now=NOW)
        assert [s.name for s in report.sections] == ["week_digest"]


class TestGwt3EmptyStates:
    """GWT-3：**无触达**与**数据不足**分开判定，皆不硬凑。"""

    def test_no_touch_week_writes_the_no_touch_note(self, tmp_path):
        reports, _pool, _store = build(tmp_path, orchestrator=ledger())
        report = reports.build(WEEK, now=NOW)
        assert report.empty and not report.insufficient
        assert report.body == NO_TOUCH_NOTE
        assert all(s.empty for s in report.sections)

    def test_touch_without_enough_feedback_writes_the_insufficient_note(self, tmp_path):
        reports, pool, _store = build(tmp_path, orchestrator=ledger(delivery_record()))
        pool.consume(feedback_event(feedback_id=FB_ADOPTED, action="adopted"))
        report = reports.build(WEEK, now=NOW)          # 1 条反馈 < 阈值 3
        assert report.insufficient and not report.empty
        assert report.body == INSUFFICIENT_NOTE

    def test_threshold_zero_never_flags_insufficient(self, tmp_path):
        reports, _pool, _store = build(
            tmp_path, orchestrator=ledger(delivery_record()), min_feedback=0,
        )
        report = reports.build(WEEK, now=NOW)
        assert not report.insufficient and not report.empty
        assert "【本周为你做了什么】" in report.body


class TestGwt4TraceAndNeutrality:
    """GWT-4：完整 Trace 锚点；生成性文案过 01 §6，**用户数据不过**（D-053）。"""

    def test_trace_collects_every_anchor(self, tmp_path):
        reports, pool, _store = build(tmp_path, reader_=memory_reader(nodes=({"type": "evolution"},)))
        pool.consume(feedback_event(feedback_id=FB_REJECTED, action="rejected",
                                    reason=REJECT_REASON))
        report = reports.build(WEEK, now=NOW)
        assert report.trace.delivery_ids == (DLV_ID,)
        assert report.trace.signal_ids and report.trace.trace_ids == (TRACE_ID,)
        assert report.trace.feedback_ids == (FB_REJECTED,)
        assert report.trace.memory_node_ids == (MN_ID,)
        assert report.trace.evidence_refs

    def test_generated_text_is_gated_by_the_neutrality_guard(self, tmp_path):
        reports, _pool, _store = build(
            tmp_path, guard=FailingGuard(), orchestrator=ledger(delivery_record()),
        )
        with pytest.raises(WeeklyReportError):
            reports.build(WEEK, now=NOW)

    def test_user_reason_is_displayed_even_with_a_first_person_pronoun(self, tmp_path):
        reports, pool, _store = build(
            tmp_path, orchestrator=ledger(delivery_record()), min_feedback=0,
        )
        pool.consume(feedback_event(feedback_id=FB_REJECTED, action="rejected",
                                    reason=REJECT_REASON))
        report = reports.build(WEEK, now=NOW)          # 真守卫生效：不因用户的话抛错
        assert REJECT_REASON in report.body
        assert "我" in REJECT_REASON


class TestGwt5Idempotence:
    """GWT-5：同一 ``(周, 留痕)`` 重跑结论恒同、写同一路径。"""

    def test_rebuild_writes_the_same_path_with_the_same_content(self, tmp_path):
        reports, pool, store = build(tmp_path, reader_=memory_reader())
        pool.consume(feedback_event(feedback_id=FB_IGNORED, action="ignored"))
        first = reports.build(WEEK, now=NOW)
        second = reports.build(WEEK, now=NOW)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert f"weekly/{WEEK}.json" in store.list_files("reflection")
        assert reports.stored(WEEK) is not None

    def test_stored_is_none_before_the_first_build(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        assert reports.stored(WEEK) is None

    def test_corrupt_stored_report_raises(self, tmp_path):
        reports, _pool, store = build(tmp_path)
        store.put("reflection", f"weekly/{WEEK}.json", b"{not json}")
        with pytest.raises(WeeklyReportError):
            reports.stored(WEEK)


class TestWeekWindow:
    """窗口口径（A4/A6）：ISO 周键与边界。"""

    def test_key_and_window_are_iso_weeks(self):
        assert week_key(date(2026, 10, 11)) == WEEK
        assert week_window(WEEK) == (date(2026, 10, 5), date(2026, 10, 11))
        assert week_key(date(2026, 9, 30)) == PREV_WEEK

    def test_year_boundary_uses_the_iso_year(self):
        assert week_key(date(2026, 1, 1)) == "2026-W01"
        assert week_key(date(2025, 12, 29)) == "2026-W01"     # ISO 口径：属次年第 1 周

    @pytest.mark.parametrize("key", ["", "2026", "2026-W1", "W41", "abc"])
    def test_bad_keys_are_rejected(self, key):
        with pytest.raises(WeeklyReportError):
            week_window(key)

    def test_due_only_matches_the_configured_day_and_time(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        assert reports.due(NOW)                                     # 周日 20:00
        assert not reports.due(NOW - timedelta(days=1))             # 周六
        assert not reports.due(NOW - timedelta(minutes=1))          # 周日 19:59
        reports.set_day_of_week(0)
        assert not reports.due(NOW)
        assert reports.due(NOW - timedelta(days=6))                 # 周一 20:00


class TestStoredWeeks:
    """已落盘周键的列举读面（[`T-UI-004.2`] 只增；表现层据此列历史与取最近一期）。"""

    def test_empty_before_any_report(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        assert reports.stored_weeks() == ()

    def test_lists_generated_weeks_ascending(self, tmp_path):
        reports, _pool, _store = build(tmp_path)
        reports.build(WEEK, now=NOW)                       # 2026-W41
        reports.build(PREV_WEEK, now=NOW - timedelta(days=7))
        assert reports.stored_weeks() == (PREV_WEEK, WEEK)  # 键零填充 ⇒ 字典序即时间序

    def test_non_week_files_do_not_impersonate_a_report(self, tmp_path):
        reports, _pool, store = build(tmp_path)
        reports.build(WEEK, now=NOW)
        store.put("reflection", "weekly/notes.json", b"{}")
        assert reports.stored_weeks() == (WEEK,)

    def test_without_a_store_the_read_face_is_empty(self):
        assert WeeklyReportBuilder().stored_weeks() == ()
