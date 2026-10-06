"""L6 反思数据池（[08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

交互层（L3）采集的 ``FeedbackEvent`` 经 [01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)
的 ``FeedbackRecorded`` 事件汇到本池，落 ``reflection`` 分区，**本机生成、不外发**。

五条口径：

- **池子只消费、不自铸 ID**——``feedback_id`` 由交互层铸造（[01 §1](../../../docs/技术架构-v2/01-平台共享契约.md)
  产生方登记），本层只从事件负载读出；[L3 的采集面](../../src/st_agent/l3/feedback/collector.py)
  零落盘，池子是**唯一**的反馈存储（其 docstring 明写归属 L6）。
- **六字段模型复用 L3 的** :class:`FeedbackEvent`（08 §1 的六字段即契约本体）——
  不另造平行模型，否则 08 §1 的每次修订都要改两处；L6 在 L3 之上，向下 import 合法
  （[铁律 7](../../../项目管理/工程宪法.md)；先例＝[`l5/daily_report.py`](../../src/st_agent/l5/daily_report.py)
  向下复用 [`l2.SliceQuery`](../../src/st_agent/l2/memory/reader.py)）。
- **键＝ `feedback_id`、幂等覆盖**——一条用户反馈的唯一标识，重复到达是同一事实的**重放**
  而非新增，故计数不随重放漂移。
- **负载不合约即拒收 + 记因**（[00 §6](../../../docs/技术架构-v2/00-架构总览.md)）：
  :meth:`FeedbackPool.consume` **抛** :class:`FeedbackPoolError`——这正是
  [01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的投递口径（订阅者抛错**不吞、不阻断
  其余订阅者**，逐条记进 ``DispatchResult``），故「拒绝」在总线上**显式可见**。
- **损坏即抛**——池内不可解析的文件绝不被读成「没有反馈」（那会被周报读成
  「本周无反馈」，正是静默失败）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.l3.feedback import ACTIONS, FeedbackEvent, FeedbackTarget
from st_agent.l6.errors import FeedbackPoolError
from st_agent.l6.pool_store import FeedbackPoolStore

__all__ = [
    "FEEDBACK_EVENT",
    "SOURCE",
    "FeedbackEntry",
    "FeedbackPool",
    "PoolCounts",
]

FEEDBACK_EVENT = "FeedbackRecorded"
"""本面消费的事件名（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)：**产生方＝交互层**）。"""

SOURCE = "l3:FeedbackRecorded"
"""池内留痕的来源标注（反馈一律经 [05 §9](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的交互层入口汇入）。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _first_message(exc: ValidationError) -> str:
    """取校验错的首条人类可读消息（去掉 pydantic 的 ``Value error,`` 包装前缀）。"""
    errors = exc.errors()
    if not errors:
        return str(exc)
    msg = str(errors[0].get("msg") or "").strip()
    return msg.removeprefix("Value error, ").strip() or str(exc)


class FeedbackEntry(BaseModel):
    """池内一条反馈留痕（六字段事件 + 落池时刻 + 来源）。"""

    model_config = ConfigDict(frozen=True)

    event: FeedbackEvent
    recorded_at: datetime
    """**落池**时刻（带时区）——与 `event.timestamp`（反馈**发生**时刻）是两件事。"""
    source: str = SOURCE

    @model_validator(mode="after")
    def _shape(self) -> "FeedbackEntry":
        if self.recorded_at.tzinfo is None:
            raise ValueError("落池时间必须带时区语义（01 §8）")
        return self


class PoolCounts(BaseModel):
    """池内统计（周报 ① / ③ 的取材面）。"""

    model_config = ConfigDict(frozen=True)

    total: int
    by_action: Mapping[str, int]
    """逐动作计数（五类动作，缺者为 0——**计数完备**，调用方不必自补缺省）。"""


class FeedbackPool:
    """反思数据池门面（[08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（池子照常可读，但不落盘）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, *, store: Any = None, now: Callable[[], datetime] | None = None) -> None:
        self._store = FeedbackPoolStore(store, now=now) if store is not None else None
        self._now = _system_now if now is None else now
        self._memory: dict[str, dict[str, Any]] = {}     # 纯内存态（无 Store 时）

    # ───────────────────────── 消费 ─────────────────────────

    def consume(self, event: Any) -> FeedbackEntry | None:
        """消费一条 ``FeedbackRecorded``（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)）。

        :return: 落池后的留痕；**不属本面**（事件名不符 / 负载非映射）→ ``None``
        :raises FeedbackPoolError: 负载不合 [08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md)
            六字段契约——**拒收并记因，不落盘**（01 §11 的失败显式化口径）
        """
        if getattr(event, "event", "") != FEEDBACK_EVENT:
            return None
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            return None
        moment = self._now()
        try:
            entry = FeedbackEntry(
                event=_to_event(payload, occurred_at=getattr(event, "occurred_at", None)),
                recorded_at=moment,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FeedbackPoolError(f"反馈负载不合 08 §1 契约，已拒收：{exc}") from exc
        self._write(entry)
        return entry

    # ───────────────────────── 读面 ─────────────────────────

    def get(self, feedback_id: str) -> FeedbackEntry | None:
        """按 `feedback_id` 取一条反馈（不存在 → ``None``；损坏 → 校验错）。"""
        raw = self._store.get(feedback_id) if self._store is not None else self._memory.get(feedback_id)
        return None if raw is None else _entry_of(raw)

    def all(self) -> tuple[FeedbackEntry, ...]:
        """池内全部反馈（按**发生时刻**升序；同时刻按 `feedback_id`）。"""
        raws = self._store.all() if self._store is not None else tuple(self._memory.values())
        return _ordered(_entry_of(raw) for raw in raws)

    def between(self, start: datetime, end: datetime) -> tuple[FeedbackEntry, ...]:
        """取 ``[start, end]`` 窗口内的反馈（按发生时刻升序；无则**空集**）。"""
        _require_range(start, end)
        return tuple(
            entry for entry in self.all()
            if start <= entry.event.timestamp <= end
        )

    def by_action(self, action: str) -> tuple[FeedbackEntry, ...]:
        """取某一动作的反馈（五类之一；未知动作 → **空集**，不抛——它是筛选而非校验）。"""
        return tuple(entry for entry in self.all() if entry.event.action == action)

    def counts(self, *, between: tuple[datetime, datetime] | None = None) -> PoolCounts:
        """逐动作计数（可选窗口）；**五类动作全部给值**（缺者为 0，不留给调用方补）。"""
        entries = self.all() if between is None else self.between(*between)
        tally = {action: 0 for action in ACTIONS}
        for entry in entries:
            tally[entry.event.action] = tally.get(entry.event.action, 0) + 1
        return PoolCounts(total=len(entries), by_action=dict(tally))

    # ───────────────────────── 内部 ─────────────────────────

    def _write(self, entry: FeedbackEntry) -> None:
        payload = entry.model_dump(mode="json")
        feedback_id = entry.event.feedback_id
        if self._store is None:
            self._memory[feedback_id] = payload
            return
        self._store.put(feedback_id, payload)


def _to_event(payload: Mapping[str, Any], *, occurred_at: Any) -> FeedbackEvent:
    """事件负载 → [08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md) 六字段事件。

    `timestamp` 取事件信封的 ``occurred_at``（[L3 的 ``to_event``](../../src/st_agent/l3/feedback/collector.py)
    把它设为反馈发生时刻，负载内不另带时间）。
    """
    target = payload.get("target")
    return FeedbackEvent(
        feedback_id=_text(payload.get("feedback_id"), "feedback_id"),
        target=FeedbackTarget(**dict(target)) if isinstance(target, Mapping) else target,  # type: ignore[arg-type]
        action=payload.get("action"),  # type: ignore[arg-type]
        reason=payload.get("reason"),  # type: ignore[arg-type]
        timestamp=occurred_at,
        context=dict(payload.get("context") or {}),
    )


def _text(value: Any, field: str) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise FeedbackPoolError(f"反馈负载缺 {field}（08 §1 六字段）")
    return value


def _entry_of(raw: Mapping[str, Any]) -> FeedbackEntry:
    raw_event = raw.get("event")
    where = raw_event.get("feedback_id", "?") if isinstance(raw_event, Mapping) else "?"
    try:
        return FeedbackEntry(**raw)
    except ValidationError as exc:
        raise FeedbackPoolError(f"池内反馈形态损坏（{where}）：{_first_message(exc)}") from exc


def _ordered(entries: Any) -> tuple[FeedbackEntry, ...]:
    return tuple(sorted(entries, key=lambda e: (e.event.timestamp, e.event.feedback_id)))


def _require_range(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise FeedbackPoolError("时间窗的两端必须带时区语义（01 §8）")
    if end < start:
        raise FeedbackPoolError(f"时间窗的终点早于起点：{start.isoformat()} → {end.isoformat()}")
