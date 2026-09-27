"""01 §10 权限模型的中性承载：形态、措辞与展示。

「能力（Skill / MCP Server / 导入物）在安装/挂载时声明权限，用户逐项批准；
权限申请展示必须说明『这个能力想做什么』」（01 §10）。

本模块住**层间中立**处：批准态的形态（``PermissionApproval``）与展示措辞
（``describe_permission``）由 L1 的 Skill 批准账本与 MCP 批准账本**共用**，
不出第二份复制（与 [D-009] 把判定件收在 ``contracts`` 同一取向）。

「批准态存在哪、怎么落盘」不属本模块——那是各账本自己的事
（共用簿记见 :mod:`st_agent.l1.permission_book`）。

.. note::
   本模块**不做**声明语法校验——那是
   :func:`st_agent.contracts.registry_types.validate_permissions` 的职责，
   由各注册入口在 ``declare`` 之前调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.registry_types import parse_permission

__all__ = [
    "DECISION_LABELS",
    "PERMISSION_KINDS",
    "PermissionApproval",
    "PermissionDecision",
    "describe_permission",
]

PermissionDecision = Literal["pending", "approved", "rejected"]
"""一条权限的批准决定（01 §10 逐项批准的三态）。"""

PERMISSION_KINDS: dict[str, str] = {
    "local_read": "读取声明范围内的本地文件",
    "net_access": "访问声明的远程主机",
    "exec_command": "在本机执行命令（最高风险）",
}
"""01 §10 三项权限的中性说明（「这个能力想做什么」的措辞来源）。"""

DECISION_LABELS: dict[str, str] = {
    "pending": "待批准",
    "approved": "已批准",
    "rejected": "已拒绝",
}
"""批准状态的中文展示（权限申请展示用）。"""


def describe_permission(declaration: str) -> str:
    """把一条权限声明转成面向用户的中性说明（01 §10 展示要求）。"""
    try:
        action, scope = parse_permission(declaration)
    except Exception as exc:
        raise ContractViolation(f"权限声明非法：{declaration!r}（{exc}）") from exc
    what = PERMISSION_KINDS.get(action, action)
    if action == "exec_command":
        return f"{declaration} —— {what}"
    return f"{declaration} —— {what}（范围 {scope}）"


class PermissionApproval(BaseModel):
    """一条权限声明的批准状态（01 §10 逐项批准）。

    校验失败抛 ``ContractViolation``；经 pydantic 构造时会被包装为
    ``ValidationError``（异常链中保留本类型），故账本侧按 ``ValidationError``
    捕获即可（见 :meth:`st_agent.l1.permission_book.PermissionBook._parse`）。
    """

    model_config = ConfigDict(frozen=True)

    permission: str = Field(min_length=1)
    decision: PermissionDecision
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def _shape(self) -> "PermissionApproval":
        if self.decision == "pending" and self.decided_at is not None:
            raise ContractViolation("pending 权限不得带 decided_at")
        if self.decision != "pending" and self.decided_at is None:
            raise ContractViolation("已决权限必须带 decided_at（逐项批准留痕）")
        if self.decided_at is not None and self.decided_at.tzinfo is None:
            raise ContractViolation("decided_at 必须带时区语义（01 §8）")
        return self
