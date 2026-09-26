"""T-L1-001.6 测试：01 §2 方言校验件（contracts.schema_check）。

覆盖：
- 方言关键字逐条：type 六取值 / properties / required / items
- A5 严格口径：``bool`` 不算 number / integer
- 宽松面：空 schema、未声明的可选属性、子集外关键字、未知 type 取值
- check_link：下游必填未被上游声明 / 同名属性类型不兼容 / 整数 → 数值放行
- 说明可读性：describe() 指出缺哪个字段、类型差在哪
"""

import pytest

from st_agent.contracts import (
    ContractViolation,
    check_link,
    check_payload,
)

OBJ = {"type": "object", "properties": {"risk_level": {"type": "string"}}}


# ───────────────────────── check_payload：合规放行 ─────────────────────────


@pytest.mark.parametrize("declared, value", [
    ("object", {"a": 1}),
    ("string", "退市风险"),
    ("number", 0.8),
    ("number", 3),
    ("integer", 3),
    ("boolean", True),
    ("array", [1, 2]),
])
def test_type_accepts_matching_value(declared, value):
    assert check_payload({"type": declared}, value).passed


@pytest.mark.parametrize("declared, value", [
    ("object", [1]),
    ("string", 3),
    ("number", "0.8"),
    ("integer", 1.5),
    ("boolean", "true"),
    ("array", {"a": 1}),
    ("object", None),
])
def test_type_rejects_mismatching_value(declared, value):
    result = check_payload({"type": declared}, value)
    assert not result.passed
    assert result.violations[0].path == "$"
    assert declared in result.violations[0].message


def test_bool_is_not_number_nor_integer():
    """A5：Python 里 bool 是 int 子类，须显式排除（否则 True 冒充数值放行）。"""
    assert not check_payload({"type": "number"}, True).passed
    assert not check_payload({"type": "integer"}, True).passed


def test_required_property_missing_is_violation():
    schema = {**OBJ, "required": ["risk_level"]}
    result = check_payload(schema, {"other": 1})
    assert not result.passed
    assert "risk_level" in result.describe()
    assert "缺失" in result.violations[0].message


def test_required_property_present_passes():
    schema = {**OBJ, "required": ["risk_level"]}
    assert check_payload(schema, {"risk_level": "high"}).passed


def test_declared_property_type_checked_when_present():
    result = check_payload(OBJ, {"risk_level": 3})
    assert not result.passed
    assert result.violations[0].path == "$.risk_level"
    assert "string" in result.violations[0].message


def test_undeclared_optional_property_may_be_absent():
    """properties 只约束「已声明且已出现」——未进 required 即缺省合法。"""
    assert check_payload(OBJ, {}).passed
    assert check_payload(OBJ, {"unrelated": 1}).passed


def test_nested_object_and_array_items():
    schema = {
        "type": "object",
        "properties": {
            "alerts": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"code": {"type": "string"}},
                          "required": ["code"]},
            },
        },
        "required": ["alerts"],
    }
    assert check_payload(schema, {"alerts": [{"code": "st"}]}).passed
    bad = check_payload(schema, {"alerts": [{"code": 7}]})
    assert not bad.passed
    assert bad.violations[0].path == "$.alerts[0].code"
    missing = check_payload(schema, {"alerts": [{}]})
    assert "$.alerts[0]" == missing.violations[0].path


# ───────────────────────── 宽松面：不误伤 ─────────────────────────


@pytest.mark.parametrize("schema", [{}, None])
def test_empty_schema_is_permissive(schema):
    """GWT-C3：未声明契约 → 不校验（历史 Skill / 宽松态）。"""
    assert check_payload(schema, {"anything": [1, 2]}).passed
    assert check_payload(schema, "even-a-scalar").passed


def test_unknown_keyword_is_ignored_not_error():
    """子集外关键字不解析、不报错（前向兼容导入 Skill）。"""
    schema = {"type": "object",
              "properties": {"code": {"type": "string", "format": "st-code"},
                             "n": {"oneOf": [{"type": "number"}]}},
              "$ref": "#/defs/x"}
    assert check_payload(schema, {"code": "ST", "n": 3}).passed


def test_unknown_type_value_is_not_checked():
    """未知 type 取值不校验该层（描述体合法性由注册期承担，非本件职责）。"""
    assert check_payload({"type": "null", "properties": {"x": {"type": "null"}}},
                         {"x": 1}).passed


def test_malformed_schema_raises():
    with pytest.raises(ContractViolation):
        check_payload({"type": "object", "properties": []}, {})
    with pytest.raises(ContractViolation):
        check_payload(["not", "an", "object"], {})


# ───────────────────────── check_link：连线校验（GWT-C4） ─────────────────


def test_link_ok_when_required_covered_and_types_agree():
    up = {"type": "object", "properties": {"risk_level": {"type": "string"}}}
    down = {"type": "object", "properties": {"risk_level": {"type": "string"}},
            "required": ["risk_level"]}
    assert check_link(up, down).compatible


def test_link_reports_required_field_absent_upstream():
    up = {"type": "object", "properties": {"other": {"type": "string"}}}
    down = {"type": "object", "properties": {"risk_level": {"type": "string"}},
            "required": ["risk_level"]}
    result = check_link(up, down)
    assert not result.compatible
    assert result.violations[0].path == "risk_level"
    assert "string" in result.violations[0].message
    assert "上游未声明" in result.describe()


def test_link_reports_type_mismatch():
    up = {"type": "object", "properties": {"alerts": {"type": "array"}}}
    down = {"type": "object", "properties": {"alerts": {"type": "string"}},
            "required": ["alerts"]}
    result = check_link(up, down)
    assert not result.compatible
    assert "array" in result.violations[0].message
    assert "string" in result.violations[0].message


def test_link_allows_integer_upstream_for_number_downstream():
    up = {"type": "object", "properties": {"n": {"type": "integer"}}}
    down = {"type": "object", "properties": {"n": {"type": "number"}},
            "required": ["n"]}
    assert check_link(up, down).compatible
    reverse = check_link({"type": "object", "properties": {"n": {"type": "number"}}},
                         {"type": "object", "properties": {"n": {"type": "integer"}},
                          "required": ["n"]})
    assert not reverse.compatible


def test_link_reports_gap_even_when_upstream_undeclared():
    """下游明示 required 即明文契约：上游未声明输出面 = 有据的匹配缺口。"""
    result = check_link({}, {"type": "object", "required": ["x"]})
    assert not result.compatible
    assert result.violations[0].path == "x"


def test_link_permissive_when_downstream_declares_no_requirement():
    """下游未声明任何必填 → 无据可判 → 兼容（连线校验不替用户臆测）。"""
    assert check_link(OBJ, {}).compatible
    assert check_link(OBJ, {"type": "object", "properties": {}}).compatible


def test_link_ignores_optional_type_mismatch_when_not_required():
    """两侧都声明才比较；未进 required 的缺失不算不匹配，但类型冲突仍报。"""
    up = {"type": "object", "properties": {"maybe": {"type": "array"}}}
    down = {"type": "object", "properties": {"maybe": {"type": "string"}}}
    assert not check_link(up, down).compatible


def test_link_malformed_schema_raises():
    with pytest.raises(ContractViolation):
        check_link({"type": "object", "properties": 1}, {})
