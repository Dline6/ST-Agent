"""01-平台共享契约 §2 方言澄清（2026-09-26 定案）+ schema 校验件（T-L1-001.6）。

**方言**：`input_schema` / `output_schema` 取**最小 JSON Schema 子集**——
关键字仅 `type` / `properties` / `required` / `items`；`type` 取值
`object` / `string` / `number` / `integer` / `boolean` / `array`。

- `properties` 只约束**已声明且已出现**属性的类型；未列入 `required`
  的属性缺省即合法（无 `required` 名单时，空 `{}` 亦合契约）
- 未出现在子集中的关键字（`oneOf` / `$ref` / `format` 等）**不解析、不报错**
  （前向兼容第三方 / 导入 Skill）
- 未知 `type` 取值同样不校验该层——本件判的是**载荷**，描述体自身的合法性
  由注册期校验（``SkillRegistry.register``）承担，二者不混

**两个消费面**（全平台唯一口径，01 §2 明令各层不得各造一套）：
- ``check_payload(schema, payload)``——核执行器输出载荷（03 §1.2 步骤 5）
- ``check_link(upstream_output, downstream_input)``——判「上游输出 → 下游输入」
  是否匹配（03 §3.1 连线校验 / T-L1-004 Pack 装载）。只做**静态结构判定**：
  下游 `required` 的每项须由上游 `properties` 声明且类型兼容；两侧都声明的
  同名属性类型须兼容。下游未列入 `required` 的字段、以及任一侧未给 `type`
  的当口一律不判不匹配——上游声明缺位时，下游的 `required` 即是唯一的
  明文契约，按它报出匹配缺口不算臆测。

本件层间中立（与 §6 的 ``NeutralityGuard`` 同构），任何层只经
``st_agent.contracts`` 引用。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.errors import ContractViolation

__all__ = [
    "DIALECT_KEYWORDS",
    "SCHEMA_TYPES",
    "LinkCheck",
    "PayloadCheck",
    "SchemaViolation",
    "check_link",
    "check_payload",
]

SCHEMA_TYPES: tuple[str, ...] = (
    "object", "string", "number", "integer", "boolean", "array",
)
"""方言认可的 `type` 取值（01 §2 方言澄清）。"""

DIALECT_KEYWORDS: tuple[str, ...] = ("type", "properties", "required", "items")
"""方言解析的关键字；其余关键字一律忽略（不报错）。"""

_NUMERIC_UPCAST: dict[str, tuple[str, ...]] = {
    # 上游声明 → 可被下游接受的声明（整数可当数值用；反之不成立）
    "integer": ("integer", "number"),
    "number": ("number",),
}


class SchemaViolation(BaseModel):
    """一条不合契约的判定（含载荷定位与人可读说明）。"""

    model_config = ConfigDict(frozen=True)

    path: Annotated[str, Field(min_length=1)]
    """载荷内定位：``$`` 为根，``$.risk_level`` / ``$.items[0].code`` 逐级下钻。"""
    message: Annotated[str, Field(min_length=1)]
    """人可读说明（中性措辞；指出缺哪个字段、类型差在哪）。"""

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class PayloadCheck(BaseModel):
    """``check_payload`` 的判定结果（载荷是否合契约 + 违规点）。"""

    model_config = ConfigDict(frozen=True)

    passed: bool
    violations: tuple[SchemaViolation, ...] = ()

    def describe(self) -> str:
        """人类可读的汇总说明（违规点以「；」连接）。"""
        if self.passed:
            return "载荷符合 schema"
        return "；".join(str(v) for v in self.violations)


class LinkCheck(BaseModel):
    """``check_link`` 的判定结果（连线是否可接 + 不匹配说明）。"""

    model_config = ConfigDict(frozen=True)

    compatible: bool
    violations: tuple[SchemaViolation, ...] = ()

    def describe(self) -> str:
        """人类可读的汇总说明（03 §3.1：「这里需要一个 XX 类型的输入」）。"""
        if self.compatible:
            return "上游输出与下游输入契约匹配"
        return "；".join(str(v) for v in self.violations)


# ───────────────────────── 载荷校验（03 §1.2 步骤 5） ─────────────────────────


def check_payload(schema: Any, payload: Any) -> PayloadCheck:
    """核验载荷是否满足 schema（01 §2 方言）。

    空 schema（``{}`` / ``None``）＝未声明契约 → 一律放行（GWT-C3 不误伤）。
    """
    if not schema:
        return PayloadCheck(passed=True)
    if not isinstance(schema, dict):
        raise ContractViolation(f"schema 须为对象（01 §2）：{type(schema).__name__}")
    violations: list[SchemaViolation] = []
    _check_node(schema, payload, "$", violations)
    return PayloadCheck(passed=not violations, violations=tuple(violations))


def _check_node(schema: dict, value: Any, path: str,
                out: list[SchemaViolation]) -> None:
    """递归校验单个节点（方言关键字逐层下钻）。"""
    declared = schema.get("type")
    if isinstance(declared, str) and declared in SCHEMA_TYPES:
        if not _type_ok(declared, value):
            out.append(SchemaViolation(
                path=path,
                message=f"类型不符：声明 {declared}，实际 {_type_name(value)}",
            ))
            return  # 类型都不对，下钻属性/元素只会连报噪音

    # 未知 type 取值：不校验该层（前向兼容），但仍处理其下关键字
    if isinstance(value, dict):
        for name in schema.get("required") or ():
            if name not in value:
                out.append(SchemaViolation(
                    path=path,
                    message=f"必填字段 {name!r} 缺失（required 声明）",
                ))
        properties = schema.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ContractViolation("schema.properties 须为对象（01 §2）")
        for name, sub in (properties or {}).items():
            if name in value:
                if not isinstance(sub, dict):
                    raise ContractViolation(
                        f"schema.properties[{name!r}] 须为对象（01 §2）")
                _check_node(sub, value[name], f"{path}.{name}", out)

    if isinstance(value, (list, tuple)):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, element in enumerate(value):
                _check_node(items, element, f"{path}[{i}]", out)


def _type_ok(declared: str, value: Any) -> bool:
    """单个值的类型判定（A5 严格口径：``bool`` 不算 number/integer）。"""
    if declared == "object":
        return isinstance(value, dict)
    if declared == "string":
        return isinstance(value, str)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "array":
        return isinstance(value, (list, tuple))
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True  # 不可达（调用前已过滤非方言取值）


def _type_name(value: Any) -> str:
    """实际类型的人可读名（用于违规说明）。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "null" if value is None else type(value).__name__


# ───────────────────────── 连线校验（03 §3.1；T-L1-003 / T-L1-004 复用） ─────


def check_link(upstream_output: Any, downstream_input: Any) -> LinkCheck:
    """判「上游输出 → 下游输入」是否可接（静态结构判定）。

    - 下游 `required` 的每项：上游 `properties` 须声明且类型兼容
      （上游声明缺位时下游的 `required` 即唯一明文契约，按它报缺口）
    - 两侧都声明的同名 `properties`：类型须兼容
    - 下游未列入 `required` 的字段、任一侧未给 `type` 的当口：不判不匹配
      （`properties` 只约束已出现属性 —— 语义见 01 §2 方言澄清）

    任一侧为空 schema ＝ 该侧未声明输出/输入面，不能据它反推「无此字段」，
    故只有下游 `required` 能证伪；下游也没声明时判兼容（不臆测）。
    """
    upstream_output = upstream_output or {}
    downstream_input = downstream_input or {}
    for schema, label in ((upstream_output, "上游输出"), (downstream_input, "下游输入")):
        if not isinstance(schema, dict):
            raise ContractViolation(f"{label} schema 须为对象（01 §2）")

    up_props = _properties_of(upstream_output)
    down_props = _properties_of(downstream_input)
    violations: list[SchemaViolation] = []

    for name in downstream_input.get("required") or ():
        if name not in up_props:
            violations.append(SchemaViolation(
                path=name,
                message=f"这里需要一个 {_expect(down_props.get(name))} 类型的输入，"
                        "但上游未声明输出该字段",
            ))
            continue
        _link_types(name, up_props[name], down_props.get(name), violations)

    for name, up_sub in up_props.items():
        if name in down_props:
            _link_types(name, up_sub, down_props[name], violations)

    return LinkCheck(compatible=not violations, violations=tuple(violations))


def _properties_of(schema: dict) -> dict[str, Any]:
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        raise ContractViolation("schema.properties 须为对象（01 §2）")
    return props


def _link_types(name: str, up_sub: Any, down_sub: Any,
                out: list[SchemaViolation]) -> None:
    """同名属性的类型兼容判定（仅两侧都声明了 type 时比较）。"""
    up_type = up_sub.get("type") if isinstance(up_sub, dict) else None
    down_type = down_sub.get("type") if isinstance(down_sub, dict) else None
    if not isinstance(up_type, str) or not isinstance(down_type, str):
        return
    if up_type not in SCHEMA_TYPES or down_type not in SCHEMA_TYPES:
        return
    accepted = _NUMERIC_UPCAST.get(up_type, (up_type,))
    if down_type not in accepted:
        out.append(SchemaViolation(
            path=name,
            message=f"类型不匹配：上游声明 {up_type}，下游需要 {down_type}",
        ))


def _expect(sub: Any) -> str:
    """从下游属性子 schema 取期望类型名（缺失则给中性描述）。"""
    if isinstance(sub, dict) and isinstance(sub.get("type"), str):
        return sub["type"]
    return "任意"
