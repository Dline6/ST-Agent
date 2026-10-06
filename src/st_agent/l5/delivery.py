"""L5 投递编排与升级链（[07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

一次触达的完整走法：

1. **判定**（:meth:`DeliveryOrchestrator.dispatch`）——按 [`.2`](budget.py) 的
   `BudgetVerdict.dispatch` 分流：`daily_report` ⇒ 只记一条 **待汇总项**
   （**不调任何渠道**，日报本体归 [T-L5-003](../../../项目管理/tasks/T-L5-003-每日报告去重频控推送疲劳监控.md)）；
   `separate` ⇒ 按该级别的渠道链从**首级**投递。
2. **投递**（[`.1`](channels.py) 的 `ChannelDispatcher`）——同一级内**即时降级**
   （渠道坏了换链上的下一个）；每级写一条 `delivery_id` 留痕。
3. **推进**（:meth:`DeliveryOrchestrator.advance`）——未读满该级的等待时长即**升级**到下一级；
   链尽仍未读 ⇒ 结算「未确认」并**转入日报汇总**（不丢弃、不静默）。
4. **结算**（:meth:`DeliveryOrchestrator.mark_read`）——交互面报「已读」（反馈对象取
   `delivery_id`，[05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md)）即停链。
5. **补发**（:meth:`DeliveryOrchestrator.resend`）——离线期被拦下的云端投递，
   恢复联网后按**用户开关**重发（销 [L0 册 `A5`](../../../项目管理/遗留问题/L0-遗留问题.md)）。

四条口径：

- **留痕自足**——每条记录连**推送文案**一起落盘，故 :meth:`~DeliveryOrchestrator.advance` 与
  :meth:`~DeliveryOrchestrator.resend` **只凭留痕即可推进**（不依赖进程内的信号上下文），
  待汇总项也可被日报面直接读出（T-L5-003）。
- **判定与推进都是「显式时刻的纯函数」**——时钟由调用方按次传入（取向同
  [L1 调度器](../../l1/scheduler/scheduler.py) 的 `due(now)` / `catch_up`）；
  同一 `(留痕状态, now)` 恒得同一结论，故升级链可离线重复验证。
- **`delivery_id` 是确定性摘要**（[01 §1](../../docs/技术架构-v2/01-平台共享契约.md)）——
  同一 `(信号, 渠道, 级次)` 重放 / 补发得**同一 ID** 且**写同一路径**，链可安全重入。
- **补发依据是**本层留痕**、不是 L0 出网审计**——审计可选、默认关（[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)），
  默认关时 `pending_reconnect()` 按设计走显式不可用；故补发清单取自
  :meth:`DeliveryOrchestrator.pending_reconnect`。

**事件**：投递**结算**产生 `DeliverySettled`（含未读超时语义，[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)），
经鸭子类型端口 ``publish(event) -> bool`` 发出；事件总线本体归 [`.3`](runtime.py)。
事件面未接入时**不假装送达**——发布恒不受理，事件仍如实记进 :meth:`DeliveryOrchestrator.settlements`，
「没发出去」由发布端自己点名归属（`UndeliveredPublisher`）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.time_events import PlatformEvent
from st_agent.l5.budget import AttentionBudget, BudgetVerdict
from st_agent.l5.channel_policy import ChannelPolicies, ChannelPolicy
from st_agent.l5.channels import ChannelDispatcher, ChannelPayload, ChannelResult
from st_agent.l5.delivery_store import (
    DAILY_REPORT_CHANNEL,
    DeliveryRecord,
    DeliveryStatus,
    DeliveryStore,
    delivery_digest_key,
    delivery_id_for,
)
from st_agent.l5.errors import DeliveryValidationError
from st_agent.l5.personalize import Personalizer
from st_agent.l5.signal import Signal

__all__ = [
    "DAILY_REPORT_CHANNEL",
    "DELIVERY_EVENT",
    "EVENT_OWNER",
    "DeliveryDispatch",
    "DeliveryOrchestrator",
    "DeliveryRecord",
    "delivery_digest_key",
    "delivery_id_for",
]

DELIVERY_EVENT = "DeliverySettled"
"""本层发布的事件名（01 §11）。"""

EVENT_OWNER = "L5 进程内事件总线（`T-L5-002.3` 交付）"
"""事件面缺位时的归属点名（`build_l5` 用它构造 `UndeliveredPublisher`）。"""

_OPEN_STATUSES: tuple[str, ...] = ("delivered",)
_TERMINAL_STATUSES: tuple[str, ...] = ("unavailable", "failed")


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class DeliveryDispatch(BaseModel):
    """一次 `dispatch` 的产出（判定 + 留痕 + 载荷，全部可解释）。"""

    model_config = ConfigDict(frozen=True)

    record: DeliveryRecord
    verdict: BudgetVerdict | None = None
    """格位判定（未注入预算时为 ``None``——见 :attr:`budget_degraded`）。"""
    payload: ChannelPayload | None = None
    """本次投出的载荷（`daily_report` 路由时为 ``None``——它不调渠道）。"""
    attempts: tuple[ChannelResult, ...] = ()
    """本级逐渠道的尝试结果（即时降级的留痕）。"""
    wording: str = ""
    """选定的措辞档（07 §4 个性化的落点，可解释）。"""
    budget_degraded: bool = False
    """未注入预算 ⇒ 按「只看级别、不判格位」的显式缺省。"""


class DeliveryOrchestrator:
    """投递编排与升级链（07 §4）。

    :param dispatcher: [`.1`](channels.py) 的渠道派发器（按链序投递 + 即时降级）
    :param policies: [`.1`](channel_policy.py) 的渠道偏好（有序链 + 每级未读等待）
    :param budget: 注意力预算（缺省 ``None`` ⇒ 显式降级为「只看级别」）
    :param personalizer: 文案个性化（缺省 :class:`Personalizer`，无记忆即中性缺省）
    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（判定照常，但不落盘）
    :param events: 事件发布端口（鸭子类型 `publish(event) -> bool`）；缺省 ``None``
        ⇒ **不假装送达**（结算记录里点名归属）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        dispatcher: ChannelDispatcher,
        policies: ChannelPolicies,
        budget: AttentionBudget | None = None,
        personalizer: Personalizer | None = None,
        store: Any = None,
        events: Any = None,
        now: Any = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._policies = policies
        self._budget = budget
        self._personalizer = personalizer if personalizer is not None else Personalizer()
        self._store = DeliveryStore(store) if store is not None else None
        self._events = events
        self._now = _system_now if now is None else now
        self._memory: dict[str, DeliveryRecord] = {}
        self._settlements: list[PlatformEvent] = []

    # ───────────────────────── ① 判定与首级投递 ─────────────────────────

    def dispatch(
        self,
        signal: Signal,
        *,
        content_type: str = "brief",
        now: datetime | None = None,
    ) -> DeliveryDispatch:
        """判定 + 按链首级投递（07 §4）。

        :param content_type: 内容类型轴（07 §2 格位三维之一）；信号的发布方不携带它，
            故按调用方给的提示判定，缺省 `brief`（短提示——最保守的一档）
        :param now: 本次的时刻（缺省取本机当前；时钟按次传入，判定可离线复现）
        """
        moment = self._now() if now is None else now
        verdict = self._resolve(signal, content_type, moment)
        if verdict is not None and verdict.dispatch == "daily_report":
            return DeliveryDispatch(
                record=self._queue(signal, step=0, moment=moment), verdict=verdict,
            )
        # 未注入预算 ⇒ 按「只看级别、不判格位」的显式缺省：不擅自把信号压进日报
        policy = self._policies.get(signal.level)
        result = self._deliver_step(
            signal, policy, step=0, verdict=verdict, moment=moment,
        )
        return result.model_copy(update={"budget_degraded": verdict is None})

    def queue_signal(
        self, signal: Signal, *, step: int = 0, now: datetime | None = None,
    ) -> DeliveryRecord:
        """把一条信号**强制汇总进日报**（不调任何渠道）——07 §6 频控排队的落点。

        [T-L5-003.2](../../../项目管理/tasks/T-L5-003.2-去重合并与频控计数.md) 的频控面在
        「格位节奏已满」时用它把本该单独触达的信号**排队进下一次日报**（**不丢弃**）；
        写出的待汇总项与 :meth:`dispatch` 的 `routine` 路由**同一形态**，故日报面只认一处。
        """
        moment = self._now() if now is None else now
        return self._queue(signal, step=step, moment=moment)

    def verdict_for(
        self, signal: Signal, *, content_type: str = "brief", now: datetime | None = None,
    ) -> BudgetVerdict | None:
        """取某条信号此刻的格位判定（未注入预算 → ``None``）——**同一份判定**的公开读面。

        [T-L5-003.2](../../../项目管理/tasks/T-L5-003.2-去重合并与频控计数.md) 的频控面据此
        拿 `max_per_slot` / `dispatch`，**不自行另算一遍**（同一口径只有一处推导）。
        """
        moment = self._now() if now is None else now
        return self._resolve(signal, content_type, moment)

    # ───────────────────────── ② 未读超时升级 ─────────────────────────

    def advance(self, *, now: datetime | None = None) -> tuple[DeliveryRecord, ...]:
        """推进所有**开着的**投递：未读满等待即升级；链尽 / 本级未成即结算并转日报。

        :return: 本次**新产生**的留痕（新一级的投递 / 待汇总项；结算属就地更新，不计入）
        """
        moment = self._now() if now is None else now
        produced: list[DeliveryRecord] = []
        for latest in self._current_steps():
            policy = self._policies.get(latest.level)
            if latest.status in _TERMINAL_STATUSES:
                # 本级（及其后整条链）都没投出去 ⇒ 结算未确认并转日报，不等超时
                settled = self._settle(latest, status="unacknowledged", moment=moment)
                self._publish_settled(settled, unread_timeout=False)
                produced.append(self._queue_from(latest, moment=moment))
                continue
            wait = policy.wait_after(latest.step)
            if wait is None:                            # 链尽仍未读 ⇒ 结算未确认 + 转日报
                settled = self._settle(latest, status="unacknowledged", moment=moment)
                self._publish_settled(settled, unread_timeout=True)
                produced.append(self._queue_from(latest, moment=moment))
                continue
            if moment < latest.created_at + timedelta(minutes=wait):
                continue                                # 还没到点（无历史不豁免求值）
            produced.append(
                self._deliver_step_record(latest, policy, step=latest.step + 1, moment=moment)
            )
        return tuple(produced)

    # ───────────────────────── ③ 已读结算 ─────────────────────────

    def mark_read(self, delivery_id: str, *, now: datetime | None = None) -> DeliveryRecord:
        """交互面报「已读」⇒ **停链**结算（07 §4）。

        已读按**信号**停链：同一信号的其余**在途**级一并结算为已读——它们传达的是
        同一条消息（升级链只是在换渠道重发），只停当前那一级会让升级链继续推。

        :return: 被报已读的那一级的留痕（其余各级同批结算，可用 :meth:`ledger` 查）
        :raises DeliveryValidationError: 无此留痕 / 该级不是可结算的投递
        """
        moment = self._now() if now is None else now
        record = self.get(delivery_id)
        if record is None:
            raise DeliveryValidationError(f"无此投递留痕：{delivery_id!r}")
        if record.status not in _OPEN_STATUSES:
            raise DeliveryValidationError(
                f"投递 {delivery_id!r} 当前状态为 {record.status!r}，不可结算为已读"
            )
        settled = self._settle(record, status="read", moment=moment)
        for other in self._open_peers(record):
            self._settle(other, status="read", moment=moment)
        self._publish_settled(settled, unread_timeout=False)
        return settled

    def _open_peers(self, record: DeliveryRecord) -> tuple[DeliveryRecord, ...]:
        """同一信号下**其余**在途的级（同一条消息的另几次投递）。"""
        return tuple(
            r for r in self.ledger()
            if r.signal_id == record.signal_id
            and r.route == "separate"
            and r.delivery_id != record.delivery_id
            and r.status in _OPEN_STATUSES
        )

    # ───────────────────────── ④ 离线补发 ─────────────────────────

    def resend(self, *, now: datetime | None = None) -> tuple[DeliveryRecord, ...]:
        """离线期被拦下的云端投递：网络恢复后按**用户开关**重发（02 §7）。

        开关关闭 ⇒ 不重发（留痕保持原状，仍在 :meth:`pending_reconnect` 里），返回空元组——
        **不是**错误、也**不是**静默成功。重发写**同一 `delivery_id` 路径**（确定性摘要），
        故不产生重复投递记录。
        """
        if not self._policies.resend_on_reconnect:
            return ()
        moment = self._now() if now is None else now
        produced: list[DeliveryRecord] = []
        for record in self.pending_reconnect():
            policy = self._policies.get(record.level)
            produced.append(
                self._deliver_step_record(record, policy, step=record.step, moment=moment)
            )
        return tuple(produced)

    # ───────────────────────── 查询面 ─────────────────────────

    def ledger(self) -> tuple[DeliveryRecord, ...]:
        """全部留痕（按 `delivery_id` 升序）。"""
        if self._store is None:
            return tuple(sorted(self._memory.values(), key=lambda r: r.delivery_id))
        return self._store.all()

    def get(self, delivery_id: str) -> DeliveryRecord | None:
        """按 `delivery_id` 取一条留痕（不存在 → ``None``；损坏 → 校验错）。"""
        if self._store is None:
            return self._memory.get(delivery_id)
        return self._store.get(delivery_id)

    def pending(self) -> tuple[DeliveryRecord, ...]:
        """**待确认**的投递（已投出、等已读——链的开态）。"""
        return tuple(r for r in self.ledger() if r.status == "delivered")

    def pending_reconnect(self) -> tuple[DeliveryRecord, ...]:
        """**待补发**的投递（离线期被拦下的云端投递；依据是本层留痕，见模块 docstring）。"""
        return tuple(
            r for r in self.ledger()
            if r.pending_reconnect and r.status in _TERMINAL_STATUSES
        )

    def daily_report_queue(self) -> tuple[DeliveryRecord, ...]:
        """**待汇总项**（`route="daily_report"`，含文案；日报本体与频控归 T-L5-003）。"""
        return tuple(
            r for r in self.ledger() if r.route == "daily_report" and r.status == "queued"
        )

    def settlements(self) -> tuple[PlatformEvent, ...]:
        """本轮发布的结算事件（**便于可解释**：未注入事件面时为空，归属见 :attr:`EVENT_OWNER`）。"""
        return tuple(self._settlements)

    # ───────────────────────── 内部 ─────────────────────────

    def _resolve(
        self, signal: Signal, content_type: str, moment: datetime,
    ) -> BudgetVerdict | None:
        """格位判定；未注入预算 ⇒ ``None``（调用方按「只看级别」的显式缺省分流）。"""
        if self._budget is None:
            return None
        return self._budget.resolve(
            level=signal.level, content_type=content_type, at=moment.time(),
        )

    def _deliver_step(
        self, signal: Signal, policy: ChannelPolicy, *, step: int,
        verdict: BudgetVerdict | None, moment: datetime,
    ) -> DeliveryDispatch:
        """按链投递第 ``step`` 级（信号入口：先备文案，再走链）。"""
        title, body, profile = self._personalizer.build(signal)
        chain = policy.channels[step:]
        if not chain:                                   # 链已走完（防御分支）
            record = self._queue(signal, step=step, moment=moment)
            return DeliveryDispatch(record=record, verdict=verdict)
        payload = ChannelPayload(
            signal_id=signal.signal_id, level=signal.level, title=title, body=body,
            trace_id=signal.source_trace_id, evidence_refs=signal.evidence_refs,
        )
        dispatch = self._dispatcher.deliver(list(chain), payload)
        delivered = dispatch.delivered
        if delivered is not None:
            step = policy.channels.index(delivered.channel)
            status: str = "delivered"
            channel: str = delivered.channel
        else:
            status = "unavailable" if dispatch.pending_reconnect else "failed"
            channel = chain[0]
        record = self._write(DeliveryRecord(
            delivery_id=delivery_id_for(signal.signal_id, channel, step),
            signal_id=signal.signal_id, dedup_key=signal.dedup_key,
            level=signal.level, step=step,
            route="separate", channel=channel, status=status,  # type: ignore[arg-type]
            detail=dispatch.detail(), pending_reconnect=dispatch.pending_reconnect,
            title=title, body=body, evidence_refs=signal.evidence_refs,
            trace_id=signal.source_trace_id, created_at=moment,
        ))
        return DeliveryDispatch(
            record=record, verdict=verdict, payload=payload,
            attempts=dispatch.attempts, wording=profile.wording,
        )

    def _deliver_step_record(
        self, previous: DeliveryRecord, policy: ChannelPolicy, *,
        step: int, moment: datetime,
    ) -> DeliveryRecord:
        """按留痕续推一级（升级 / 补发共用；**只凭留痕**，无需信号上下文）。"""
        chain = policy.channels[step:]
        if not chain:                                   # 链已走完（防御分支）
            return self._queue_from(previous, moment=moment)
        payload = ChannelPayload(
            signal_id=previous.signal_id, level=previous.level,
            title=previous.title, body=previous.body, trace_id=previous.trace_id,
            evidence_refs=previous.evidence_refs,
        )
        dispatch = self._dispatcher.deliver(list(chain), payload)
        delivered = dispatch.delivered
        if delivered is not None:
            step = policy.channels.index(delivered.channel)
            status: str = "delivered"
            channel: str = delivered.channel
        else:
            status = "unavailable" if dispatch.pending_reconnect else "failed"
            channel = chain[0]
        return self._write(DeliveryRecord(
            delivery_id=delivery_id_for(previous.signal_id, channel, step),
            signal_id=previous.signal_id, dedup_key=previous.dedup_key,
            level=previous.level, step=step,
            route="separate", channel=channel, status=status,  # type: ignore[arg-type]
            detail=dispatch.detail(), pending_reconnect=dispatch.pending_reconnect,
            title=previous.title, body=previous.body,
            evidence_refs=previous.evidence_refs, trace_id=previous.trace_id,
            created_at=moment,
        ))

    def _queue(self, signal: Signal, *, step: int, moment: datetime) -> DeliveryRecord:
        """记一条**待汇总项**（不调任何渠道；日报本体归 T-L5-003）。"""
        title, body, _profile = self._personalizer.build(signal)
        return self._write(DeliveryRecord(
            delivery_id=delivery_id_for(signal.signal_id, DAILY_REPORT_CHANNEL, step),
            signal_id=signal.signal_id, dedup_key=signal.dedup_key,
            level=signal.level, step=step,
            route="daily_report", channel=DAILY_REPORT_CHANNEL, status="queued",
            detail="按注意力预算汇总进日报，不单独触达（07 §4）",
            title=title, body=body, evidence_refs=signal.evidence_refs,
            trace_id=signal.source_trace_id, created_at=moment,
        ))

    def _queue_from(self, previous: DeliveryRecord, *, moment: datetime) -> DeliveryRecord:
        """由已有留痕转一条待汇总项（链尽未读 / 投递未成时用）。"""
        return self._write(DeliveryRecord(
            delivery_id=delivery_id_for(
                previous.signal_id, DAILY_REPORT_CHANNEL, previous.step + 1,
            ),
            signal_id=previous.signal_id, dedup_key=previous.dedup_key,
            level=previous.level, step=previous.step + 1,
            route="daily_report", channel=DAILY_REPORT_CHANNEL, status="queued",
            detail="未在读到时确认，按缺省汇总进日报（07 §4：不丢弃）",
            title=previous.title, body=previous.body,
            evidence_refs=previous.evidence_refs, trace_id=previous.trace_id,
            created_at=moment,
        ))

    def _settle(
        self, record: DeliveryRecord, *, status: DeliveryStatus, moment: datetime,
    ) -> DeliveryRecord:
        settled = record.model_copy(update={"status": status, "settled_at": moment})
        return self._write(settled)

    def _write(self, record: DeliveryRecord) -> DeliveryRecord:
        if self._store is not None:
            return self._store.put(record)
        self._memory[record.delivery_id] = record
        return record

    def _current_steps(self) -> tuple[DeliveryRecord, ...]:
        """每个信号**当前那一级**的留痕（开态与终态都在内，由 :meth:`advance` 分流）。"""
        latest: dict[str, DeliveryRecord] = {}
        for record in self.ledger():
            if record.route != "separate":
                continue                                # 待汇总项不是链上的一级
            current = latest.get(record.signal_id)
            if current is None or record.step > current.step:
                latest[record.signal_id] = record
        return tuple(
            record for record in latest.values()
            if record.status in _OPEN_STATUSES or record.status in _TERMINAL_STATUSES
        )

    def _publish_settled(self, record: DeliveryRecord, *, unread_timeout: bool) -> bool:
        """发布 `DeliverySettled`（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)）。

        :return: 事件面**是否受理**——未接入事件面时恒 ``False``（**不假装送达**）：
        事件仍如实记进 :meth:`settlements`，调用方据此在数据面区分「发了」与「没发」。
        """
        event = PlatformEvent(
            event=DELIVERY_EVENT,
            payload={
                "delivery_id": record.delivery_id,
                "signal_id": record.signal_id,
                "channel": record.channel,
                "status": record.status,
                "unread_timeout": unread_timeout,
            },
            trace_id=record.trace_id or None,
            occurred_at=record.settled_at or record.created_at,
        )
        self._settlements.append(event)
        if self._events is None:
            return False
        try:
            accepted = self._events.publish(event)
        except Exception as exc:  # 注入端口的实现缺陷 → 显式暴露，不静默降级
            raise DeliveryValidationError(f"事件发布端口异常：{exc}") from exc
        return accepted is not False
