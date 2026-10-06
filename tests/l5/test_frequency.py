"""L5 去重合并与频控计数（07 §6）的验收用例。

对齐任务文件 [`T-L5-003.2`](../项目管理/tasks/T-L5-003.2-去重合并与频控计数.md) 的
GWT-1..5——同键合并附次数、格位节奏计数与排队、纯函数推进与损坏显式暴露、
只增接线（缺省不改既有投递语义）、计数读面。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from l5_helpers import NOW, PASS, content, event
from st_agent.l0.storage import Store
from st_agent.l5.budget import AttentionBudget, BudgetCell, BudgetKey
from st_agent.l5.channel_policy import ChannelPolicies
from st_agent.l5.channels import ChannelDispatcher, ChannelPayload, ChannelResult
from st_agent.l5.delivery import DAILY_REPORT_CHANNEL, DeliveryOrchestrator
from st_agent.l5.errors import FrequencyValidationError
from st_agent.l5.frequency import DEFAULT_WINDOW_MINUTES, FrequencyController
from st_agent.l5.signal import adopt_signal

CST = timezone(timedelta(hours=8))
MINUTE = timedelta(minutes=1)


class RecordingChannel:
    """记录投递载荷的渠道替身。"""

    def __init__(self, channel: str, *, ok: bool = True) -> None:
        self.channel = channel
        self._ok = ok
        self.payloads: list[ChannelPayload] = []

    def deliver(self, payload: ChannelPayload) -> ChannelResult:
        if not self._ok:
            return ChannelResult(channel=self.channel, status="failed", detail="不可达")
        self.payloads.append(payload)
        return ChannelResult(channel=self.channel, status="ok")

    def health(self):
        raise NotImplementedError

    def degrade(self, reason: str) -> ChannelResult:
        return ChannelResult(channel=self.channel, status="unavailable", detail=reason)


def sig(*, level: str = "important", dedup_key: str = "sh.600000:density", conclusion: str | None = None):
    over = {"dedup_key": dedup_key}
    if conclusion is not None:
        over["content_ref"] = content(conclusion=conclusion)
    return adopt_signal(event(level=level, **over))


def build(tmp_path, *, store=None, freq=None, channels=None, budget=None):
    store = store if store is not None else Store.create(tmp_path / "root", PASS)
    channels = {"desktop": RecordingChannel("desktop")} if channels is None else channels
    policies = ChannelPolicies(store=store, now=lambda: NOW)
    orch = DeliveryOrchestrator(
        dispatcher=ChannelDispatcher(channels), policies=policies,
        budget=budget if budget is not None else AttentionBudget(), store=store, now=lambda: NOW,
    )
    controller = freq if freq is not None else FrequencyController(store=store, now=lambda: NOW)
    return orch, controller, store


class TestGwt1DedupMerge:
    """GWT-1：同 `dedup_key` 窗口内多次触发 ⇒ 合并为一条并附触发次数。"""

    def test_same_key_within_window_merges_with_trigger_count(self, tmp_path):
        controller = FrequencyController()
        first = controller.admit(sig(conclusion="公告密度上行"), now=NOW)
        second = controller.admit(sig(conclusion="公告密度进一步上行"), now=NOW + 5 * MINUTE)
        assert first.kind == "separate" and first.trigger_count == 1
        assert second.kind == "merge" and second.trigger_count == 2
        assert second.window_start == first.window_start

    def test_different_keys_do_not_merge(self, tmp_path):
        controller = FrequencyController()
        a = controller.admit(sig(dedup_key="sh.600000:density"), now=NOW)
        b = controller.admit(sig(dedup_key="sh.600001:density"), now=NOW)
        assert (a.kind, a.trigger_count) == ("separate", 1)
        assert (b.kind, b.trigger_count) == ("separate", 1)

    def test_beyond_window_starts_a_new_run(self, tmp_path):
        controller = FrequencyController(window_minutes=30)
        controller.admit(sig(), now=NOW)
        after = controller.admit(sig(), now=NOW + 31 * MINUTE)
        assert (after.kind, after.trigger_count) == ("separate", 1)

    def test_window_is_configurable(self, tmp_path):
        assert FrequencyController().window_minutes() == DEFAULT_WINDOW_MINUTES
        controller = FrequencyController(window_minutes=1)
        controller.admit(sig(), now=NOW)
        assert controller.admit(sig(), now=NOW + 2 * MINUTE).kind == "separate"


class TestGwt2SlotRate:
    """GWT-2：超出格位节奏上限 ⇒ 排队进下一次日报，不丢弃；翻到下一格位即重置。"""

    def test_over_budget_queues_into_the_report(self, tmp_path):
        orch, controller, store = build(tmp_path)
        budget = AttentionBudget()
        # 08:00 属「早通勤」× workday × long_form ⇒ 缺省格位放行全部级别、上限 2
        one = controller.gate(
            orch, sig(dedup_key="sh.600001:density", conclusion="第一条"),
            content_type="long_form", now=NOW,
        )
        two = controller.gate(
            orch, sig(dedup_key="sh.600002:density", conclusion="第二条"),
            content_type="long_form", now=NOW,
        )
        three = controller.gate(
            orch, sig(dedup_key="sh.600003:density", conclusion="第三条"),
            content_type="long_form", now=NOW,
        )
        assert one.decision.kind == "separate" and one.dispatch is not None
        assert two.decision.kind == "separate"
        assert three.decision.kind == "queue" and three.queued is not None
        assert three.queued.route == "daily_report"
        assert three.queued.channel == DAILY_REPORT_CHANNEL
        assert amount_of(orch) == 2      # 第三条没单独触达

    def test_next_slot_resets_the_counter(self, tmp_path):
        cells = {
            key: BudgetCell(allowed_levels=frozenset({"emergency", "important"}), max_per_slot=1)
            for key in (
                BudgetKey(mode="workday", slot_id="morning_commute", content_type="long_form"),
                BudgetKey(mode="workday", slot_id="market_hours", content_type="long_form"),
            )
        }
        orch, controller, _store = build(tmp_path, budget=AttentionBudget(cells=cells))
        first = controller.gate(
            orch, sig(dedup_key="sh.600001:density", conclusion="早通勤一"),
            content_type="long_form", now=NOW,
        )
        blocked = controller.gate(
            orch, sig(dedup_key="sh.600002:density", conclusion="早通勤二"),
            content_type="long_form", now=NOW + 30 * MINUTE,
        )
        # 09:30 起是「盘中」——另一个格位，计数从 0 起
        later = datetime(2026, 10, 6, 10, 0, tzinfo=CST)
        reset = controller.gate(
            orch, sig(dedup_key="sh.600003:density", conclusion="盘中一"),
            content_type="long_form", now=later,
        )
        assert first.decision.kind == "separate"
        assert blocked.decision.kind == "queue"          # 同一格位、上限 1 已满
        assert reset.decision.kind == "separate"         # 翻到下一格位 ⇒ 计数重置
        assert reset.decision.slot_key.endswith(".market_hours.long_form")

    def test_muted_class_is_routed_to_the_report(self, tmp_path):
        orch, _controller, store = build(tmp_path)
        mutes = SimpleNamespace(is_muted=lambda key: key == "sh.600000:density")
        controller = FrequencyController(store=store, mutes=mutes, now=lambda: NOW)
        decision = controller.gate(
            orch, sig(conclusion="已关闭类"), content_type="long_form", now=NOW,
        )
        assert decision.decision.kind == "queue"
        assert "关闭单独触达" in decision.decision.reason

    def test_mutes_port_defect_is_explicit(self, tmp_path):
        def boom(_key):
            raise RuntimeError("清单读不动")

        controller = FrequencyController(mutes=SimpleNamespace(is_muted=boom))
        with pytest.raises(FrequencyValidationError):
            controller.admit(sig(), now=NOW)

    def test_budget_routing_to_report_is_queued(self, tmp_path):
        orch, controller, _store = build(tmp_path)
        # 盘中 × brief 缺省只放行 emergency ⇒ 本条重要级走日报
        midday = datetime(2026, 10, 6, 11, 0, tzinfo=CST)
        decision = controller.gate(
            orch, sig(level="important", conclusion="盘中日常"), content_type="brief", now=midday,
        )
        assert decision.decision.kind == "queue"
        assert decision.queued.route == "daily_report"


class TestGwt3PureAndExplicit:
    """GWT-3：显式时刻的纯函数推进；计数损坏显式暴露（不读成「没超限」）。"""

    def test_a_new_controller_resumes_from_disk(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        FrequencyController(store=store, now=lambda: NOW).admit(sig(), now=NOW)
        resumed = FrequencyController(store=store, now=lambda: NOW)
        again = resumed.admit(sig(), now=NOW)             # 同一 (状态, now) ⇒ 同「第 2 次触发」
        assert again.kind == "merge" and again.trigger_count == 2

    def test_corrupted_state_is_explicit(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        store.put("execution_log", "frequency/dedup.json", b"{not json")
        controller = FrequencyController(store=store, now=lambda: NOW)
        with pytest.raises(FrequencyValidationError):
            controller.admit(sig(), now=NOW)

    def test_bad_window_is_rejected(self):
        with pytest.raises(FrequencyValidationError):
            FrequencyController(window_minutes=0)


class TestGwt4AdditiveWiring:
    """GWT-4：未注入频控面时既有投递面行为逐字节不变（只增不改）。"""

    def test_dispatch_unchanged_without_frequency(self, tmp_path):
        orch, _controller, _store = build(tmp_path)
        result = orch.dispatch(sig(conclusion="无频控面"), content_type="long_form", now=NOW)
        assert result.record.route == "separate" and result.record.status == "delivered"

    def test_dedup_key_is_recorded_on_the_ledger_row(self, tmp_path):
        orch, _controller, _store = build(tmp_path)
        orch.dispatch(sig(conclusion="带键"), content_type="long_form", now=NOW)
        assert {r.dedup_key for r in orch.ledger()} == {"sh.600000:density"}


class TestGwt5ReadFace:
    """GWT-5：逐 `dedup_key` 的当前计数与窗口起点可查、可解释。"""

    def test_counts_expose_trigger_counts(self, tmp_path):
        controller = FrequencyController()
        controller.admit(sig(), now=NOW)
        controller.admit(sig(), now=NOW + MINUTE)
        counts = {c.dedup_key: c for c in controller.counts()}
        assert counts["sh.600000:density"].trigger_count == 2
        assert controller.trigger_count("sh.600000:density") == 2
        assert controller.trigger_count("sh.999999:unknown") == 1

    def test_slot_usage_is_readable(self, tmp_path):
        orch, controller, _store = build(tmp_path)
        controller.gate(orch, sig(conclusion="一"), content_type="long_form", now=NOW)
        usage = controller.slot_usage()
        assert list(usage.values()) == [1]


def amount_of(orch) -> int:
    """单独触达（`separate`）的留痕条数。"""
    return sum(1 for r in orch.ledger() if r.route == "separate")
