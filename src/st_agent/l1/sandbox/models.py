"""执行沙箱数据形态（03 §1.5；01 §10 权限 / §11 事件）。

- ``GuardVerdict``：一次越界核对的结论（放行 / 拦截 + 原因 + 警示 + 事件）
- ``ViolationRecord``：越界留痕（落 ``execution_log`` 分区，供警示 UI 与
  L6 消费）。按 A7，只含 ``skill_id`` / 越界类别 / 时间 / 关联 trace，
  **不含**被访问的资源串（路径 / 主机 / 命令），事件载荷同款最小口径。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.time_events import PlatformEvent
from st_agent.l1.sandbox.errors import SandboxValidationError

__all__ = [
    "DISABLED_PREFIX",
    "VIOLATION_PREFIX",
    "GuardVerdict",
    "ViolationKind",
    "ViolationRecord",
    "check_violation_id",
]

VIOLATION_PREFIX = "sandbox-violation/"
"""``execution_log`` 分区内越界留痕的目录前缀。"""

DISABLED_PREFIX = "sandbox-disabled/"
"""``config`` 分区内「已禁用 Skill」标记的目录前缀（03 §1.5 禁用选项）。"""

ViolationKind = Literal["local_read", "net_access", "exec_command"]
"""越界类别（与 §10 三项权限同名）。"""


def check_violation_id(value: str) -> str:
    """校验越界留痕标识形态（非法 → ``SandboxValidationError``）。"""
    if not isinstance(value, str) or not value.startswith("viol_") or len(value) > 128:
        raise SandboxValidationError(f"非法越界留痕标识 {value!r}（须为 viol_ 前缀）")
    return value


def new_violation_id() -> str:
    """生成一个越界留痕标识。"""
    return f"viol_{uuid.uuid4().hex[:20]}"


class ViolationRecord(BaseModel):
    """一次越界拦截的留痕（警示 UI / L6 数据源；A7 最小口径）。"""

    model_config = ConfigDict(frozen=True)

    violation_id: str
    skill_id: str
    kind: ViolationKind
    warning: str = Field(min_length=1)
    """中性警示文案（固定语，不含被访问资源串）。"""
    occurred_at: datetime
    trace_id: str
    """关联推理链（01 §11：BehaviorViolation 必带 trace_id）。"""

    @model_validator(mode="after")
    def _shape(self) -> "ViolationRecord":
        check_violation_id(self.violation_id)
        if self.occurred_at.tzinfo is None:
            raise SandboxValidationError("occurred_at 必须带时区语义（01 §8）")
        if not self.trace_id.strip():
            raise SandboxValidationError("trace_id 不得为空（01 §11）")
        return self


class GuardVerdict(BaseModel):
    """一次越界核对的结论。

    ``allowed=True`` 时其余字段为空；``allowed=False`` 时给出原因、警示
    文案与已组装的 ``BehaviorViolation`` 事件（供上层直接投递/展示）。
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    kind: ViolationKind | None = None
    reason: str = ""
    """拒绝原因（人可读、中性措辞；如「未声明 local_read 范围」）。"""
    warning: str = ""
    """警示文案（拒绝时必填；含「这个 Skill 行为异常」与禁用指引）。"""
    event: PlatformEvent | None = None
    """拒绝对应的 ``BehaviorViolation`` 事件（01 §11）。"""
    violation_id: str | None = None

    @model_validator(mode="after")
    def _deny_has_details(self) -> "GuardVerdict":
        if self.allowed:
            return self
        if not self.reason.strip() or not self.warning.strip():
            raise SandboxValidationError("拦截结论必须给出 reason 与 warning")
        if self.kind is None or self.event is None:
            raise SandboxValidationError("拦截结论必须给出 kind 与 BehaviorViolation 事件")
        return self
