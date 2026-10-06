"""L5 推送疲劳监控与答复回流（07 §7）的验收用例。

对齐任务文件 [`T-L5-003.3`](../项目管理/tasks/T-L5-003.3-推送疲劳监控与答复回流.md) 的
GWT-1..5——连续忽略计数与阈值、已读打断 / 不重复发问、三种答复三种落点、
计数可读且答复经 `FeedbackRecorded` 回流、中性化与损坏显式化。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from l5_helpers import NOW, PASS, content, event
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l0.storage import Store
from st_agent.l5.budget import AttentionBudget
from st_agent.l5.channel_policy import ChannelPolicies
from st_agent.l5.channels import ChannelDispatcher, ChannelPayload, ChannelResult
from st_agent.l5.delivery import DeliveryOrchestrator
from st_agent.l5.errors import FatigueValidationError
from st_agent.l5.fatigue import DEFAULT_THRESHOLD, FatigueMonitor
from st_agent.l5.fatigue_store import THRESHOLD_CONFIG_ID
from st_agent.l5.frequency import FrequencyController
from st_agent.l5.signal import adopt_signal

CST = timezone(timedelta(hours=8))
MINUTE = timedelta(minutes=1)
KEY = "sh.600000:announcement_density"


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
    """总判不合规的中性化守卫替身。"""

    def check_output(self, text: str):
        return SimpleNamespace(
            passed=False, findings=[SimpleNamespace(kind="first_person", matched="我")],
        )


def sig(dedup_key: str = KEY, conclusion: str = "该标的公告密度上行", level: str = "routine"):
    return adopt_signal(event(
        level=level, dedup_key=dedup_key, content_ref=content(conclusion=conclusion),
    ))


def setup(tmp_path, *, threshold: int = DEFAULT_THRESHOLD, guard=None, policies=None):
    store = Store.create(tmp_path / "root", PASS)
    policies = policies if policies is not None else ChannelPolicies(
        store=store, policies={"routine": (("desktop", "email"), (1,))}, now=lambda: NOW,
    )
    orch = DeliveryOrchestrator(
        dispatcher=ChannelDispatcher({"desktop": RecordingChannel("desktop"),
                                      "email": RecordingChannel("email")}),
        policies=policies, budget=AttentionBudget(), store=store, now=lambda: NOW,
    )
    monitor = FatigueMonitor(
        store=store, orchestrator=orch, policies=policies,
        threshold=threshold, guard=guard, now=lambda: NOW,
    )
    return orch, monitor, policies, store


def ignore(orch, *, key: str = KEY, count: int = 1, start: datetime = NOW) -> None:
    """把 ``count`` 条**同键不同文**的信号走完链、结算为「未确认」（= 一次忽略）。"""
    for index in range(count):
        moment = start + index * 5 * MINUTE
        orch.dispatch(
            sig(key, conclusion=f"第 {index + 1} 次公告密度上行（{moment:%m-%d %H:%M}）"),
            content_type="long_form", now=moment,
        )
        orch.advance(now=moment + 2 * MINUTE)      # 升级到第二级
        orch.advance(now=moment + 3 * MINUTE)      # 链尽未读 ⇒ 结算未确认


class TestGwt1CountAndInquiry:
    """GWT-1：连续忽略达阈值即发问；已读打断；已发问未答复不重复发；未达阈值不发问。"""

    def test_threshold_triggers_an_inquiry(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=3)
        ignore(orch, count=3)
        produced = monitor.sweep(now=NOW + 30 * MINUTE)
        assert len(produced) == 1
        inquiry = produced[0]
        assert inquiry.dedup_key == KEY and inquiry.ignored_streak == 3
        assert inquiry.delivery_id
        assert any(r.signal_id == inquiry.signal_id for r in orch.ledger())

    def test_below_threshold_does_not_ask(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=5)
        ignore(orch, count=2)
        assert monitor.sweep(now=NOW + 30 * MINUTE) == ()

    def test_a_read_in_between_resets_the_streak(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=3)
        ignore(orch, count=2)
        opener = orch.dispatch(
            sig(KEY, conclusion="一条会被读的"), content_type="long_form", now=NOW + 20 * MINUTE,
        )
        orch.mark_read(opener.record.delivery_id, now=NOW + 21 * MINUTE)
        ignore(orch, count=2, start=NOW + 30 * MINUTE)
        assert monitor.sweep(now=NOW + 60 * MINUTE) == ()      # 连续计数被已读打断

    def test_unanswered_inquiry_is_not_repeated(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=2)
        ignore(orch, count=2)
        assert len(monitor.sweep(now=NOW + 30 * MINUTE)) == 1
        assert monitor.sweep(now=NOW + 31 * MINUTE) == ()      # 等答复 ⇒ 不重复发

    def test_counts_are_readable_with_anchors(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=5)
        ignore(orch, count=3)
        state = {s.dedup_key: s for s in monitor.counts()}[KEY]
        assert state.ignored_streak == 3 and state.threshold == 5
        assert state.muted is False and state.as_of is not None


class TestGwt2Answers:
    """GWT-2：三种答复三种落点且可回溯。"""

    def _ask(self, tmp_path, *, threshold=2):
        orch, monitor, policies, store = setup(tmp_path, threshold=threshold)
        ignore(orch, count=threshold)
        inquiry = monitor.sweep(now=NOW + 30 * MINUTE)[0]
        return orch, monitor, policies, store, inquiry

    def test_reduce_narrows_the_channel_chain(self, tmp_path):
        _orch, monitor, policies, store, inquiry = self._ask(tmp_path)
        result = monitor.answer(inquiry.delivery_id, "reduce", now=NOW + 40 * MINUTE)
        assert result.choice == "reduce"
        assert policies.get(inquiry.level).channels == ("desktop",)   # 收窄了一级
        assert result.applied.startswith("已收窄渠道偏好")

    def test_disable_mutes_the_class(self, tmp_path):
        orch, monitor, _policies, store, inquiry = self._ask(tmp_path)
        monitor.answer(inquiry.delivery_id, "disable", now=NOW + 40 * MINUTE)
        assert monitor.is_muted(KEY) is True
        assert {s.dedup_key: s for s in monitor.counts()}[KEY].muted is True
        # 静音清单经频控面生效：该类改判为汇总进日报（不丢弃）
        controller = FrequencyController(store=store, mutes=monitor, now=lambda: NOW)
        decision = controller.gate(
            orch, sig(KEY, "静音后的一条"), content_type="long_form", now=NOW,
        )
        assert decision.decision.kind == "queue"

    def test_keep_changes_nothing(self, tmp_path):
        _orch, monitor, policies, _store, inquiry = self._ask(tmp_path)
        before = policies.get(inquiry.level).channels
        result = monitor.answer(inquiry.delivery_id, "keep", now=NOW + 40 * MINUTE)
        assert policies.get(inquiry.level).channels == before
        assert monitor.is_muted(KEY) is False
        assert result.applied.startswith("保持原状")

    def test_answered_inquiry_releases_the_slot(self, tmp_path):
        _orch, monitor, _policies, _store, inquiry = self._ask(tmp_path)
        monitor.answer(inquiry.delivery_id, "keep", now=NOW + 40 * MINUTE)
        with pytest.raises(FatigueValidationError):
            monitor.answer(inquiry.delivery_id, "keep", now=NOW + 41 * MINUTE)

    def test_unknown_delivery_or_choice_is_rejected(self, tmp_path):
        _orch, monitor, _policies, _store, _inquiry = self._ask(tmp_path)
        with pytest.raises(FatigueValidationError):
            monitor.answer("dlv_" + "0" * 20, "keep")
        with pytest.raises(FatigueValidationError):
            monitor.answer("dlv_" + "0" * 20, "banish")


class TestGwt3FeedbackRecorded:
    """GWT-3：答复经 `FeedbackRecorded` 回流；非本层的反馈不越权处理。"""

    def _feedback(self, delivery_id: str, choice: str | None):
        return PlatformEvent(
            event="FeedbackRecorded",
            payload={
                "target": {"kind": "delivery", "ref": delivery_id},
                "action": "adopted",
                "context": {} if choice is None else {"fatigue_choice": choice},
            },
            occurred_at=NOW,
        )

    def test_consume_applies_the_answer(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=2)
        ignore(orch, count=2)
        inquiry = monitor.sweep(now=NOW + 30 * MINUTE)[0]
        result = monitor.consume(self._feedback(inquiry.delivery_id, "disable"))
        assert result is not None and result.choice == "disable"
        assert monitor.is_muted(KEY) is True

    def test_foreign_feedback_is_left_alone(self, tmp_path):
        _orch, monitor, _policies, _store = setup(tmp_path)
        assert monitor.consume(self._feedback("dlv_" + "9" * 20, "keep")) is None
        assert monitor.consume(self._feedback("dlv_" + "9" * 20, None)) is None
        assert monitor.consume(SimpleNamespace(event="SkillRunCompleted", payload={})) is None


class TestGwt4ExplicitFailure:
    """GWT-4：询问文案过 01 §6；阈值与状态损坏显式暴露。"""

    def test_neutrality_hit_raises(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=2, guard=FailingGuard())
        ignore(orch, count=2)
        with pytest.raises(FatigueValidationError):
            monitor.sweep(now=NOW + 30 * MINUTE)

    def test_corrupted_state_is_explicit(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        store.put("execution_log", "fatigue/state.json", b"{not json")
        monitor = FatigueMonitor(store=store, now=lambda: NOW)
        with pytest.raises(FatigueValidationError):
            monitor.counts()

    def test_threshold_entry_is_writable_and_validated(self, tmp_path):
        _orch, monitor, _policies, _store = setup(tmp_path)
        assert monitor.threshold() == DEFAULT_THRESHOLD
        change = monitor.set_threshold(3)
        assert change is not None and change.config_id == THRESHOLD_CONFIG_ID
        assert monitor.threshold() == 3
        assert monitor.entry(THRESHOLD_CONFIG_ID).default == 3
        with pytest.raises(FatigueValidationError):
            monitor.set_threshold(0)


class TestGwt5Deterministic:
    """GWT-5：同一 `(留痕状态, now)` 重复推进 ⇒ 结论恒同、不重复发问。"""

    def test_a_new_monitor_resumes_without_re_asking(self, tmp_path):
        orch, monitor, policies, store = setup(tmp_path, threshold=2)
        ignore(orch, count=2)
        first = monitor.sweep(now=NOW + 30 * MINUTE)
        assert len(first) == 1
        resumed = FatigueMonitor(
            store=store, orchestrator=orch, policies=policies, threshold=2, now=lambda: NOW,
        )
        assert resumed.sweep(now=NOW + 30 * MINUTE) == ()
        assert {s.dedup_key: s for s in resumed.counts()}[KEY].asked_delivery_id == first[0].delivery_id


class TestCooldown:
    """复问冷却：答复后不立刻再问，须再攒满一轮阈值（07 §7 的「不骚扰」口径）。"""

    def test_answering_does_not_trigger_an_immediate_re_ask(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=3)
        ignore(orch, count=3)
        first = monitor.sweep(now=NOW + 40 * MINUTE)
        assert len(first) == 1
        monitor.answer(first[0].delivery_id, "keep", now=NOW + 41 * MINUTE)
        assert monitor.sweep(now=NOW + 42 * MINUTE) == ()        # 计数没变 ⇒ 不再问

    def test_a_full_new_round_of_ignores_asks_again(self, tmp_path):
        orch, monitor, _policies, _store = setup(tmp_path, threshold=3)
        ignore(orch, count=3)
        first = monitor.sweep(now=NOW + 40 * MINUTE)
        monitor.answer(first[0].delivery_id, "keep", now=NOW + 41 * MINUTE)
        ignore(orch, count=3, start=NOW + 60 * MINUTE)           # 连续计数爬到 6
        again = monitor.sweep(now=NOW + 120 * MINUTE)
        assert len(again) == 1
        assert again[0].ignored_streak == 6
