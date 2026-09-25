"""SkillRun 留痕模型（03 §1.2 步骤 5；GWT-6 推理链可查的数据形态）。

``SkillRun`` 为 frozen 模型：一次执行的输入快照、输出信封、耗时、
依赖链与错误。落盘形态为 ``execution_log`` 分区
``skill-run/<skill_run_id>.json``（A5）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner.errors import RunnerValidationError

__all__ = [
    "RUN_PREFIX",
    "SkillRun",
    "checked_skill_run",
]

RUN_PREFIX = "skill-run/"
"""``execution_log`` 分区内 SkillRun 记录的目录前缀。"""


class SkillRun(BaseModel):
    """一次 Skill 执行的留痕（GWT-6：输入快照 + 输出 + 耗时 + 依赖链）。"""

    model_config = ConfigDict(frozen=True)

    skill_run_id: Annotated[str, Field(min_length=3, max_length=128)]
    skill_id: Annotated[str, Field(min_length=3, max_length=128)]
    trace_id: Annotated[str, Field(min_length=3, max_length=128)]
    """关联推理链（01 §4：记录挂到 ``trace_id``）。"""
    params: dict[str, Any] = {}
    """执行时实际使用的完整参数（输入快照）。"""
    upstream: tuple[str, ...] = ()
    """本次实际执行的依赖 skill_id 链（拓扑序）。"""
    envelope: ResultEnvelope
    """本次执行的输出信封（成功或任一失败分支）。"""
    duration_ms: int = Field(ge=0)
    started_at: datetime


def checked_skill_run(**fields: Any) -> SkillRun:
    """构造 SkillRun（非法 → ``RunnerValidationError``，不抛裸异常）。"""
    try:
        return SkillRun(**fields)
    except ValidationError as exc:
        raise RunnerValidationError(f"SkillRun 记录非法：{exc}") from exc
