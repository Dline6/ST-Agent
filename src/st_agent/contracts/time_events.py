"""01-平台共享契约 §8 数据快照与时间口径 + §11 事件清单（上行通知）。

§8 契约要点：
- 所有分析性结论基于 ``dataset_snapshot_id`` 标识的数据快照，结论必须携带 ``as_of``
- 数据源延迟/缺失走 ResultEnvelope ``unavailable`` 分支并标注「最后更新时间」；
  禁止用旧数据冒充新数据
- 时间统一使用**用户本地时区**存储与展示，内部传输带时区语义

``as_of`` 的取值与「最后更新时间」的登记机制由 L0 数据缓存同步状态承载
（数据库设计 §05 sync_state：逐表水位 + last_success_at + 陈旧判据）；
本模块交付的是**类型口径**：DataAnchor（快照锚点）与 StalenessVerdict
（新鲜度判定载体），并按 02-L0 §5 的分工登记契约与 L0 的边界。

§11 契约要点：下层向上层只通过事件通信；核心事件 8 组；
所有事件必须携带关联 ``trace_id`` / ``change_id``（如适用）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.errors import ContractViolation

__all__ = [
    "CORE_EVENTS",
    "DataAnchor",
    "PlatformEvent",
    "StalenessVerdict",
]

# ───────────────────────── §8 时间锚点 ─────────────────────────


def _require_tz(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ContractViolation("时间必须带时区语义（01 §8：内部传输带时区）")
    return v


class DataAnchor(BaseModel):
    """数据快照锚点（§8）：分析性结论的必备组成。

    ``dataset_snapshot_id`` 锚定于 L0 ``sync_state`` 的各任务水位与成功时间组合
    （数据库设计 §05——不另行维护快照登记表）；``as_of`` 按数据域取该域
    数据的实际截止时间。
    """

    model_config = ConfigDict(frozen=True)

    dataset_snapshot_id: Annotated[str, Field(min_length=3, max_length=128)]
    """§1 dataset_snapshot_id 字符串形态（数据一致性锚点）。"""
    as_of: datetime
    """本结果基于的数据快照时间（该数据域的实际截止时间）。"""
    domain: Literal["kline", "financial", "company_report", "sector", "macro", "announcement"]
    """数据域（对齐 BaoStock 五域 + 公告域；陈旧判据逐域定义于数据库设计 §05）。"""
    validator_name: Annotated[str, Field(min_length=1)] = "tz_check"

    @model_validator(mode="after")
    def _as_of_tz_aware(self) -> "DataAnchor":
        _require_tz(self.as_of)
        return self

    def is_stale(self, now: datetime, max_age: timedelta) -> bool:
        """按「预期新鲜度」判陈旧。判据表在数据库设计 §05；此处为通用超龄判定。

        L0 落地时应以 sync_state 自检 SQL 为准（逐表水位），本方法仅用于
        无 sync_state 可查的内存路径。
        """
        return (now - self.as_of) > max_age


class StalenessVerdict(BaseModel):
    """新鲜度判定载体（§8「最后更新时间」标注的类型形态）。

    对应 ResultEnvelope ``unavailable`` 分支的 last_updated_at + 提示文案语义。
    """

    model_config = ConfigDict(frozen=True)

    stale: bool
    last_updated_at: datetime
    """「最后更新时间 T」（数据实际截止时间；unavailable 分支必带）。"""
    detail: Annotated[str, Field(min_length=1)]
    """中性提示文案（如「行情数据停留于 T，今日同步未完成」）。"""

    @model_validator(mode="after")
    def _last_updated_tz_aware(self) -> "StalenessVerdict":
        _require_tz(self.last_updated_at)
        return self


# ───────────────────────── §11 事件清单 ─────────────────────────

CORE_EVENTS: tuple[tuple[str, str, str], ...] = (
    ("SkillRunCompleted", "L1", "L3（对话渲染）、L6（反思数据池）"),
    ("SkillRunFailed", "L1", "L3（对话渲染）、L6（反思数据池）"),
    ("MemoryConflictDetected", "L2", "L3（发起冲突裁决对话）"),
    ("SignalEmitted", "L1/L4", "L5（触达编排）"),
    ("DeliverySettled", "L5", "L5 升级链自身、L6"),
    ("FeedbackRecorded", "交互层", "L6"),
    ("NetworkRequestLogged", "L0 网关", "设置-网络活动面板"),
    ("BehaviorViolation", "L1 沙箱", "警示 UI、L6"),
    ("ChangeApplied", "配置注册表", "L6 变更历史、L5 告知通道"),
    ("ChangeRolledBack", "配置注册表", "L6 变更历史、L5 告知通道"),
)
"""§11 核心事件清单的机器可读副本：(事件名, 发布方, 订阅方)。

DeliverySettled 含未读超时语义；共 10 个事件名（§11 表 8 行，
其中 3 行为成对枚举拆分）。§11 增删事件时同步此表。
"""


class PlatformEvent(BaseModel):
    """跨层上行事件的统一信封（§11）。

    下层向上层只通过事件通信（依赖规则，架构总览 §3.3）；
    所有事件必须携带关联 ``trace_id`` / ``change_id``（如适用）。
    """

    model_config = ConfigDict(frozen=True)

    event: Literal[
        "SkillRunCompleted", "SkillRunFailed",
        "MemoryConflictDetected",
        "SignalEmitted",
        "DeliverySettled",
        "FeedbackRecorded",
        "NetworkRequestLogged",
        "BehaviorViolation",
        "ChangeApplied", "ChangeRolledBack",
    ]
    """事件名（§11 核心事件清单成员）。"""
    payload: dict[str, object] = {}
    """事件负载（结构由发布层定义；跨层注册新结构先改 01 契约）。"""
    trace_id: str | None = None
    """关联推理链（如适用；SkillRun/Signal/Behavior 类必带）。"""
    change_id: str | None = None
    """关联配置变更（如适用；Change 类必带）。"""
    occurred_at: datetime
    """事件发生时间（带时区，§8 时间口径）。"""

    @model_validator(mode="after")
    def _correlation_ids(self) -> "PlatformEvent":
        _require_tz(self.occurred_at)
        change_events = {"ChangeApplied", "ChangeRolledBack"}
        traced_events = {"SkillRunCompleted", "SkillRunFailed", "SignalEmitted",
                         "DeliverySettled", "BehaviorViolation", "MemoryConflictDetected"}
        if self.event in change_events and not (self.change_id or "").strip():
            raise ContractViolation(f"{self.event} 必须携带关联 change_id（01 §11）")
        if self.event in traced_events and not (self.trace_id or "").strip():
            raise ContractViolation(f"{self.event} 必须携带关联 trace_id（01 §11）")
        return self
