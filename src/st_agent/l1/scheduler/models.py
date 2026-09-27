"""调度判定与运行状态的数据形态（T-L1-005.1；03 §6）。

- ``ScheduleTarget``：一个可触发的目标。两类来源——① 已注册工作流的
  ``schedule``（03 §3.1 的 ``WorkflowSchedule``）；② 描述体声明了
  ``frequency_minutes`` 参数的 Skill（03 §6「各 Skill 的频率参数」）。
  两类统一收进 ``WorkflowSchedule`` 的语义里判到期，``origin`` 显式标注来源，
  免得「换了来源看起来像同一件事」。
- ``SchedulerState``：逐目标的运行状态（落 ``config`` 分区
  ``scheduler/<target_id>.json``——可变状态，与 ``skill-registry`` / 工作流同构；
  ``execution_log`` 是 append-only 审计，不承担该面）。

时钟一律要求**带时区**（01 §8 时间口径）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l1.scheduler.errors import SchedulerValidationError
from st_agent.l1.workflow.models import WorkflowSchedule

__all__ = [
    "FREQUENCY_PARAM",
    "RUN_STATUSES",
    "STATE_PREFIX",
    "PermissionSource",
    "RunStatus",
    "ScheduleOrigin",
    "ScheduleTarget",
    "ScheduledRun",
    "SchedulerState",
    "TargetKind",
    "check_aware",
    "check_target_id",
]

STATE_PREFIX = "scheduler/"
"""``config`` 分区内运行状态的目录前缀。"""

FREQUENCY_PARAM = "frequency_minutes"
"""Skill 侧频率参数的参数名（03 §6「各 Skill 的频率参数」；唯一载体为 ``sk_stock_watch``）。"""

TARGET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
"""目标标识形态（同时是 ``config`` 分区内的安全相对路径段）。"""

TargetKind = Literal["workflow", "skill"]
ScheduleOrigin = Literal["workflow-schedule", "skill-frequency"]
RunStatus = Literal["ok", "failed", "gap", "skipped", "deferred"]

RUN_STATUSES: tuple[str, ...] = ("ok", "failed", "gap", "skipped", "deferred")
"""运行状态的取值域——``ok`` 成功 / ``failed`` 执行失败 / ``gap`` 依赖缺口未执行 /
``skipped`` 被禁用或按配置跳过 / ``deferred`` 离线不可执行、待补。"""


def check_target_id(value: str) -> str:
    """校验目标标识（非法 → ``SchedulerValidationError``）。"""
    if not isinstance(value, str) or not TARGET_ID_PATTERN.match(value):
        raise SchedulerValidationError(
            f"非法调度目标标识 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，"
            "≤128 字符——该标识同时用作状态文件名）"
        )
    return value


def check_aware(moment: datetime, *, label: str = "时刻") -> datetime:
    """校验时刻带时区（01 §8；非法 → ``SchedulerValidationError``）。"""
    if not isinstance(moment, datetime) or moment.tzinfo is None \
            or moment.utcoffset() is None:
        raise SchedulerValidationError(f"{label}必须带时区（01 §8 时间口径）")
    return moment


class ScheduleTarget(BaseModel):
    """一个可触发的调度目标（03 §6）。

    :param target_id: 工作流的 ``flow_id`` 或 Skill 的 ``skill_id``
    :param kind: 目标类型（工作流 / Skill）
    :param origin: 频率来源（工作流自带的 ``schedule`` / Skill 的频率参数）
    :param schedule: 统一的触发方式语义；``kind="skill"`` 时恒为
        ``mode="interval"``（由 ``frequency_minutes`` 折算）
    """

    model_config = ConfigDict(frozen=True)

    target_id: Annotated[str, Field(min_length=1, max_length=128)]
    kind: TargetKind
    origin: ScheduleOrigin
    schedule: WorkflowSchedule

    @model_validator(mode="after")
    def _shape(self) -> "ScheduleTarget":
        check_target_id(self.target_id)
        if self.kind == "workflow" and self.origin != "workflow-schedule":
            raise SchedulerValidationError(
                "kind=workflow 的 origin 必须为 workflow-schedule"
            )
        if self.kind == "skill":
            if self.origin != "skill-frequency":
                raise SchedulerValidationError(
                    "kind=skill 的 origin 必须为 skill-frequency"
                )
            if self.schedule.mode != "interval":
                raise SchedulerValidationError(
                    "kind=skill 的触发方式恒为 interval（由 frequency_minutes 折算）"
                )
        return self


class SchedulerState(BaseModel):
    """逐目标的运行状态（落 ``config`` 分区；T-L1-005.1）。

    :param last_run_at: 上次**已记账**的运行的时刻（成功与失败都推进——
        「跑过」与「跑成」是两件事；判到期只看前者）
    :param last_success_at: 上次**跑成**的时刻（只在 ``status=ok`` 时推进）——
        「最近一次成功是什么时候」是调度健康度的真实信号
    :param last_status: 上次运行的状态（见 ``RUN_STATUSES``）
    :param last_detail: 上次运行的说明（中性措辞；失败原因 / 缺口点名 / 跳过理由）
    :param last_skill_run_id: 上次执行的 ``skill_run_id``（``ok`` / ``failed`` 时有）
    :param pending: 离线期间**错过的到期点**（升序去重；`T-L1-005.3`）——网络恢复后
        由 ``Scheduler.catch_up`` 逐目标处置（补跑或跳过）并清空
    :param updated_at: 状态最后写入的时刻；**从未运行过的目标没有状态**（本字段为 None）
    """

    model_config = ConfigDict(frozen=True)

    target_id: Annotated[str, Field(min_length=1, max_length=128)]
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_status: RunStatus | None = None
    last_detail: str = ""
    last_skill_run_id: str | None = None
    pending: tuple[datetime, ...] = ()
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _shape(self) -> "SchedulerState":
        check_target_id(self.target_id)
        for label, moment in (("上次运行时刻", self.last_run_at),
                              ("上次成功时刻", self.last_success_at),
                              ("状态更新时间", self.updated_at)):
            if moment is not None:
                check_aware(moment, label=label)
        for moment in self.pending:
            check_aware(moment, label="待补到期点")
        if self.last_status is None and (
                self.last_run_at is not None or self.last_skill_run_id is not None):
            raise SchedulerValidationError("有上次运行痕迹却无状态")
        if self.last_status is not None and self.updated_at is None:
            raise SchedulerValidationError("有状态却无更新时间")
        return self


class ScheduledRun(BaseModel):
    """一次调度触发的执行结果（``Scheduler.tick`` / ``.trigger`` 的返回项）。

    :param skill_id: 实际执行的 Skill——工作流目标是其复合 Skill（``sk_<名>_v<版>``）；
        未解析出（依赖缺口 / 已禁用）时为 ``None``
    :param status: 记账状态，与 ``SchedulerState.last_status`` 同域
    :param detail: 中性措辞的说明（失败原因 / 缺口点名 / 跳过理由）
    :param envelope: 执行信封（``ok`` / ``empty`` / 各失败分支原样透出）；
        未执行时为 ``None``
    :param trace: 本次执行的推理链（01 §4；**由调度器创建**）。全仓没有 Trace
        的落盘点——执行链只经返回值交接，调度器若不带回即永久丢失，可解释性
        （铁律 5）就断在这里
    :param trace_id: 本次执行的推理链标识（``trace.trace_id`` 的字符串形态，便于展示）
    """

    model_config = ConfigDict(frozen=True)

    target_id: Annotated[str, Field(min_length=1, max_length=128)]
    skill_id: str | None = None
    status: RunStatus
    detail: str = ""
    skill_run_id: str | None = None
    envelope: ResultEnvelope | None = None
    trace: Trace | None = None
    trace_id: str | None = None
    ran_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> "ScheduledRun":
        check_target_id(self.target_id)
        check_aware(self.ran_at, label="运行时刻")
        return self


@runtime_checkable
class PermissionSource(Protocol):
    """调度执行的权限来源（鸭子类型；01 §10）。

    「能力在安装/挂载时声明权限，用户逐项批准」——Skill 侧的批准记录尚无落点
    （见 L1 遗留册 `D1`），故本件按**注入**取批准集，不臆造持久化面：调度器只
    负责「要哪些、从哪要」，批不批由注入方决定；来源给空集即一律 fail-closed
    （沙箱拦下，不静默放行）。
    """

    def approved_for(self, target: ScheduleTarget) -> tuple[str, ...]:
        """该目标本次执行已获批准的权限声明集（无 → 空元组）。"""
        ...
