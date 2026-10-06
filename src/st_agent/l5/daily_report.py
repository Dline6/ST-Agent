"""L5 每日报告（「给你的信」，[07 §5](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

到用户配置的时刻本地生成四段：**昨日回顾 + 今日关注 + 多视角摘要 + Memory 更新摘要**。

五条口径：

- **只凭本层留痕 + 注入的记忆面组装**——待汇总项（[`.2`](delivery.py)）**连文案一起落盘**
  （[`delivery_store.py`](delivery_store.py) 的「留痕自足」），故日报面**不依赖进程内信号
  上下文**，可跨进程重建；「Memory 更新摘要」经 [04 §3.1](../../docs/技术架构-v2/04-L2-记忆图谱.md)
  的 `MemoryReader` **注入**取材。
- **生成是「显式时刻的纯函数」**——同一 `(day, 留痕状态)` 恒得同一结论、**写同一路径**
  `execution_log/daily_report/<YYYY-MM-DD>.json`（**既有分区**）；到点重跑不产生重复报告。
  **实际到点触发**（常驻循环 / 调度器目标）不本层——本层交 :meth:`~DailyReportBuilder.due`
  的判定与 :meth:`~DailyReportBuilder.build`，接线归里程碑关卡。
- **多视角摘要并列保留、不合并分歧**（[铁律 4](../../../项目管理/工程宪法.md)）——逐待汇总项
  原样给出其文案（结论 + 逐视角摘要），不产出合并后的单一结论。
- **空态显式**——段级空态各给说明；**四段皆空** ⇒ 正文写「今天没有需要打扰你的事」，
  **不硬凑**（[story-07](../../docs/PRD-v2-Agent/story-07-ambient-delivery.md) 空状态）。
- **整段文案过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2**（命中即抛错）。

**「可订阅到任一渠道」＝经 [`.1`](channels.py) 的渠道面投出**（[`ChannelDispatcher`](channels.py)
按链序 + 即时降级），**不另造投递路径**；模板（到点时刻 / 四段开关 / 订阅渠道）注册进
[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)——时刻是标量、模板是对象（写面归本类）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l2.memory import SliceQuery
from st_agent.l5.channels import CHANNEL_KINDS, ChannelPayload, ChannelDispatch
from st_agent.l5.daily_report_store import (
    CONFIG_PREFIX,
    TEMPLATE_CONFIG_ID,
    TIME_CONFIG_ID,
    DailyReportStore,
)
from st_agent.l5.errors import DailyReportValidationError

__all__ = [
    "DEFAULT_CHANNELS",
    "DEFAULT_REPORT_TIME",
    "NO_CONTENT_NOTE",
    "SECTION_LABELS",
    "SECTION_NAMES",
    "DailyReport",
    "DailyReportBuilder",
    "ReportSection",
    "ReportTemplate",
    "ReportTrace",
]

SectionName = Literal["yesterday_review", "today_focus", "lens_digest", "memory_update"]

SECTION_NAMES: tuple[SectionName, ...] = (
    "yesterday_review", "today_focus", "lens_digest", "memory_update",
)
"""四段（07 §5 的顺序即此序）：昨日回顾 / 今日关注 / 多视角摘要 / Memory 更新摘要。"""

SECTION_LABELS: Mapping[str, str] = {
    "yesterday_review": "昨日回顾",
    "today_focus": "今日关注",
    "lens_digest": "多视角摘要",
    "memory_update": "Memory 更新摘要",
}

NO_CONTENT_NOTE = "今天没有需要打扰你的事"
"""四段皆空时的正文（story-07 空状态：不硬凑）。"""

DEFAULT_REPORT_TIME = "08:00"
"""缺省到点时刻（story-07 GWT「每天早晨 8 点」）。"""

DEFAULT_CHANNELS: tuple[str, ...] = ("desktop",)
"""缺省订阅渠道（最轻的一环；用户可配到任一渠道）。"""

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ReportSection(BaseModel):
    """报告的一段（段名 + 中性陈述行；`empty_note` 仅在无内容时给出）。"""

    model_config = ConfigDict(frozen=True)

    name: SectionName
    label: str
    lines: tuple[str, ...] = ()
    empty_note: str = ""

    @property
    def empty(self) -> bool:
        return not self.lines


class ReportTrace(BaseModel):
    """**完整 Trace** 的锚点（07 §5「生成过程走完整 Trace」；01 §4 / §1）。"""

    model_config = ConfigDict(frozen=True)

    signal_ids: tuple[str, ...] = ()
    delivery_ids: tuple[str, ...] = ()
    memory_node_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    """参与组装的留痕所关联的推理链（[01 §4](../../docs/技术架构-v2/01-平台共享契约.md)）。"""

    @property
    def empty(self) -> bool:
        return not (self.signal_ids or self.delivery_ids
                    or self.memory_node_ids or self.evidence_refs or self.trace_ids)


class ReportTemplate(BaseModel):
    """结构模板（07 §5）：启用哪几段 + 订阅到哪些渠道。"""

    model_config = ConfigDict(frozen=True)

    sections: tuple[SectionName, ...] = SECTION_NAMES
    channels: tuple[str, ...] = DEFAULT_CHANNELS


class DailyReport(BaseModel):
    """一份「给你的信」（四段 + 完整 Trace + 投出形态）。"""

    model_config = ConfigDict(frozen=True)

    day: date
    """报告所覆盖的**当日**（`<YYYY-MM-DD>` 即落盘键）。"""
    generated_at: datetime
    sections: tuple[ReportSection, ...]
    empty: bool
    """四段皆空（正文即 :data:`NO_CONTENT_NOTE`）。"""
    title: Annotated[str, Field(min_length=1)]
    body: Annotated[str, Field(min_length=1)]
    trace: ReportTrace
    anchor: str
    """投出载荷的来源锚点（本报告无 §1 信号，取「日期的确定性摘要」）。"""
    delivered_channel: str = ""
    """已投出的渠道（未投出为空——**不假装送达**）。"""


class DailyReportBuilder:
    """日报的组装 / 投出 / 模板门面（07 §5）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（组装照常，但不落盘）
    :param orchestrator: [`.2`](delivery.py) 的 `DeliveryOrchestrator`（鸭子面：
        `ledger` / `daily_report_queue` / `pending`）；缺省 ``None`` ⇒ 无留痕可读，
        当日关注与昨日回顾走**空态**
    :param reader: L2 `MemoryReader` 的鸭子面（只用到 `query(SliceQuery(...))`）；
        缺省 ``None`` ⇒ Memory 更新摘要走**空态**（不臆造）
    :param frequency: [`.2`](frequency.py) 的 `FrequencyController` 鸭子面（只用到
        `trigger_count(dedup_key)`）；缺省 ``None`` ⇒ 触发次数按 1（**不臆造合并**）
    :param dispatcher: [`.1`](channels.py) 的 `ChannelDispatcher`（订阅投出）
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        orchestrator: Any = None,
        reader: Any = None,
        frequency: Any = None,
        dispatcher: Any = None,
        guard: NeutralityGuard | None = None,
        default_time: str = DEFAULT_REPORT_TIME,
        default_template: ReportTemplate | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = DailyReportStore(store, now=now) if store is not None else None
        self._orchestrator = orchestrator
        self._reader = reader
        self._frequency = frequency
        self._dispatcher = dispatcher
        self._guard = guard if guard is not None else NeutralityGuard()
        self._default_time = _require_time(default_time)
        self._default_template = default_template if default_template is not None else ReportTemplate()
        self._now = _system_now if now is None else now
        self._time = self._default_time
        self._template = self._default_template

    # ───────────────────────── 到点判定 ─────────────────────────

    def due(self, now: datetime | None = None) -> bool:
        """此刻是否已到（或过了）用户配置的日报时刻——**纯比较**，不含「今天发过没发」。

        「一天一次」的去重归调用方（常驻循环 / 调度器目标）：本层保持纯函数口径
        （同 [`.2`](delivery.py) 的判定），便于离线复算与测试。
        """
        moment = self._now() if now is None else now
        return moment.timetz().replace(tzinfo=None) >= self.report_time()

    def report_time(self) -> time:
        """当前到点时刻（落盘条目优先，缺省回落内置）。"""
        raw = self._time if self._store is None else self._store.current(TIME_CONFIG_ID, self._time)
        try:
            return _require_time(raw)
        except DailyReportValidationError:
            return self._default_time

    # ───────────────────────── 组装 ─────────────────────────

    def build(self, day: date, *, now: datetime | None = None, persist: bool = True) -> DailyReport:
        """组装 ``day`` 那一天的报告（07 §5）。"""
        moment = self._now() if now is None else now
        template = self.template()
        ledger = tuple(_call(self._orchestrator, "ledger"))
        queue = tuple(_call(self._orchestrator, "daily_report_queue"))
        pending = tuple(_call(self._orchestrator, "pending"))
        anchors = {
            "signal_ids": [],
            "delivery_ids": [],
            "evidence_refs": [],
            "memory_node_ids": [],
            "trace_ids": [],
        }
        built: dict[SectionName, ReportSection] = {}
        for name in template.sections:
            built[name] = (
                self._yesterday(day, ledger, anchors) if name == "yesterday_review" else
                self._today(queue, pending, anchors) if name == "today_focus" else
                self._digest(queue, anchors) if name == "lens_digest" else
                self._memory(day, anchors)
            )
        sections = tuple(built[name] for name in template.sections)
        empty = all(section.empty for section in sections)
        title = f"{day.isoformat()} 给你的信"
        body = _compose(sections, empty)
        self._require_neutral(title, "日报标题")
        self._require_neutral(body, "日报正文")
        report = DailyReport(
            day=day, generated_at=moment, sections=sections, empty=empty,
            title=title, body=body,
            trace=ReportTrace(
                signal_ids=tuple(dict.fromkeys(anchors["signal_ids"])),
                delivery_ids=tuple(dict.fromkeys(anchors["delivery_ids"])),
                memory_node_ids=tuple(dict.fromkeys(anchors["memory_node_ids"])),
                evidence_refs=tuple(dict.fromkeys(anchors["evidence_refs"])),
                trace_ids=tuple(dict.fromkeys(anchors["trace_ids"])),
            ),
            anchor=digest_id("rep", day.isoformat()),
        )
        if persist and self._store is not None:
            self._store.put(day, report.model_dump(mode="json"))
        return report

    def deliver(self, report: DailyReport, *, now: datetime | None = None) -> DailyReport:
        """把报告订阅投出（经 [`.1`](channels.py) 的渠道面）；未接投出面 ⇒ 原样返回。"""
        if self._dispatcher is None:
            return report
        payload = ChannelPayload(
            signal_id=report.anchor, level="routine",
            title=report.title, body=report.body,
            trace_id=report.trace.trace_ids[0] if report.trace.trace_ids else report.anchor,
        )
        dispatch: ChannelDispatch = self._dispatcher.deliver(
            list(self.template().channels), payload,
        )
        channel = dispatch.delivered.channel if dispatch.delivered is not None else ""
        return report.model_copy(update={"delivered_channel": channel})

    # ───────────────────────── 模板与条目 ─────────────────────────

    def template(self) -> ReportTemplate:
        """当前模板（落盘条目优先，缺省回落内置）。"""
        if self._store is None:
            return self._template
        raw = self._store.current(TEMPLATE_CONFIG_ID, None)
        if not isinstance(raw, Mapping):
            return self._template
        try:
            return _template_from_payload(raw)
        except DailyReportValidationError:
            return self._template

    def set_time(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """设置到点时刻（`HH:MM`；越界即拒、不落盘不留痕）。"""
        target = _require_time(value)
        self._time = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(TIME_CONFIG_ID, target.strftime("%H:%M")),
            previous=self._store.current(TIME_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def set_template(
        self, template: ReportTemplate | Mapping[str, Any], *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """替换结构模板（四段开关 + 订阅渠道链）；越界即拒、不落盘不留痕。

        取值是对象 ⇒ 按 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) **不在统一标量
        落值面内**，写面即本方法（裁法同预算格位 / 渠道偏好链）。
        """
        target = _template_from_value(template)
        self._template = target
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
        """本层登记进 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的两个条目（含当前取值）。"""
        return (
            self._entry(TIME_CONFIG_ID, self.report_time().strftime("%H:%M")),
            self._entry(TEMPLATE_CONFIG_ID, _template_payload(self.template())),
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

    def _yesterday(self, day: date, ledger: Sequence[Any], anchors: dict) -> ReportSection:
        yesterday = day - timedelta(days=1)
        records = [r for r in ledger if _day_of(r) == yesterday]
        if not records:
            return ReportSection(
                name="yesterday_review", label=SECTION_LABELS["yesterday_review"],
                empty_note=f"{yesterday.isoformat()} 没有需要送到你面前的事",
            )
        separate = sum(1 for r in records if getattr(r, "route", "") == "separate")
        queued = len(records) - separate
        by_status = _count_by(records, "status")
        lines = [
            f"{yesterday.isoformat()} 共记录投递 {len(records)} 条"
            f"（单独触达 {separate} 条、汇总进日报 {queued} 条）",
            "其中已读 {read} 条、未确认 {unack} 条、未送达 {undelivered} 条".format(
                read=by_status.get("read", 0),
                unack=by_status.get("unacknowledged", 0),
                undelivered=by_status.get("unavailable", 0) + by_status.get("failed", 0),
            ),
        ]
        anchors["signal_ids"].extend(str(getattr(r, "signal_id", "")) for r in records)
        anchors["delivery_ids"].extend(str(getattr(r, "delivery_id", "")) for r in records)
        anchors["trace_ids"].extend(
            str(getattr(r, "trace_id", "")) for r in records if getattr(r, "trace_id", "")
        )
        return ReportSection(
            name="yesterday_review", label=SECTION_LABELS["yesterday_review"],
            lines=tuple(lines),
        )

    def _today(self, queue: Sequence[Any], pending: Sequence[Any], anchors: dict) -> ReportSection:
        """今日关注：待汇总项（按去重键合并 + 触发次数）与在途投递的头条。"""
        latest = _latest_by_dedup(queue)
        if not latest and not pending:
            return ReportSection(
                name="today_focus", label=SECTION_LABELS["today_focus"],
                empty_note="今天暂无需单独知会的事项",
            )
        lines: list[str] = []
        for record, key in latest:
            count = self._trigger_count(key)
            suffix = f"（同类触发 {count} 次）" if count > 1 else ""
            lines.append(f"{getattr(record, 'title', '')}{suffix}")
            anchors["signal_ids"].append(str(getattr(record, "signal_id", "")))
            anchors["delivery_ids"].append(str(getattr(record, "delivery_id", "")))
            anchors["evidence_refs"].extend(getattr(record, "evidence_refs", ()) or ())
            if getattr(record, "trace_id", ""):
                anchors["trace_ids"].append(str(record.trace_id))
        for record in pending:
            lines.append(f"在途：{getattr(record, 'title', '')}")
            anchors["delivery_ids"].append(str(getattr(record, "delivery_id", "")))
        return ReportSection(
            name="today_focus", label=SECTION_LABELS["today_focus"], lines=tuple(lines),
        )

    def _digest(self, queue: Sequence[Any], anchors: dict) -> ReportSection:
        """多视角摘要：逐待汇总项**原样**给出结论 + 逐视角摘要（分歧并列，不合并）。"""
        latest = _latest_by_dedup(queue)
        if not latest:
            return ReportSection(
                name="lens_digest", label=SECTION_LABELS["lens_digest"],
                empty_note="今天没有待汇总的多视角结论",
            )
        lines: list[str] = []
        for record, _key in latest:
            lines.append(str(getattr(record, "title", "")))
            lines.extend(_lines_of(getattr(record, "body", "")))
        return ReportSection(
            name="lens_digest", label=SECTION_LABELS["lens_digest"], lines=tuple(lines),
        )

    def _memory(self, day: date, anchors: dict) -> ReportSection:
        if self._reader is None:
            return ReportSection(
                name="memory_update", label=SECTION_LABELS["memory_update"],
                empty_note="未接入记忆读取面，本次不做 Memory 更新摘要",
            )
        try:
            result = self._reader.query(
                SliceQuery(task_type="reflection", topic="", view="timeline")
            )
        except Exception as exc:                       # 读取面实现缺陷 → 降级但仍显式说明
            return ReportSection(
                name="memory_update", label=SECTION_LABELS["memory_update"],
                empty_note=f"记忆读取面不可用（{exc}）",
            )
        slices = tuple(getattr(result, "slices", ()) or ())
        if not slices:
            return ReportSection(
                name="memory_update", label=SECTION_LABELS["memory_update"],
                empty_note="记忆尚无更新可用作摘要",
            )
        lines: list[str] = []
        for slice_ in slices:
            node = getattr(slice_, "node", None)
            node_id = str(getattr(node, "memory_node_id", ""))
            if node_id:
                anchors["memory_node_ids"].append(node_id)
            lines.append(
                f"{getattr(node, 'type', '')}：置信度 {_confidence_of(slice_):.2f}，"
                f"{_source_note(slice_)}，更新于 {_updated_of(node)}"
            )
        return ReportSection(
            name="memory_update", label=SECTION_LABELS["memory_update"], lines=tuple(lines),
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _trigger_count(self, dedup_key: str) -> int:
        if self._frequency is None or not dedup_key:
            return 1
        try:
            return int(self._frequency.trigger_count(dedup_key))
        except Exception:                              # 计数面缺陷 → 按 1（不臆造合并）
            return 1

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise DailyReportValidationError(
                f"{where} 未过 01 §6 中性化校验（{hits}）：模板须中性（铁律 2）"
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
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _call(target: Any, name: str) -> Sequence[Any]:
    """读面调用（未注入面 → 空集；不是错误，是「没有留痕」）。"""
    if target is None:
        return ()
    method = getattr(target, name, None)
    if method is None:
        return ()
    return tuple(method())


def _day_of(record: Any) -> date | None:
    created = getattr(record, "created_at", None)
    return created.date() if isinstance(created, datetime) else None


def _count_by(records: Sequence[Any], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in records:
        key = str(getattr(record, field, ""))
        out[key] = out.get(key, 0) + 1
    return out


def _latest_by_dedup(queue: Sequence[Any]) -> list[tuple[Any, str]]:
    """按 `dedup_key` 归并待汇总项（同一去重键只呈现**一条**；07 §6 的合并）。

    无 `dedup_key` 的留痕（缺省空串）各成一条——它们没有合并键，**不臆造**归并。
    """
    out: list[tuple[Any, str]] = []
    seen: dict[str, Any] = {}
    for record in queue:
        key = str(getattr(record, "dedup_key", "") or "")
        if not key:
            out.append((record, ""))
            continue
        if key in seen:
            continue
        seen[key] = record
        out.append((record, key))
    return out


def _lines_of(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    return [line for line in text.splitlines() if line.strip()]


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


def _compose(sections: Sequence[ReportSection], empty: bool) -> str:
    if empty:
        return NO_CONTENT_NOTE
    blocks: list[str] = []
    for section in sections:
        head = f"【{section.label}】"
        if section.empty:
            blocks.append(f"{head}{section.empty_note}")
            continue
        blocks.append("\n".join([head, *section.lines]))
    return "\n".join(blocks)


def _require_time(value: Any) -> time:
    """`HH:MM` 字符串 / `datetime.time` → :class:`datetime.time`（越界即拒，不猜）。"""
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        match = _TIME_RE.match(value.strip())
        if match:
            return time(int(match.group(1)), int(match.group(2)))
    raise DailyReportValidationError(f"日报时刻须为 HH:MM（24 小时制），收到 {value!r}")


def _template_from_value(value: Any) -> ReportTemplate:
    if isinstance(value, ReportTemplate):
        return value
    if not isinstance(value, Mapping):
        raise DailyReportValidationError(
            f"日报模板须为 ReportTemplate 或映射，收到 {type(value).__name__}"
        )
    sections = value.get("sections", SECTION_NAMES)
    channels = value.get("channels", DEFAULT_CHANNELS)
    return _validate_template(sections, channels)


def _template_from_payload(raw: Mapping[str, Any]) -> ReportTemplate:
    return _validate_template(raw.get("sections"), raw.get("channels"))


def _validate_template(sections: Any, channels: Any) -> ReportTemplate:
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        raise DailyReportValidationError(f"模板的 sections 须为列表，收到 {type(sections).__name__}")
    unknown = [name for name in sections if name not in SECTION_NAMES]
    if unknown:
        raise DailyReportValidationError(
            f"未知报告段 {unknown}；合法段 = {list(SECTION_NAMES)}（07 §5）"
        )
    if not sections:
        raise DailyReportValidationError("模板至少要启用一段（07 §5）")
    if len(set(sections)) != len(sections):
        raise DailyReportValidationError(f"同一段不得出现两次：{list(sections)}")
    if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)):
        raise DailyReportValidationError(f"模板的 channels 须为列表，收到 {type(channels).__name__}")
    bad = [c for c in channels if c not in CHANNEL_KINDS]
    if bad:
        raise DailyReportValidationError(
            f"未知订阅渠道 {bad}；合法渠道 = {list(CHANNEL_KINDS)}（07 §3）"
        )
    if not channels:
        raise DailyReportValidationError("模板至少要订阅一个渠道（07 §5「可订阅到任一渠道」）")
    if len(set(channels)) != len(channels):
        raise DailyReportValidationError(f"同一渠道不得订阅两次：{list(channels)}")
    ordered = tuple(name for name in SECTION_NAMES if name in set(sections))
    return ReportTemplate(sections=ordered, channels=tuple(channels))


def _template_payload(template: ReportTemplate) -> dict[str, Any]:
    return {"sections": list(template.sections), "channels": list(template.channels)}


# ───────────────────────── 01 §7 条目的登记形态 ─────────────────────────

_ENTRY_SPECS: dict[str, tuple[str, dict[str, Any], str, PanelField]] = {
    TIME_CONFIG_ID: (
        "日报生成时刻",
        {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"},
        "每天什么时候本地生成「给你的信」（24 小时制 HH:MM）",
        PanelField(
            widget="text", label="日报生成时刻", help_text="24 小时制 HH:MM，如 08:00",
        ),
    ),
    TEMPLATE_CONFIG_ID: (
        "日报结构模板",
        {"type": "object"},
        "日报包含哪几段（昨日回顾 / 今日关注 / 多视角摘要 / Memory 更新摘要），以及订阅到哪些渠道",
        PanelField(
            widget="matrix", label="日报结构模板",
            help_text="四段开关与订阅渠道链；写入走日报自有 API",
        ),
    ),
}
