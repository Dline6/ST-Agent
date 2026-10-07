"""L6 变更流与单项回滚（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) / [§6](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 把变更流定为**所有演进动作的唯一通道**：

```
Change Proposal（含理由、影响范围、trace 依据）
  → 审批门（按授权档位决定：用户批准 / 自动放行）
  → 生效（产生 change_id，记入变更历史）
  → 告知（发布 ChangeApplied）
  → 可回滚（任一 change_id，一键恢复前一版本）
```

要旨：

- **生效只经 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 门面**——`change_id` 由门面铸造
  （「一次调用同时完成『值生效』与『产生 `change_id`』」），本层**不另造**标识与留痕机制；
  **不存在第二条写值路径**（[08 §7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线：无静默调参）。
- **审批门按档位与风险类判定**——判据由 [`authorization.py`](authorization.py) 给出（本模块**不自己判档位**）：
  `manual` ⇒ **不立案**（提案止步建议面）；`collaborative` ⇒ 立案待批准；`autonomous` 且风险类为
  「可自主」⇒ **自动放行**；其余 ⇒ 立案待批准。逐条结论与原因**可查**。
- **回滚以 `ChangeRecord.old_value` 为据回放**——回放**经门面落值**、因此**本身也是一次变更**
  （产生**新** `change_id`、新留痕），原记录**保留**并标注已回滚（[08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md)）。
  **边界**：`old_value` 为空（全仓既定语义＝该条目在此之前**从未落过值**）时**不回放**并如实说明——
  「恢复未设置」这一状态在 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的统一落值面上表达不出，
  不写一个假值冒充恢复。
- **告知是发布事件、不是自己投递**——生效发 `ChangeApplied`、回滚发 `ChangeRolledBack`
  （[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)，必带 `change_id`）；**未接总线即如实标注
  未受理并点名**（不假装送达）。L5 告知通道的**订阅**归里程碑集成关卡。

本模块**不做**出厂重置（[`reset.py`](reset.py)）与档位判定（[`authorization.py`](authorization.py)）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import ChangeRecord
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l6.change_store import EvolutionChangeStore
from st_agent.l6.errors import ChangeFlowError

__all__ = [
    "CHANGE_APPLIED",
    "CHANGE_ROLLED_BACK",
    "NO_AUTHORIZATION_REASON",
    "NO_REGISTRY_REASON",
    "ChangeProposal",
    "ChangeRun",
    "EvolutionChange",
    "EvolutionChangeFlow",
    "GateDecision",
    "PendingChange",
]

CHANGE_APPLIED = "ChangeApplied"
"""演进变更生效的上行事件名（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

CHANGE_ROLLED_BACK = "ChangeRolledBack"
"""演进变更回滚的上行事件名（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

NOTIFY_ABSENT_NOTE = "未接入事件总线，本次告知未送达（组合根注入 events 归里程碑集成关卡）"

NO_REGISTRY_REASON = "未接入 01 §7 配置门面，无法落值生效（不假装生效）"

NO_AUTHORIZATION_REASON = "未接入演进授权判据，不予立案（fail-closed，08 §5）"

PendingStatus = Literal["pending", "deferred", "accepted", "rejected"]


class ChangeProposal(BaseModel):
    """一条**配置变更候选**（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 的 Change Proposal）。

    形态与 [08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 第四段 / [§3](../../../docs/技术架构-v2/08-L6-反思演进.md)
    第 2 步 / [§4](../../../docs/技术架构-v2/08-L6-反思演进.md) 提案三处来源的候选**逐字段一致**
    （`config_id` + 现值 + 建议值 + 理由 + trace 依据）——三条来源共用本形态，不各造一套。
    """

    model_config = ConfigDict(frozen=True)

    config_id: str
    current: Any = None
    suggested: Any = None
    reason: str = ""
    """中性陈述式理由（**生成性文案**，过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    trace_ref: str = ""
    """该建议所依据的留痕锚点（[01 §4](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    source: str = ""
    """提案来源标识（如 `weekly-report` / `training` / `proposal-engine`），便于追溯。"""


class GateDecision(BaseModel):
    """审批门的结论（**不立案不自动也显式**——原因随行，不静默）。"""

    model_config = ConfigDict(frozen=True)

    filed: bool
    """是否**立案**（进待批准队列）。`manual` 档为 ``False``——提案止步于建议面。"""
    auto: bool
    """是否**自动放行**（经生效，无需用户批准）。"""
    reason: str = ""
    tier: str = ""


class EvolutionChange(BaseModel):
    """一次**演进变更**（在 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的 `ChangeRecord` 之上补提案与授权面）。"""

    model_config = ConfigDict(frozen=True)

    change_id: str
    """由 **01 §7 门面铸造**的变更标识（回滚单位）。"""
    seq: int = 0
    """本层留痕的**单调序号**（生效顺序）——`applied_at` 精度内并列时靠它定序，
    故 08 §6 的「逆序回放」在任何时钟精度下都**确定**（不靠标识随机序碰运气）。"""
    config_id: str
    old_value: Any = None
    new_value: Any = None
    reason: str = ""
    trace_ref: str = ""
    source: str = ""
    tier: str = ""
    pending_id: str = ""
    """本变更由哪条待批准项产生（自动放行时为空串）。"""
    applied_at: datetime
    rolled_back_at: datetime | None = None
    rollback_change_id: str = ""
    """回滚本变更时由门面铸造的**新** `change_id`（未回滚为空串）。"""
    notified: bool = False
    notify_note: str = ""


class PendingChange(BaseModel):
    """一条**待批准 / 排队**的提案（处置过的不抹除——留痕即事实）。"""

    model_config = ConfigDict(frozen=True)

    pending_id: str
    proposal: ChangeProposal
    status: PendingStatus = "pending"
    filed_at: datetime
    decided_at: datetime | None = None
    deferrals: int = 0
    """被「延后」的次数（**排队而非丢弃**，[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""


class ChangeRun(BaseModel):
    """一次变更流动作的结论（提交 / 接受 / 回滚共用）。"""

    model_config = ConfigDict(frozen=True)

    decision: GateDecision
    applied: bool = False
    pending: PendingChange | None = None
    change: EvolutionChange | None = None
    note: str = ""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class EvolutionChangeFlow:
    """变更流面（立案 / 审批门 / 生效 / 告知 / 变更历史 / 单项回滚）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**
    :param registry: 01 §7 的 [`ConfigRegistryFacade`](../l1/registry/facade.py)（鸭子类型
        ``set`` / ``changes``）；缺省 ``None`` ⇒ 生效被**拒**并点名（不假装落值）
    :param authorization: 演进授权判据（[`EvolutionAuthorization`](authorization.py) 的
        ``tier()`` / ``classify()``）；缺省 ``None`` ⇒ **不立案**（fail-closed）
    :param events: 事件总线（鸭子类型 ``publish``）；缺省 ``None`` ⇒ 告知**未送达**并点名
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        registry: Any = None,
        authorization: Any = None,
        events: Any = None,
        guard: NeutralityGuard | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = EvolutionChangeStore(store, now=now)
        self._registry = registry
        self._authorization = authorization
        self._events = events
        self._guard = guard if guard is not None else NeutralityGuard()
        self._now = _system_now if now is None else now

    # ───────────────────────── 审批门 ─────────────────────────

    def gate(self, proposal: ChangeProposal) -> GateDecision:
        """按**授权档位与风险类**判定该提案可否自动放行、以及是否立案。

        :raises ChangeFlowError: 未接入授权判据（**fail-closed**，不臆测档位）
        """
        if self._authorization is None:
            raise ChangeFlowError(NO_AUTHORIZATION_REASON)
        try:
            tier = self._authorization.tier()
            risk = self._authorization.classify(proposal.config_id)
        except Exception as exc:  # 判据不可用 → 显式暴露，不静默放行
            raise ChangeFlowError(f"演进授权判据不可用（{exc}）") from exc
        if tier == "manual":
            return GateDecision(
                filed=False, auto=False, tier=tier,
                reason="当前为手动档：副驾只建议，任何配置永不自动变（08 §5）——本提案不立案",
            )
        if tier == "autonomous" and risk == "autonomous-ok":
            return GateDecision(
                filed=True, auto=True, tier=tier,
                reason=f"自主档 + 风险类「可自主」（{proposal.config_id}），自动放行（08 §5）",
            )
        if tier == "autonomous" and risk == "never-autonomous":
            return GateDecision(
                filed=True, auto=False, tier=tier,
                reason=f"风险类「永不可自主」（{proposal.config_id}），任何档位下都不自动（08 §7 红线）",
            )
        return GateDecision(
            filed=True, auto=False, tier=tier,
            reason=f"当前为 {tier} 档 / 风险类 {risk}：需用户批准后才生效（08 §5）",
        )

    # ───────────────────────── 提交与处置 ─────────────────────────

    def submit(self, proposal: ChangeProposal, *, source: str = "") -> ChangeRun:
        """提交一条提案：过审批门 → 自动放行则生效，否则立案待批准。

        :raises ChangeFlowError: 提案形态非法（缺 `config_id` / 值为空）· 未接授权判据
        """
        item = _require_proposal(proposal, source=source)
        self._require_neutral(item.reason, "变更理由")
        decision = self.gate(item)
        if not decision.filed:                       # manual：提案止步建议面，不立案
            return ChangeRun(decision=decision, note=decision.reason)
        if decision.auto:
            return self._apply(item, decision=decision, pending=None)
        pending = self._file(item, decision)
        return ChangeRun(decision=decision, pending=pending, note=decision.reason)

    def accept(self, pending_id: str) -> ChangeRun:
        """**接受**一条提案（→ 立即生效并记入变更历史）。"""
        current = self._require_pending(pending_id)
        if current.status in ("accepted", "rejected"):
            raise ChangeFlowError(f"提案 {pending_id!r} 已处置过（{current.status}），不重复处置")
        decision = self.gate(current.proposal)
        run = self._apply(current.proposal, decision=decision, pending=current)
        return run

    def reject(self, pending_id: str) -> PendingChange:
        """**否决**一条提案（留痕，条目值逐字节不变）。"""
        current = self._require_pending(pending_id)
        if current.status in ("accepted", "rejected"):
            raise ChangeFlowError(f"提案 {pending_id!r} 已处置过（{current.status}），不重复处置")
        updated = current.model_copy(update={"status": "rejected", "decided_at": self._now()})
        self._persist_pending(updated)
        return updated

    def defer(self, pending_id: str) -> PendingChange:
        """**延后**一条提案（**排队而非丢弃**：留在待批准读面、可再次处置）。"""
        current = self._require_pending(pending_id)
        if current.status in ("accepted", "rejected"):
            raise ChangeFlowError(f"提案 {pending_id!r} 已处置过（{current.status}），不重复处置")
        updated = current.model_copy(update={
            "status": "deferred", "deferrals": current.deferrals + 1,
        })
        self._persist_pending(updated)
        return updated

    # ───────────────────────── 回滚 ─────────────────────────

    def rollback(self, change_id: str) -> ChangeRun:
        """**一键回滚**一条演进变更——回放其 `old_value`（经门面落值、产生新 `change_id`）。

        :raises ChangeFlowError: 变更不存在 / 已回滚 / 未接门面
        """
        target = self.get(change_id)
        if target is None:
            raise ChangeFlowError(f"无此演进变更：{change_id!r}")
        if target.rolled_back_at is not None:
            raise ChangeFlowError(f"变更 {change_id!r} 已回滚过（{target.rolled_back_at.isoformat()}）")
        decision = self.gate(target_as_proposal(target))
        if target.old_value is None:
            # 该条目在本变更之前【从未落过值】（全仓既定语义：ChangeRecord.old_value 为空
            # ⇒ 此前无留痕）。「恢复未设置」这一状态在 01 §7 的统一落值面上表达不出
            # （落值面只接受取值域内的取值），故【不回放】并如实说明——不写一个假值冒充恢复。
            return ChangeRun(
                decision=decision, applied=False,
                note="该变更之前该条目未设置（old_value 为空），统一落值面表达不了「恢复未设置」"
                     "——未回放，如实说明（不写假值冒充恢复）",
            )
        record = self._write_value(target.config_id, target.old_value, trace_ref=target.trace_ref)
        moment = self._now()
        if record is None:                    # 取值已等于 old_value ⇒ 01 §7 不留痕，不谎称已回滚
            return ChangeRun(
                decision=decision, applied=False,
                note="目标取值已等于该变更的回滚值，未产生变更留痕（01 §7：取值不变不留痕）",
            )
        restored = target.model_copy(update={
            "rolled_back_at": moment, "rollback_change_id": record.change_id,
        })
        self._persist_change(restored)
        rollback_change = EvolutionChange(
            change_id=record.change_id, seq=self._next_seq(), config_id=target.config_id,
            old_value=record.old_value, new_value=record.new_value,
            reason=f"回滚变更 {target.change_id}（08 §6：一键恢复前一版本）",
            trace_ref=target.trace_ref, source=target.source, tier=target.tier,
            applied_at=moment,
        )
        rollback_change, delivered, note = self._notify(
            CHANGE_ROLLED_BACK, rollback_change,
            extra={"rolled_back_change_id": target.change_id},
        )
        self._persist_change(rollback_change)
        return ChangeRun(
            decision=decision, applied=True, change=rollback_change,
            note="已回滚并告知" if delivered else note,
        )

    # ───────────────────────── 读面 ─────────────────────────

    def get(self, change_id: str) -> EvolutionChange | None:
        """取一条演进变更（不存在 → ``None``；损坏 → 抛）。"""
        raw = self._store.get_change(change_id)
        if raw is None:
            return None
        return _parse_change(raw, change_id)

    def history(self) -> tuple[EvolutionChange, ...]:
        """**变更历史时间线**（按（生效顺序, 生效时刻, `change_id`）升序；含回滚记录）。"""
        out = [_parse_change(raw, str(raw.get("change_id", ""))) for raw in self._store.all_changes()]
        return tuple(sorted(out, key=lambda c: (c.seq, c.applied_at, c.change_id)))

    def changes(self) -> tuple[ChangeRecord, ...]:
        """门面的**跨族**变更读面（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)；
        消费方不依赖各族落盘前缀）。

        :raises ChangeFlowError: 未接门面
        """
        if self._registry is None:
            raise ChangeFlowError(NO_REGISTRY_REASON)
        return tuple(self._registry.changes())

    def pending(self, status: PendingStatus | None = None) -> tuple[PendingChange, ...]:
        """待批准 / 排队读面（`status=None` 即全部，含已处置的——不抹除）。"""
        out: list[PendingChange] = []
        for raw in self._store.all_pending():
            try:
                item = PendingChange(**raw)
            except ValidationError as exc:
                raise ChangeFlowError(f"待批准提案形态损坏：{exc}") from exc
            if status is None or item.status == status:
                out.append(item)
        return tuple(sorted(out, key=lambda p: (p.filed_at, p.pending_id)))

    def get_pending(self, pending_id: str) -> PendingChange | None:
        """取一条待批准项（不存在 → ``None``）。"""
        raw = self._store.get_pending(pending_id)
        if raw is None:
            return None
        try:
            return PendingChange(**raw)
        except ValidationError as exc:
            raise ChangeFlowError(f"待批准提案形态损坏（{pending_id}）：{exc}") from exc

    # ───────────────────────── 内部 ─────────────────────────

    def _apply(
        self, proposal: ChangeProposal, *, decision: GateDecision, pending: PendingChange | None,
    ) -> ChangeRun:
        # 写之前先取「该条目当前的生效取值」——条目**从未落过值**时，门面读到的是
        # 声明面的默认（Family.entry 按声明物化），这正是「变更前的生效取值」。
        # 门面铸的 ChangeRecord 对首次落值给 old_value=None（全仓既定语义：此前无留痕），
        # 而回滚需要的是**生效取值**；故此处补上，使 08 §6 的「恢复默认」对首改条目也成立。
        declared = self._declared_default(proposal.config_id)
        record = self._write_value(proposal.config_id, proposal.suggested, trace_ref=proposal.trace_ref)
        moment = self._now()
        if record is None:
            note = "条目取值已是该建议值，未产生变更（01 §7：取值不变不留痕）"
            if pending is not None:
                self._persist_pending(pending.model_copy(
                    update={"status": "accepted", "decided_at": moment}
                ))
            return ChangeRun(decision=decision, applied=False, pending=pending, note=note)
        change = EvolutionChange(
            change_id=record.change_id, seq=self._next_seq(), config_id=proposal.config_id,
            old_value=record.old_value if record.old_value is not None else declared,
            new_value=record.new_value,
            reason=proposal.reason, trace_ref=proposal.trace_ref,
            source=proposal.source, tier=decision.tier,
            pending_id="" if pending is None else pending.pending_id,
            applied_at=datetime.fromisoformat(record.applied_at),
        )
        change, delivered, note = self._notify(CHANGE_APPLIED, change)
        self._persist_change(change)
        if pending is not None:
            self._persist_pending(pending.model_copy(
                update={"status": "accepted", "decided_at": moment}
            ))
        return ChangeRun(
            decision=decision, applied=True, pending=pending, change=change,
            note="已生效并告知" if delivered else note,
        )

    def _write_value(self, config_id: str, value: Any, *, trace_ref: str) -> ChangeRecord | None:
        """经 01 §7 门面落值（**唯一**写值路径；`change_id` 由门面铸造）。"""
        if self._registry is None:
            raise ChangeFlowError(NO_REGISTRY_REASON)
        try:
            return self._registry.set(config_id, value, trace_id=trace_ref or None)
        except Exception as exc:
            raise ChangeFlowError(f"{config_id!r} 落值失败（{exc}）") from exc

    def _declared_default(self, config_id: str) -> Any:
        """条目**当前的生效取值**（未落值时即声明面默认）；读不到 → ``None``（不臆造）。"""
        if self._registry is None:
            return None
        try:
            entry = self._registry.entry(config_id)
        except Exception:                              # noqa: BLE001 - 读面不可用不阻断落值
            return None
        return None if entry is None else entry.default

    def _notify(
        self, event_name: str, change: EvolutionChange, *, extra: Mapping[str, Any] | None = None,
    ) -> tuple[EvolutionChange, bool, str]:
        """发布告知事件（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)）；未接总线**不假装送达**。"""
        payload: dict[str, Any] = {
            "config_id": change.config_id,
            "old_value": change.old_value,
            "new_value": change.new_value,
            "reason": change.reason,
            "source": change.source,
            "tier": change.tier,
        }
        payload.update(dict(extra or {}))
        if self._events is None:
            return change.model_copy(
                update={"notified": False, "notify_note": NOTIFY_ABSENT_NOTE}
            ), False, NOTIFY_ABSENT_NOTE
        event = PlatformEvent(
            event=event_name, payload=payload, trace_id=change.trace_ref or None,
            change_id=change.change_id, occurred_at=change.applied_at,
        )
        try:
            result = self._events.publish(event)
        except Exception as exc:  # 注入端口的实现缺陷 → 显式暴露，不静默降级
            raise ChangeFlowError(f"事件发布端口异常：{exc}") from exc
        delivered = result if isinstance(result, bool) else getattr(result, "delivered", False)
        note = "" if delivered else "事件已发布但无订阅者受理（01 §11：未送达不假装送达）"
        return change.model_copy(
            update={"notified": bool(delivered), "notify_note": note}
        ), bool(delivered), note

    def _file(self, proposal: ChangeProposal, decision: GateDecision) -> PendingChange:
        pending = PendingChange(
            pending_id=_pending_id(proposal), proposal=proposal, filed_at=self._now(),
        )
        self._persist_pending(pending)
        return pending

    def _require_pending(self, pending_id: str) -> PendingChange:
        current = self.get_pending(pending_id)
        if current is None:
            raise ChangeFlowError(f"无此待批准提案：{pending_id!r}")
        return current

    def _persist_pending(self, item: PendingChange) -> None:
        self._store.put_pending(item.pending_id, item.model_dump(mode="json"))

    def _persist_change(self, change: EvolutionChange) -> None:
        self._store.put_change(change.change_id, change.model_dump(mode="json"))

    def _next_seq(self) -> int:
        """本层留痕的单调序号（生效顺序）——见 :attr:`EvolutionChange.seq`。"""
        return max((c.seq for c in self.history()), default=-1) + 1

    def _require_neutral(self, text: str, where: str) -> None:
        if not text:
            return
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise ChangeFlowError(f"{where} 未过 01 §6 中性化校验（{hits}）：文案须中性（铁律 2）")


def _require_proposal(proposal: ChangeProposal, *, source: str) -> ChangeProposal:
    if not isinstance(proposal, ChangeProposal):
        try:
            proposal = ChangeProposal(**dict(proposal))       # 三条来源的候选形态一致
        except (ValidationError, TypeError, ValueError) as exc:
            raise ChangeFlowError(f"提案形态非法：{exc}") from exc
    if not (proposal.config_id or "").strip():
        raise ChangeFlowError("提案须给出 `config_id`（08 §5 的 Change Proposal 之一）")
    if proposal.suggested is None:
        raise ChangeFlowError("提案须给出建议值（08 §5 的 Change Proposal 之一）")
    return proposal if not source else proposal.model_copy(update={"source": source})


def _pending_id(proposal: ChangeProposal) -> str:
    """提案的**确定性摘要**标识 ⇒ 同一提案重复提交是同一件事（幂等覆盖）。"""
    return digest_id(
        "pend",
        proposal.config_id,
        json.dumps(proposal.suggested, sort_keys=True, default=str),
        proposal.source,
    )


def target_as_proposal(change: EvolutionChange) -> ChangeProposal:
    """变更 → 提案形态（回滚也要过审批门，故需一个等价提案）。"""
    return ChangeProposal(
        config_id=change.config_id, current=change.new_value, suggested=change.old_value,
        reason=change.reason, trace_ref=change.trace_ref, source=change.source,
    )


def _parse_change(raw: Mapping[str, Any], change_id: str) -> EvolutionChange:
    try:
        return EvolutionChange(**raw)
    except ValidationError as exc:
        raise ChangeFlowError(f"演进变更留痕形态损坏（{change_id}）：{exc}") from exc
