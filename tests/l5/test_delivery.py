"""L5 投递编排与升级链（07 §4）的验收用例。

对齐任务文件 [`T-L5-002.2`](../项目管理/tasks/T-L5-002.2-投递编排与升级链.md) 的
GWT-1..7——逐级升级与留痕、已读结算与链尽转日报、routine 路由、文案个性化与中性化、
离线补发（销 L0 册 `A5`）、确定性重入、`DeliverySettled`。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from l5_helpers import NOW, PASS, TRACE_ID, event
from st_agent.contracts.identifiers import digest_id
from st_agent.l0.storage import Store
from st_agent.l2.memory import MemoryGraph, MemoryReader, MemoryWriter, SliceQuery, checked_node
from st_agent.l5.budget import AttentionBudget
from st_agent.l5.channel_policy import ChannelPolicies
from st_agent.l5.channels import (
    ChannelDispatcher,
    ChannelPayload,
    ChannelResult,
    DesktopChannel,
    EmailChannel,
)
from st_agent.l5.delivery import (
    DAILY_REPORT_CHANNEL,
    DELIVERY_EVENT,
    EVENT_OWNER,
    DeliveryOrchestrator,
    delivery_digest_key,
)
from st_agent.l5.errors import DeliveryValidationError
from st_agent.l5.personalize import DEFAULT_WORDING, Personalizer
from st_agent.l5.signal import adopt_signal

MINUTE = timedelta(minutes=1)


class RecordingChannel:
    """记录投递载荷的渠道替身（可配置失败 / 未接线）。"""

    def __init__(self, channel: str, *, ok: bool = True, reason: str = "") -> None:
        self.channel = channel
        self._ok = ok
        self._reason = reason or f"{channel} 不可达"
        self.payloads: list[ChannelPayload] = []

    def deliver(self, payload: ChannelPayload) -> ChannelResult:
        if not self._ok:
            return ChannelResult(channel=self.channel, status="failed", detail=self._reason)
        self.payloads.append(payload)
        return ChannelResult(channel=self.channel, status="ok")

    def health(self):
        raise NotImplementedError       # 本替身只走 deliver（`health` 不由投递路径调用）

    def degrade(self, reason: str) -> ChannelResult:
        return ChannelResult(channel=self.channel, status="unavailable", detail=reason)


class RecordingPublisher:
    """事件发布端口的替身。"""

    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> bool:
        self.events.append(event)
        return True


def signal(level: str = "important", **over):
    return adopt_signal(event(level=level, **over))


def build(
    tmp_path,
    *,
    channels: dict | None = None,
    dispatcher: ChannelDispatcher | None = None,
    policies: ChannelPolicies | None = None,
    budget: AttentionBudget | None = None,
    personalizer: Personalizer | None = None,
    events=None,
    store: Store | None = None,
):
    store = store if store is not None else Store.create(tmp_path / "root", PASS)
    if dispatcher is None:
        dispatcher = ChannelDispatcher(channels or {})
    policies = policies if policies is not None else ChannelPolicies(store=store, now=lambda: NOW)
    return DeliveryOrchestrator(
        dispatcher=dispatcher, policies=policies, budget=budget,
        personalizer=personalizer, store=store, events=events, now=lambda: NOW,
    ), store


class TestGwt1Escalation:
    """GWT-1：按链逐级升级，每级一枚 `delivery_id` 留痕，级次可查。"""

    def test_first_step_goes_to_the_first_channel(self, tmp_path):
        desktop = RecordingChannel("desktop")
        orch, _store = build(tmp_path, channels={"desktop": desktop})
        result = orch.dispatch(signal("emergency"))
        assert result.payload is not None
        assert result.record.channel == "desktop" and result.record.step == 0
        assert result.record.status == "delivered"
        assert len(desktop.payloads) == 1

    def test_wait_then_escalate_produces_a_new_delivery_id(self, tmp_path):
        desk, mail, im = (RecordingChannel(c) for c in ("desktop", "email", "im_webhook"))
        orch, _store = build(
            tmp_path, channels={"desktop": desk, "email": mail, "im_webhook": im},
        )
        first = orch.dispatch(signal("emergency"), now=NOW)
        assert orch.advance(now=NOW + 4 * MINUTE) == ()          # 未到点，不升
        stepped = orch.advance(now=NOW + 5 * MINUTE)
        assert len(stepped) == 1
        assert stepped[0].channel == "email" and stepped[0].step == 1
        assert stepped[0].delivery_id != first.record.delivery_id
        stepped2 = orch.advance(now=NOW + 5 * MINUTE + 15 * MINUTE)
        assert len(stepped2) == 1 and stepped2[0].channel == "im_webhook"
        assert len(im.payloads) == 1

    def test_ledger_keeps_every_step(self, tmp_path):
        orch, _store = build(tmp_path, channels={
            c: RecordingChannel(c) for c in ("desktop", "email", "im_webhook")
        })
        orch.dispatch(signal("emergency"), now=NOW)
        orch.advance(now=NOW + 5 * MINUTE)
        steps = sorted(
            (r.step, r.channel) for r in orch.ledger() if r.route == "separate"
        )
        assert steps == [(0, "desktop"), (1, "email")]

    def test_pending_lists_open_deliveries(self, tmp_path):
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")})
        orch.dispatch(signal("emergency"), now=NOW)
        assert len(orch.pending()) == 1

    def test_immediate_failover_inside_a_step(self, tmp_path):
        """本级内渠道失效 ⇒ 即时降级到链上的下一个（07 §3），级次随实际渠道走。"""
        broken, mail = RecordingChannel("desktop", ok=False), RecordingChannel("email")
        orch, _store = build(tmp_path, channels={"desktop": broken, "email": mail})
        result = orch.dispatch(signal("emergency"), now=NOW)
        assert result.record.channel == "email" and result.record.step == 1
        assert result.record.status == "delivered"


class TestGwt2Settlement:
    """GWT-2：已读即停链；链尽未读 ⇒ 结算未确认并转入日报汇总（不丢弃）。"""

    def test_mark_read_stops_the_chain(self, tmp_path):
        desk, mail = RecordingChannel("desktop"), RecordingChannel("email")
        orch, _store = build(tmp_path, channels={"desktop": desk, "email": mail})
        first = orch.dispatch(signal("emergency"), now=NOW)
        settled = orch.mark_read(first.record.delivery_id, now=NOW + 1 * MINUTE)
        assert settled.status == "read" and settled.settled_at == NOW + 1 * MINUTE
        assert orch.advance(now=NOW + 60 * MINUTE) == ()       # 已结算 ⇒ 不再升级
        assert mail.payloads == []

    def test_mark_read_settles_every_open_step_of_the_signal(self, tmp_path):
        """已读按**信号**停链——只停当前那级会让升级链继续推同一条消息。"""
        desk, mail = RecordingChannel("desktop"), RecordingChannel("email")
        orch, _store = build(tmp_path, channels={"desktop": desk, "email": mail})
        first = orch.dispatch(signal("emergency"), now=NOW)
        orch.advance(now=NOW + 5 * MINUTE)                     # 升到 email
        # 用户读的是**第一级**那条通知，但同一条消息在途的级都得停
        orch.mark_read(first.record.delivery_id, now=NOW + 6 * MINUTE)
        assert all(r.status == "read" for r in orch.ledger() if r.route == "separate")
        assert orch.advance(now=NOW + 120 * MINUTE) == ()      # 链已停，不再推到 IM
        assert orch.pending() == ()

    def test_mark_read_rejects_unknown_or_settled(self, tmp_path):
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")})
        with pytest.raises(DeliveryValidationError, match="无此投递留痕"):
            orch.mark_read("dlv_" + "0" * 20)
        first = orch.dispatch(signal("emergency"), now=NOW)
        orch.mark_read(first.record.delivery_id, now=NOW)
        with pytest.raises(DeliveryValidationError, match="不可结算为已读"):
            orch.mark_read(first.record.delivery_id, now=NOW)

    def test_chain_end_without_read_is_unacknowledged_and_queued(self, tmp_path):
        orch, _store = build(tmp_path, channels={
            c: RecordingChannel(c) for c in ("desktop", "email", "im_webhook")
        })
        orch.dispatch(signal("emergency"), now=NOW)
        orch.advance(now=NOW + 5 * MINUTE)                     # → email
        final = orch.advance(now=NOW + 5 * MINUTE + 15 * MINUTE)  # → im（链尾）
        assert final[0].channel == "im_webhook"
        produced = orch.advance(now=NOW + 120 * MINUTE)         # 链尽仍未读
        assert produced[0].route == "daily_report"
        assert orch.get(final[0].delivery_id).status == "unacknowledged"
        queue = orch.daily_report_queue()
        assert len(queue) == 1 and queue[0].body              # 待汇总项自带文案

    def test_unavailable_chain_is_settled_and_queued_at_once(self, tmp_path):
        orch, _store = build(tmp_path, channels={})            # 什么都没接线
        result = orch.dispatch(signal("emergency"), now=NOW)
        assert result.record.status == "failed"
        produced = orch.advance(now=NOW)                       # 不必等超时
        assert produced[0].route == "daily_report"
        assert orch.get(result.record.delivery_id).status == "unacknowledged"


class TestGwt3DailyRoute:
    """GWT-3：`routine` 走日报路由时**不调任何渠道**；显式放行后走正常链。"""

    class _SpyDispatcher(ChannelDispatcher):
        def __init__(self):
            super().__init__({c: RecordingChannel(c) for c in ("desktop", "email")})
            self.calls: list[dict] = []

        def deliver(self, chain, payload):
            self.calls.append({"chain": list(chain), "payload": payload})
            return super().deliver(chain, payload)

    def test_routine_is_routed_to_the_report_without_touching_channels(self, tmp_path):
        spy = self._SpyDispatcher()
        budget = AttentionBudget()                             # 缺省格位只放行 emergency
        orch, _store = build(tmp_path, dispatcher=spy, budget=budget)
        result = orch.dispatch(signal("routine"), now=NOW)
        assert result.record.route == "daily_report"
        assert result.record.channel == DAILY_REPORT_CHANNEL
        assert result.record.status == "queued"
        assert result.record.title and result.record.body      # 攒着内容，不丢弃
        assert spy.calls == []                                 # 未调任何渠道
        assert orch.daily_report_queue()[0].delivery_id == result.record.delivery_id

    def test_user_opted_in_routine_walks_the_chain(self, tmp_path):
        spy = self._SpyDispatcher()
        store = Store.create(tmp_path / "root", PASS)
        budget = AttentionBudget(store=store, now=lambda: NOW)
        budget.set_cell(
            budget.resolve(level="routine", content_type="brief", at=NOW.time()).key,
            {"allowed_levels": ["emergency", "routine"], "max_per_slot": 1},
        )
        orch, _store = build(tmp_path, dispatcher=spy, budget=budget, store=store)
        result = orch.dispatch(signal("routine"), now=NOW)
        assert result.record.route == "separate"
        assert result.record.channel == "desktop"
        assert len(spy.calls) == 1

    def test_missing_budget_degrades_visibly(self, tmp_path):
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")})
        result = orch.dispatch(signal("routine"), now=NOW)
        assert result.budget_degraded is True
        assert result.record.route == "separate"               # 没有预算 ⇒ 不擅自压进日报


class TestGwt4Personalization:
    """GWT-4：措辞随记忆切片而变、一律过 01 §6；缺记忆即中性缺省。"""

    def test_no_memory_uses_the_neutral_default(self):
        title, body, profile = Personalizer().build(signal())
        assert profile.wording == DEFAULT_WORDING and profile.source == "default"
        assert "该标的近两日公告密度高于其 30 日均值" in body
        assert "证据 2 条" in body

    def test_wording_follows_the_top_memory_slice(self):
        class FakeReader:
            def __init__(self, confidence):
                self._confidence = confidence

            def query(self, _spec):
                slice_ = SimpleNamespace(
                    confidence=self._confidence,
                    node=SimpleNamespace(type="attention",
                                         memory_node_id="mn_" + "9" * 20),
                )
                return SimpleNamespace(slices=(slice_,))

        concise = Personalizer(reader=FakeReader(0.2)).build(signal())[2]
        detailed = Personalizer(reader=FakeReader(0.9)).build(signal())[2]
        assert (concise.wording, detailed.wording) == ("concise", "detailed")
        assert concise.source == detailed.source == "memory"
        assert concise.slice_refs == ("mn_" + "9" * 20,)
        assert "0.20" in concise.note and "04 §3.1" in concise.note

        _t, concise_body, _p = Personalizer(reader=FakeReader(0.2)).build(signal())
        _t, detailed_body, _p = Personalizer(reader=FakeReader(0.9)).build(signal())
        assert "证据 2 条" not in concise_body
        assert TRACE_ID in detailed_body

    def test_all_wording_levels_keep_every_lens_stance(self):
        """个性化改的是「说多少」，不是「说什么」——分歧在三种档位下都并列保留（铁律 4）。"""
        for confidence in (0.2, 0.5, 0.9):
            reader = SimpleNamespace(query=lambda _s, _c=confidence: SimpleNamespace(
                slices=(SimpleNamespace(
                    confidence=_c,
                    node=SimpleNamespace(type="attention", memory_node_id="mn_" + "9" * 20),
                ),),
            ))
            _title, body, _profile = Personalizer(reader=reader).build(signal())
            assert "资金面显示净流入" in body
            assert "基本面指标走弱" in body
            assert "同类标的公告密度普遍上行" in body

    def test_broken_reader_degrades_with_a_note(self):
        class BoomReader:
            def query(self, _spec):
                raise RuntimeError("memory 分区损坏")

        _t, _b, profile = Personalizer(reader=BoomReader()).build(signal())
        assert profile.source == "default"
        assert "记忆读取面不可用" in profile.note

    def test_real_memory_reader_selects_the_wording(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        writer = MemoryWriter(MemoryGraph(store))
        writer.add_node(checked_node(
            type="attention", memory_node_id="mn_" + "7" * 20, confidence=0.9,
            source="user_stated", privacy_level="private",
            created_at=NOW, updated_at=NOW, watchlist=("sh.600000",),
        ))
        reader = MemoryReader(MemoryGraph(store), now=NOW)
        _title, _body, profile = Personalizer(reader=reader).build(signal())
        assert profile.wording == "detailed" and profile.source == "memory"
        assert profile.slice_refs == ("mn_" + "7" * 20,)

    def test_personalized_copy_is_carried_into_the_ledger(self, tmp_path):
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")})
        result = orch.dispatch(signal("emergency"), now=NOW)
        assert result.record.title.startswith("紧急：")
        assert result.wording == DEFAULT_WORDING


class TestGwt5OfflineResend:
    """GWT-5：离线待补发记入本层留痕；恢复后按开关补发，同 ID 不重复；不依赖 L0 审计。"""

    def _offline_rig(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        from st_agent.l0.net.gateway import EgressGateway

        gw = EgressGateway(store)                                # 审计默认关
        sent: list[bytes] = []

        def transport(host, message):
            sent.append(message)
            return len(message)

        mail = EmailChannel(gw, host="smtp.example.com", transport=transport,
                            recipient="me@example.com")
        policies = ChannelPolicies(store=store, now=lambda: NOW)
        policies.set_policy("important", ["email"], [])
        orch = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher({"email": mail}), policies=policies,
            store=store, now=lambda: NOW,
        )
        return orch, gw, policies, sent

    def test_offline_delivery_lands_in_pending_reconnect(self, tmp_path):
        orch, gw, _policies, sent = self._offline_rig(tmp_path)
        gw.set_online(False)
        result = orch.dispatch(signal("important"), now=NOW)
        assert result.record.status == "unavailable"
        assert result.record.pending_reconnect is True
        assert sent == []
        assert len(orch.pending_reconnect()) == 1

    def test_resend_reuses_the_same_delivery_id(self, tmp_path):
        orch, gw, _policies, sent = self._offline_rig(tmp_path)
        gw.set_online(False)
        first = orch.dispatch(signal("important"), now=NOW)
        gw.set_online(True)
        resent = orch.resend(now=NOW + 10 * MINUTE)
        assert len(resent) == 1
        assert resent[0].delivery_id == first.record.delivery_id   # 同一 ID，写同一路径
        assert resent[0].status == "delivered"
        assert len(sent) == 1
        assert orch.pending_reconnect() == ()
        assert len([r for r in orch.ledger() if r.route == "separate"]) == 1   # 无重复记录

    def test_resend_is_skipped_when_user_turned_it_off(self, tmp_path):
        orch, gw, policies, sent = self._offline_rig(tmp_path)
        gw.set_online(False)
        orch.dispatch(signal("important"), now=NOW)
        policies.set_resend_on_reconnect(False)
        gw.set_online(True)
        assert orch.resend(now=NOW + 10 * MINUTE) == ()
        assert sent == []
        assert len(orch.pending_reconnect()) == 1                  # 留痕原状，不静默丢

    def test_resend_works_with_audit_off(self, tmp_path):
        """L0 册 `A5` 的收口口径：补发依据是本层留痕，审计默认关也照常工作。"""
        orch, gw, _policies, sent = self._offline_rig(tmp_path)
        assert gw.audit_enabled is False
        gw.set_online(False)
        orch.dispatch(signal("important"), now=NOW)
        gw.set_online(True)
        assert len(orch.resend(now=NOW)) == 1
        assert len(sent) == 1


class TestGwt6Determinism:
    """GWT-6：同一 `(信号, 渠道, 级次)` 恒得同一 `delivery_id`；重放不产生重复留痕。"""

    def test_delivery_id_is_a_deterministic_digest(self):
        sid = digest_id("sig", "important", "k", TRACE_ID)
        first = digest_id("dlv", *delivery_digest_key(sid, "email", 1))
        second = digest_id("dlv", *delivery_digest_key(sid, "email", 1))
        assert first == second
        assert first.startswith("dlv_") and len(first) == 24

    def test_response_is_a_pure_function_of_state_and_time(self, tmp_path):
        orch, _store = build(tmp_path, channels={
            c: RecordingChannel(c) for c in ("desktop", "email", "im_webhook")
        })
        orch.dispatch(signal("emergency"), now=NOW)
        assert orch.advance(now=NOW + 4 * MINUTE) == ()
        assert orch.advance(now=NOW + 4 * MINUTE) == ()       # 同一 (状态, 时刻) 恒同结论
        first = orch.advance(now=NOW + 5 * MINUTE)
        assert orch.advance(now=NOW + 5 * MINUTE) == ()       # 已推进 ⇒ 不重复
        assert first[0].step == 1

    def test_replaying_dispatch_writes_the_same_record(self, tmp_path):
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")})
        first = orch.dispatch(signal("emergency"), now=NOW)
        again = orch.dispatch(signal("emergency"), now=NOW + 90 * MINUTE)
        assert again.record.delivery_id == first.record.delivery_id
        assert len(orch.ledger()) == 1                        # 覆盖写，无重复记录


class TestGwt7Events:
    """GWT-7：结算发布 `DeliverySettled`（含未读超时语义）；未注入事件面不假装送达。"""

    def test_mark_read_publishes_settled_with_trace_id(self, tmp_path):
        publisher = RecordingPublisher()
        orch, _store = build(
            tmp_path, channels={"desktop": RecordingChannel("desktop")}, events=publisher,
        )
        first = orch.dispatch(signal("emergency"), now=NOW)
        orch.mark_read(first.record.delivery_id, now=NOW + 1 * MINUTE)
        assert len(publisher.events) == 1
        event_ = publisher.events[0]
        assert event_.event == DELIVERY_EVENT
        assert event_.trace_id == TRACE_ID
        assert event_.payload["unread_timeout"] is False
        assert event_.payload["status"] == "read"
        assert event_.payload["delivery_id"] == first.record.delivery_id

    def test_unread_timeout_settlement_is_marked(self, tmp_path):
        publisher = RecordingPublisher()
        orch, _store = build(tmp_path, channels={}, events=publisher)
        orch.dispatch(signal("important"), now=NOW)            # 什么都没接线 ⇒ 未成
        orch.advance(now=NOW)
        assert publisher.events[0].payload["unread_timeout"] is False   # 非超时，是投递未成

    def test_chain_end_settlement_carries_unread_timeout(self, tmp_path):
        publisher = RecordingPublisher()
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")},
                             events=publisher)
        orch.dispatch(signal("routine"), now=NOW)              # routine 链仅一级、无等待
        orch.advance(now=NOW)
        assert publisher.events[0].payload["unread_timeout"] is True

    def test_missing_event_port_is_not_faked(self, tmp_path):
        from st_agent.l1.events import UndeliveredPublisher

        publisher = UndeliveredPublisher(EVENT_OWNER)
        orch, _store = build(tmp_path, channels={"desktop": RecordingChannel("desktop")},
                             events=publisher)
        first = orch.dispatch(signal("emergency"), now=NOW)
        orch.mark_read(first.record.delivery_id, now=NOW)
        assert len(orch.settlements()) == 1                    # 事件如实记下（不丢）
        assert len(publisher.events()) == 1                    # 交到了发布端
        assert publisher.publish(orch.settlements()[0]) is False   # 而该发布端**不受理**
        assert publisher.owner == EVENT_OWNER                  # 归属点名，不假装送达
        assert "未接入事件总线" in publisher.note

    def test_publisher_failure_is_explicit(self, tmp_path):
        class BoomPublisher:
            def publish(self, _event):
                raise RuntimeError("总线未就绪")

        orch, _store = build(
            tmp_path, channels={"desktop": RecordingChannel("desktop")}, events=BoomPublisher(),
        )
        first = orch.dispatch(signal("emergency"), now=NOW)
        with pytest.raises(DeliveryValidationError, match="事件发布端口异常"):
            orch.mark_read(first.record.delivery_id, now=NOW)


class TestInMemoryMode:
    """无 ``Store`` 的纯内存态：留痕留在内存里，判定与结算照常。"""

    def test_in_memory_ledger_works(self, tmp_path):
        orch = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher({"desktop": RecordingChannel("desktop")}),
            policies=ChannelPolicies(), now=lambda: NOW,
        )
        first = orch.dispatch(signal("emergency"), now=NOW)
        assert orch.get(first.record.delivery_id) is not None
        assert len(orch.pending()) == 1


class TestStoreBackedLedger:
    """带 ``Store`` 时留痕落 `execution_log/delivery/`，重开装置仍可续推。"""

    def test_ledger_survives_a_new_orchestrator(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        channels = {c: RecordingChannel(c) for c in ("desktop", "email", "im_webhook")}
        policies = ChannelPolicies(store=store, now=lambda: NOW)
        first = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher(channels), policies=policies,
            store=store, now=lambda: NOW,
        )
        first.dispatch(signal("emergency"), now=NOW)
        files = [n for n in store.list_files("execution_log") if n.startswith("delivery/")]
        assert len(files) == 1

        reopened = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher(channels), policies=policies,
            store=store, now=lambda: NOW,
        )
        assert len(reopened.pending()) == 1
        produced = reopened.advance(now=NOW + 5 * MINUTE)      # 只凭留痕即可续推
        assert produced[0].channel == "email"

    def test_unknown_signal_is_skipped_without_guessing(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        orch, _s = build(tmp_path, channels={"desktop": RecordingChannel("desktop")}, store=store)
        orch.dispatch(signal("emergency"), now=NOW)
        blank = DeliveryOrchestrator(
            dispatcher=ChannelDispatcher({}), policies=ChannelPolicies(store=store),
            store=store, now=lambda: NOW,
        )
        assert len(blank.advance(now=NOW + 5 * MINUTE)) == 1   # 留痕自足，不需要信号上下文
