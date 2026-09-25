"""T-SC-001.5 测试：01-平台共享契约 §8 时间锚点 + §11 事件信封。"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from st_agent.contracts import (
    CORE_EVENTS,
    DataAnchor,
    PlatformEvent,
    ResultEnvelope,
    StalenessVerdict,
)

TZ = timezone(timedelta(hours=8))   # 用户本地时区（东八区）
T = datetime(2026, 9, 25, 15, 0, tzinfo=TZ)


# ───────────────────────── §8 时间锚点 ─────────────────────────


class TestSection8DataAnchor:
    """GWT-5：标注数据时间统一用 dataset_snapshot_id + as_of，不另立口径。"""

    def anchor(self, **kw) -> DataAnchor:
        base = dict(dataset_snapshot_id="snap_0123456789abcdef0123",
                    as_of=T, domain="kline")
        base.update(kw)
        return DataAnchor(**base)

    def test_anchor_carries_snapshot_and_as_of(self):
        a = self.anchor()
        assert a.dataset_snapshot_id.startswith("snap_")
        assert a.as_of == T and a.domain == "kline"

    def test_domains_cover_baostock_five_plus_announcement(self):
        """数据域枚举对齐 BaoStock 五域 + 公告域（数据库设计 02–04 / 02-L0 §5）。"""
        for d in ("kline", "financial", "company_report", "sector", "macro", "announcement"):
            assert self.anchor(domain=d).domain == d
        with pytest.raises(ValidationError):
            self.anchor(domain="weather")

    def test_naive_as_of_rejected(self):
        """as_of 必须带时区（§8 内部传输带时区语义）。"""
        with pytest.raises(ValidationError, match="时区"):
            self.anchor(as_of=datetime(2026, 9, 25, 15, 0))

    def test_staleness_check(self):
        a = self.anchor()
        assert a.is_stale(T + timedelta(days=4), timedelta(days=3)) is True
        assert a.is_stale(T + timedelta(days=2), timedelta(days=3)) is False

    def test_staleness_verdict_carries_last_updated(self):
        """「最后更新时间」的载体：unavailable 分支的配对类型。"""
        v = StalenessVerdict(stale=True, last_updated_at=T,
                             detail="行情数据停留于 2026-09-25 15:00，今日同步未完成")
        assert v.last_updated_at == T and v.stale is True
        with pytest.raises(ValidationError, match="时区"):
            StalenessVerdict(stale=False, last_updated_at=datetime(2026, 9, 25),
                             detail="x")

    def test_unavailable_envelope_pairs_with_verdict(self):
        """§8 → §5 衔接：数据源延迟走 unavailable + last_updated_at（同一口径）。"""
        env = ResultEnvelope.unavailable("行情数据延迟", last_updated_at=T)
        assert env.last_updated_at == T


# ───────────────────────── §11 事件信封 ─────────────────────────


class TestSection11PlatformEvent:
    """GWT-SC11：事件名属于核心清单且携带关联 trace_id / change_id（如适用）。"""

    def test_all_core_events_constructible(self):
        """10 个事件名全部可构造（必带的关联 ID 就位）。"""
        now = datetime.now(TZ)
        cases = {
            "SkillRunCompleted": dict(trace_id="tr_0123456789abcdef012345"),
            "SkillRunFailed": dict(trace_id="tr_0123456789abcdef012345"),
            "MemoryConflictDetected": dict(trace_id="tr_0123456789abcdef012345"),
            "SignalEmitted": dict(trace_id="tr_0123456789abcdef012345"),
            "DeliverySettled": dict(trace_id="tr_0123456789abcdef012345"),
            "FeedbackRecorded": dict(trace_id="tr_0123456789abcdef012345"),
            "NetworkRequestLogged": dict(),
            "BehaviorViolation": dict(trace_id="tr_0123456789abcdef012345"),
            "ChangeApplied": dict(change_id="chg_0123456789abcdef0123"),
            "ChangeRolledBack": dict(change_id="chg_0123456789abcdef0123"),
        }
        for name, extra in cases.items():
            ev = PlatformEvent(event=name, occurred_at=now, **extra)   # type: ignore[arg-type]
            assert ev.event == name
        assert set(cases) == {e[0] for e in CORE_EVENTS}

    def test_traced_event_requires_trace_id(self):
        """SkillRun/Signal 类事件必须携带 trace_id（§11 强制关联）。"""
        with pytest.raises(ValidationError, match="trace_id"):
            PlatformEvent(event="SignalEmitted",
                          occurred_at=datetime.now(TZ))

    def test_change_event_requires_change_id(self):
        """Change 类事件必须携带 change_id。"""
        with pytest.raises(ValidationError, match="change_id"):
            PlatformEvent(event="ChangeApplied", occurred_at=datetime.now(TZ))

    def test_unknown_event_rejected(self):
        with pytest.raises(ValidationError):
            PlatformEvent(event="ServerCoffeeEmpty", occurred_at=datetime.now(TZ))

    def test_naive_occurred_at_rejected(self):
        with pytest.raises(ValidationError, match="时区"):
            PlatformEvent(event="FeedbackRecorded",
                          trace_id="tr_0123456789abcdef012345",
                          occurred_at=datetime(2026, 9, 25, 15, 0))

    def test_event_immutable(self):
        ev = PlatformEvent(event="SignalEmitted", trace_id="tr_0123456789abcdef012345",
                           occurred_at=datetime.now(TZ))
        with pytest.raises(ValidationError):
            ev.event = "FeedbackRecorded"
