"""输出复用数据形态（03 §1.3；01 §5 信封 / §8 时间锚点）。

- ``ReusableOutput``：可复用**登记索引**——只记定位与时间锚点，
  载荷单一来源于 ``SkillRunner`` 已落的 ``SkillRun`` 记录
  （``execution_log`` 分区 ``skill-run/<skill_run_id>.json``），不复制双份（A1）
- ``ReuseView``：一次复用查询的完整产出——登记索引 + 原信封 + 新鲜度判定。
  **只给判定不给建议**：§1.3「引用过期快照时上层自行决定是否重算」，
  故判定载体是 ``StalenessVerdict``（其 ``stale`` 字段即上层自决的输入）
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import StalenessVerdict
from st_agent.l1.reuse.errors import ReuseValidationError

__all__ = [
    "OUTPUT_PREFIX",
    "REUSABLE_STATUSES",
    "ReusableOutput",
    "ReuseView",
]

OUTPUT_PREFIX = "skill-output/"
"""``execution_log`` 分区内可复用登记索引的目录前缀。"""

REUSABLE_STATUSES: tuple[str, ...] = ("ok", "empty")
"""可登记为复用结果的信封分支（A2）。

``empty`` 是合法结论（如「今日全市场无退市高危信号」），且按 §1.2 不算
依赖失败，故一并登记；其余分支是失败面，登记会把失败当结果传播（禁止）。
"""


def _require_tz(value: datetime | None, field: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ReuseValidationError(f"{field} 必须带时区语义（01 §8）")
    return value


class ReusableOutput(BaseModel):
    """一条可复用输出登记（索引形态；载荷见 ``SkillRun`` 记录）。"""

    model_config = ConfigDict(frozen=True)

    skill_run_id: Annotated[str, Field(min_length=3, max_length=128)]
    skill_id: Annotated[str, Field(min_length=3, max_length=128)]
    status: Literal["ok", "empty"]
    """产生该输出的信封分支（只可能是可复用分支）。"""
    as_of: datetime | None = None
    """输出所基于的数据快照时间（§8）；执行器未给则为 None。"""
    registered_at: datetime
    """登记时刻（本地时区）。"""

    @model_validator(mode="after")
    def _shape(self) -> "ReusableOutput":
        if not self.skill_run_id.strip() or not self.skill_id.strip():
            raise ReuseValidationError("skill_run_id / skill_id 不得为空白")
        _require_tz(self.as_of, "as_of")
        _require_tz(self.registered_at, "registered_at")
        return self


class ReuseView(BaseModel):
    """一次复用查询的产出（03 §1.3）。

    ``verdict`` 是**判定**而非建议：上层依据 ``verdict.stale`` 与 ``detail``
    自行决定继续用还是重算，运行时不做此决定。
    """

    model_config = ConfigDict(frozen=True)

    output: ReusableOutput
    envelope: ResultEnvelope
    """被引用的原始输出信封（取自 ``SkillRun`` 记录）。"""
    verdict: StalenessVerdict
    """该引用相对当前数据面的新鲜度判定。"""
