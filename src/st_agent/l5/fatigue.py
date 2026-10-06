"""L5 推送疲劳监控与答复回流（[07 §7](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

每类信号（**类 ＝ `dedup_key`**，[§6](../../../docs/技术架构-v2/07-L5-主动触达.md) 的同一口径）维护**连续忽略计数**；
超阈值（可配，默认 5）⇒ 产生**询问信号**「这类推送对你有用吗？要不要减少？」。

五条口径：

- **「忽略」取自本层留痕、不是另一套记账**——链尽未读结算为 `unacknowledged`（[`.2`](delivery.py)）
  即一次忽略；**一次「已读」把该类的连续计数归零**（是**连续**计数，不是累计）。
- **询问按原文「询问信号」复用 [§1](signal.py) 的 `Signal`**（逐视角摘要承载 L5 的疲劳观察，
  `lens_id` **只校验形态、不解析 L4 阵容**）⇒ 经 [`.2`](delivery.py) 正常投出、
  得一枚**可寻址的 `delivery_id`**（[08 §1](../../docs/技术架构-v2/08-L6-反思演进.md) 的反馈对象三类之一）。
  路由仍由 [07 §2](07-L5-主动触达.md) 的注意力预算决定——缺省「少而准」下询问通常随**日报**到达
  （那是 `daily_report` 路由，**不是**没发）。
- **`feedback_id` 不由本层铸**（[01 §1](../../docs/技术架构-v2/01-平台共享契约.md) 登记产生方＝交互层 L3）：
  本层只产询问信号与计数；答复经交互层采集成 `FeedbackEvent`，本层经
  [01 §11](../../docs/技术架构-v2/01-平台共享契约.md) 的 `FeedbackRecorded` **订阅面** 消费
  （:meth:`FatigueMonitor.consume`）；:meth:`FatigueMonitor.answer` 是同一落点的**第二条通道**
  （无 UI 时的直接承接，两通道同源、留痕一致）。
- **三种答复三种落点**：「减少」经 [§3](../../../docs/技术架构-v2/07-L5-主动触达.md) 的**既有写面**收窄该级别的渠道链；
  「关闭」记入本层**静音清单**（[§6](../../../docs/技术架构-v2/07-L5-主动触达.md) 的频控面读它把该类改判为汇总进日报，
  **关闭单独触达 ≠ 丢弃**）；「保持」不改配置、只把连续计数归零并记下「已询问」。
- **状态损坏即抛**（[00 §6](../../docs/技术架构-v2/00-架构总览.md)），空表会被读成「没有疲劳」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import SignalId, digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l5.errors import FatigueValidationError
from st_agent.l5.fatigue_store import (
    CONFIG_PREFIX,
    THRESHOLD_CONFIG_ID,
    FatigueStore,
)
from st_agent.l5.signal import Signal, SignalContent, SignalLensStance

__all__ = [
    "ANSWERS",
    "DEFAULT_THRESHOLD",
    "FEEDBACK_EVENT",
    "MUTE_REASONS",
    "OBSERVER_LENS",
    "Answer",
    "AnswerResult",
    "FatigueMonitor",
    "FatigueState",
    "Inquiry",
]

FEEDBACK_EVENT = "FeedbackRecorded"
"""本面订阅的事件名（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)：**产生方＝交互层**，
L5 只消费、不自铸 `feedback_id`——[D-083](../../../项目管理/决策日志.md) ④）。"""

DEFAULT_THRESHOLD = 5
"""连续忽略阈值的缺省值（[07 §7](../../docs/技术架构-v2/07-L5-主动触达.md)：默认 5）。"""

Answer = Literal["reduce", "disable", "keep"]

ANSWERS: tuple[Answer, ...] = ("reduce", "disable", "keep")
"""三类答复：减少 / 关闭 / 保持（07 §7）。"""

MUTE_REASONS = "用户答复关闭该类单独触达（07 §7）"

OBSERVER_LENS = digest_id("lens", "l5:fatigue-observer")
"""询问信号的观察面标识（`lens` 形态合法；**不是 L4 阵容里的视角**，[`signal.py`](signal.py) 只校验形态）。"""

_INQUIRY_LEVELS = ("emergency", "important", "routine")


class FatigueState(BaseModel):
    """一个类（`dedup_key`）的当前疲劳状态（**可解释**：计数 + 阈值 + 最近答复 + 时间锚）。"""

    model_config = ConfigDict(frozen=True)

    dedup_key: str
    ignored_streak: int
    """**连续**忽略计数（中间一次「已读」即归零）。"""
    threshold: int
    muted: bool = False
    """是否已被用户答复「关闭」（静音清单）。"""
    last_answer: str | None = None
    answered_at: datetime | None = None
    asked_delivery_id: str = ""
    """已发问且**尚未答复**时非空（此类不再重复发问）。"""
    as_of: datetime | None = None


class Inquiry(BaseModel):
    """一次产生的询问（可解释：问的是哪个类、落在哪枚投递上）。"""

    model_config = ConfigDict(frozen=True)

    dedup_key: str
    signal_id: str
    delivery_id: str
    level: str
    ignored_streak: int
    question: str
    asked_at: datetime


class AnswerResult(BaseModel):
    """一次答复的落点（答复与改动一一对应）。"""

    model_config = ConfigDict(frozen=True)

    dedup_key: str
    delivery_id: str
    choice: Answer
    applied: str
    """中性说明本次答复**实际落到了哪里**（既有写面 / 静音清单 / 无改动）。"""


class FatigueMonitor:
    """疲劳监控门面（07 §7）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（监控照常，但不落盘）
    :param orchestrator: [`.2`](delivery.py) 的 `DeliveryOrchestrator`（鸭子面：
        `ledger` / `dispatch`）；缺省 ``None`` ⇒ 无留痕可读、也投不出询问
    :param policies: [`.1`](channel_policy.py) 的 `ChannelPolicies`（「减少」的既有写面）
    :param threshold: 连续忽略阈值的内置缺省（缺省 :data:`DEFAULT_THRESHOLD`）
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        orchestrator: Any = None,
        policies: Any = None,
        threshold: int | None = None,
        guard: NeutralityGuard | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = FatigueStore(store, now=now) if store is not None else None
        self._orchestrator = orchestrator
        self._policies = policies
        self._threshold = (
            DEFAULT_THRESHOLD if threshold is None else _require_threshold(threshold)
        )
        self._guard = guard if guard is not None else NeutralityGuard()
        self._now = _system_now if now is None else now
        self._state: dict[str, dict[str, Any]] = {}      # 纯内存态（无 Store 时）
        self._mutes: dict[str, dict[str, Any]] = {}

    # ───────────────────────── 计数 ─────────────────────────

    def counts(self) -> tuple[FatigueState, ...]:
        """逐类的当前疲劳状态（**连续忽略计数由留痕现算**，不另存一份漂移源）。"""
        streaks = _streaks(self._orchestrator)
        state = self._load_state()
        mutes = self._load_mutes()
        keys = sorted(set(streaks) | set(state) | set(mutes))
        out: list[FatigueState] = []
        for key in keys:
            if not key:
                continue
            entry = state.get(key, {})
            streak, last = streaks.get(key, (0, None))
            out.append(FatigueState(
                dedup_key=key, ignored_streak=streak, threshold=self.threshold(),
                muted=key in mutes,
                last_answer=entry.get("last_answer"),
                answered_at=_as_moment(entry.get("answered_at")),
                asked_delivery_id=str(entry.get("asked_delivery_id", "")),
                as_of=getattr(last, "created_at", None),
            ))
        return tuple(out)

    def is_muted(self, dedup_key: str) -> bool:
        """该类是否已被用户答复「关闭」——[`.2`](frequency.py) 频控面读的**鸭子端口**。"""
        return dedup_key in self._load_mutes()

    # ───────────────────────── 发问 ─────────────────────────

    def sweep(self, now: datetime | None = None) -> tuple[Inquiry, ...]:
        """推进监控：达阈值且**未在等待答复**的类产生询问信号并投出（07 §7）。

        :return: 本次**新产生**的询问（未过阈值 / 已发问未答复 / 已静音 / 未接编排面 → 不发）
        """
        moment = self._now() if now is None else now
        if self._orchestrator is None:
            return ()
        streaks = _streaks(self._orchestrator)
        state = self._load_state()
        mutes = self._load_mutes()
        threshold = self.threshold()
        produced: list[Inquiry] = []
        for key in sorted(streaks):
            if not key or key in mutes:
                continue
            streak, last = streaks[key]
            if streak < threshold:
                continue
            entry = state.get(key, {})
            if str(entry.get("asked_delivery_id", "")):
                continue                               # 已发问、等答复 ⇒ 不重复发
            asked_at = _asked_streak(entry)
            if streak < asked_at + threshold:
                continue                               # 冷却期：再攒满一轮阈值才复问
            inquiry = self._ask(key, streak, last, moment)
            if inquiry is None:
                continue
            entry.update({
                "asked_delivery_id": inquiry.delivery_id,
                "asked_at": moment.isoformat(),
                "level": inquiry.level,
                "ignored_streak": streak,
                "asked_streak": streak,
                "threshold": threshold,
            })
            state[key] = entry
            produced.append(inquiry)
        self._save_state(state)
        return tuple(produced)

    def _ask(self, key: str, streak: int, last: Any, moment: datetime) -> Inquiry | None:
        """构造并投出询问信号（文案过 01 §6；无编排面 ⇒ ``None``）。"""
        level = str(getattr(last, "level", "") or "routine")
        if level not in _INQUIRY_LEVELS:
            level = "routine"
        question = (
            f"「{key}」这类推送已连续 {streak} 次未被确认，是否减少这类推送？"
            f"（可减少 / 关闭 / 保持）"
        )
        summary = f"该类连续忽略计数为 {streak}，达到阈值 {self.threshold()}"
        self._require_neutral(question, "询问文案")
        self._require_neutral(summary, "询问的观察摘要")
        signal = Signal(
            signal_id=SignalId.of(digest_id("sig", "l5:fatigue", key, str(streak), moment.isoformat())).value,
            level=level,  # type: ignore[arg-type]
            content_ref=SignalContent(
                conclusion=question,
                lens_stances=(SignalLensStance(
                    lens_id=OBSERVER_LENS, stance="neutral", summary=summary,
                ),),
            ),
            evidence_refs=tuple(getattr(last, "evidence_refs", ()) or ()),
            dedup_key=f"l5:fatigue:{key}",
            source_trace_id=str(getattr(last, "trace_id", "")),
        )
        dispatch = self._orchestrator.dispatch(signal, content_type="interactive", now=moment)
        record = getattr(dispatch, "record", None)
        delivery_id = str(getattr(record, "delivery_id", ""))
        if not delivery_id:
            return None
        return Inquiry(
            dedup_key=key, signal_id=signal.signal_id, delivery_id=delivery_id,
            level=level, ignored_streak=streak, question=question, asked_at=moment,
        )

    # ───────────────────────── 答复 ─────────────────────────

    def answer(
        self, delivery_id: str, choice: str, *, now: datetime | None = None
    ) -> AnswerResult:
        """把用户对某次询问的答复落到该落的面上（07 §7）。

        :raises FatigueValidationError: 无此询问 / 答复取值非法 / 该询问已答复
        """
        moment = self._now() if now is None else now
        if choice not in ANSWERS:
            raise FatigueValidationError(
                f"答复须为 {' / '.join(ANSWERS)} 之一，收到 {choice!r}"
            )
        state = self._load_state()
        key = next(
            (k for k, v in state.items() if str(v.get("asked_delivery_id", "")) == delivery_id),
            None,
        )
        if key is None:
            raise FatigueValidationError(f"无待答复的询问（delivery_id={delivery_id!r}）")
        entry = state[key]
        applied = self._apply(key, choice, entry, moment)
        entry.update({
            "last_answer": choice, "answered_at": moment.isoformat(),
            "asked_delivery_id": "",                   # 释放：允许后续再发问
        })
        state[key] = entry
        self._save_state(state)
        return AnswerResult(
            dedup_key=key, delivery_id=delivery_id, choice=choice, applied=applied,
        )

    def _apply(self, key: str, choice: str, entry: Mapping[str, Any], moment: datetime) -> str:
        """答复的落点（三种答复三种面，见模块 docstring）。"""
        if choice == "disable":
            mutes = self._load_mutes()
            mutes[key] = {"muted_at": moment.isoformat(), "reason": MUTE_REASONS}
            self._save_mutes(mutes)
            return "已记入静音清单：该类不再单独触达，改汇总进日报（不丢弃）"
        if choice == "keep":
            return "保持原状：未改任何配置，连续计数已归零"
        level = str(entry.get("level") or "routine")
        if self._policies is None:
            return "保持原状：未接入渠道偏好写面，「减少」无处落值（显式标注，不静默）"
        policy = self._policies.get(level)
        chain = tuple(policy.channels)
        if len(chain) <= 1:
            return (
                f"保持原状：该级别（{level}）的渠道链只剩 {len(chain)} 级，无可再收窄"
                "（显式标注，不静默）"
            )
        self._policies.set_policy(level, chain[:-1], waits_minutes=policy.waits_minutes[:-1])
        return f"已收窄渠道偏好（{level}）：{' → '.join(chain[:-1])}（01 §7 留痕、可回滚）"

    def consume(self, event: Any) -> AnswerResult | None:
        """消费一条 `FeedbackRecorded`（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)）。

        只接**指向询问推送**（`target.kind="delivery"`）且携 `context.fatigue_choice` 的反馈；
        其余（别的推送 / 结论 / 建议的反馈）**不属本层**，原样返回 ``None``。
        """
        if getattr(event, "event", "") != FEEDBACK_EVENT:
            return None
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            return None
        target = payload.get("target")
        if not isinstance(target, Mapping) or target.get("kind") != "delivery":
            return None
        context = payload.get("context")
        choice = context.get("fatigue_choice") if isinstance(context, Mapping) else None
        if choice not in ANSWERS:
            return None
        delivery_id = str(target.get("ref", ""))
        state = self._load_state()
        if not any(
            str(v.get("asked_delivery_id", "")) == delivery_id for v in state.values()
        ):
            return None                                # 不是本层的询问 ⇒ 不属本层
        return self.answer(delivery_id, choice)

    # ───────────────────────── 01 §7 条目与写面 ─────────────────────────

    def threshold(self) -> int:
        """当前连续忽略阈值——落盘条目优先，缺省回落内置。"""
        if self._store is None:
            return self._threshold
        value = self._store.current(THRESHOLD_CONFIG_ID, self._threshold)
        return value if isinstance(value, int) and not isinstance(value, bool) else self._threshold

    def set_threshold(
        self, value: Any, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """设置连续忽略阈值（正整数；越界即拒、不落盘不留痕）。"""
        target = _require_threshold(value)
        self._threshold = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(THRESHOLD_CONFIG_ID, target),
            previous=self._store.current(THRESHOLD_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本层登记进 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的条目（含当前取值）。"""
        return (self._entry(THRESHOLD_CONFIG_ID, self.threshold()),)

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

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise FatigueValidationError(
                f"{where} 未过 01 §6 中性化校验（{hits}）：询问文案须中性（铁律 2）"
            )

    def _entry(self, config_id: str, default: Any) -> ConfigEntry:
        return ConfigEntry(
            config_id=config_id,
            display_name="推送疲劳阈值",
            value_schema={"type": "integer", "minimum": 1},
            default=default,
            description_for_chat="某类推送连续忽略多少次后，主动问一句要不要减少这类推送",
            panel_form_spec=PanelField(
                widget="number", label="推送疲劳阈值（连续忽略次数）",
                help_text="达到该次数即产生询问信号；用户可减少 / 关闭 / 保持",
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )

    def _load_state(self) -> dict[str, dict[str, Any]]:
        return dict(self._state) if self._store is None else self._store.load_state()

    def _save_state(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        if self._store is None:
            self._state = {k: dict(v) for k, v in state.items()}
            return
        self._store.save_state(state)

    def _load_mutes(self) -> dict[str, dict[str, Any]]:
        return dict(self._mutes) if self._store is None else self._store.load_mutes()

    def _save_mutes(self, mutes: Mapping[str, Mapping[str, Any]]) -> None:
        if self._store is None:
            self._mutes = {k: dict(v) for k, v in mutes.items()}
            return
        self._store.save_mutes(mutes)


# ───────────────────────── 由留痕现算（不另存一份漂移源） ─────────────────────────


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _streaks(orchestrator: Any) -> dict[str, tuple[int, Any]]:
    """逐类的**连续忽略计数**与其最后一条被忽略的留痕。

    每条信号取其**最高级次**的那条留痕（升级链的终态在那一级）：

    - `unacknowledged`（链尽未读）⇒ 一次**忽略**，计数 +1；
    - `read` ⇒ **打断**（连续计数到此为止）；
    - `delivered`（在途，尚无结论）⇒ 跳过（既不计数也不打断）；
    - `unavailable` / `failed`（没送出去）⇒ 跳过——**送不到不是「用户忽略」**。
    """
    if orchestrator is None:
        return {}
    by_signal: dict[str, Any] = {}
    for record in tuple(orchestrator.ledger()):
        if getattr(record, "route", "") != "separate":
            continue
        current = by_signal.get(record.signal_id)
        if current is None or record.step > current.step:
            by_signal[record.signal_id] = record
    grouped: dict[str, list[Any]] = {}
    for record in by_signal.values():
        key = str(getattr(record, "dedup_key", "") or "")
        grouped.setdefault(key, []).append(record)
    out: dict[str, tuple[int, Any]] = {}
    for key, records in grouped.items():
        records.sort(key=lambda r: r.created_at)
        streak = 0
        last: Any = None
        for record in reversed(records):
            status = str(getattr(record, "status", ""))
            if status == "read":
                break
            if status == "unacknowledged":
                streak += 1
                if last is None:
                    last = record
        out[key] = (streak, last)
    return out


def _asked_streak(entry: Mapping[str, Any]) -> int:
    """上一次发问时的连续忽略计数（复问的**冷却基线**；无记录 → 0）。

    没有它，用户答复「保持」之后计数没变，下一次 :meth:`FatigueMonitor.sweep` 会**立刻再问**
    ——那是骚扰，不是「少而准」。故复问要求**再攒满一轮阈值**（`streak ≥ 基线 + 阈值`）。
    计数被「已读」打断时从头计起，故用户越是回应、复问越少（保守：宁少勿扰）。
    """
    value = entry.get("asked_streak")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _require_threshold(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FatigueValidationError(f"疲劳阈值须为正整数，收到 {value!r}")
    return value


def _as_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None

