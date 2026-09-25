"""01-平台共享契约 §5 ResultEnvelope（统一结果与错误模型）。

契约要点（§5）：
- 所有 Skill / 视角 / 数据源 / 渠道的返回值必须包装为统一信封
- **禁止编造结果或静默失败**（失败显式化，架构总览 §6）
- 六种 status 全平台语义一致，UI 层按状态渲染：
  - ``ok``：正常结果
  - ``empty``：合法的空结果，**必须**携带原因说明，不得返回裸空
  - ``unavailable``：数据源不可用；必须提示「数据不可用/延迟，最后更新时间 T」
  - ``dependency_failed``：上游依赖失败；下游必须显式标注，不得用错误数据继续
  - ``validation_failed``：输入/参数校验失败；必须说明理由，不允许保存
  - ``failed``：其他执行失败；须含可查日志入口
- ``as_of``：本结果基于的数据快照时间（§8 时间锚点）

实现约定：frozen 模型 + 字段级交叉校验（model_validator）把「非 ok 必填 reason」
「empty 不得裸空」「unavailable 必须有 last_updated_at」固化为构造期不变量。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from st_agent.contracts.errors import ContractViolation

__all__ = [
    "EnvelopeStatus",
    "EvidenceRef",
    "ResultEnvelope",
    "new_evidence_id",
]

#: status=ok/empty 时允许出现在 evidence_refs 的证据类型（§3 evidence_refs 同款口径）
EvidenceKind = Literal[
    "announcement_id",
    "dataset_snapshot_id",
    "skill_run_id",
    "memory_node_id",
]


def new_evidence_id() -> str:
    """构造一个证据引用的占位 ID（引用对象由产生方回填，格式同 §1）。"""
    return f"ev_{uuid.uuid4().hex[:20]}"


class EvidenceRef(BaseModel):
    """证据引用（§5 ``evidence_refs`` 元素；§3 复用同一口径）。

    ``ref`` 存被引对象的 ID 字符串，``kind`` 声明其 §1 契约类型——
    显式 kind 使信封自描述，消费方无需注册表即可校验。
    """

    model_config = ConfigDict(frozen=True)

    kind: EvidenceKind
    ref: Annotated[str, Field(min_length=3, max_length=128)]

    @field_validator("ref")
    @classmethod
    def _ref_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ContractViolation("evidence ref 不得为空白串")
        return v


class ResultEnvelope(BaseModel):
    """统一结果信封（§5）。所有跨层返回值的唯一合法形态。"""

    model_config = ConfigDict(frozen=True)

    status: Literal[
        "ok",
        "empty",
        "unavailable",
        "failed",
        "dependency_failed",
        "validation_failed",
    ]
    data: Any | None = None
    """status=ok 时的载荷；empty 时必须为 None。"""
    reason: str | None = None
    """非 ok 时必填：人可读原因（中性措辞，渲染前须过 §6 校验）。"""
    evidence_refs: tuple[EvidenceRef, ...] = ()
    """ok/empty 时附证据引用；失败分支可为空。"""
    as_of: datetime | None = None
    """本结果基于的数据快照时间（§8；内部传输必带时区语义）。"""
    last_updated_at: datetime | None = None
    """unavailable 时的「最后更新时间 T」（数据实际截止时间）。"""
    log_ref: str | None = None
    """failed 时的可查日志入口（SkillRun / Trace 定位串）。"""

    _OK: ClassVar[str] = "ok"

    @field_validator("as_of", "last_updated_at")
    @classmethod
    def _tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ContractViolation("时间字段必须带时区语义（01 §8：内部传输带时区）")
        return v

    @model_validator(mode="after")
    def _enforce_branch_semantics(self) -> "ResultEnvelope":
        s = self.status
        if s == "ok":
            if self.data is None:
                raise ContractViolation("status=ok 时 data 不得为 None（载荷缺失请用 empty）")
        else:
            if self.data is not None:
                raise ContractViolation(f"status={s} 时 data 必须为 None（载荷只属于 ok）")
            reason = (self.reason or "").strip()
            if not reason:
                raise ContractViolation(f"status={s} 时 reason 必填（禁止裸失败/裸空，01 §5）")
        if s == "empty" and self.data is not None:
            raise ContractViolation("status=empty 时 data 必须为 None（合法的空结果不带载荷）")
        if s == "unavailable" and self.last_updated_at is None:
            raise ContractViolation(
                "status=unavailable 时必须给 last_updated_at（『最后更新时间 T』，01 §5）"
            )
        if s == "failed" and not (self.log_ref or "").strip():
            raise ContractViolation("status=failed 时必须给 log_ref（须含可查日志入口，01 §5）")
        return self

    @classmethod
    def ok(cls, data: Any, *, as_of: datetime | None = None,
           evidence_refs: tuple[EvidenceRef, ...] = ()) -> "ResultEnvelope":
        """构造正常结果信封。"""
        return cls(status="ok", data=data, as_of=as_of, evidence_refs=evidence_refs)

    @classmethod
    def empty(cls, reason: str, *, as_of: datetime | None = None,
              evidence_refs: tuple[EvidenceRef, ...] = ()) -> "ResultEnvelope":
        """构造合法空结果信封（reason 必填，如「今日全市场无退市高危信号」）。"""
        return cls(status="empty", reason=reason, as_of=as_of, evidence_refs=evidence_refs)

    @classmethod
    def unavailable(cls, reason: str, *, last_updated_at: datetime,
                    as_of: datetime | None = None) -> "ResultEnvelope":
        """构造数据源不可用信封（标注「最后更新时间 T」）。"""
        return cls(status="unavailable", reason=reason, last_updated_at=last_updated_at,
                   as_of=as_of)

    @classmethod
    def dependency_failed(cls, reason: str, *, log_ref: str | None = None) -> "ResultEnvelope":
        """构造上游依赖失败信封（下游须显式标注「依赖失败」）。"""
        return cls(status="dependency_failed", reason=reason, log_ref=log_ref)

    @classmethod
    def validation_failed(cls, reason: str) -> "ResultEnvelope":
        """构造输入/参数校验失败信封（不允许保存）。"""
        return cls(status="validation_failed", reason=reason)

    @classmethod
    def failed(cls, reason: str, *, log_ref: str) -> "ResultEnvelope":
        """构造其他执行失败信封（log_ref 指向可查日志）。"""
        return cls(status="failed", reason=reason, log_ref=log_ref)


#: 便于上层泛型处理的判别常量（§5 分支语义的机器可读副本）
EnvelopeStatus = ResultEnvelope.model_fields["status"].annotation
assert EnvelopeStatus is not None
