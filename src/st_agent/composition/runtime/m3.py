"""M3 运行时：M2 面 + L5 主动触达与信号发布。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l1.events import EventBus
from st_agent.l1.runtime import (
    DOTENV_NAME,
    L1Runtime,
    open_runtime,
)
from st_agent.l2.memory import (
    ONBOARDING_KIND,
    SHARE_CONFIRMATION,
    ConfidenceModel,
    ConflictQueue,
    FragmentImporter,
    MemoryDeleter,
    MemoryGraph,
    MemoryReader,
    MemoryShare,
    MemoryWriter,
    OnboardingProtocol,
    SliceQuery,
    WritePolicy,
    checked_node,
    memory_policy_family,
    new_node_id,
    official_onboarding_entry,
)
from st_agent.l5.channels import (
    ChannelPayload,
    DesktopChannel,
    EmailChannel,
    ImWebhookChannel,
    TtsChannel,
)
from st_agent.l5.errors import SignalEmissionError
from st_agent.l5.runtime import L5Stack, build_l5
from st_agent.l5.sources import (
    DEFAULT_MONITOR_RULES,
    signal_event_for_analysis,
    signal_events_for_run,
)

from st_agent.composition.common import (
    _now,
)
from st_agent.composition.facades.chat import (
    DialogFacade,
)
from st_agent.composition.runtime.m1 import (
    M1Runtime,
    _L2Stack,
    _as_kwargs,
)
from st_agent.composition.runtime.m2 import (
    M2Runtime,
    _build_m2_pieces,
)

@dataclass(frozen=True)
class TickResult:
    """一次 ``tick`` 的产出（[00 §5](../../docs/技术架构-v2/00-架构总览.md) 步 4–8 的当轮切片）。"""

    now: datetime
    runs: tuple[Any, ...] = ()
    """本轮调度触发的执行结果（``ScheduledRun``）。"""
    signals: tuple[PlatformEvent, ...] = ()
    """本轮**发布出去**的 ``SignalEmitted``（逐条目一条，按发布顺序）。"""
    escalations: tuple[Any, ...] = ()
    """本轮升级链新产生的留痕（新一级投递 / 待汇总项）。"""
    report: Any = None
    """本轮的日报（未到点 ⇒ ``None``；当天已投 ⇒ 已投的那份）。"""
    inquiries: tuple[Any, ...] = ()
    """本轮疲劳面新产生的询问（达阈值且未在等答复者）。"""
    weekly_report: Any = None
    """本轮的**每周反思报告**（M4 面；未到点 / 本周已投 ⇒ ``None``）——只增字段，
    M1/M2/M3 的 ``tick`` 恒不带它。"""


class _SignalEmittingAnalyze:
    """把 L4 的 ``AnalyzeService`` 包一层：分析照常返回，随后按规则发布一条信号。

    总线只按**鸭子面**消费该端口（``analyze(...)`` → 含 ``envelope: ResultEnvelope`` 的
    载荷），故包装件对总线与渲染面**完全透明**（返回的是原件返回的同一个对象）。

    **信号生成失败不损坏分析结果**——分析是用户显式发起的动作，不该被一次文案校验或
    无人受理拖垮；失败原因记进 ``failures`` 并在 :class:`M3Runtime` 上**可查**
    （[00 §6](../../docs/技术架构-v2/00-架构总览.md) 失败显式化：不吞、不静默降级）。
    """

    def __init__(self, *, analyze: Any, publish: Any, failures: list) -> None:
        self._analyze = analyze
        self._publish = publish
        self._failures = failures

    def analyze(self, confirmation: Any, *, values: Any = None, now: Any = None) -> Any:
        outcome = self._analyze.analyze(confirmation, values=values, now=now)
        try:
            event = signal_event_for_analysis(outcome)
        except SignalEmissionError as exc:
            self._failures.append(f"多视角分析未产生信号：{exc}")
            return outcome
        if event is not None:
            self._publish(event)
        return outcome


def _publish_signal(events: Any, event: PlatformEvent, failures: list) -> None:
    """发布一条信号到 [01 §11](../../docs/技术架构-v2/01-平台共享契约.md) 的投递面；**未被受理即显式记因**。

    「发布出去了」与「有人受理了」是两回事（[01 §11] 的失败显式化口径）：没有订阅者、
    或订阅者拒收（如负载不合契约），都记进 ``failures`` —— 不假装送达、也不静默丢弃。
    """
    result = events.publish(event)
    if result.delivered:
        return
    detail = " / ".join(
        f"{outcome.subscriber_id}:{outcome.error or '未受理'}" for outcome in result.outcomes
    )
    failures.append(
        f"信号 {event.payload.get('dedup_key', '')!r} 发布后无人受理"
        f"（{detail or '无订阅者'}）"
    )


@dataclass(frozen=True)
class M3Runtime:
    """一次 M3 装配的全部句柄（M2 全套 + L5 面 + 事件总线；由 :func:`build_m3_runtime` 构造）。

    L5 面与总线**并列**在 :attr:`m2` 之外（同 M2 对 M1 的做法）：M2 是 M2 关卡的冻结交付物，
    改造它会让 M2 的用例与本对象互相牵连；而「M2 + L5 经一条总线接起来」正是本关卡要
    证明的装配事实（[`T-INT-004`]）。

    :meth:`tick` 是本层唯一的**职责循环**（[00 §5] 步 4–7 的当轮切片）：判定全在里面、
    时刻按次传入，故可离线复算；「多久 tick 一次」不在这里（住生产入口的常驻循环）。
    """

    m2: M2Runtime
    l5: L5Stack
    events: EventBus
    monitor_rules: tuple = DEFAULT_MONITOR_RULES
    """上游产生规则表（[`l5.sources`](../l5/sources.py)）——**离线关卡**可注入替代表。"""
    emission_failures: list = field(default_factory=list)
    """信号产生 / 发布的显式失败记录（**可查**，不吞）。"""
    now: Any = _now

    # ── 与 M1/M2 同形的便捷取用（入口与用例按同一面取句柄） ──────────────────

    @property
    def m1(self) -> M1Runtime:
        return self.m2.m1

    @property
    def store(self):
        return self.m2.store

    @property
    def chat(self) -> DialogFacade:
        return self.m2.chat

    @property
    def reader(self) -> MemoryReader:
        return self.m2.reader

    @property
    def scheduler(self) -> Any:
        """L1 调度面（定时监控的到期判定与执行，[03 §6](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。"""
        return self.m1.runtime.scheduler

    # ── 职责循环（[00 §5] 步 4–7） ────────────────────────────────────────

    def tick(self, now: datetime | None = None) -> TickResult:
        """推进一轮主动服务：**调度 → 信号 → 升级链 → 日报 → 疲劳巡查**。

        每一步都不做「兜底式」的猜测：未命中规则不产信号、未到点不生成日报、
        当天已投过不重复投、未达阈值不发问；各类失败一律留痕（``emission_failures`` /
        各面自己的可读面），**不静默略过**。
        """
        moment = self.now() if now is None else now
        runs = tuple(self.scheduler.tick(moment))
        signals: list[PlatformEvent] = []
        for run in runs:
            try:
                produced = signal_events_for_run(run, rules=self.monitor_rules)
            except SignalEmissionError as exc:
                self.emission_failures.append(
                    f"调度运行 {getattr(run, 'target_id', '')!r} 未产生信号：{exc}"
                )
                continue
            for event in produced:
                _publish_signal(self.events, event, self.emission_failures)
                signals.append(event)
        escalations = tuple(self.l5.delivery.advance(now=moment))
        report = self._daily_report(moment)
        inquiries = (
            tuple(self.l5.fatigue.sweep(moment)) if self.l5.fatigue is not None else ()
        )
        return TickResult(
            now=moment, runs=runs, signals=tuple(signals), escalations=escalations,
            report=report, inquiries=inquiries,
        )

    def _daily_report(self, moment: datetime) -> Any:
        """到点则生成并投出当日报告；**「一天一次」的去重从盘上读**（重启安全）。

        ``DailyReportBuilder.due`` 的判据只是「此刻是否已过配置时刻」（[07 §5]
        的纯函数口径），故调用方须自己接住「今天已经发过」——本面把它接在**留痕**上：
        该日报告已存在且 ``delivered_channel`` 非空即不再投（未投出则照常重试，不丢）。
        """
        reports = self.l5.reports
        if reports is None or not reports.due(moment):
            return None
        day = moment.date()
        already = reports.stored(day)
        if already is not None and already.delivered_channel:
            return already
        return reports.deliver(reports.build(day, now=moment), now=moment)


def _build_l5(
    runtime: L1Runtime,
    l2: _L2Stack,
    *,
    now: Any,
    events: Any,
    channels: Mapping[str, Any] | None = None,
    notify: Any = None,
    synthesize: Any = None,
    cloud_transport: Any = None,
    email_host: str = "",
    webhook_host: str = "",
    credentials: Mapping[str, str] | None = None,
) -> L5Stack:
    """叠 L5 面（[07](../../docs/技术架构-v2/07-L5-主动触达.md)）：四类渠道接线 + 薄装配缝。

    渠道接线口径（[D-082](../../项目管理/决策日志.md) ③ / [07 §3](../../docs/技术架构-v2/07-L5-主动触达.md)）：

    - **本地渠道收注入的原生端口**——``notify`` / ``synthesize`` 由**生产入口**从表现层取来
      （``app`` 不 import ``ui``，层序表里也没有 ``ui``）。端口缺省 ``None`` ⇒ 该渠道
      ``health().available is False`` 并点名缺口，**不假装能送**。
    - **云端渠道收传输端口 + 主机**——两者都给齐才接线；缺任一个即该渠道走**显式不可用**
      （空主机不臆造端点，[`T-INT-004`] A5）。凭据经 ``credentials`` 按渠道给
      ``credential_id``，明文**只经 ``CredentialVault.use()``** 一跳动用（不进网关、不进审计）。
    """
    resolved: dict[str, Any] = dict(channels or {})
    gateway = runtime.gateway
    credential_ids = dict(credentials or {})

    resolved.setdefault("desktop", DesktopChannel(notify))
    resolved.setdefault("tts", TtsChannel(synthesize))
    if "email" not in resolved:
        resolved["email"] = EmailChannel(
            gateway, host=email_host,
            transport=cloud_transport if (cloud_transport is not None and email_host) else None,
            vault=runtime.vault, credential_id=credential_ids.get("email"),
        )
    if "im_webhook" not in resolved:
        resolved["im_webhook"] = ImWebhookChannel(
            gateway, host=webhook_host,
            transport=cloud_transport if (cloud_transport is not None and webhook_host) else None,
            vault=runtime.vault, credential_id=credential_ids.get("im_webhook"),
        )
    return build_l5(
        runtime.store, registry=runtime.config_registry, events=events,
        channels=resolved, reader=l2.reader, now=now,
    )


def build_m3_runtime(
    root: Path | str,
    passphrase: str,
    *,
    create: bool = False,
    market_query: Any = None,
    sender: Any = None,
    transport: Any = None,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    understander: Any = None,
    synthesizer: Any = None,
    reviewer: Any = None,
    catalog: Any = None,
    executor: Any = None,
    channels: Mapping[str, Any] | None = None,
    notify: Any = None,
    synthesize: Any = None,
    cloud_transport: Any = None,
    email_host: str = "",
    webhook_host: str = "",
    credentials: Mapping[str, str] | None = None,
    monitor_rules: tuple = DEFAULT_MONITOR_RULES,
    now: Any = _now,
    **l1_kwargs: Any,
) -> M3Runtime:
    """装配 M3 全栈（＝ M2 的 L0–L4 + L5；[`T-INT-004`] 的**生产组合根**）。

    与 :func:`build_m2_runtime` 走**同一段** L2/L3/L4 装配（:func:`_build_m2_pieces`），
    差别是三处接线（都在组合根、不改任何层）：

    1. **装一条进程内事件总线**，把四端接上——L2 冲突队列的 ``events`` 端口（新提案自动
       上报）、L3 裁决承接面（``attach``）、L3 反馈采集面（``sink=总线``）、L5 的采纳面与
       疲劳答复面（``build_l5`` 自订阅）；
    2. **把 L4 的 ``analyze`` 端口包一层**，令一次多视角分析按 [06 §6](../../docs/技术架构-v2/06-L4-多视角推理.md)
       产出「多视角摘要」形态的信号（:class:`_SignalEmittingAnalyze`）；
    3. **叠 L5 面**（:func:`_build_l5`）——四类渠道、个性化、投递编排、日报、频控、疲劳。

    信号产生点、到点触发与常驻循环的分工见 [`T-INT-004`]：本函数只**装配**，
    「何时推进一轮」由生产入口按 :meth:`M3Runtime.tick` 的节奏决定。

    :param notify / synthesize: 原生能力端口（桌面通知 / TTS），由入口从表现层注入。
    :param cloud_transport / email_host / webhook_host / credentials: 云端渠道的接线与凭据
        （缺任一项即该渠道显式不可用，见 :func:`_build_l5`）。
    :param channels: 完整的渠道映射覆写口（**离线关卡**用它注入记录型替身，同 M2 的
        确定性注入口径）；给了即不与上面几项合并——替身说了算。
    """
    bus = EventBus()
    failures: list[str] = []
    runtime, l2, l3, l4 = _build_m2_pieces(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env, dotenv_path=dotenv_path,
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        catalog=catalog, executor=executor, now=now,
        events=bus, feedback_sink=bus,
        deliberations_of=lambda analyze: _SignalEmittingAnalyze(
            analyze=analyze,
            publish=lambda event: _publish_signal(bus, event, failures),
            failures=failures,
        ),
        **l1_kwargs,
    )
    l5 = _build_l5(
        runtime, l2, now=now, events=bus, channels=channels, notify=notify,
        synthesize=synthesize, cloud_transport=cloud_transport,
        email_host=email_host, webhook_host=webhook_host, credentials=credentials,
    )
    return M3Runtime(
        m2=M2Runtime(
            m1=M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3)),
            roster=l4.roster, deliberation=l4.deliberation, examiner=l4.examiner,
            viewer=l4.viewer, analyze=l4.analyze,
        ),
        l5=l5, events=bus, monitor_rules=monitor_rules,
        emission_failures=failures, now=now,
    )
