"""L6 每周反思报告（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

每周到用户配置的时刻（**周几 + HH:MM** 两项标量条目）本地生成四段式报告：
**① 本周为你做了什么 · ② 偏好漂移 · ③ 错误与理解 · ④ 建议调整**。

七条口径：

- **窗口＝ ISO 周、落盘幂等**——窗口由周键 ``<ISO 年>-W<ISO 周>`` 确定性派生
  （[`date.isocalendar()`](https://docs.python.org/3/library/datetime.html#datetime.date.isocalendar)），
  同一周的重跑写同一路径 ``reflection/weekly/<周键>.json``，**不产生重复报告**。
- **四段只凭既有读面 + 注入面组装**——① 取 L5 投递留痕与频控 / 疲劳计数，② 取
  [04 §3.1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 记忆读取面的 `evolution` **节点**
  （[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 写「evolution 边」，而 [04 §1](../../../docs/技术架构-v2/04-L2-记忆图谱.md)
  的 `evolution` 是**节点类型**、`evolves_from` 才是边类型，读取面按节点收——依实现口径落），
  ③ 取本层 [`.1`](pool.py) 池内的**否定**反馈，④ 取**注入的建议面**（见下）。**不新增上游数据源**。
- **两处空态分开判定、皆不硬凑**——该周**无任何触达** ⇒ 正文只写「本周无触达，无反馈可分析」；
  有触达但本周反馈数**低于阈值**（可配，缺省 3）⇒ 正文只写「数据还不够，下周见」。
  两者是**不同情形**（完全无触达 / 有触达无反馈），不合并。
- **§6 中性化的适用面**（[D-053](../../../项目管理/决策日志.md)）——**生成性文案**（标题 / 标签 /
  统计陈述 / 空态说明 / 候选理由）过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2，
  命中即抛；**用户数据**（③ 中用户给出的 `reason` 原文）**属数据展示、不过 §6**——用户写「我觉得…」
  时报告应原样呈现其话，而非把它当生成性文案拒掉。两者以 :class:`ReportLine.generated` 显式区分。
- **第四段只产候选、不生效**——每条 = :class:`ProposalCandidate`（`config_id` + 现值 + 建议值 +
  理由 + trace 依据），**不产 `change_id`、不落变更历史**；生效 / 回滚走 [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)
  的变更流（[08 §7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线：不存在静默调参路径）。
- **投出不在此处**——实际投出（经 [07 §3](../../../docs/技术架构-v2/07-L5-主动触达.md) 渠道面）由
  :meth:`WeeklyReportBuilder.deliver` 交渠道面、由调用方（[`.3`](runtime.py) 的装配面）接到点驱动；
  :meth:`due` 只做「此刻是否已到该周的该时刻」的**纯比较**，「本周发过没发过」从留痕读。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l2.memory import SliceQuery
from st_agent.l5.channels import CHANNEL_KINDS, ChannelPayload
from st_agent.l6.errors import WeeklyReportError
from st_agent.l6.weekly_store import (
    CONFIG_PREFIX,
    DAY_CONFIG_ID,
    MIN_FEEDBACK_CONFIG_ID,
    TEMPLATE_CONFIG_ID,
    TIME_CONFIG_ID,
    WEEK_KEY_RE,
    WeeklyReportStore,
)

__all__ = [
    "DEFAULT_CHANNELS",
    "DEFAULT_DAY_OF_WEEK",
    "DEFAULT_MIN_FEEDBACK",
    "DEFAULT_TIME",
    "INSUFFICIENT_NOTE",
    "NO_ADVISOR_NOTE",
    "NO_TOUCH_NOTE",
    "SECTION_LABELS",
    "WEEK_SECTIONS",
    "ProposalCandidate",
    "ReportLine",
    "ReportSection",
    "ReportTrace",
    "WeeklyReport",
    "WeeklyReportBuilder",
    "ReportTemplate",
    "due_on",
    "week_key",
    "week_window",
]

SectionName = Literal[
    "week_digest", "preference_drift", "errors_understood", "adjustment_proposals"
]

WEEK_SECTIONS: tuple[SectionName, ...] = (
    "week_digest", "preference_drift", "errors_understood", "adjustment_proposals",
)
"""四段（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 的顺序即此序）。"""

SECTION_LABELS: Mapping[str, str] = {
    "week_digest": "本周为你做了什么",
    "preference_drift": "偏好漂移",
    "errors_understood": "错误与理解",
    "adjustment_proposals": "建议调整",
}

NO_TOUCH_NOTE = "本周无触达，无反馈可分析"
"""该周**无任何触达**时的正文（story-09 空状态：不硬凑）。"""

INSUFFICIENT_NOTE = "数据还不够，下周见"
"""**有触达但反馈数低于阈值**时的正文（story-09 边缘情况：不硬凑）。"""

NO_ADVISOR_NOTE = "未接入建议面，本次不产生建议（提案生成规则属 08 §4–§5）"
"""未注入建议面时第四段的说明——**显式缺席**，不臆造一条建议。"""

DEFAULT_DAY_OF_WEEK = 6
"""缺省到点星期（`datetime.weekday()` 口径：周一＝0，故 6＝周日；story-09「每周日晚 8 点」）。"""

DEFAULT_TIME = "20:00"
"""缺省到点时刻（story-09「晚 8 点」）。"""

DEFAULT_MIN_FEEDBACK = 3
"""缺省数据不足阈值（本周反馈数低于它即判「数据不够」）。"""

DEFAULT_CHANNELS: tuple[str, ...] = ("desktop",)
"""缺省订阅渠道（最轻的一环；用户可配到任一渠道）。"""

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
# 周键形态的唯一出处在 `weekly_store`（落盘侧列举读面也要用它筛键，见 `WEEK_KEY_RE`）。


# ───────────────────────── 周键与窗口 ─────────────────────────


def week_key(day: date) -> str:
    """ISO 周键（``<ISO 年>-W<ISO 周>``）——报告的确定性落盘键与窗口标识。

    用 ISO 历（[`date.isocalendar`](https://docs.python.org/3/library/datetime.html#datetime.date.isocalendar)）
    而非自然年：跨年那一周（如 12-29 属次年第 1 周）只有 ISO 口径能给出唯一且连续的键。
    """
    iso_year, iso_week, _ = day.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def week_window(key: str) -> tuple[date, date]:
    """周键 → ``(周一, 周日)`` 闭区间（越界 / 非法形态即拒，不猜）。"""
    match = WEEK_KEY_RE.match((key or "").strip())
    if not match:
        raise WeeklyReportError(f"周键须为 <ISO 年>-W<ISO 周>，收到 {key!r}")
    iso_year, iso_week = int(match.group(1)), int(match.group(2))
    try:
        start = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise WeeklyReportError(f"周键 {key!r} 不是合法的 ISO 周：{exc}") from exc
    return start, start + timedelta(days=6)


def due_on(moment: datetime, *, day_of_week: int, at: time) -> bool:
    """``moment`` 是否已到该周的该时刻——**纯比较**，不含「本周发过没发过」（去重归调用方）。"""
    if moment.weekday() != day_of_week:
        return False
    return moment.timetz().replace(tzinfo=None) >= at


# ───────────────────────── 模型 ─────────────────────────


class ReportLine(BaseModel):
    """报告一行（**生成性文案**与**用户数据**显式分开——[D-053](../../../项目管理/决策日志.md)）。"""

    model_config = ConfigDict(frozen=True)

    text: str
    generated: bool = True
    """``True``＝本层生成的文案，须过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)；
    ``False``＝用户数据原样呈现（如否定反馈的 `reason`），**不过 §6**。"""


class ReportSection(BaseModel):
    """报告的一段（段名 + 行 + 仅在无内容时给出的说明）。"""

    model_config = ConfigDict(frozen=True)

    name: SectionName
    label: str
    lines: tuple[ReportLine, ...] = ()
    empty_note: str = ""

    @property
    def empty(self) -> bool:
        return not self.lines


class ProposalCandidate(BaseModel):
    """第四段的一条**建议候选**（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 第 4 段）。

    **无 `change_id`**——本层只**产出提案**，生效 / 回滚一律经 [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)
    的变更流（[§7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线：不存在静默调参路径）。
    """

    model_config = ConfigDict(frozen=True)

    config_id: str
    current: Any = None
    suggested: Any = None
    reason: str
    """中性陈述式理由（**生成性文案**，须过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    trace_ref: str = ""
    """该建议所依据的留痕锚点（[01 §4](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

    def as_line(self) -> ReportLine:
        """候选 → 报告行（`config_id` + 现值 → 建议值 + 理由 —— 中性显示形态）。"""
        move = f"{self.current!r} → {self.suggested!r}" if (
            self.current is not None or self.suggested is not None
        ) else "未给出建议值"
        return ReportLine(text=f"{self.config_id}：{move}（{self.reason}）")


class ReportTrace(BaseModel):
    """**完整 Trace** 的锚点（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)「生成走完整 Trace」；[01 §4](../../../docs/技术架构-v2/01-平台共享契约.md) / [§1](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

    model_config = ConfigDict(frozen=True)

    signal_ids: tuple[str, ...] = ()
    delivery_ids: tuple[str, ...] = ()
    feedback_ids: tuple[str, ...] = ()
    memory_node_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (
            self.signal_ids or self.delivery_ids or self.feedback_ids
            or self.memory_node_ids or self.evidence_refs or self.trace_ids
        )


class ReportTemplate(BaseModel):
    """结构模板（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）：启用哪几段 + 订阅到哪些渠道。"""

    model_config = ConfigDict(frozen=True)

    sections: tuple[SectionName, ...] = WEEK_SECTIONS
    channels: tuple[str, ...] = DEFAULT_CHANNELS


class WeeklyReport(BaseModel):
    """一份每周反思报告（四段 + 完整 Trace + 空态标记 + 投出形态）。"""

    model_config = ConfigDict(frozen=True)

    week: str
    """周键（`<ISO 年>-W<ISO 周>`；即落盘键）。"""
    period_start: date
    period_end: date
    generated_at: datetime
    sections: tuple[ReportSection, ...]
    empty: bool
    """该周无任何触达（正文即 :data:`NO_TOUCH_NOTE`）。"""
    insufficient: bool = False
    """有触达但反馈数低于阈值（正文即 :data:`INSUFFICIENT_NOTE`）。"""
    title: Annotated[str, Field(min_length=1)]
    body: Annotated[str, Field(min_length=1)]
    trace: ReportTrace
    proposals: tuple[ProposalCandidate, ...] = ()
    anchor: str
    """投出载荷的来源锚点（本报告无 §1 信号，取「周键的确定性摘要」）。"""
    delivered_channel: str = ""
    """已投出的渠道（未投出为空——**不假装送达**）。"""


# ───────────────────────── 组装 / 投出 / 条目 ─────────────────────────


class WeeklyReportBuilder:
    """周报的组装 / 投出 / 模板门面（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（组装照常，但不落盘）
    :param pool: [`.1`](pool.py) 的 ``FeedbackPool`` 鸭子面（只用到 `between` / `by_action`）；
        缺省 ``None`` ⇒ ③ 走空态、① 的反馈计数按 0 并显式说明
    :param orchestrator: L5 `DeliveryOrchestrator` 鸭子面（`ledger` / `settlements`）；
        缺省 ``None`` ⇒ 无投递留痕可读、判为「本周无触达」
    :param frequency: L5 `FrequencyController` 鸭子面（`counts()`）
    :param fatigue: L5 `FatigueMonitor` 鸭子面（`counts()`）
    :param reader: L2 `MemoryReader` 鸭子面（`query(SliceQuery(...))`）；缺省 ``None`` ⇒ ② 走空态
    :param advisor: **建议面**鸭子端口（`proposals(evidence) -> Sequence[ProposalCandidate | Mapping]`）；
        缺省 ``None`` ⇒ ④ 显式写「未接入建议面」——**不臆造建议**
        （提案的生成规则属 [08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)–[§5](../../docs/技术架构-v2/08-L6-反思演进.md) 的范围）
    :param dispatcher: L5 `ChannelDispatcher`（订阅投出；缺省 ``None`` ⇒ 不投出）
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        pool: Any = None,
        orchestrator: Any = None,
        frequency: Any = None,
        fatigue: Any = None,
        reader: Any = None,
        advisor: Any = None,
        dispatcher: Any = None,
        guard: NeutralityGuard | None = None,
        default_day_of_week: int = DEFAULT_DAY_OF_WEEK,
        default_time: str = DEFAULT_TIME,
        default_template: ReportTemplate | None = None,
        default_min_feedback: int = DEFAULT_MIN_FEEDBACK,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = WeeklyReportStore(store, now=now) if store is not None else None
        self._pool = pool
        self._orchestrator = orchestrator
        self._frequency = frequency
        self._fatigue = fatigue
        self._reader = reader
        self._advisor = advisor
        self._dispatcher = dispatcher
        self._guard = guard if guard is not None else NeutralityGuard()
        self._default_day = _require_day(default_day_of_week)
        self._default_time = _require_time(default_time)
        self._default_template = (
            default_template if default_template is not None else ReportTemplate()
        )
        self._default_min_feedback = _require_min_feedback(default_min_feedback)
        self._now = _system_now if now is None else now

    # ───────────────────────── 到点判定 ─────────────────────────

    def due(self, now: datetime | None = None) -> bool:
        """此刻是否已到（或过了）用户配置的周报时刻——**纯比较**（去重归调用方）。"""
        moment = self._now() if now is None else now
        return due_on(moment, day_of_week=self.day_of_week(), at=self.report_time())

    def day_of_week(self) -> int:
        """当前到点星期（落盘条目优先，缺省回落内置）。"""
        raw = self._default_day if self._store is None else self._store.current(
            DAY_CONFIG_ID, self._default_day,
        )
        try:
            return _require_day(raw)
        except WeeklyReportError:
            return self._default_day

    def report_time(self) -> time:
        """当前到点时刻（落盘条目优先，缺省回落内置）。"""
        raw = self._default_time if self._store is None else self._store.current(
            TIME_CONFIG_ID, self._default_time,
        )
        try:
            return _require_time(raw)
        except WeeklyReportError:
            return self._default_time

    def min_feedback(self) -> int:
        """当前数据不足阈值（落盘条目优先，缺省回落内置）。"""
        raw = self._default_min_feedback if self._store is None else self._store.current(
            MIN_FEEDBACK_CONFIG_ID, self._default_min_feedback,
        )
        try:
            return _require_min_feedback(raw)
        except WeeklyReportError:
            return self._default_min_feedback

    def template(self) -> ReportTemplate:
        """当前模板（落盘条目优先，缺省回落内置）。"""
        if self._store is None:
            return self._default_template
        raw = self._store.current(TEMPLATE_CONFIG_ID, None)
        if not isinstance(raw, Mapping):
            return self._default_template
        try:
            return _template_from_payload(raw)
        except WeeklyReportError:
            return self._default_template

    # ───────────────────────── 组装 ─────────────────────────

    def build(self, week: str, *, now: datetime | None = None, persist: bool = True) -> WeeklyReport:
        """组装 ``week`` 那一周的报告（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""
        moment = self._now() if now is None else now
        start, end = week_window(week)
        window = _window_bounds(start, end, moment)
        template = self.template()

        deliveries = _in_window(_call_list(self._orchestrator, "ledger"), window)
        feedback = self._feedback_in(window)
        anchors: dict[str, list[str]] = {
            "signal_ids": [], "delivery_ids": [], "feedback_ids": [],
            "memory_node_ids": [], "evidence_refs": [], "trace_ids": [],
        }
        anchors["delivery_ids"].extend(str(getattr(r, "delivery_id", "")) for r in deliveries)
        anchors["signal_ids"].extend(str(getattr(r, "signal_id", "")) for r in deliveries)
        anchors["feedback_ids"].extend(e.event.feedback_id for e in feedback)
        anchors["trace_ids"].extend(
            str(getattr(r, "trace_id", "")) for r in deliveries if getattr(r, "trace_id", "")
        )
        anchors["evidence_refs"].extend(
            ref for r in deliveries for ref in (getattr(r, "evidence_refs", ()) or ())
        )

        proposals = self._proposals(deliveries, feedback, anchors)
        built: dict[SectionName, ReportSection] = {
            "week_digest": self._digest(deliveries, feedback),
            "preference_drift": self._drift(anchors),
            "errors_understood": self._errors(feedback),
            "adjustment_proposals": self._adjustments(proposals),
        }
        sections = tuple(built[name] for name in template.sections)

        no_touch = not deliveries
        insufficient = (not no_touch) and len(feedback) < self.min_feedback()
        title = f"{week} 每周反思"
        body = (
            NO_TOUCH_NOTE if no_touch
            else INSUFFICIENT_NOTE if insufficient
            else _compose(sections)
        )
        self._require_neutral(title, "周报标题")
        for section in sections:
            for line in section.lines:
                if line.generated:               # 用户数据行不过 §6（D-053）
                    self._require_neutral(line.text, f"周报「{section.label}」行")
            if section.empty_note:
                self._require_neutral(section.empty_note, f"周报「{section.label}」空态")
        for candidate in proposals:
            self._require_neutral(candidate.reason, "建议候选理由")

        report = WeeklyReport(
            week=week, period_start=start, period_end=end, generated_at=moment,
            sections=sections, empty=no_touch, insufficient=insufficient,
            title=title, body=body,
            trace=ReportTrace(
                signal_ids=_dedup(anchors["signal_ids"]),
                delivery_ids=_dedup(anchors["delivery_ids"]),
                feedback_ids=_dedup(anchors["feedback_ids"]),
                memory_node_ids=_dedup(anchors["memory_node_ids"]),
                evidence_refs=_dedup(anchors["evidence_refs"]),
                trace_ids=_dedup(anchors["trace_ids"]),
            ),
            proposals=proposals,
            anchor=digest_id("wk", week),
        )
        if persist and self._store is not None:
            self._store.put(week, report.model_dump(mode="json"))
        return report

    def deliver(self, report: WeeklyReport, *, now: datetime | None = None) -> WeeklyReport:
        """把报告订阅投出（经 L5 渠道面）；未接投出面 / 渠道全不可用 ⇒ **原样返回**。

        投出结果回写留痕（同 [T-L5-003.1](../../../项目管理/tasks/done/M3/T-L5-003.1-每日报告本体与模板配置.md)
        的日报口径）：真正投出的渠道写回该周报告，于是「这一周是否已投过」成为**可从盘上读出的
        既成事实**，调用方的「一周一次」去重因此**重启安全**；未投出则**不写**（下一轮还会再试）。
        """
        if self._dispatcher is None:
            return report
        payload = ChannelPayload(
            signal_id=report.anchor, level="routine",
            title=report.title, body=report.body,
            trace_id=report.trace.trace_ids[0] if report.trace.trace_ids else report.anchor,
        )
        dispatch = self._dispatcher.deliver(list(self.template().channels), payload)
        delivered = getattr(dispatch, "delivered", None)
        channel = str(getattr(delivered, "channel", "") or "")
        updated = report.model_copy(update={"delivered_channel": channel})
        if channel and self._store is not None:
            self._store.put(report.week, updated.model_dump(mode="json"))
        return updated

    def stored(self, week: str) -> WeeklyReport | None:
        """取某周**已生成**的报告（读面：落盘即事实）。

        :return: 该周的报告；从未生成过 → ``None``
        :raises WeeklyReportError: 落盘形态损坏（**不返回空壳**、也不当作「没生成过」）
        """
        if self._store is None:
            return None
        raw = self._store.get(week)
        if raw is None:
            return None
        try:
            return WeeklyReport.model_validate(raw)
        except ValidationError as exc:
            raise WeeklyReportError(f"周报形态损坏（{week}）：{exc}") from exc

    def stored_weeks(self) -> tuple[str, ...]:
        """已生成过的周键（升序）——表现层列历史报告 / 取最近一期的读面。

        与 :meth:`stored` 同属读面（**落盘即事实**）：只列形态合法的键，不判定内容；
        未接落盘（``store is None``）⇒ 空（同 :meth:`stored` 的「读不到就是没有」口径）。
        """
        if self._store is None:
            return ()
        return self._store.weeks()

    def publish(self, week: str, *, now: datetime | None = None) -> WeeklyReport | None:
        """把 ``week`` 的报告**投出一次**——「一周一次」在这里落地、且**重启安全**。

        判据取**留痕**（不是进程内记忆）：该周报告已存在且已投出 ⇒ 返回 ``None``（不再投）；
        已生成但**未投出**（渠道当时不可用）⇒ 拿盘上那份**重投**（不重新生成，避免同周出现
        两份不同内容）；从未生成 ⇒ 先生成再投。

        调用方只在 :meth:`due` 为真时调它（「何时推进一轮」归生产入口的常驻循环）。
        """
        stored = self.stored(week)
        if stored is not None and stored.delivered_channel:
            return None
        report = stored if stored is not None else self.build(week, now=now)
        return self.deliver(report, now=now)

    # ───────────────────────── 条目与写面 ─────────────────────────

    def set_day_of_week(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """设置到点星期（0–6，周一＝0；越界即拒、不落盘不留痕）。"""
        target = _require_day(value)
        self._default_day = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(DAY_CONFIG_ID, target),
            previous=self._store.current(DAY_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def set_time(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """设置到点时刻（`HH:MM`；越界即拒、不落盘不留痕）。"""
        target = _require_time(value)
        self._default_time = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(TIME_CONFIG_ID, target.strftime("%H:%M")),
            previous=self._store.current(TIME_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def set_min_feedback(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """设置数据不足阈值（非负整数；越界即拒、不落盘不留痕）。"""
        target = _require_min_feedback(value)
        self._default_min_feedback = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(MIN_FEEDBACK_CONFIG_ID, target),
            previous=self._store.current(MIN_FEEDBACK_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def set_template(
        self, template: ReportTemplate | Mapping[str, Any], *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """替换结构模板（四段开关 + 订阅渠道链）；越界即拒、不落盘不留痕。

        取值是对象 ⇒ 按 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) **不在统一标量落值面内**，
        写面即本方法（裁法同 L5 的日报模板条目）。
        """
        target = _template_from_value(template)
        self._default_template = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(TEMPLATE_CONFIG_ID, _template_payload(target)),
            previous=self._store.current(TEMPLATE_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本层登记进 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的四个条目（含当前取值）。"""
        return (
            self._entry(DAY_CONFIG_ID, self.day_of_week()),
            self._entry(TIME_CONFIG_ID, self.report_time().strftime("%H:%M")),
            self._entry(TEMPLATE_CONFIG_ID, _template_payload(self.template())),
            self._entry(MIN_FEEDBACK_CONFIG_ID, self.min_feedback()),
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        """按 `config_id` 取条目登记形态；不属本层 → ``None``；**损坏 → 校验错**。"""
        if not config_id.startswith(CONFIG_PREFIX):
            return None
        if self._store is not None:
            stored = self._store.entry(config_id)
            if stored is not None:
                return stored
        for entry in self.entries():
            if entry.config_id == config_id:
                return entry
        return None

    # ───────────────────────── 四段取材 ─────────────────────────

    def _feedback_in(self, window: tuple[datetime, datetime]) -> tuple[Any, ...]:
        if self._pool is None:
            return ()
        try:
            return tuple(self._pool.between(window[0], window[1]))
        except Exception as exc:            # 池子损坏 → 显式失败，不当空池（08 §1 的静默失败面）
            raise WeeklyReportError(f"反思数据池不可读（{exc}）") from exc

    def _digest(self, deliveries: Sequence[Any], feedback: Sequence[Any]) -> ReportSection:
        """① 本周为你做了什么：投递 / 结算统计 + 反馈计数 + 过滤噪音（**中性陈述**）。"""
        if not deliveries and not feedback:
            return ReportSection(
                name="week_digest", label=SECTION_LABELS["week_digest"],
                empty_note="本周没有需要送到你面前的事",
            )
        by_status = _count_by(deliveries, "status")
        by_action = _count_by_action(feedback)
        separate = sum(1 for r in deliveries if getattr(r, "route", "") == "separate")
        lines = [
            ReportLine(text=(
                f"本周共记录投递 {len(deliveries)} 条"
                f"（单独触达 {separate} 条、汇总进日报 {len(deliveries) - separate} 条）"
            )),
            ReportLine(text=(
                "其中已读 {read} 条、未确认 {unack} 条、未送达 {undelivered} 条".format(
                    read=by_status.get("read", 0),
                    unack=by_status.get("unacknowledged", 0),
                    undelivered=by_status.get("unavailable", 0) + by_status.get("failed", 0),
                )
            )),
        ]
        if feedback:
            lines.append(ReportLine(text=(
                "本周收到反馈 {total} 条：采纳 {adopted} 条、忽略 {ignored} 条、"
                "否定 {rejected} 条、追问 {queried} 条、点赞 {liked} 条".format(
                    total=len(feedback), adopted=by_action.get("adopted", 0),
                    ignored=by_action.get("ignored", 0), rejected=by_action.get("rejected", 0),
                    queried=by_action.get("queried", 0), liked=by_action.get("liked", 0),
                )
            )))
        noise = self._noise_note()
        if noise:
            lines.append(ReportLine(text=noise))
        return ReportSection(
            name="week_digest", label=SECTION_LABELS["week_digest"], lines=tuple(lines),
        )

    def _noise_note(self) -> str:
        """过滤噪音的**当前**计数（去重合并 / 静音）——显式标注「当前」以免与本周数混淆。"""
        merged = _merged_count(self._frequency)
        muted = _muted_count(self._fatigue)
        if merged is None and muted is None:
            return ""
        parts: list[str] = []
        if merged is not None:
            parts.append(f"去重窗口内合并 {merged} 条")
        if muted is not None:
            parts.append(f"静音 {muted} 类")
        return "过滤噪音（当前计数）：" + "、".join(parts)

    def _drift(self, anchors: dict[str, list[str]]) -> ReportSection:
        """② 偏好漂移：取记忆读取面的 `evolution` 节点（本批按**节点**口径落）。"""
        if self._reader is None:
            return ReportSection(
                name="preference_drift", label=SECTION_LABELS["preference_drift"],
                empty_note="未接入记忆读取面，本次不做偏好漂移分析",
            )
        try:
            result = self._reader.query(
                SliceQuery(task_type="reflection", topic="", view="timeline")
            )
        except Exception as exc:            # 读取面缺陷 → 降级但仍显式说明
            return ReportSection(
                name="preference_drift", label=SECTION_LABELS["preference_drift"],
                empty_note=f"记忆读取面不可用（{exc}）",
            )
        slices = _evolution_slices(getattr(result, "slices", ()) or ())
        if not slices:
            return ReportSection(
                name="preference_drift", label=SECTION_LABELS["preference_drift"],
                empty_note="记忆里尚无偏好演化记录可供分析",
            )
        lines: list[ReportLine] = []
        for slice_ in slices:
            node = getattr(slice_, "node", None)
            node_id = str(getattr(node, "memory_node_id", ""))
            if node_id:
                anchors["memory_node_ids"].append(node_id)
            dimension = getattr(node, "dimension", None) or "未标维度"
            lines.append(ReportLine(text=(
                f"维度 {dimension}：置信度 {_confidence_of(slice_):.2f}，"
                f"{_source_note(slice_)}，更新于 {_updated_of(node)}"
            )))
        return ReportSection(
            name="preference_drift", label=SECTION_LABELS["preference_drift"], lines=tuple(lines),
        )

    def _errors(self, feedback: Sequence[Any]) -> ReportSection:
        """③ 错误与理解：被否定的推送 + 用户给出的原因（**原因原文属用户数据**）。"""
        rejected = [e for e in feedback if getattr(e.event, "action", "") == "rejected"]
        if not rejected:
            return ReportSection(
                name="errors_understood", label=SECTION_LABELS["errors_understood"],
                empty_note="本周没有被否定的推送",
            )
        lines: list[ReportLine] = [
            ReportLine(text=f"本周被否定的推送 {len(rejected)} 条；用户给出的原因如下："),
        ]
        for entry in rejected:
            target = getattr(entry.event, "target", None)
            ref = str(getattr(target, "ref", ""))
            reason = str(getattr(entry.event, "reason", "") or "")
            lines.append(ReportLine(text=f"{ref}：{reason}", generated=False))
        return ReportSection(
            name="errors_understood", label=SECTION_LABELS["errors_understood"],
            lines=tuple(lines),
        )

    def _adjustments(self, proposals: Sequence[ProposalCandidate]) -> ReportSection:
        """④ 建议调整：候选逐条列出（未接入建议面 ⇒ **显式缺席**，不臆造）。"""
        if not proposals:
            return ReportSection(
                name="adjustment_proposals", label=SECTION_LABELS["adjustment_proposals"],
                empty_note=(
                    NO_ADVISOR_NOTE if self._advisor is None
                    else "本周没有需要调整的配置"
                ),
            )
        return ReportSection(
            name="adjustment_proposals", label=SECTION_LABELS["adjustment_proposals"],
            lines=tuple(candidate.as_line() for candidate in proposals),
        )

    def _proposals(
        self, deliveries: Sequence[Any], feedback: Sequence[Any], anchors: dict[str, list[str]]
    ) -> tuple[ProposalCandidate, ...]:
        if self._advisor is None:
            return ()
        evidence = {
            "week_deliveries": len(deliveries),
            "feedback_total": len(feedback),
            "rejected": sum(1 for e in feedback if getattr(e.event, "action", "") == "rejected"),
            "ignored": sum(1 for e in feedback if getattr(e.event, "action", "") == "ignored"),
            "delivery_ids": tuple(anchors["delivery_ids"]),
            "feedback_ids": tuple(anchors["feedback_ids"]),
        }
        try:
            raw = self._advisor.proposals(evidence)
        except Exception as exc:
            raise WeeklyReportError(f"建议面不可用（{exc}）") from exc
        out: list[ProposalCandidate] = []
        for item in raw or ():
            if isinstance(item, ProposalCandidate):
                out.append(item)
                continue
            try:
                out.append(ProposalCandidate(**dict(item)))
            except (ValidationError, TypeError, ValueError) as exc:
                raise WeeklyReportError(f"建议候选不合 08 §2 形态：{exc}") from exc
        return tuple(out)

    # ───────────────────────── 内部 ─────────────────────────

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise WeeklyReportError(
                f"{where} 未过 01 §6 中性化校验（{hits}）：文案须中性（铁律 2）"
            )

    def _entry(self, config_id: str, default: Any) -> ConfigEntry:
        display, schema, chat, panel = _ENTRY_SPECS[config_id]
        return ConfigEntry(
            config_id=config_id, display_name=display, value_schema=schema, default=default,
            description_for_chat=chat, panel_form_spec=panel, scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )


# ───────────────────────── 取值解释（越界一律显式拒） ─────────────────────────


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _window_bounds(start: date, end: date, moment: datetime) -> tuple[datetime, datetime]:
    """周窗口 → 带时区的时间窗（用 `moment` 的时区；末日取 23:59:59.999999 的**闭区间**）。"""
    tz = moment.tzinfo
    lower = datetime.combine(start, time.min, tzinfo=tz)
    upper = datetime.combine(end, time.max, tzinfo=tz)
    return lower, upper


def _in_window(records: Sequence[Any], window: tuple[datetime, datetime]) -> tuple[Any, ...]:
    lower, upper = window
    return tuple(
        r for r in records
        if isinstance(getattr(r, "created_at", None), datetime)
        and lower <= r.created_at <= upper
    )


def _call_list(target: Any, name: str) -> Sequence[Any]:
    """读面调用（未注入面 → 空集；不是错误，是「没有留痕」）。"""
    if target is None:
        return ()
    method = getattr(target, name, None)
    return () if method is None else tuple(method())


def _count_by(records: Sequence[Any], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in records:
        key = str(getattr(record, field, ""))
        out[key] = out.get(key, 0) + 1
    return out


def _count_by_action(feedback: Sequence[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in feedback:
        key = str(getattr(getattr(entry, "event", None), "action", ""))
        out[key] = out.get(key, 0) + 1
    return out


def _merged_count(frequency: Any) -> int | None:
    """去重窗口内被合并的条数（每个 `dedup_key` 的 `trigger_count - 1` 之和）。"""
    if frequency is None:
        return None
    try:
        counts = tuple(frequency.counts())
    except Exception:
        return None
    return sum(max(0, int(getattr(c, "trigger_count", 1)) - 1) for c in counts)


def _muted_count(fatigue: Any) -> int | None:
    """被用户答复「关闭」的类数（静音清单规模）。"""
    if fatigue is None:
        return None
    try:
        states = tuple(fatigue.counts())
    except Exception:
        return None
    return sum(1 for s in states if getattr(s, "muted", False))


def _evolution_slices(slices: Sequence[Any]) -> tuple[Any, ...]:
    """只取 `evolution` 节点切片（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 的偏好漂移取材面）。"""
    return tuple(
        s for s in slices
        if str(getattr(getattr(s, "node", None), "type", "")) == "evolution"
    )


def _confidence_of(slice_: Any) -> float:
    try:
        return float(getattr(slice_, "confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _source_note(slice_: Any) -> str:
    return {"user_stated": "用户自述", "inferred": "系统推断"}.get(
        str(getattr(slice_, "source", "")), "来源未知"
    )


def _updated_of(node: Any) -> str:
    updated = getattr(node, "updated_at", None)
    return updated.strftime("%Y-%m-%d") if isinstance(updated, datetime) else "未知"


def _dedup(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in values if v))


def _compose(sections: Sequence[ReportSection]) -> str:
    blocks: list[str] = []
    for section in sections:
        head = f"【{section.label}】"
        if section.empty:
            blocks.append(f"{head}{section.empty_note}")
            continue
        blocks.append("\n".join([head, *[line.text for line in section.lines]]))
    return "\n".join(blocks)


def _require_day(value: Any) -> int:
    """`0`–`6` 的整数（周一＝0，同 `datetime.weekday()`）；越界即拒、不猜。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WeeklyReportError(f"到点星期须为 0–6 的整数（周一＝0），收到 {value!r}")
    if not 0 <= value <= 6:
        raise WeeklyReportError(f"到点星期须在 0–6 之间（周一＝0），收到 {value!r}")
    return value


def _require_time(value: Any) -> time:
    """`HH:MM` 字符串 / `datetime.time` → :class:`datetime.time`（越界即拒，不猜）。"""
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        match = _TIME_RE.match(value.strip())
        if match:
            return time(int(match.group(1)), int(match.group(2)))
    raise WeeklyReportError(f"周报时刻须为 HH:MM（24 小时制），收到 {value!r}")


def _require_min_feedback(value: Any) -> int:
    """非负整数阈值（`0` 合法＝只在完全无反馈时判不足）；越界即拒。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WeeklyReportError(f"数据不足阈值须为非负整数，收到 {value!r}")
    if value < 0:
        raise WeeklyReportError(f"数据不足阈值不得为负，收到 {value!r}")
    return value


def _template_from_value(value: Any) -> ReportTemplate:
    if isinstance(value, ReportTemplate):
        return value
    if not isinstance(value, Mapping):
        raise WeeklyReportError(
            f"周报模板须为 ReportTemplate 或映射，收到 {type(value).__name__}"
        )
    return _validate_template(value.get("sections", WEEK_SECTIONS),
                              value.get("channels", DEFAULT_CHANNELS))


def _template_from_payload(raw: Mapping[str, Any]) -> ReportTemplate:
    return _validate_template(raw.get("sections"), raw.get("channels"))


def _validate_template(sections: Any, channels: Any) -> ReportTemplate:
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        raise WeeklyReportError(f"模板的 sections 须为列表，收到 {type(sections).__name__}")
    unknown = [name for name in sections if name not in WEEK_SECTIONS]
    if unknown:
        raise WeeklyReportError(
            f"未知报告段 {unknown}；合法段 = {list(WEEK_SECTIONS)}（08 §2）"
        )
    if not sections:
        raise WeeklyReportError("模板至少要启用一段（08 §2）")
    if len(set(sections)) != len(sections):
        raise WeeklyReportError(f"同一段不得出现两次：{list(sections)}")
    if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)):
        raise WeeklyReportError(f"模板的 channels 须为列表，收到 {type(channels).__name__}")
    bad = [c for c in channels if c not in CHANNEL_KINDS]
    if bad:
        raise WeeklyReportError(
            f"未知订阅渠道 {bad}；合法渠道 = {list(CHANNEL_KINDS)}（07 §3）"
        )
    if not channels:
        raise WeeklyReportError("模板至少要订阅一个渠道（08 §2「经 L5 渠道分发」）")
    if len(set(channels)) != len(channels):
        raise WeeklyReportError(f"同一渠道不得订阅两次：{list(channels)}")
    ordered = tuple(name for name in WEEK_SECTIONS if name in set(sections))
    return ReportTemplate(sections=ordered, channels=tuple(channels))


def _template_payload(template: ReportTemplate) -> dict[str, Any]:
    return {"sections": list(template.sections), "channels": list(template.channels)}


# ───────────────────────── 01 §7 条目的登记形态 ─────────────────────────

_ENTRY_SPECS: dict[str, tuple[str, dict[str, Any], str, PanelField]] = {
    DAY_CONFIG_ID: (
        "每周反思报告生成日",
        {"type": "integer", "minimum": 0, "maximum": 6},
        "每周哪一天本地生成反思报告（0＝周一 … 6＝周日）",
        PanelField(widget="text", label="生成日", help_text="0–6，周一＝0；缺省 6（周日）"),
    ),
    TIME_CONFIG_ID: (
        "每周反思报告生成时刻",
        {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"},
        "生成日当天的什么时候本地生成反思报告（24 小时制 HH:MM）",
        PanelField(widget="text", label="生成时刻", help_text="24 小时制 HH:MM，如 20:00"),
    ),
    TEMPLATE_CONFIG_ID: (
        "每周反思报告结构模板",
        {"type": "object"},
        "报告包含哪几段（本周为你做了什么 / 偏好漂移 / 错误与理解 / 建议调整），以及订阅到哪些渠道",
        PanelField(
            widget="matrix", label="报告结构模板",
            help_text="四段开关与订阅渠道链；写入走周报自有 API",
        ),
    ),
    MIN_FEEDBACK_CONFIG_ID: (
        "数据不足阈值",
        {"type": "integer", "minimum": 0},
        "本周反馈数低于该值时，报告只写「数据还不够，下周见」",
        PanelField(widget="text", label="数据不足阈值", help_text="非负整数，缺省 3"),
    ),
}
