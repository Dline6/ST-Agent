"""L5 去重合并与频控计数（[07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

两条规则：

- **去重合并**——同一 `dedup_key`（同一标的同一事件类型）在**去重窗口**（可配，默认 30 分钟）
  内多次触发 ⇒ **合并为一条**并附**触发次数**，不产生多次刷屏（呼应 anti-metric「不追求推送量」）。
  「合并」发生在**触达与日报呈现**上、**不发生在信号上**——信号已由
  [`.1`](signal.py) 采纳（`signal_id` 幂等），本层只是把同一 `dedup_key` 的多次触发收敛为
  一条呈现 + 计数。
- **全局频控**——超出 [07 §2](../../docs/技术架构-v2/07-L5-主动触达.md) 格位节奏上限
  `max_per_slot` 的信号**排队进下一次日报、不丢弃**（[`.2`](delivery.py) 的 `queue_signal`）。

三条口径：

- **计数是「显式时刻的纯函数推进」**——时钟由调用方按次传入（取向同
  [`.2`](delivery.py) 的 `dispatch` / `advance`），故同一 `(状态, now)` 恒得同一结论、
  可离线重复验证。
- **状态损坏即抛**（:class:`~st_agent.l5.errors.FrequencyValidationError`）——空表会被读成
  「没有超限」，正是静默失败（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）。
- **只增不改**——接线经**注入**（见 :meth:`FrequencyController.gate` 与
  [`runtime.build_l5`](runtime.py) 的可选参数）；**未注入时** [`.2`](delivery.py) 的
  `dispatch` 行为**逐字节不变**（先例＝[`ConflictQueue` 增可选 `events` 端口](../../l2/memory/conflict.py)）。

**「类」＝ `dedup_key`**——它是 [07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md) 的
「同一标的同一事件类型」合并键，也是信号面上唯一现成的分类标识（[07 §7](../../../docs/技术架构-v2/07-L5-主动触达.md)
的疲劳监控同用它，见 [`fatigue.py`](fatigue.py)）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l5.budget import DEFAULT_SLOTS, BudgetVerdict, TimeSlot
from st_agent.l5.errors import FrequencyValidationError
from st_agent.l5.frequency_store import (
    CONFIG_PREFIX,
    WINDOW_CONFIG_ID,
    FrequencyStore,
)
from st_agent.l5.signal import Signal

__all__ = [
    "DEFAULT_WINDOW_MINUTES",
    "RATE_KINDS",
    "DedupCount",
    "FrequencyController",
    "GateResult",
    "RateDecision",
]

RATE_KINDS: tuple[str, ...] = ("separate", "merge", "queue")
"""三类裁决：单独触达 / 合并（同键窗口内重复）/ 排队进日报。"""

DEFAULT_WINDOW_MINUTES = 30
"""去重窗口的缺省时长（分钟）——产品口径，可经条目 / 构造参数覆写。"""

RateKind = Literal["separate", "merge", "queue"]


class RateDecision(BaseModel):
    """一次频控裁决（**可解释**：给出类别、触发次数与中性理由）。"""

    model_config = ConfigDict(frozen=True)

    kind: RateKind
    """`separate` 单独触达 / `merge` 合并为一条 / `queue` 排队进下一次日报。"""
    trigger_count: int
    """该 `dedup_key` 在当前去重窗口内的**触发次数**（首次为 1）。"""
    reason: str
    """中性陈述式理由（可解释性落点）。"""
    window_start: datetime | None = None
    """本次所属去重窗口的起点（`merge` / 计数为主要语义时非空）。"""
    slot_key: str = ""
    """命中的格位键（`{情境}.{时段}.{内容类型}`；未判格位时为空）。"""


class DedupCount(BaseModel):
    """一个 `dedup_key` 的当前去重计数（日报面据此呈现「N 次触发」）。"""

    model_config = ConfigDict(frozen=True)

    dedup_key: str
    trigger_count: int
    window_start: datetime


class GateResult(BaseModel):
    """一次「过闸」的结果（裁决 + 实际落点）。"""

    model_config = ConfigDict(frozen=True)

    decision: RateDecision
    dispatch: Any = None
    """`separate` 时的 `DeliveryDispatch`。"""
    queued: Any = None
    """`queue` 时的 `DeliveryRecord`（待汇总项）。"""


class FrequencyController:
    """去重窗口与格位节奏的计数门面（07 §6）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（计数照常，但不落盘）
    :param slots: 时段集合（缺省 [`.2`](budget.py) 的 `DEFAULT_SLOTS`；用于算格位周期）
    :param window_minutes: 去重窗口的内置缺省（缺省 :data:`DEFAULT_WINDOW_MINUTES`）
    :param mutes: [`.3`](fatigue.py) 静音清单的鸭子面（只用到 `is_muted(dedup_key) -> bool`）；
        缺省 ``None`` ⇒ 无静音（行为逐字节不变）——用户答复「关闭」的类在此被**改判为汇总进日报**
        （关闭单独触达 ≠ 丢弃，07 §7）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        slots: Sequence[TimeSlot] | None = None,
        window_minutes: int | None = None,
        mutes: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = FrequencyStore(store, now=now) if store is not None else None
        self._slots = tuple(slots) if slots is not None else DEFAULT_SLOTS
        self._window = (
            DEFAULT_WINDOW_MINUTES if window_minutes is None else _require_minutes(window_minutes)
        )
        self._mutes = mutes
        self._now = _system_now if now is None else now
        self._dedup: dict[str, dict[str, Any]] = {}      # 纯内存态（无 Store 时）
        self._slot: dict[str, dict[str, Any]] = {}

    # ───────────────────────── 裁决 ─────────────────────────

    def admit(
        self,
        signal: Signal,
        verdict: BudgetVerdict | None = None,
        *,
        now: datetime | None = None,
    ) -> RateDecision:
        """裁决这条信号此刻该怎么走（07 §6）。

        :param verdict: [`.2`](budget.py) 的格位判定（缺省 ``None`` ⇒ 无 `max_per_slot`
            可比，按「不超限」的显式缺省放行并标注）
        """
        moment = self._now() if now is None else now
        window = timedelta(minutes=self.window_minutes())
        dedup = self._load_dedup()
        previous = dedup.get(signal.dedup_key)
        start = _as_moment(previous.get("window_start")) if previous else None
        if start is not None and moment - start <= window:
            count = int(previous.get("count", 1)) + 1
            window_start = start
            merged = True
        else:
            count = 1
            window_start = moment
            merged = False
        dedup[signal.dedup_key] = {"window_start": window_start.isoformat(), "count": count}
        self._save_dedup(dedup)

        if merged:
            return RateDecision(
                kind="merge", trigger_count=count, window_start=window_start,
                reason=(
                    f"同一去重键（{signal.dedup_key}）在 {self.window_minutes()} 分钟窗口内"
                    f"第 {count} 次触发 ⇒ 合并为一条（07 §6，避免刷屏）"
                ),
            )
        if verdict is not None and verdict.dispatch == "daily_report":
            return RateDecision(
                kind="queue", trigger_count=count, window_start=window_start,
                reason="按注意力预算汇总进日报，不单独触达（07 §4）",
            )
        if self._is_muted(signal.dedup_key):
            return RateDecision(
                kind="queue", trigger_count=count, window_start=window_start,
                reason=(
                    f"该类（{signal.dedup_key}）已被用户关闭单独触达（07 §7 静音清单）"
                    "⇒ 汇总进日报（不丢弃）"
                ),
            )
        if verdict is None:
            return RateDecision(
                kind="separate", trigger_count=count, window_start=window_start,
                reason="未注入注意力预算：按「只看级别、不判格位」的显式缺省放行",
            )
        slot_key = _slot_key(verdict)
        period_start = _period_start(self._slots, verdict.key.slot_id, moment)
        counters = self._load_slots()
        current = counters.get(slot_key)
        used = (
            int(current.get("count", 0))
            if current is not None and current.get("period_start") == period_start.isoformat()
            else 0
        )
        if used >= verdict.max_per_slot:
            counters[slot_key] = {"period_start": period_start.isoformat(), "count": used}
            self._save_slots(counters)
            return RateDecision(
                kind="queue", trigger_count=count, window_start=window_start, slot_key=slot_key,
                reason=(
                    f"格位「{slot_key}」本时段已单独触达 {used} 条、达节奏上限 "
                    f"{verdict.max_per_slot} ⇒ 排队进下一次日报（不丢弃，07 §6）"
                ),
            )
        counters[slot_key] = {"period_start": period_start.isoformat(), "count": used + 1}
        self._save_slots(counters)
        return RateDecision(
            kind="separate", trigger_count=count, window_start=window_start, slot_key=slot_key,
            reason=(
                f"格位「{slot_key}」本时段第 {used + 1} 条（上限 {verdict.max_per_slot}）⇒ 单独触达"
            ),
        )

    def gate(
        self,
        orchestrator: Any,
        signal: Signal,
        *,
        content_type: str = "brief",
        now: datetime | None = None,
    ) -> GateResult:
        """裁决**并落点**：`separate` 走 [`.2`](delivery.py) 的 `dispatch`、`merge` 不动、
        `queue` 走 `queue_signal`（**同一次调用完成判定与执行**，调用方不必自己分流）。

        :param orchestrator: [`.2`](delivery.py) 的 `DeliveryOrchestrator`（鸭子面：
            `verdict_for` / `dispatch` / `queue_signal`）
        """
        moment = self._now() if now is None else now
        verdict = orchestrator.verdict_for(signal, content_type=content_type, now=moment)
        decision = self.admit(signal, verdict, now=moment)
        if decision.kind == "separate":
            return GateResult(
                decision=decision,
                dispatch=orchestrator.dispatch(signal, content_type=content_type, now=moment),
            )
        if decision.kind == "queue":
            return GateResult(
                decision=decision, queued=orchestrator.queue_signal(signal, now=moment),
            )
        return GateResult(decision=decision)          # merge：不产生新的触达

    # ───────────────────────── 读面 ─────────────────────────

    def trigger_count(self, dedup_key: str) -> int:
        """某 `dedup_key` 在当前去重窗口内的触发次数（无记录 → 1，即「只触发过一次」）。"""
        entry = self._load_dedup().get(dedup_key)
        return 1 if entry is None else int(entry.get("count", 1))

    def counts(self) -> tuple[DedupCount, ...]:
        """全部去重计数（按 `dedup_key` 升序）——日报面据此呈现「N 次触发」。"""
        out: list[DedupCount] = []
        for key, entry in self._load_dedup().items():
            start = _as_moment(entry.get("window_start"))
            if start is None:
                continue
            out.append(DedupCount(
                dedup_key=key, trigger_count=int(entry.get("count", 1)), window_start=start,
            ))
        return tuple(sorted(out, key=lambda c: c.dedup_key))

    def slot_usage(self) -> Mapping[str, int]:
        """逐格位的当前时段计数（只读快照）。"""
        return {
            key: int(entry.get("count", 0)) for key, entry in self._load_slots().items()
        }

    # ───────────────────────── 01 §7 条目与写面 ─────────────────────────

    def window_minutes(self) -> int:
        """当前去重窗口时长（分钟）——落盘条目优先，缺省回落内置。"""
        if self._store is None:
            return self._window
        value = self._store.current(WINDOW_CONFIG_ID, self._window)
        return value if isinstance(value, int) and not isinstance(value, bool) else self._window

    def set_window_minutes(
        self, minutes: Any, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """设置去重窗口时长（越界即拒、不落盘不留痕）。"""
        target = _require_minutes(minutes)
        self._window = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(WINDOW_CONFIG_ID, target),
            previous=self._store.current(WINDOW_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本层登记进 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的条目（含当前取值）。"""
        return (self._entry(WINDOW_CONFIG_ID, self.window_minutes()),)

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

    # ───────────────────────── 内部 ─────────────────────────

    def _entry(self, config_id: str, default: Any) -> ConfigEntry:
        return ConfigEntry(
            config_id=config_id,
            display_name="去重窗口时长",
            value_schema={"type": "integer", "minimum": 1},
            default=default,
            description_for_chat=(
                "同一标的同一事件类型在多久内重复触发就合并成一条（分钟）"
            ),
            panel_form_spec=PanelField(
                widget="number", label="去重窗口时长（分钟）",
                help_text="窗口内同一去重键的多次触发合并为一条并附触发次数",
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )

    def _is_muted(self, dedup_key: str) -> bool:
        """该类是否被用户答复「关闭」（07 §7 的静音清单；实现缺陷 → 显式抛错，不静默放行）。"""
        if self._mutes is None or not dedup_key:
            return False
        try:
            return bool(self._mutes.is_muted(dedup_key))
        except Exception as exc:
            raise FrequencyValidationError(f"静音清单端口异常：{exc}") from exc

    def _load_dedup(self) -> dict[str, dict[str, Any]]:
        return dict(self._dedup) if self._store is None else self._store.load_dedup()
    def _save_dedup(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        if self._store is None:
            self._dedup = {k: dict(v) for k, v in state.items()}
            return
        self._store.save_dedup(state)

    def _load_slots(self) -> dict[str, dict[str, Any]]:
        return dict(self._slot) if self._store is None else self._store.load_slots()

    def _save_slots(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        if self._store is None:
            self._slot = {k: dict(v) for k, v in state.items()}
            return
        self._store.save_slots(state)


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _require_minutes(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FrequencyValidationError(f"去重窗口须为正整数分钟，收到 {value!r}")
    return value


def _as_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _slot_key(verdict: BudgetVerdict) -> str:
    key = verdict.key
    return f"{key.mode}.{key.slot_id}.{key.content_type}"


def _period_start(slots: Sequence[TimeSlot], slot_id: str, moment: datetime) -> datetime:
    """`moment` 所在的那一次格位（时段实例）的起点。

    时段是半开 `[start, end)`、**支持跨零点**（[07 §2](07-L5-主动触达.md)）；跨零点时段在
    零点前的那一刻属于**前一天**开始的那一次，故起点须往前推一天——否则同一段会被切成两次。
    """
    now = moment.timetz().replace(tzinfo=None)
    for slot in slots:
        if slot.slot_id != slot_id:
            continue
        day = moment.date()
        if slot.start > slot.end:                      # 跨零点
            if now < slot.start:
                day = day - timedelta(days=1)
        return _combine(day, slot.start, moment)
    # 未知时段 id（判定与时段表不同源）⇒ 退回「以调用时刻为界」，不猜别处口径
    return moment


def _combine(day: Any, at: time, moment: datetime) -> datetime:
    return datetime.combine(day, at, tzinfo=moment.tzinfo)
