"""工具目录读面（T-AGT-003；[01 §2] 工具暴露面 + [03 §1] 读面落点）。

把 ``SkillDescriptor`` 投影成**模型可见的工具条目**——工具名取 ``skill_id`` 的
base（版本剥离的能力身份）、描述取 ``description``、参数面由 ``parameters``
与 ``input_schema`` 派生。它是**既有注册表的投影**，不是第二份能力登记
（[01 §2]：L1 注册面与模型可见面恒等），也**只投影、不执行**。

四条口径（[D-095]）：

- **一 base 一条目**——同 base 的多个已注册版本**折叠为一条**，执行目标
  （``ToolEntry.skill_id``）取该 base 的**最高版本**；版本升级只换执行目标，
  不新增条目、不改工具名（[01 §2] 的版本折叠口径）。
- **参数面从描述体派生**——``parameters`` 的 ``ParameterSpec`` 转 JSON Schema
  （``enum`` → ``"type":"string"`` + ``enum`` 表；``min_value`` / ``max_value``
  → ``minimum`` / ``maximum``；``default`` 非空照搬），与 ``input_schema``
  的 ``properties`` 合进**同一份 object 根 schema**（可直喂 L0 ``ToolSpec``）；
  ``required`` **只取** ``input_schema``（``ParameterSpec`` 是带默认值的可调
  标量、非必填）。两处**同名冲突即拒**，不静默取其一；``enum`` 上的数值上下界
  在 JSON Schema 里**无法表达**，同样**显式拒**（不静默丢一条已声明的约束）。
- **来源无差别**——条目**不带 ``source`` 字段**：``mcp-mapped`` 与官方 / 自建 /
  导入走**同一条投影路径**，无特例分支（两描述体仅 ``source`` 不同 ⇒ 投影相等）。
- **不吞失败、只读**——描述体缺失 / 形态坏 / 参数面冲突一律**显式失败**并点明
  出错的 ``skill_id``，**不留半个条目、不静默跳过**；投影**不写任何存储**。
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.capability_types import ParameterSpec, SkillDescriptor
from st_agent.l1.skills.errors import SkillValidationError
from st_agent.l1.skills.ids import base_of, parse_skill_id

__all__ = [
    "ToolEntry",
    "project_catalog",
    "project_tool_entry",
]

_KEY_TYPE = "type"
_KEY_PROPERTIES = "properties"
_KEY_REQUIRED = "required"

_PARAM_TYPE_TO_JSON_SCHEMA: dict[str, str] = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
}
"""``ParameterSpec.type`` → JSON Schema ``type``（``enum`` 单列，见 :func:`_param_schema`）。"""


class ToolEntry(BaseModel):
    """一条**模型可见的工具条目**（[01 §2] 描述体的投影；跨任务交接形态）。

    ``name`` 是版本剥离的能力身份（``skill_id`` 的 base），故同 base 的版本
    升级沿用同一工具名；``skill_id`` 是本次投影解析出的**执行目标**（该 base
    的最高版本），供调用方回指来源并交执行面用。
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    """工具名（＝``skill_id`` 的 base；字母表 ⊆ L0 ``TOOL_NAME_PATTERN``）。"""
    skill_id: str = Field(min_length=3)
    """本条目的执行目标（该 base 的最高已注册版本）。"""
    description: str = Field(min_length=1)
    """做什么的、何时该被调用（供模型选择）。"""
    parameters: dict[str, Any] = Field(default_factory=dict)
    """参数的 JSON Schema（**object** 根；[01 §2] 方言，可直喂 L0 ``ToolSpec``）。"""


def project_tool_entry(descriptor: SkillDescriptor) -> ToolEntry:
    """把一个描述体投影成一条工具条目（纯函数，只读）。

    ``skill_id`` 形态非法 / ``input_schema`` 形态坏 / 参数面两处同名冲突
    → ``SkillValidationError``（携出错的 ``skill_id`` 与违规点），不返回半成品。
    """
    base = _base_of(descriptor)
    return ToolEntry(
        name=base,
        skill_id=descriptor.skill_id,
        description=descriptor.description,
        parameters=_derive_parameters(descriptor),
    )


def project_catalog(descriptors: Iterable[SkillDescriptor]) -> tuple[ToolEntry, ...]:
    """把一组描述体投影成**工具目录**（按 base 折叠，按 base 升序）。

    :param descriptors: 注册表读取面给出的全部版本（如 ``SkillRegistry.list_all()``；
        顺序无关——本函数自行比较版本）。
    """
    latest: dict[str, SkillDescriptor] = {}
    for descriptor in descriptors:
        base = _base_of(descriptor)
        current = latest.get(base)
        if current is None or _version_of(descriptor) > _version_of(current):
            latest[base] = descriptor
    return tuple(project_tool_entry(latest[base]) for base in sorted(latest))


# ───────────────────────── 内部工具 ─────────────────────────


def _base_of(descriptor: SkillDescriptor) -> str:
    """取描述体的 base（形态非法即拒——投影的**形状断言**在此发生）。"""
    try:
        return base_of(descriptor.skill_id)
    except SkillValidationError as exc:
        raise SkillValidationError(f"工具目录投影失败：{descriptor.skill_id!r} 非法（{exc}）") from exc


def _version_of(descriptor: SkillDescriptor) -> tuple[int, int]:
    """取描述体的 ``(主, 次)`` 供比版本（形态非法即拒）。"""
    try:
        _, version = parse_skill_id(descriptor.skill_id)
    except SkillValidationError as exc:
        raise SkillValidationError(f"工具目录投影失败：{descriptor.skill_id!r} 非法（{exc}）") from exc
    return (version.major, version.minor)


def _derive_parameters(descriptor: SkillDescriptor) -> dict[str, Any]:
    """由 ``parameters`` 与 ``input_schema`` 派生条目的 object 根参数 schema。"""
    schema = _checked_input_schema(descriptor)
    properties: dict[str, Any] = {
        name: copy.deepcopy(raw) for name, raw in dict(schema.get(_KEY_PROPERTIES) or {}).items()
    }
    for spec in descriptor.parameters:
        if spec.name in properties:
            raise SkillValidationError(
                f"工具目录投影失败：{descriptor.skill_id!r} 的参数面两处同名冲突 "
                f"（{spec.name!r} 同时出现在 parameters 与 input_schema.properties）"
            )
        properties[spec.name] = _param_schema(spec)
    out: dict[str, Any] = {_KEY_TYPE: "object", _KEY_PROPERTIES: properties}
    required = schema.get(_KEY_REQUIRED)
    if required:  # 只取 input_schema 的必填名单；普通参数是带默认值的可调标量
        out[_KEY_REQUIRED] = list(required)
    return out


def _checked_input_schema(descriptor: SkillDescriptor) -> dict[str, Any]:
    """``input_schema`` 的**形状自检**（[01 §2] 方言：object 根 / properties 为对象）。

    ``SkillDescriptor`` 的 ``input_schema`` 是自由 ``dict``（[01 §2] 的方言由
    ``contracts.schema_check`` 在**载荷**面判定，描述体本身不校验根形态），
    故投影前须自检；不合即拒、不静默按空处理（否则条目会静默少一批参数）。
    """
    schema = descriptor.input_schema
    if not isinstance(schema, dict):
        raise SkillValidationError(
            f"工具目录投影失败：{descriptor.skill_id!r} 的 input_schema 非对象"
        )
    declared = schema.get(_KEY_TYPE)
    if declared is not None and declared != "object":
        raise SkillValidationError(
            f"工具目录投影失败：{descriptor.skill_id!r} 的 input_schema type 须为 'object'"
            f"（得到 {declared!r}）"
        )
    props = schema.get(_KEY_PROPERTIES)
    if props is not None and not isinstance(props, dict):
        raise SkillValidationError(
            f"工具目录投影失败：{descriptor.skill_id!r} 的 input_schema.properties 须为对象"
        )
    required = schema.get(_KEY_REQUIRED)
    if required is not None and not isinstance(required, list):
        raise SkillValidationError(
            f"工具目录投影失败：{descriptor.skill_id!r} 的 input_schema.required 须为数组"
        )
    return schema


def _param_schema(spec: ParameterSpec) -> dict[str, Any]:
    """单个 ``ParameterSpec`` → JSON Schema（约束一项不丢：min / max / choices / default）。"""
    if spec.type == "enum":
        if spec.min_value is not None or spec.max_value is not None:
            # 取值域内的数值上下界在 string 枚举上**无法表达**；静默丢弃即丢一条已声明
            # 的约束（``ParameterSpec`` 的构造期校验不管这对组合），故显式拒。
            raise SkillValidationError(
                f"工具目录投影失败：参数 {spec.name!r} 为 enum 却带数值上下界"
                "（JSON Schema 的枚举取值域无常量上的数值约束）"
            )
        out: dict[str, Any] = {_KEY_TYPE: "string", "enum": list(spec.choices)}
    else:
        out = {_KEY_TYPE: _PARAM_TYPE_TO_JSON_SCHEMA[spec.type]}
        if spec.min_value is not None:
            out["minimum"] = spec.min_value
        if spec.max_value is not None:
            out["maximum"] = spec.max_value
    if spec.default is not None:
        out["default"] = spec.default
    out["description"] = spec.description
    return out
