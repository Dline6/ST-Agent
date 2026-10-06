"""上游信号产生规则（`l5.sources`）的验收用例（[07 §1](../../docs/技术架构-v2/07-L5-主动触达.md)）。

规则面是「上游产出 → `SignalEmitted`」的唯一映射处：本文件钉住三件事——**匹配**（按注册名
命中带版本的拼接名）、**取值**（缺字段 / 非映射 / 换时区一律显式抛）、**文案**（生成后即过
[01 §6](../../docs/技术架构-v2/01-平台共享契约.md)，命中不投出去）。求值是纯函数，故全部离线。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from l5_helpers import ANN_ID, NOW, RUN_ID, TRACE_ID

from st_agent.contracts.capability_types import LensOpinion
from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.l1.scheduler.models import ScheduledRun
from st_agent.l4.deliberation import DeliberationResult
from st_agent.l5.errors import SignalEmissionError
from st_agent.l5.signal import adopt_signal
from st_agent.l5.sources import (
    DEFAULT_MONITOR_RULES,
    MONITOR_LENS,
    MonitorRule,
    signal_event_for_analysis,
    signal_events_for_run,
)

WATCH = DEFAULT_MONITOR_RULES[0]
"""`sk_stock_watch` 的规则（官方 Pack 里唯一带 `frequency_minutes` 的可调度监控）。"""

TRIGGERS = (
    {"code": "sh.600000", "condition": "异动", "detail": "2026-09-25 涨跌幅 +7.50%"},
    {"code": "sh.600000", "condition": "换手率", "detail": "2026-09-25 换手率 12.00%"},
)


def _run(*, skill_id: str = "sk_stock_watch_v1.0", status: str = "ok",
         data: object = None, evidence: bool = True) -> ScheduledRun:
    payload = {"triggered": list(TRIGGERS)} if data is None else data
    return ScheduledRun(
        target_id=skill_id, skill_id=skill_id, status=status,
        envelope=ResultEnvelope.ok(
            payload,
            evidence_refs=((EvidenceRef(kind="announcement_id", ref=ANN_ID),) if evidence else ()),
        ),
        skill_run_id=RUN_ID, trace_id=TRACE_ID, ran_at=NOW,
    )


class TestMonitorRules:
    """规则表与 `signal_events_for_run` 的匹配 / 取值 / 文案三条线。"""

    def test_rule_matches_the_versioned_skill_id(self):
        """运行结果给的是**带版本**的拼接名，规则声明的是**注册名**——匹配要剥版本。"""
        events = signal_events_for_run(_run())
        assert len(events) == 2, "逐条目一条"
        assert events[0].payload["dedup_key"] == "l1:stock-watch:sh.600000:异动"
        assert events[1].payload["dedup_key"] == "l1:stock-watch:sh.600000:换手率"

    def test_events_are_adoptable(self):
        """产出的结论 / 摘要过 [01 §6]，且证据与溯源齐备 ⇒ 采纳侧照单收下。"""
        signal = adopt_signal(signal_events_for_run(_run())[0])
        assert signal.level == "important"
        assert RUN_ID in signal.evidence_refs and ANN_ID in signal.evidence_refs
        assert signal.source_trace_id == TRACE_ID
        assert signal.content_ref.lens_stances[0].lens_id == MONITOR_LENS

    def test_unknown_skill_produces_nothing(self):
        assert signal_events_for_run(_run(skill_id="sk_unrelated_v1.0")) == ()

    def test_non_ok_run_produces_nothing(self):
        assert signal_events_for_run(_run(status="failed")) == ()
        assert signal_events_for_run(_run(status="gap")) == ()

    def test_absent_or_empty_items_produce_nothing(self):
        assert signal_events_for_run(_run(data={})) == ()
        assert signal_events_for_run(_run(data={"triggered": []})) == ()

    def test_non_mapping_item_is_rejected(self):
        with pytest.raises(SignalEmissionError, match="须为映射"):
            signal_events_for_run(_run(data={"triggered": [1]}))

    def test_missing_declared_field_is_named(self):
        """缺规则所需字段即抛，并**点名**是哪个字段（不静默补空）。"""
        broken = {"triggered": [{"code": "sh.600000", "condition": "异动"}]}
        with pytest.raises(SignalEmissionError, match="detail"):
            signal_events_for_run(_run(data=broken))

    def test_non_neutral_text_is_not_emitted(self):
        """生成的文案未过 [01 §6] ⇒ 抛错（**不把不合规文案投出去**）。"""
        dirty = {"triggered": [{
            "code": "sh.600000", "condition": "异动",
            "detail": "我认为这标的要涨",
        }]}
        with pytest.raises(SignalEmissionError, match="01 §6"):
            signal_events_for_run(_run(data=dirty))

    def test_naive_timestamp_is_rejected(self):
        run = _run().model_copy(update={"ran_at": datetime(2026, 9, 28, 8, 30)})
        with pytest.raises(SignalEmissionError, match="时区"):
            signal_events_for_run(run)

    def test_missing_trace_is_rejected(self):
        run = _run().model_copy(update={"trace_id": None})
        with pytest.raises(SignalEmissionError, match="trace_id"):
            signal_events_for_run(run)

    def test_custom_rules_win(self):
        """规则表可整体覆写（组合根与离线关卡的注入口）。"""
        only = (MonitorRule(
            skill_id="sk_stock_watch", level="routine", items_field="triggered",
            key_fields=("condition",), dedup_prefix="custom",
            conclusion="{condition} 命中（自定义规则）", summary="{condition} 的观察摘要",
        ),)
        events = signal_events_for_run(_run(), rules=only)
        assert [e.payload["dedup_key"] for e in events] == [
            "custom:异动", "custom:换手率",
        ]
        assert all(e.payload["level"] == "routine" for e in events)

    def test_template_rejects_positional_fields(self):
        with pytest.raises(SignalEmissionError, match="具名字段"):
            MonitorRule(
                skill_id="sk_x", level="routine", items_field="items",
                key_fields=("code",), dedup_prefix="x",
                conclusion="{} 命中", summary="{code} 的观察",
            ).required_fields


def _opinion(index: int, stance: str, reason: str | None = None) -> LensOpinion:
    return LensOpinion(
        lens_id=digest_id("lens", f"test-{index}"), stance=stance,
        key_reasons=(reason or f"该视角的第 {index} 条理由（中性陈述）",),
        evidence_refs=(ANN_ID,), confidence="medium", skills_triggered=(),
        trace_id=TRACE_ID,
    )


def _analysis(*, stances: tuple[str, ...] = ("positive", "negative", "neutral"),
              reasons: tuple[str, ...] = (), with_trace: bool = True):
    """一份合契约的多视角分析（真实 `LensOpinion` / `DeliberationResult`）。"""
    opinions = tuple(
        _opinion(index, stance, reasons[index] if index < len(reasons) else None)
        for index, stance in enumerate(stances)
    )
    trace = SimpleNamespace(trace_id=SimpleNamespace(value=TRACE_ID)) if with_trace else None
    return SimpleNamespace(
        envelope=ResultEnvelope.ok({"opinions": len(opinions)}, as_of=NOW),
        topic="sh.600000 是否值得关注",
        result=DeliberationResult(
            topic="topic", mode="deep", envelope=ResultEnvelope.ok({}), opinions=opinions,
        ),
        map=SimpleNamespace(trace=trace),
        traces=(),
    )


def _duck_analysis(*opinions: Any, with_trace: bool = True):
    """鸭子形态的分析结果——用于构造**契约类型本身造不出**的坏输入（如空理由列表）。"""
    trace = SimpleNamespace(trace_id=SimpleNamespace(value=TRACE_ID)) if with_trace else None
    return SimpleNamespace(
        envelope=ResultEnvelope.ok({"opinions": len(opinions)}, as_of=NOW),
        topic="sh.600000 是否值得关注",
        result=SimpleNamespace(opinions=tuple(opinions)),
        map=SimpleNamespace(trace=trace),
        traces=(),
    )


class TestDeliberationRule:
    """`signal_event_for_analysis`：多视角摘要形态 + 触发条件。"""

    def test_bearish_lens_triggers_one_digest_signal(self):
        event = signal_event_for_analysis(_analysis())
        assert event is not None
        signal = adopt_signal(event)
        assert signal.level == "important"
        assert signal.dedup_key == "l4:deliberation:sh.600000 是否值得关注"
        assert len(signal.content_ref.lens_stances) == 3, "逐视角并列（不合并分歧）"
        assert "多视角分析" in signal.content_ref.conclusion

    def test_unanimous_positive_produces_nothing(self):
        assert signal_event_for_analysis(_analysis(stances=("positive", "positive"))) is None

    def test_failed_envelope_produces_nothing(self):
        analysis = _analysis()
        analysis.envelope = ResultEnvelope.empty("无参与视角")
        assert signal_event_for_analysis(analysis) is None

    def test_below_the_trigger_threshold_produces_nothing(self):
        """阈值可调：要 2 个看空视角而只有 1 个 ⇒ 不产信号。"""
        from st_agent.l5.sources import DeliberationRule

        rule = DeliberationRule(min_triggering=2)
        assert signal_event_for_analysis(_analysis(stances=("negative", "positive")),
                                         rule=rule) is None

    def test_no_opinions_produces_nothing(self):
        assert signal_event_for_analysis(_analysis(stances=())) is None

    def test_missing_reason_list_is_rejected(self):
        """契约类型造不出空理由（01 §3 要求非空），故用鸭子形态钉住本面的守卫。"""
        broken = _duck_analysis(SimpleNamespace(
            lens_id=digest_id("lens", "test-0"), stance="negative",
            key_reasons=(), evidence_refs=(ANN_ID,),
        ))
        with pytest.raises(SignalEmissionError, match="理由列表"):
            signal_event_for_analysis(broken)

    def test_missing_trace_anchor_is_rejected(self):
        with pytest.raises(SignalEmissionError, match="推理链锚点"):
            signal_event_for_analysis(_analysis(with_trace=False))

    def test_non_neutral_reason_is_not_emitted(self):
        analysis = _analysis(reasons=("我担心这个标的",))
        with pytest.raises(SignalEmissionError, match="01 §6"):
            signal_event_for_analysis(analysis)
