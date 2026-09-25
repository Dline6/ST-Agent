"""01-平台共享契约 §2 SkillDescriptor / §3 LensOpinion。

§2 契约要点：每个 Skill（官方 / 自建 / MCP 映射 / 导入）必须具备完整描述，
作为 L1 注册、L3 调度、生态分享的共同基础。11 组字段全部必填，
``name`` 过 §6 中性化校验（本模块通过 ``name_neutrality_check`` 回调注入，
避免契约包对具体词表的硬依赖——校验器实例由上层传入）。

§3 契约要点：LensOpinion 是 Multi-Lens 中每个视角的**唯一合法输出形态**
（禁止「角色台词」）：stance 四枚举、key_reasons 陈述式无第一人称、
evidence_refs 只引用 §1 合法 ID 类型、confidence 三枚举。
"""

from __future__ import annotations

from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.errors import ContractViolation

__all__ = [
    "LensOpinion",
    "ParameterSpec",
    "Provenance",
    "SkillDescriptor",
]

# ───────────────────────── §2 SkillDescriptor ─────────────────────────

SkillSource = Literal["official", "user-built", "mcp-mapped", "imported"]
OfflineLevel = Literal["full", "degraded", "none"]
VersionPolicy = Literal["follow-latest", "locked"]


class ParameterSpec(BaseModel):
    """§2 parameters 列表项：名称、类型、默认值、取值范围、说明。

    供面板表单（L1 Studio）与对话配置（L3 ConfigDraft）共用——
    即 10-platform-capabilities 契约 6「双通道」的参数承载单元。
    """

    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=64)]
    type: Literal["string", "number", "integer", "boolean", "enum"]
    default: str | float | int | bool | None = None
    choices: tuple[str, ...] = ()
    """type=enum 时的取值集合。"""
    min_value: float | None = None
    max_value: float | None = None
    description: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _check_bounds_and_choices(self) -> "ParameterSpec":
        if self.type == "enum" and not self.choices:
            raise ContractViolation(f"参数 {self.name!r} type=enum 时 choices 不得为空")
        if self.type != "enum" and self.choices:
            raise ContractViolation(f"参数 {self.name!r} 仅 type=enum 可带 choices")
        if self.type in ("number", "integer"):
            if self.min_value is not None and self.max_value is not None \
                    and self.min_value > self.max_value:
                raise ContractViolation(f"参数 {self.name!r} 取值范围 min>max")
            if self.default is not None and self.min_value is not None \
                    and float(self.default) < self.min_value:
                raise ContractViolation(f"参数 {self.name!r} default 低于取值下限")
            if self.default is not None and self.max_value is not None \
                    and float(self.default) > self.max_value:
                raise ContractViolation(f"参数 {self.name!r} default 高于取值上限")
        if self.default is not None and self.type == "enum" \
                and str(self.default) not in self.choices:
            raise ContractViolation(f"参数 {self.name!r} default 不在 choices 内")
        return self


class Provenance(BaseModel):
    """§2 provenance：来源追溯（导入类必填分享者、时间、校验和；09 §5）。"""

    model_config = ConfigDict(frozen=True)

    sharer: str | None = None
    """分享者标识（导入类必填）。"""
    imported_at: str | None = None
    """导入时间（ISO 8601 带时区；导入类必填）。"""
    checksum: str | None = None
    """校验和（导入类必填；sha256 十六进制 64 位）。"""
    origin_chain: tuple[str, ...] = ()
    """多次转手的出处链（09 §5 导入历史）。"""

    @model_validator(mode="after")
    def _imported_required_fields(self) -> "Provenance":
        # 完整性在 SkillDescriptor 层判（那里知道 source）；此处只做形状自检
        if self.checksum is not None and len(self.checksum) != 64:
            raise ContractViolation("checksum 须为 64 位十六进制（sha256）")
        return self


class SkillDescriptor(BaseModel):
    """§2 Skill 元数据——L1 注册 / L3 调度 / 生态分享的共同基础。"""

    model_config = ConfigDict(frozen=True, validate_default=True)

    skill_id: Annotated[str, Field(min_length=3, max_length=128)]
    """全局唯一，含版本语义（§1；``sk_`` 前缀 ID 或带版本后缀的注册名）。"""
    name: Annotated[str, Field(min_length=1, max_length=64)]
    """中性功能化命名（构造时经 ``name_neutrality_check`` 校验）。"""
    description: Annotated[str, Field(min_length=1)]
    """做什么的、何时该被调用（供意图匹配使用）。"""
    input_schema: dict[str, object]
    """输入契约（类型化，供连线校验与依赖解析）。"""
    output_schema: dict[str, object]
    """输出契约（同上）。"""
    parameters: tuple[ParameterSpec, ...] = ()
    """可配参数列表（可为空，但字段必须显式给出）。"""
    dependencies: tuple[str, ...] = ()
    """依赖的其他 skill_id 列表（可为空）。"""
    source: SkillSource
    provenance: Provenance
    permissions: tuple[str, ...] = ()
    """权限声明列表（§10 语法，由 st_agent.contracts.permissions 校验函数复核）。"""
    offline_level: OfflineLevel
    version_policy: VersionPolicy

    @model_validator(mode="after")
    def _imported_provenance_required(self) -> "SkillDescriptor":
        if self.source == "imported":
            missing = [f for f in ("sharer", "imported_at", "checksum")
                       if getattr(self.provenance, f) is None]
            if missing:
                raise ContractViolation(
                    f"source=imported 时 provenance 缺必填字段: {missing}（09 §5）"
                )
        return self


def make_skill_descriptor_validator(
    name_neutrality_check: Callable[[str], bool],
) -> Callable[[SkillDescriptor], SkillDescriptor]:
    """把 §6 命名校验注入 SkillDescriptor 构造（依赖倒置：契约包不依赖词表实现）。

    用法：``SkillDescriptor.model_validator 注册器`` 由上层组装；
    本仓库内的便捷入口见 tests（传 NeutralityGuard.check_name）。
    """
    def _validate(desc: SkillDescriptor) -> SkillDescriptor:
        if not name_neutrality_check(desc.name):
            raise ContractViolation(f"Skill 命名未过中性化校验: {desc.name!r}（01 §6）")
        return desc
    return _validate


# ───────────────────────── §3 LensOpinion ─────────────────────────

Stance = Literal["positive", "negative", "neutral", "insufficient-data"]
Confidence = Literal["high", "medium", "low"]


class LensOpinion(BaseModel):
    """§3 结构化观点——Multi-Lens 中每个视角的唯一合法输出形态。"""

    model_config = ConfigDict(frozen=True)

    lens_id: Annotated[str, Field(min_length=3, max_length=128)]
    """产生该观点的视角（§1 lens_id）。"""
    stance: Stance
    key_reasons: tuple[Annotated[str, Field(min_length=1)], ...]
    """理由列表（陈述式，无第一人称；渲染前须过 §6 输出校验）。"""
    evidence_refs: tuple[Annotated[str, Field(min_length=3)], ...]
    """证据引用：announcement_id / dataset_snapshot_id / skill_run_id /
    memory_node_id（§1 四类合法 ID 的字符串形态；kind 标注见 ResultEnvelope.EvidenceRef）。"""
    confidence: Confidence
    skills_triggered: tuple[str, ...]
    """触发的 skill_run_id 列表。"""
    trace_id: Annotated[str, Field(min_length=3, max_length=128)]
    """该观点的推理链锚点。"""

    @model_validator(mode="after")
    def _reasons_nonempty(self) -> "LensOpinion":
        if not self.key_reasons:
            raise ContractViolation("key_reasons 不得为空（结构化观点须给出理由，01 §3）")
        return self
