"""L1 定时调度器（T-L1-005.1 判定 + `.2` 执行接线 + `.3` 离线处置；03 §6）。

职责边界：**判到期、触发执行、记状态、处置离线**。时钟由调用方按次传入
（``now``），故判定是**确定性**的：同一 ``(目标集, 状态, now)`` 必得同一结论，
可离线重复验证。

两类目标统一收进 ``WorkflowSchedule`` 语义判到期：

- **工作流**：按其注册时声明的 ``schedule``（03 §3.1）
- **Skill**：按其描述体声明的 ``frequency_minutes`` 折算成 ``mode=interval``
  （03 §6「各 Skill 的频率参数」）

到期口径（写在这里，不靠猜）：

- ``manual`` / ``event``：**不**进入自动到期集（前者待人手动触发，后者待显式
  事件触发——事件总线尚无归属，本件不臆造订阅机制）
- ``interval``：无历史运行 → 到期（否则周期目标永远不会起跑）；此后
  ``now ≥ 上次运行 + interval_minutes``
- ``cron``：``now`` 所在整分命中表达式，且（无历史运行或上次运行早于该整分）
  ——故同一分钟内不重复。**无历史不豁免求值**：没到点就是没到点

触发即走 03 §1.2 完整流水线：调度器**创建** ``Trace``（01 §1 的
``trace_id`` 归属），执行经 ``SkillRunner.run``（留痕 SkillRun / Trace /
ResultEnvelope），结果信封原样透出并折成记账状态。工作流目标经**复合 Skill**
执行（03 §3.2）——未物化即记 ``gap`` **不执行**，不落半成品。

记账口径：**不该跑的目标（缺口 / 已禁用）也记账并推进到期**——否则每一轮都会
把同一条缺口再刷一遍，状态反而看不清。

离线口径（``T-L1-005.3``；03 §6 第三句 + 02 §7 三级）：离线判定取 ``EgressGateway.online``
一个源头。到期的目标按其所引 Skill 的 ``offline_level`` 分流——``full`` / ``degraded``
**照常执行**并在说明里标注「离线执行、数据停留在最后快照」（信封上的数据截止时间
由执行器经 ``ctx.freshness`` 落在 ``as_of`` / ``last_updated_at``，调度器不越俎代庖）；
``none`` 记 ``deferred`` 并累计到 ``pending``，网络恢复后由 :meth:`Scheduler.catch_up`
按用户配置补跑或跳过。

.. warning::
   已注册工作流携带**语法非法**的 cron 时，该目标不进自动到期集，并因
   :meth:`Scheduler.invalid` 显式列出——不静默跳过，也不拖垮同一环境里的其余目标
   （``WorkflowSchedule`` 只校验 cron 非空，语法由本件认定）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import ValidationError

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.trace import Trace
from st_agent.l1.scheduler.cron import CronSpec
from st_agent.l1.scheduler.errors import (
    CronSyntaxError,
    SchedulerTargetNotFoundError,
    SchedulerValidationError,
)
from st_agent.l1.scheduler.models import (
    FREQUENCY_PARAM,
    RUN_STATUSES,
    STATE_PREFIX,
    PermissionSource,
    ScheduleTarget,
    ScheduledRun,
    SchedulerState,
    check_aware,
    check_target_id,
)
from st_agent.l1.scheduler.policy import (
    DEFAULT_OFFLINE_CATCH_UP,
    OfflineCatchUp,
    SchedulerPolicy,
)
from st_agent.l1.workflow.composite import composite_skill_id
from st_agent.l1.workflow.models import WorkflowSchedule

__all__ = ["DEFAULT_POLL_SECONDS", "Scheduler"]

_PASSIVE_MODES = ("manual", "event")
"""不进入自动到期集的触发方式。"""

DEFAULT_POLL_SECONDS = 30.0
"""``run_forever`` 的默认轮询间隔（秒）——调度精度到分钟，30 s 足够不误点。"""

_OK_ENVELOPE_STATUSES = ("ok", "empty")
"""视为「跑成了」的信封状态（``empty`` 是合法结论，不算失败）。"""

_PURPOSE = "定时调度触发"


class Scheduler:
    """调度门面（03 §6）。

    :param store: 可选 ``Store`` 句柄——给了则运行状态落 ``config`` 分区
        （``scheduler/<target_id>.json``）；不给则仅进程内（与 ``SkillSandbox`` 同构）
    :param workflows: 工作流注册面（鸭子类型：``list_all()`` 产出带 ``flow_id`` /
        ``schedule`` 的对象，即 ``WorkflowStore``）
    :param skills: Skill 注册面（鸭子类型：``list_all()`` 产出带 ``skill_id`` /
        ``parameters`` 的描述体，即 ``SkillRegistry``）
    :param sandbox: 可选沙箱（``is_disabled(skill_id)``）——已禁用 Skill 不入目标集，
        已在目标集内的 Skill 被禁用后不再被执行
    :param runner: 可选执行面（鸭子类型：``run(skill_id, values, *, trace,
        approved_permissions, initiator, purpose)``，即 ``SkillRunner``）——
        ``tick`` / ``trigger`` 依赖它，缺失即拒
    :param permissions: 可选权限来源（``PermissionSource``）——缺省即空集，
        需要声明的执行由沙箱 fail-closed 拦下
    :param gateway: 可选出网网关（鸭子类型：``online`` 布尔属性，即 ``EgressGateway``）
        ——离线判定取它一个源头（02 §7）；缺省视为在线（不知道就不臆测）
    :param policy: 可选策略条目门面（``SchedulerPolicy``）——缺省由 ``store`` 自建；
        无 ``store`` 则取契约缺省值
    """

    def __init__(self, store=None, *, workflows=None, skills=None, sandbox=None,
                 runner=None, permissions=None, gateway=None, policy=None,
                 frequency_param: str = FREQUENCY_PARAM) -> None:
        if permissions is not None and not isinstance(permissions, PermissionSource):
            raise SchedulerValidationError(
                "权限来源不合口径（须提供 approved_for(target) -> tuple[str, ...]）；"
                f"收到 {type(permissions).__name__}"
            )
        self._store = store
        self._workflows = workflows
        self._skills = skills
        self._sandbox = sandbox
        self._runner = runner
        self._permissions = permissions
        self._gateway = gateway
        if policy is not None:
            self._policy = policy
        elif store is not None:
            self._policy = SchedulerPolicy(store)
        else:
            self._policy = None
        self._frequency_param = frequency_param
        self._states: dict[str, SchedulerState] = {}
        self._cron: dict[str, CronSpec] = {}

    # ───────────────────────── 目标枚举 ─────────────────────────

    def targets(self) -> tuple[ScheduleTarget, ...]:
        """全部已注册的调度目标（按 ``target_id`` 升序，结果确定）。

        含 ``mode=manual`` / ``mode=event`` 的工作流——它们能被人手动触发
        （``T-L1-005.2`` 的 ``trigger``），只是不进自动到期集。
        """
        found: list[ScheduleTarget] = []
        for dag in _iterate(self._workflows):
            found.append(ScheduleTarget(
                target_id=dag.flow_id, kind="workflow",
                origin="workflow-schedule", schedule=dag.schedule,
            ))
        for descriptor in _iterate(self._skills):
            minutes = _frequency_of(descriptor, self._frequency_param)
            if minutes is None:
                continue
            if self._sandbox is not None and self._sandbox.is_disabled(descriptor.skill_id):
                continue
            found.append(ScheduleTarget(
                target_id=descriptor.skill_id, kind="skill",
                origin="skill-frequency",
                schedule=WorkflowSchedule(mode="interval", interval_minutes=minutes),
            ))
        return tuple(sorted(found, key=lambda t: t.target_id))

    def target(self, target_id: str) -> ScheduleTarget:
        """取单个目标（未注册 → ``SchedulerTargetNotFoundError``）。"""
        check_target_id(target_id)
        for target in self.targets():
            if target.target_id == target_id:
                return target
        raise SchedulerTargetNotFoundError(f"目标 {target_id!r} 未在运行环境中注册")

    def invalid(self) -> tuple[tuple[str, str, str], ...]:
        """语法非法而无法参与判定的目标：``(target_id, 表达式, 原因)``（升序）。

        不静默的出口——调用方/UI 据此提示「该工作流的触发表达式没法用」。
        """
        issues: list[tuple[str, str, str]] = []
        for target in self.targets():
            expression = target.schedule.cron or ""
            if target.schedule.mode != "cron":
                continue
            try:
                self._cron_for(target)
            except CronSyntaxError as exc:
                issues.append((target.target_id, expression, str(exc)))
        return tuple(sorted(issues))

    # ───────────────────────── 到期判定 ─────────────────────────

    def due_targets(self, now: datetime) -> tuple[ScheduleTarget, ...]:
        """此刻到期的目标（``now`` 须带时区；按 ``target_id`` 升序）。

        纯查询：不改状态、不触碰存储（故可任意次调用而结论不变）。
        """
        check_aware(now)
        return tuple(t for t in self.targets() if self.is_due(t, now))

    def is_due(self, target: ScheduleTarget, now: datetime) -> bool:
        """单个目标此刻是否到期（口径见模块 docstring）。"""
        check_aware(now)
        if target.schedule.mode in _PASSIVE_MODES:
            return False
        state = self.state(target.target_id)
        if target.schedule.mode == "interval":
            if state.last_run_at is None:
                return True
            interval = timedelta(minutes=target.schedule.interval_minutes)
            return now >= state.last_run_at + interval
        try:
            spec = self._cron_for(target)
        except CronSyntaxError:
            return False
        scheduled_at = now.replace(second=0, microsecond=0)
        if not spec.matches(scheduled_at):
            return False
        return state.last_run_at is None or state.last_run_at < scheduled_at

    def next_due_at(self, target_id: str, *, now: datetime) -> datetime | None:
        """下次到期时刻（``manual`` / ``event`` → ``None``）。

        此刻已到期时返回当前整分——「现在就该跑」比「下一个还没到」有用。
        """
        check_aware(now)
        target = self.target(target_id)
        schedule = target.schedule
        if schedule.mode in _PASSIVE_MODES:
            return None
        if self.is_due(target, now):
            return now.replace(second=0, microsecond=0)
        state = self.state(target_id)
        if schedule.mode == "interval":
            if state.last_run_at is None:
                return now.replace(second=0, microsecond=0)
            return state.last_run_at + timedelta(minutes=schedule.interval_minutes)
        return self._cron_for(target).next_after(now)

    # ───────────────────────── 运行状态 ─────────────────────────

    def state(self, target_id: str) -> SchedulerState:
        """某目标的运行状态；从未运行过 → 全空的 ``SchedulerState``（不抛错）。"""
        check_target_id(target_id)
        cached = self._states.get(target_id)
        if cached is not None:
            return cached
        if self._store is not None:
            path = self._path(target_id)
            if path in self._store.list_files("config"):
                raw = self._store.get("config", path)
                try:
                    state = SchedulerState.model_validate_json(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                    raise SchedulerValidationError(f"调度状态记录损坏无法解析：{exc}") from exc
                self._states[target_id] = state
                return state
        empty = SchedulerState(target_id=target_id)
        self._states[target_id] = empty
        return empty

    def states(self) -> tuple[SchedulerState, ...]:
        """全部已有状态（按 ``target_id`` 升序；从未运行过的目标不出现）。"""
        seen = {t.target_id for t in self.targets()}
        if self._store is not None:
            for name in self._store.list_files("config"):
                if name.startswith(STATE_PREFIX) and name.endswith(".json"):
                    seen.add(name[len(STATE_PREFIX):-len(".json")])
        return tuple(sorted(
            (s for s in (self.state(tid) for tid in seen) if s.last_status is not None),
            key=lambda s: s.target_id,
        ))

    def record_run(self, target_id: str, *, status: str, now: datetime,
                   detail: str = "", skill_run_id: str | None = None,
                   ran_at: datetime | None = None,
                   pending: tuple[datetime, ...] | None = None) -> SchedulerState:
        """记一次运行并推进状态（落 ``config`` 分区；有 ``Store`` 时以盘为准）。

        :param status: 见 ``RUN_STATUSES``（``ok`` / ``failed`` / ``gap`` /
            ``skipped`` / ``deferred``）
        :param now: 记账时刻（须带时区）
        :param ran_at: 本次运行发生的时刻；缺省即 ``now``。**成功与失败都推进**
            ``last_run_at``——判到期看的是「跑过」，不是「跑成」；只有 ``status=ok``
            才推进 ``last_success_at``
        :param pending: 待补到期点；**缺省即沿用原值**（只有 ``deferred`` 的登记与
            ``catch_up`` 的收尾会改它）
        """
        check_target_id(target_id)
        check_aware(now, label="记账时刻")
        if status not in RUN_STATUSES:
            raise SchedulerValidationError(
                f"未知运行状态 {status!r}（合法值：{'/'.join(RUN_STATUSES)}）"
            )
        run_at = now if ran_at is None else check_aware(ran_at, label="运行时刻")
        previous = self.state(target_id)
        state = SchedulerState(
            target_id=target_id, last_run_at=run_at,
            last_success_at=run_at if status == "ok" else previous.last_success_at,
            last_status=status, last_detail=detail,
            last_skill_run_id=skill_run_id,
            pending=previous.pending if pending is None else tuple(pending),
            updated_at=now,
        )
        self._states[target_id] = state
        if self._store is not None:
            self._store.put("config", self._path(target_id),
                            state.model_dump_json().encode("utf-8"))
        return state

    # ───────────────────────── 触发执行（T-L1-005.2） ─────────────────────────

    def tick(self, now: datetime) -> tuple[ScheduledRun, ...]:
        """把此刻全部到期目标跑一遍（判定 → 执行 → 记账；按 ``target_id`` 升序）。"""
        check_aware(now)
        return tuple(self._fire(t, now) for t in self.due_targets(now))

    def trigger(self, target_id: str, *, now: datetime) -> ScheduledRun:
        """手动触发一次（不经到期判定）：立即执行并记账。

        ``manual`` / ``event`` 目标只能经此路径跑——它们不进自动到期集。
        """
        check_aware(now)
        return self._fire(self.target(target_id), now)

    def run_forever(self, *, poll_seconds: float = DEFAULT_POLL_SECONDS,
                    stop: threading.Event | None = None,
                    clock: Callable[[], datetime] | None = None) -> int:
        """轮询循环：每 ``poll_seconds`` 秒 ``tick`` 一次，``stop`` 置位即退。

        返回累计触发的执行次数。循环本身不含业务——判定与记账都在 :meth:`tick`，
        故「跑多久」不影响结论，只影响何时被轮到。
        """
        stopper = threading.Event() if stop is None else stop
        reader = _local_now if clock is None else clock
        fired = 0
        while not stopper.is_set():
            fired += len(self.tick(reader()))
            stopper.wait(poll_seconds)
        return fired

    def catch_up(self, now: datetime) -> tuple[ScheduledRun, ...]:
        """网络恢复后的**待补处置**（``T-L1-005.3``；03 §6 第三句）。

        对每个有待补到期点的目标，按用户配置（``SchedulerPolicy``）：

        - ``catch-up``：补跑**一次**（一次补偿该目标的全部待补到期点——错过的
          条数写进说明，不静默；不按条数重放，免得长离线后连刷几十遍）
        - ``skip``：不执行、记 ``skipped`` 并说明按配置跳过

        两种处置都清空待补集，故重复调用不再重复执行（幂等）。**仍离线**时补跑会再
        记一次 ``deferred`` 且待补集原样保留——「还没恢复」不等于「已处置」。

        目标已不在调度面（离线期间被禁用 / 注销）时按 ``skipped`` 收尾并注明缘由，
        免得待补项永远挂着。
        """
        check_aware(now)
        mode = self._catch_up_mode()
        known = {t.target_id: t for t in self.targets()}
        return tuple(
            self._settle(known[target_id], now, mode) if target_id in known
            else self._abandon(target_id, now)
            for target_id in sorted(s.target_id for s in self.states() if s.pending)
        )

    # ───────────────────────── 内部工具（执行面） ─────────────────────────

    def _abandon(self, target_id: str, now: datetime) -> ScheduledRun:
        """目标已离开调度面时的待补收尾（显式留痕，不静默丢弃）。"""
        detail = "目标已不在调度面（已禁用或已注销），待补到期点不再补做"
        run = ScheduledRun(target_id=target_id, status="skipped",
                           detail=detail, ran_at=now)
        self.record_run(target_id, status=run.status, now=now,
                        detail=detail, pending=())
        return run

    def _settle(self, target: ScheduleTarget, now: datetime,
                mode: OfflineCatchUp) -> ScheduledRun:
        """处置单个目标的全部待补到期点。"""
        missed = self.state(target.target_id).pending
        if mode == "skip":
            detail = f"离线期间错过 {len(missed)} 个到期点，按配置跳过（不补做）"
            run = ScheduledRun(target_id=target.target_id, status="skipped",
                               detail=detail, ran_at=now)
            self.record_run(target.target_id, status=run.status, now=now,
                            detail=detail, pending=())
            return run
        run = self._perform(target, now, offline_note=(
            f"离线期间错过 {len(missed)} 个到期点，恢复后补跑一次"))
        self.record_run(target.target_id, status=run.status, now=now,
                        detail=run.detail, skill_run_id=run.skill_run_id,
                        pending=missed if run.status == "deferred" else ())
        return run

    def _fire(self, target: ScheduleTarget, now: datetime) -> ScheduledRun:
        """执行一个目标并把结果记账（不该跑的也记账，见模块 docstring）。"""
        run = self._perform(target, now)
        previous = self.state(target.target_id)
        pending = previous.pending
        if run.status == "deferred":
            pending = _add_instant(pending, run.ran_at)
        self.record_run(target.target_id, status=run.status, now=now,
                        detail=run.detail, skill_run_id=run.skill_run_id,
                        pending=pending)
        return run

    def _perform(self, target: ScheduleTarget, now: datetime,
                 *, offline_note: str = "") -> ScheduledRun:
        """执行一个目标，**不**记账（记账由 :meth:`_fire` / :meth:`_settle` 做）。"""
        if self._runner is None:
            raise SchedulerValidationError(
                "调度器未注入执行面（SkillRunner）——tick / trigger / catch_up 不可用"
            )
        skill_id = self._skill_of(target)
        descriptor = self._descriptor(skill_id)
        if descriptor is None:
            detail = (f"复合 Skill {skill_id} 未物化（依赖缺失，03 §3.2）"
                      if target.kind == "workflow" else f"Skill {skill_id} 未注册")
            return ScheduledRun(target_id=target.target_id, status="gap",
                                detail=detail, ran_at=now)
        if self._sandbox is not None and self._sandbox.is_disabled(skill_id):
            return ScheduledRun(target_id=target.target_id, skill_id=skill_id,
                                status="skipped", ran_at=now,
                                detail=f"Skill {skill_id} 已被用户禁用")
        notes = [offline_note] if offline_note else []
        if self._offline():
            if descriptor.offline_level == "none":
                return ScheduledRun(
                    target_id=target.target_id, skill_id=skill_id,
                    status="deferred", ran_at=now,
                    detail=(f"离线且 {skill_id} 声明 offline_level=none"
                            "（必须联网，02 §7）——待网络恢复后按配置补跑或跳过"),
                )
            notes.append(f"离线执行：{skill_id} 声明 "
                         f"offline_level={descriptor.offline_level}，数据停留在最后快照")
        outcome = self._runner.run(
            skill_id, _default_values(descriptor),
            trace=Trace(trace_id=TraceId.generate()),
            approved_permissions=self._approved(target),
            initiator=f"schedule:{target.target_id}", purpose=_PURPOSE,
        )
        envelope = outcome.envelope
        ok = envelope.status in _OK_ENVELOPE_STATUSES
        if not ok:
            notes.append(envelope.reason or f"执行信封 {envelope.status}")
        return ScheduledRun(
            target_id=target.target_id, skill_id=skill_id,
            status="ok" if ok else "failed", detail="；".join(notes),
            skill_run_id=outcome.skill_run_id, envelope=envelope,
            trace=outcome.trace, trace_id=outcome.trace.trace_id.value, ran_at=now,
        )

    def _offline(self) -> bool:
        """是否离线（取网关一个源头；未注入网关 → 视为在线，不臆测）。"""
        return self._gateway is not None and not self._gateway.online

    def _catch_up_mode(self) -> OfflineCatchUp:
        if self._policy is None:
            return DEFAULT_OFFLINE_CATCH_UP
        return self._policy.value()

    # ───────────────────────── 内部工具 ─────────────────────────

    def _cron_for(self, target: ScheduleTarget) -> CronSpec:
        """取（并缓存）目标的 cron 求值件（语法非法 → ``CronSyntaxError``）。"""
        spec = self._cron.get(target.target_id)
        if spec is None:
            spec = CronSpec.parse(target.schedule.cron or "")
            self._cron[target.target_id] = spec
        return spec

    def _skill_of(self, target: ScheduleTarget) -> str:
        """目标实际执行的 Skill——工作流取其**复合 Skill**（03 §3.2）。"""
        if target.kind == "workflow":
            return composite_skill_id(target.target_id)
        return target.target_id

    def _descriptor(self, skill_id: str):
        """该 Skill 的描述体（未注册 → ``None``）。"""
        for descriptor in _iterate(self._skills):
            if descriptor.skill_id == skill_id:
                return descriptor
        return None

    def _approved(self, target: ScheduleTarget) -> tuple[str, ...]:
        """本次执行已批准的权限集（无来源 → 空集，交沙箱 fail-closed）。"""
        if self._permissions is None:
            return ()
        return tuple(self._permissions.approved_for(target))

    @staticmethod
    def _path(target_id: str) -> str:
        return f"{STATE_PREFIX}{check_target_id(target_id)}.json"


def _local_now() -> datetime:
    """本机当前时刻（带本地时区；``run_forever`` 的缺省时钟）。"""
    return datetime.now().astimezone()


def _default_values(descriptor) -> dict:
    """执行入参：该 Skill 描述体**当前**的参数默认值（双通道改参即生效）。"""
    return {p.name: p.default for p in descriptor.parameters if p.default is not None}


def _add_instant(pending: tuple[datetime, ...], instant: datetime) -> tuple[datetime, ...]:
    """把一个待补到期点并入集合（秒级去重、升序）——同一时刻不重复登记。"""
    moment = instant.replace(microsecond=0)
    if moment in pending:
        return pending
    return tuple(sorted((*pending, moment)))


def _iterate(registry) -> tuple:
    """注册面的统一取法（``None`` → 空）。"""
    return tuple(registry.list_all()) if registry is not None else ()


def _frequency_of(descriptor, param_name: str) -> int | None:
    """描述体的频率参数值（未声明 → ``None``；声明了但值不可用 → 拒绝）。

    值不可用（缺省值非正整数）是**注册侧的形态错误**，显式拒绝而不是把该 Skill
    静默从调度面摘掉——「声明了频率却用不了」与「没声明频率」必须能分辨。
    """
    for spec in getattr(descriptor, "parameters", ()) or ():
        if spec.name != param_name:
            continue
        value = spec.default
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SchedulerValidationError(
                f"Skill {descriptor.skill_id!r} 声明了 {param_name} 却无可用的默认值"
                f"（收到 {value!r}，须为 ≥1 的整数）"
            )
        return value
    return None
