"""01-平台共享契约 §4 Trace（推理链）。

契约要点（§4）：
- 每条面向用户的结论必须可展开为完整 Trace
- 结构为步骤列表，每步可下钻：``step_type`` 五枚举（data_fetch / skill_run /
  memory_read / lens_opinion / aggregation）、``ref``（对应 §1 ID）、
  ``input_digest``、``output_digest``、``duration``、``timestamp``
- ``conclusion_ref``：最终结论的载体（消息 / 分歧图 / 信号）
- **Trace 不可事后修改（追加可以）**；断网、降级路径同样记入 Trace

实现约定（append-only 由 ``append_step`` 唯一入口 + ``TraceError`` 防改写保证）：
- 步骤以 frozen 模型承载；Trace 容器内部用 tuple 存储，外部拿不到可变引用
- digest 由实现方以 sha256 计算（本地可复算，不依赖外部工具）
- 降级路径：``append_degraded`` 显式登记「本步在降级模式下执行」，
  保证降级信息进 Trace 而非静默缺失（失败显式化）
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import PlatformId, TraceId

__all__ = [
    "STEP_TYPES",
    "ConclusionRef",
    "Trace",
    "TraceError",
    "TraceStep",
    "digest_of",
]

StepType = Literal["data_fetch", "skill_run", "memory_read", "lens_opinion", "aggregation"]
STEP_TYPES: tuple[str, ...] = ("data_fetch", "skill_run", "memory_read", "lens_opinion",
                               "aggregation")
"""契约 §4 的五种 step_type（机器可读副本）。"""


class TraceError(ContractViolation):
    """试图改写既有 Trace 步骤等契约违规。"""


def digest_of(payload: Any) -> str:
    """计算输入/输出摘要（sha256，本地可复算）。

    任意 JSON 可序列化对象 → 规范化 JSON → 摘要；不可序列化时回退 repr。
    """
    try:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        normalized = repr(payload)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class TraceStep(BaseModel):
    """推理链单步（§4 steps[] 元素）。frozen：创建后不可改。"""

    model_config = ConfigDict(frozen=True)

    step_type: StepType
    ref: Annotated[str, Field(min_length=3, max_length=128)]
    """本步对应的 §1 ID（如 skill_run_id / dataset_snapshot_id / lens_opinion 锚点）。"""
    input_digest: str
    output_digest: str
    duration_ms: int = Field(ge=0)
    timestamp: datetime
    degraded: bool = False
    """断网/降级路径显式标记（§4：降级路径同样记入 Trace）。"""
    note: str | None = None
    """降级说明或补充信息（中性措辞）。"""

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ContractViolation("timestamp 必须带时区语义（01 §8）")
        return v


class ConclusionRef(BaseModel):
    """最终结论的载体引用（§4 conclusion_ref）。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["message", "divergence_map", "signal"]
    """消息 / 分歧图 / 信号——契约列出的三类结论载体。"""
    ref: Annotated[str, Field(min_length=3, max_length=128)]


class Trace(BaseModel):
    """推理链容器（§4）。append-only：``append_step`` 是唯一写入口。"""

    model_config = ConfigDict(frozen=True)

    trace_id: TraceId
    steps: tuple[TraceStep, ...] = ()
    conclusion_ref: ConclusionRef | None = None
    closed: bool = False
    """conclusion_ref 登记后即封链；封链后禁止再追加（结论已定，链已完整）。"""

    def append_step(self, step: TraceStep) -> "Trace":
        """追加一步，返回新 Trace（frozen 模型的函数式更新）。

        契约「追加可以」；封链后追加 → TraceError。
        """
        if self.closed:
            raise TraceError(
                f"Trace {self.trace_id.value} 已封链（conclusion_ref 已登记），禁止追加"
            )
        return self.model_copy(update={"steps": (*self.steps, step)})

    def append_degraded(self, step: TraceStep, note: str) -> "Trace":
        """降级路径专用入口：强制 degraded=True + 中性说明（§4 降级要求）。"""
        if step.degraded is not True:
            step = step.model_copy(update={"degraded": True, "note": note})
        return self.append_step(step)

    def conclude(self, conclusion_ref: ConclusionRef) -> "Trace":
        """登记结论载体并封链。重复 conclude → TraceError。"""
        if self.closed:
            raise TraceError(
                f"Trace {self.trace_id.value} 已封链，禁止重复 conclude"
            )
        return self.model_copy(update={"conclusion_ref": conclusion_ref, "closed": True})

    def replay(self) -> list[tuple[str, str, str, str]]:
        """沿 steps 回放推理链：[(step_type, ref, input_digest, output_digest)]。

        GWT-3「可沿 Trace 还原完整推理链」的机器入口。
        """
        return [(s.step_type, s.ref, s.input_digest, s.output_digest) for s in self.steps]
