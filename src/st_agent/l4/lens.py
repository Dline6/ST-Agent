"""视角模型 Lens（[06 §1](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

Lens 是 Multi-Lens 的**定义层**（不是一次观点输出——那是 [`contracts.capability_types.LensOpinion`](../contracts/capability_types.py) §3）：
一个视角 = 一组 Skill（`skill_bundle`）+ 一套评判准则（`judging_criteria`）+ 一个中性名字（`name`）。

06 §1 字段表的机器可读形态（六字段 + 停用位）：

| 字段 | 说明 |
| --- | --- |
| `lens_id` | [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) `lens` 前缀标识（由本层产生，[`contracts.identifiers.LensId`](../contracts/identifiers.py)） |
| `name` | 中性功能化命名（保存时过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) `check_name`，由 `LensRoster` 执行；模型本身不硬绑词表——依赖倒置，同 `SkillDescriptor`） |
| `skill_bundle` | 该视角运行的一组 Skill（引用 `skill_id`；存在性由 `LensRoster` 经 `SkillRegistry.get` 解析） |
| `judging_criteria` | 评判准则：规则化表达，**预留两种入口**（自然语言 `natural` / 规则表达式 `rule`——06 §1 明列「配置形态属 PRD 待澄清项，实现预留两种入口」） |
| `confidence_policy` | 信心度判定规则：数据充分度 → `high`/`medium`/`low`（与 [01 §3](../../../docs/技术架构-v2/01-平台共享契约.md) `Confidence` 同枚举） |
| `kind` | `builtin`（官方预置）/ `custom`（用户自建） |
| `enabled` | 启用位：内置视角**只能停用不可删**（任务 A2），自定义视角可删——故阵容读面按 `enabled` 过滤 |

**构造期不变量**（钉在 `model_validator`，任何构造路径都走同一校验管道）：
`name`/`lens_id` 非空、`skill_bundle` 每项形态合法（`sk_` 前缀拼接规则）、
`kind` 二枚举。`skill_bundle` 允许为空（内置视角若 A1 不成立可暂留空占位，由下游回填）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.capability_types import Confidence
from st_agent.l1.skills.ids import SKILL_ID_PATTERN
from st_agent.l4.errors import LensValidationError

__all__ = [
    "BUILTIN_LENSES_COUNT",
    "ConfidencePolicy",
    "JudgingCriteria",
    "Lens",
    "LensKind",
]

LensKind = Literal["builtin", "custom"]
"""`kind`：官方预置 / 用户自建（06 §1）。"""

BUILTIN_LENSES_COUNT = 7
"""官方预置视角数（06 §1 阵容表：机会/风险/基本面/情绪/流动性/宏观/合规）。"""


class JudgingCriteria(BaseModel):
    """评判准则（06 §1）——规则化表达，预留两种配置入口。

    PRD 待澄清项（06 §1 原注）：形态是自然语言还是规则表达式尚未定，故**两种入口都建模**——
    `natural`（一段自然语言准则）与 `rule`（结构化规则表达式，此处以 JSON 形态承载，
    具体 DSL 由 `T-L4-002` 编排消费时再定）。两者至少给一个非空，否则准则缺失。
    """

    model_config = ConfigDict(frozen=True)

    natural: Annotated[str, Field(min_length=0)] = ""
    """自然语言准则（可为空串表示未填）。"""
    rule: dict[str, object] = Field(default_factory=dict)
    """规则表达式准则（结构化，JSON 形态；可为空表示未填）。"""

    @model_validator(mode="after")
    def _at_least_one_entry(self) -> "JudgingCriteria":
        if not self.natural.strip() and not self.rule:
            raise LensValidationError("judging_criteria 须至少给 natural 或 rule 之一（06 §1）")
        return self


class ConfidencePolicy(BaseModel):
    """信心度判定规则（06 §1）：数据充分度 → high/medium/low。

    落地形态是**分档阈值**：给定一个 0–1 的数据充分度，按阈值映射到三档 `Confidence`。
    阈值单调（high ≥ medium ≥ low），且 `low` 档兜底（任何充分度都有判定）。
    """

    model_config = ConfigDict(frozen=True)

    high_at: Annotated[float, Field(ge=0.0, le=1.0)] = 0.8
    """充分度 ≥ 此值 → high。"""
    medium_at: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    """充分度 ≥ 此值（且 < high_at）→ medium，否则 low。"""

    @model_validator(mode="after")
    def _monotonic(self) -> "ConfidencePolicy":
        if self.medium_at > self.high_at:
            raise LensValidationError(
                f"confidence_policy 阈值非单调：medium_at({self.medium_at}) > high_at({self.high_at})"
            )
        return self

    def evaluate(self, sufficiency: float) -> Confidence:
        """由数据充分度（0–1）判定信心度档位。"""
        if sufficiency >= self.high_at:
            return "high"
        if sufficiency >= self.medium_at:
            return "medium"
        return "low"


class Lens(BaseModel):
    """06 §1 视角定义（值对象，frozen）。"""

    model_config = ConfigDict(frozen=True, validate_default=True)

    lens_id: Annotated[str, Field(min_length=3, max_length=128)]
    """[01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) `lens` 前缀标识。"""
    name: Annotated[str, Field(min_length=1, max_length=64)]
    """中性功能化命名（保存时过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) `check_name`）。"""
    description: Annotated[str, Field(min_length=1)]
    """该视角关注什么、何时产出观点（自定义视角的 `description` 亦须过 §6 第一人称校验）。"""
    skill_bundle: tuple[Annotated[str, Field(min_length=3, max_length=128)], ...] = ()
    """运行的一组 skill_id（形态须为 `sk_<注册名>_v<主>.<次>`；可为空，由下游回填）。"""
    judging_criteria: JudgingCriteria
    """评判准则（二入口）。"""
    confidence_policy: ConfidencePolicy = Field(default_factory=ConfidencePolicy)
    """信心度判定规则。"""
    kind: LensKind
    """官方预置 / 用户自建。"""
    enabled: bool = True
    """启用位：内置视角只能停用不可删（任务 A2）。"""

    @model_validator(mode="after")
    def _skill_bundle_shape(self) -> "Lens":
        for sid in self.skill_bundle:
            if not SKILL_ID_PATTERN.match(sid):
                raise LensValidationError(
                    f"skill_bundle 含非法 skill_id {sid!r}"
                    "（须为 sk_<注册名>_v<主>.<次>，[01 §1]）"
                )
        return self
