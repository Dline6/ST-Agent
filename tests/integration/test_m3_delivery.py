"""`T-INT-004` · M3 集成关卡：主动触达投递闭环的跨层装配用例。

只测**装配关系与跨层数据流**（单任务行为已由各自套件覆盖，见各任务 备注）：本关卡的
GWT 锚在 [00 §5 端到端数据流](../../docs/技术架构-v2/00-架构总览.md) 的**第 4–7 步**——
信号生成（L1 定时监控 / L4 Deliberation）→ 注意力预算 → 渠道升级链 → 交付留痕 → 反馈回流。

全部离线（出网面由 `rig_m3` 的替身接管）；真实 LLM 端点与真实渠道端点在 `tests/live/`（GWT-10）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m3 import (
    NOW,
    Clock,
    M3Rig,
    RecordingChannel,
    RecordingTransport,
    seeded_m3,
)

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l5.budget import BudgetKey
from st_agent.l5.channels import ChannelHealth
from st_agent.l5.delivery_store import delivery_id_for
from st_agent.l5.signal import SignalContent, SignalLensStance, signal_event

_MONITOR_DEDUP = "l1:stock-watch:sh.600000:异动"
"""盯盘种子里**必然出现**的一类（`sh.600000` 异动：阈值 5.0，实测 +7.5）。"""

_MANUAL_DEDUP = "l1:test-manual:sh.600000:用户自定规则"
"""用例手工投递的信号用的去重键——与盯盘的四条**不同键**，便于把留痕按键筛出来。"""

_LENS_NAMES = (
    "机会视角", "风险视角", "基本面视角", "情绪视角", "流动性视角", "宏观视角", "合规视角",
)

_SECRET = "s3cret-smtp-value"


@pytest.fixture()
def rig(tmp_path: Path) -> M3Rig:
    return seeded_m3(tmp_path / ROOT_NAME)


# ───────────────────────────── 共用小工具 ─────────────────────────────


def _publish(
    rig: M3Rig,
    *,
    level: str = "emergency",
    dedup_key: str = _MANUAL_DEDUP,
    seq: int = 0,
    at: datetime | None = None,
) -> None:
    """像上游那样往总线上投一条合契的 `SignalEmitted`（[01 §11] 的负载结构）。

    ``seq`` 进 ``trace_id``（同一去重键的多次触发要**不同**的溯源链，才不是同一条信号）。
    """
    moment = NOW if at is None else at
    rig.m3.events.publish(signal_event(
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
        source_trace_id=f"tr_{seq:020x}",
        occurred_at=moment,
    ))


def _admit_important(rig: M3Rig, *, max_per_slot: int = 1) -> None:
    """把「当前情境 × 当前时段 × brief」那一格改成**也允许 `important`**（用户可配，07 §2）。"""
    verdict = rig.m3.l5.budget.resolve(
        level="important", content_type="brief", at=rig.clock().time(),
    )
    assert verdict is not None
    rig.m3.l5.budget.set_cell(
        BudgetKey(
            mode=verdict.key.mode, slot_id=verdict.key.slot_id,
            content_type="brief",
        ),
        {"allowed_levels": ["emergency", "important"], "max_per_slot": max_per_slot},
    )


def _records(rig: M3Rig, dedup_key: str) -> list:
    """按去重键筛本次信号的投递留痕（按落盘时刻 + 级次排序）。"""
    return sorted(
        (r for r in rig.m3.l5.delivery.ledger() if r.dedup_key == dedup_key),
        key=lambda r: (r.created_at, r.step),
    )


def _ids(rig: M3Rig) -> set[str]:
    """当前全部留痕 id（用来把「这一次」新落的账筛出来）。"""
    return {r.delivery_id for r in rig.m3.l5.delivery.ledger()}


def _new_records(rig: M3Rig, before: set[str]) -> list:
    return [r for r in rig.m3.l5.delivery.ledger() if r.delivery_id not in before]


def _adopted(level: str, *, dedup_key: str, seq: int, at: datetime) -> Any:
    """像上游那样造一条已采纳的信号（不经总线；用于直接调频控面的裁决）。"""
    from st_agent.l5.signal import adopt_signal

    return adopt_signal(signal_event(
        level=level,
        content_ref=SignalContent(
            conclusion="sh.600000 触发异动条件：涨跌幅 +7.50%（上游投递的信号）",
            lens_stances=(SignalLensStance(
                lens_id=digest_id("lens", "l1:test-observer"),
                stance="neutral", summary="观察：该标的当日涨跌幅超过盯盘阈值",
            ),),
        ),
        evidence_refs=("run_abcdef0123456789abcd",),
        dedup_key=dedup_key, source_trace_id=f"tr_{seq:020x}", occurred_at=at,
    ))


def _ignore_once(rig: M3Rig, *, seq: int, at: datetime) -> None:
    """造一次**完整忽略**：同一类再次单独触达 → 推到链尽 → 结算为未确认。

    两次忽略要跨过[去重窗口](../../docs/技术架构-v2/07-L5-主动触达.md)（缺省 30 分钟），
    故调用方按需推进 `rig.clock`（判定取的是运行时的「此刻」，不是信号的发生时刻）。
    推进**三次**：逐级等到未读超时（5 分钟 → 15 分钟），末次把链尾结算为未确认。
    """
    rig.clock.set(at)
    _publish(rig, dedup_key=_MANUAL_DEDUP, seq=seq, at=at)
    rig.m3.l5.delivery.advance(now=at + timedelta(hours=1))
    rig.m3.l5.delivery.advance(now=at + timedelta(hours=2))
    rig.m3.l5.delivery.advance(now=at + timedelta(hours=2, minutes=1))


# ─────────────────────────── GWT-1 生产装配根 ───────────────────────────


def test_gwt1_M3_root_assembles_L0_to_L5_on_one_store(rig: M3Rig) -> None:
    """`build_m3_runtime` 把 L0–L4 **+ L5** 装到**同一个 `Store`**，各件用真实依赖构造。"""
    m3 = rig.m3
    store = m3.m1.runtime.store
    assert len(m3.m1.runtime.skills.list_all()) > 0, "官方 Pack 应可列出（L1 装配成功）"
    # M2 面逐项仍在（复用同一段装配，不是另起一套）
    assert len(m3.m2.roster.list_enabled()) == 7
    assert m3.m2.m1.graph.store is store
    # L5 六面接的是真面：渠道派发器 / 偏好 / 预算 / 编排 / 日报 / 频控 / 疲劳
    l5 = m3.l5
    assert l5.dispatcher is not None and l5.policies is not None and l5.budget is not None
    assert l5.delivery._dispatcher is l5.dispatcher            # noqa: SLF001（装配断言）
    assert l5.delivery._budget is l5.budget                    # noqa: SLF001
    assert l5.reports is not None and l5.frequency is not None and l5.fatigue is not None
    assert l5.reports._orchestrator is l5.delivery              # noqa: SLF001
    assert l5.frequency._mutes is l5.fatigue                    # noqa: SLF001（静音清单是频控端口）
    # 五个 01 §7 族已注入统一门面
    facade = m3.m1.runtime.config_registry
    for config_id in (
        "attention-budget.current-mode", "channel-delivery.emergency",
        "frequency.dedup-window-minutes", "daily-report.time", "fatigue.ignore-threshold",
    ):
        assert facade.entry(config_id) is not None, config_id


def test_gwt1_bus_wires_all_four_ends(rig: M3Rig) -> None:
    """事件总线上三端齐备：L2 冲突上报 · L3 裁决承接 · L3 反馈采集 · L5 采纳与答复。"""
    assert set(rig.m3.events.subscribers()) == {
        "l3:conflict-adjudicator", "l5:delivery-orchestrator", "l5:fatigue-monitor",
    }
    assert rig.m3.m2.m1.queue._events is rig.m3.events             # noqa: SLF001（L2 发布端）
    assert rig.m3.m2.m1.feedback._sink is rig.m3.events            # noqa: SLF001（L3 反馈送出）
    assert rig.m3.l5.subscription is not None
    assert rig.m3.l5.feedback_subscription is not None


# ─────────────────── GWT-2 信号生成 · L1 定时监控（真实产生点） ───────────────────


def test_gwt2_scheduled_monitor_run_becomes_signals(rig: M3Rig) -> None:
    """一次真调度运行 → 信号 → 总线采纳（[00 §5] 步 4 的端到端）。"""
    tick = rig.m3.tick(NOW)
    assert [r.skill_id for r in tick.runs] == ["sk_stock_watch_v1.0"], "可调度监控应到期"
    run = tick.runs[0]
    assert run.status == "ok" and run.envelope is not None
    assert rig.sender.calls == [], "官方执行器只读注入的本地缓存，不应出网"

    keys = [e.payload["dedup_key"] for e in tick.signals]
    assert keys and all(k.startswith("l1:stock-watch:") for k in keys)
    # 逐条目一条：同标的两类事（异动 / 换手率）**不合并**（07 §6 的「类」＝标的事件类型）
    assert len(keys) == len(set(keys)) == 4
    assert _MONITOR_DEDUP in keys
    # L5 侧**自动采纳**（发布这一跳完成，无需调用方手递）
    assert [s.dedup_key for s in rig.m3.l5.accepted] == keys
    # 载荷可溯源：证据引用含本次 `skill_run_id`，`source_trace_id` 即调度器造的链
    sample = tick.signals[0]
    assert run.skill_run_id in sample.payload["evidence_refs"]
    assert sample.payload["source_trace_id"] == run.trace_id
    assert rig.m3.emission_failures == []


def test_gwt2_signal_texts_pass_the_neutrality_gate(rig: M3Rig) -> None:
    """生成文案过 [01 §6] 执行点 2（发布端过一道，采纳侧还会再过一道）。"""
    guard = NeutralityGuard()
    for event in rig.m3.tick(NOW).signals:
        content = event.payload["content_ref"]
        assert guard.check_output(content["conclusion"]).passed
        for stance in content["lens_stances"]:
            assert guard.check_output(stance["summary"]).passed


def test_gwt2_untouched_skill_produces_no_signal(rig: M3Rig) -> None:
    """未命中规则的 Skill 不产信号（规则不是「凡跑必有」）——空态显式、不臆造。"""
    from st_agent.l5.sources import MonitorRule, signal_events_for_run

    tick = rig.m3.tick(NOW)
    run = tick.runs[0]
    only = (MonitorRule(
        skill_id="sk_something_else", level="routine", items_field="triggered",
        key_fields=("code",), dedup_prefix="l1:other",
        conclusion="{code} 命中（替身规则）", summary="{code} 的观察（替身规则）",
    ),)
    assert signal_events_for_run(run, rules=only) == ()


def test_gwt2_next_tick_waits_for_the_next_period(rig: M3Rig) -> None:
    """间隔 60 分钟：下一分钟再 tick 不重复执行（判定是纯函数，账在调度状态里）。"""
    rig.m3.tick(NOW)
    again = rig.m3.tick(NOW + timedelta(minutes=1))
    assert again.runs == () and again.signals == ()
    later = rig.m3.tick(NOW + timedelta(minutes=61))
    assert [r.skill_id for r in later.runs] == ["sk_stock_watch_v1.0"]


# ─────────────── GWT-3 信号生成 · L4 Deliberation（06 §6 触达联动） ───────────────


def _analyze(rig: M3Rig, text: str = "分析下 sh.600000 是否值得关注"):
    turn = rig.m3.chat.post(text)
    assert turn.needs_confirmation is True
    return turn, rig.m3.chat.confirm_and_dispatch(turn.session_id)


def test_gwt3_deliberation_emits_a_multi_lens_digest_signal(rig: M3Rig) -> None:
    """一次多视角分析 ⇒ 一条**多视角摘要**形态的信号（逐视角并列、不合并不裁决）。"""
    before = len(rig.m3.l5.accepted)
    _, outcome = _analyze(rig)
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    assert outcome.analysis is not None                     # 包装件对总线与渲染面透明

    accepted = rig.m3.l5.accepted[before:]
    assert len(accepted) == 1, "一次分析恰产一条信号"
    signal = accepted[-1]
    assert signal.level == "important"
    assert signal.dedup_key == "l4:deliberation:sh.600000 是否值得关注"
    # 逐视角摘要覆盖**全部参与视角**（并列保留，不合并分歧——铁律 4）
    stances = signal.content_ref.lens_stances
    assert len(stances) == len(outcome.analysis.result.opinions) == 7
    assert {s.lens_id for s in stances} == {
        o.lens_id for o in outcome.analysis.result.opinions
    }
    assert "多视角分析" in signal.content_ref.conclusion
    assert rig.m3.emission_failures == []


def test_gwt3_no_signal_without_a_bearish_lens(rig: M3Rig) -> None:
    """全部视角一致偏多 ⇒ **不产信号**（不硬凑，06 §6）。"""
    rig.synthesizer._stances = {name: "positive" for name in _LENS_NAMES}   # noqa: SLF001
    before = len(rig.m3.l5.accepted)
    _, outcome = _analyze(rig)
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    assert rig.m3.l5.accepted[before:] == []
    assert rig.m3.emission_failures == []


# ─────────────────────── GWT-4 预算与情境模式生效 ───────────────────────


def test_gwt4_default_cell_routes_the_monitor_signals_into_the_daily_report(rig: M3Rig) -> None:
    """缺省格位只放行 `emergency`：`important` 信号走日报路由（不丢弃、不静默）。"""
    tick = rig.m3.tick(NOW)
    assert tick.signals, "盯盘信号应已发布"
    records = rig.m3.l5.delivery.ledger()
    assert records and all(r.route == "daily_report" for r in records)
    verdict = rig.m3.l5.delivery.verdict_for(rig.m3.l5.accepted[0], now=NOW)
    assert verdict is not None and verdict.dispatch == "daily_report"
    # 待汇总项连文案一起落盘 ⇒ 日报面读得到（留痕自足，07 §5）
    queue = rig.m3.l5.delivery.daily_report_queue()
    assert queue and queue[0].body


def test_gwt4_widened_cell_lets_the_same_signals_go_out_separately(rig: M3Rig) -> None:
    """把该格位放宽到也允许 `important` ⇒ 同一批信号**逐个单独触达**（预算说了算）。"""
    _admit_important(rig, max_per_slot=9)
    tick = rig.m3.tick(NOW)
    assert tick.signals
    assert rig.channels["desktop"].payloads, "桌面通知渠道应收到触达"
    assert all(r.route != "daily_report" for r in rig.m3.l5.delivery.ledger())


# ─────────────────────── GWT-5 升级链跨渠道按序推进 ───────────────────────


def test_gwt5_escalation_chain_records_every_step(rig: M3Rig) -> None:
    """未读满等待 ⇒ 逐级推进；每级一枚**确定性** `delivery_id`（重放 / 补发安全）。"""
    rig.m3.tick(NOW)                              # 先让当日报纸落地（此后不再重投）
    _publish(rig, seq=1)
    first = _records(rig, _MANUAL_DEDUP)[-1]
    assert (first.channel, first.step) == ("desktop", 0), "缺省格位放行 emergency"
    assert first.delivery_id == delivery_id_for(
        rig.m3.l5.accepted[-1].signal_id, "desktop", 0,
    )

    stepped = rig.m3.tick(NOW + timedelta(minutes=6))       # desktop 未读等待 5 分钟
    assert [r.channel for r in stepped.escalations] == ["email"]
    final = rig.m3.tick(NOW + timedelta(minutes=30))        # email 未读等待 15 分钟
    assert [r.channel for r in final.escalations] == ["im_webhook"]

    chain = [(r.channel, r.step) for r in _records(rig, _MANUAL_DEDUP)]
    assert chain == [("desktop", 0), ("email", 1), ("im_webhook", 2)]
    # 每级一封真实载荷（三个渠道各收到一次）
    for kind in ("desktop", "email", "im_webhook"):
        assert [p for p in rig.channels[kind].payloads], kind
    # 链尽仍未读 ⇒ 下一次推进把它结算为**未确认**并**转入日报汇总**（不丢弃）
    rig.m3.tick(NOW + timedelta(minutes=31))
    settled = [(r.channel, r.status) for r in _records(rig, _MANUAL_DEDUP)]
    assert ("im_webhook", "unacknowledged") in settled
    assert any(
        r.route == "daily_report" and r.dedup_key == _MANUAL_DEDUP
        for r in rig.m3.l5.delivery.ledger()
    )


def test_gwt5_read_interrupts_the_chain(rig: M3Rig) -> None:
    """「已读」由交互面的显式事件给出：链就此停住，不再升级。"""
    rig.m3.tick(NOW)
    _publish(rig, seq=2)
    first = _records(rig, _MANUAL_DEDUP)[-1]
    rig.m3.l5.delivery.mark_read(first.delivery_id, now=NOW)
    stepped = rig.m3.tick(NOW + timedelta(minutes=30))
    assert stepped.escalations == ()
    assert [r.channel for r in _records(rig, _MANUAL_DEDUP)] == ["desktop"]


# ─────────────── GWT-6 去重 / 频控 / 疲劳在装配态生效 ───────────────


def test_gwt6_same_dedup_key_merges_within_the_window(rig: M3Rig) -> None:
    """同一 `dedup_key` 短时重复触发 ⇒ 合并为一条（附触发次数），不刷屏。"""
    _admit_important(rig)
    _publish(rig, level="important", seq=1)
    _publish(rig, level="important", seq=2)                 # 同键第二次（窗口内）
    assert rig.m3.l5.frequency.trigger_count(_MANUAL_DEDUP) == 2
    assert [r.step for r in _records(rig, _MANUAL_DEDUP)] == [0], "合并 ⇒ 不产生第二次触达"


def test_gwt6_slot_rhythm_queues_instead_of_dropping(rig: M3Rig) -> None:
    """格位节奏用满 ⇒ 裁决为**排队**进下一次日报（不丢弃，07 §6），且原因点名格位。"""
    _admit_important(rig, max_per_slot=1)
    gate = rig.m3.l5.frequency.gate
    first = gate(
        rig.m3.l5.delivery, _adopted("important", dedup_key="l1:test:a", seq=1, at=NOW),
        now=NOW,
    )
    second = gate(
        rig.m3.l5.delivery, _adopted("important", dedup_key="l1:test:b", seq=2, at=NOW),
        now=NOW,
    )
    assert first.decision.kind == "separate", "第一条按节奏单独触达"
    assert second.decision.kind == "queue", "超节奏者排队"
    assert "格位" in second.decision.reason and "上限" in second.decision.reason
    assert second.queued is not None and second.queued.route == "daily_report"
    assert first.dispatch is not None and first.dispatch.record.route == "separate"


def test_gwt6_fatigue_threshold_produces_an_inquiry_signal(rig: M3Rig) -> None:
    """逐类连续忽略达阈值 ⇒ 产生**可寻址**的询问信号（经正常投递，07 §7）。"""
    rig.m3.l5.fatigue.set_threshold(2)
    _ignore_once(rig, seq=1, at=NOW)
    # 第二次要**同时**跨过去重窗口（30 分钟）与格位周期（否则会被判排队而非触达）
    _ignore_once(rig, seq=2, at=NOW + timedelta(hours=3))
    counts = {s.dedup_key: s for s in rig.m3.l5.fatigue.counts()}
    assert counts[_MANUAL_DEDUP].ignored_streak == 2

    inquiries = rig.m3.tick(NOW + timedelta(hours=5)).inquiries
    assert len(inquiries) == 1
    inquiry = inquiries[0]
    assert inquiry.dedup_key == _MANUAL_DEDUP
    assert inquiry.delivery_id in {r.delivery_id for r in rig.m3.l5.delivery.ledger()}
    assert "连续" in inquiry.question


def test_gwt6_no_repeat_question_before_an_answer(rig: M3Rig) -> None:
    """已发问而用户未答复 ⇒ 不再发问（「少而准」不是「连着问」，07 §7）。"""
    rig.m3.l5.fatigue.set_threshold(1)
    _ignore_once(rig, seq=1, at=NOW)
    first = rig.m3.tick(NOW + timedelta(hours=4)).inquiries
    assert len(first) == 1
    assert rig.m3.tick(NOW + timedelta(hours=5)).inquiries == ()


# ─────────────────────── GWT-7 渠道不可用走显式降级 ───────────────────────


def test_gwt7_unavailable_channel_is_reported_and_the_chain_moves_on(rig: M3Rig) -> None:
    """桌面不可达 ⇒ **显式**记为降级渠道并自动改走下一级（不静默丢弃）。"""
    rig.channels["desktop"].ok = False
    _publish(rig, seq=1)
    record = _records(rig, _MANUAL_DEDUP)[-1]
    assert record.channel == "email", "首级不成即降级到下一渠道"
    assert record.detail.startswith("desktop:"), "降级原因逐级留痕（第一步即桌面）"
    assert "替身：desktop 不可达" in record.detail
    assert rig.channels["email"].payloads, "第二级真的收到了"


def test_gwt7_unwired_native_port_is_unavailable_not_faked(tmp_path: Path) -> None:
    """原生端口未接线（本仓无 TTS 合成面 / 未注入通知端口）⇒ 显式不可用并点名缺口。"""
    rig = seeded_m3(
        tmp_path / ROOT_NAME,
        channels={
            "email": RecordingChannel("email"),
            "im_webhook": RecordingChannel("im_webhook"),
        },
    )
    for kind in ("desktop", "tts"):
        health = rig.m3.l5.dispatcher.adapter(kind).health()
        assert isinstance(health, ChannelHealth)
        assert health.available is False, kind
        assert health.reason, kind


def test_gwt7_offline_cloud_channel_is_marked_pending_reconnect(rig: M3Rig) -> None:
    """渠道全都送不出去 ⇒ 如实记 `unavailable` + `pending_reconnect`（恢复后按开关补发）。"""
    for kind in ("desktop", "email", "im_webhook"):
        rig.channels[kind].ok = False
    _publish(rig, seq=1)
    record = _records(rig, _MANUAL_DEDUP)[-1]
    assert record.status == "unavailable"
    assert record.pending_reconnect is True
    assert "不可达" in record.detail
    # 待补发清单读得到；补发开关是用户可配的显式项（缺省开）
    assert record.delivery_id in {r.delivery_id for r in rig.m3.l5.delivery.pending_reconnect()}
    assert rig.m3.l5.policies.resend_on_reconnect is True


def test_gwt7_resend_rewrites_the_same_delivery_path(rig: M3Rig) -> None:
    """网络恢复后按开关补发：写**同一** `delivery_id`（确定性派生）⇒ 不产生重复留痕。"""
    for kind in ("desktop", "email", "im_webhook"):
        rig.channels[kind].ok = False
    _publish(rig, seq=1)
    failed = _records(rig, _MANUAL_DEDUP)[-1]
    assert failed.status == "unavailable" and failed.pending_reconnect is True

    for kind in ("desktop", "email", "im_webhook"):
        rig.channels[kind].ok = True
    resent = rig.m3.l5.delivery.resend(now=NOW + timedelta(minutes=2))
    assert [r.channel for r in resent] == ["desktop"]
    assert resent[0].delivery_id == failed.delivery_id, "补发写同一路径（幂等）"
    assert resent[0].status == "delivered"
    assert len(_records(rig, _MANUAL_DEDUP)) == 1, "同一 id ⇒ 覆盖同一份，不产生重复留痕"


# ─────────────────────────── GWT-8 反馈回流闭环 ───────────────────────────


def _one_inquiry(rig: M3Rig, *, threshold: int = 1):
    """造出一个询问（交出它，供答复用）。"""
    rig.m3.l5.fatigue.set_threshold(threshold)
    _ignore_once(rig, seq=1, at=NOW)
    (inquiry,) = rig.m3.tick(NOW + timedelta(hours=4)).inquiries
    return inquiry


def test_gwt8_answer_reaches_l5_through_the_feedback_bus(rig: M3Rig) -> None:
    """L3 反馈入口 → 总线 `FeedbackRecorded` → L5 疲劳面按答复落值（[00 §5] 步 7）。"""
    inquiry = _one_inquiry(rig)
    result = rig.m3.chat.record_feedback(
        {"kind": "delivery", "ref": inquiry.delivery_id}, "ignored",
        context={"fatigue_choice": "disable"},
    )
    assert result.status == "ok", result.reason
    assert result.data.delivery.delivered is True, "已接总线，反馈应真的送达"
    assert rig.m3.l5.fatigue.is_muted(inquiry.dedup_key) is True


def test_gwt8_disable_mutes_the_class_into_the_daily_report(rig: M3Rig) -> None:
    """「关闭」入静音清单 ⇒ 频控把该类**改判为汇总进日报**（关闭单独触达 ≠ 丢弃）。"""
    inquiry = _one_inquiry(rig)
    rig.m3.l5.fatigue.answer(inquiry.delivery_id, "disable", now=NOW + timedelta(hours=4))
    assert rig.m3.l5.fatigue.is_muted(inquiry.dedup_key) is True

    before_ids = _ids(rig)
    sent_before = len(rig.channels["desktop"].payloads)
    rig.clock.set(NOW + timedelta(hours=4))     # 跨过去重窗口与格位周期：这是一次「新触发」
    _publish(rig, dedup_key=inquiry.dedup_key, seq=2)
    new = _new_records(rig, before_ids)
    assert len(new) == 1, "静音类不再单独触达（只落一条待汇总项）"
    assert new[0].route == "daily_report"
    assert len(rig.channels["desktop"].payloads) == sent_before, "未再经渠道送出"


def test_gwt8_reduce_narrows_the_channel_chain(rig: M3Rig) -> None:
    """「减少」经 [07 §3] 的**既有写面**收窄该级别的渠道链（留痕可回溯）。"""
    inquiry = _one_inquiry(rig)
    before = rig.m3.l5.policies.get(inquiry.level).channels
    assert len(before) > 1
    rig.m3.l5.fatigue.answer(inquiry.delivery_id, "reduce", now=NOW + timedelta(hours=4))
    after = rig.m3.l5.policies.get(inquiry.level).channels
    assert after == before[:-1]
    assert rig.m3.l5.policies.changes(), "收窄留痕（01 §7 可回滚）"


def test_gwt8_keep_changes_no_config_and_cools_down_the_question(rig: M3Rig) -> None:
    """「保持」不改任何配置；问答基线随之推进 ⇒ **不会**立刻再问（07 §7 的不骚扰口径）。"""
    inquiry = _one_inquiry(rig)
    before = {p.level: p.channels for p in rig.m3.l5.policies.map()}
    rig.m3.l5.fatigue.answer(inquiry.delivery_id, "keep", now=NOW + timedelta(hours=4))
    assert {p.level: p.channels for p in rig.m3.l5.policies.map()} == before
    assert rig.m3.l5.fatigue.is_muted(inquiry.dedup_key) is False
    state = {s.dedup_key: s for s in rig.m3.l5.fatigue.counts()}
    assert state[inquiry.dedup_key].last_answer == "keep"
    assert state[inquiry.dedup_key].asked_delivery_id == "", "问答基线已释放"
    # 计数由留痕现算（不另存一份可被答复改写的计数），故复问仍须再攒满一轮阈值
    assert rig.m3.tick(NOW + timedelta(hours=5)).inquiries == ()


# ─────────────────── GWT-9 网关联动与出网审计 ───────────────────


def test_gwt9_cloud_hop_goes_through_the_gateway(tmp_path: Path) -> None:
    """云端渠道经 [02 §6] 网关的 `channel_delivery` 类目发出（真接线 + 真审计）。"""
    transport = RecordingTransport()
    rig = seeded_m3(
        tmp_path / ROOT_NAME,
        channels={"desktop": RecordingChannel("desktop"), "tts": RecordingChannel("tts")},
        cloud_transport=transport, email_host="smtp.example.com",
        credentials={"email": "smtp-main"}, audit=True,
    )
    rig.m3.m1.runtime.vault.add("smtp-main", "smtp", _SECRET)
    rig.channels["desktop"].ok = False                        # 首级不成 → 降级到真云端 email
    _publish(rig, seq=1)

    assert transport.calls, "云端传输端口应被调用"
    host, message = transport.calls[0]
    assert host == "smtp.example.com"
    # 凭据**真的**被取用并渲染进这一跳（对端收得到），但明文不进网关的审计
    assert _SECRET.encode("utf-8") in message
    records = rig.m3.m1.runtime.gateway.query(kind="channel_delivery")
    assert len(records) == 1
    assert records[0].target_host == "smtp.example.com"
    assert _SECRET not in json.dumps(
        [r.model_dump(mode="json") for r in records], ensure_ascii=False, default=str,
    )


def test_gwt9_audit_can_be_off_and_the_hop_still_works(tmp_path: Path) -> None:
    """审计默认关（[02 §6]）——关着也照常投出，只是无审计留痕（不假装有）。"""
    transport = RecordingTransport()
    rig = seeded_m3(
        tmp_path / ROOT_NAME,
        channels={"desktop": RecordingChannel("desktop"), "tts": RecordingChannel("tts")},
        cloud_transport=transport, email_host="smtp.example.com", audit=False,
    )
    rig.channels["desktop"].ok = False
    _publish(rig, seq=1)
    assert transport.calls
    assert rig.m3.m1.runtime.gateway.query(kind="channel_delivery") == ()
