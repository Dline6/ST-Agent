"""01 §12 UI 描述契约：形状、枚举与不变量。

契约级用例——它锁的是**所有渲染方的共同前提**（[T-UI-001.3] 的 ④ 对齐把它登记为跨层
共享型），所以放在 `tests/contracts/` 而不是 `tests/ui/`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import DescriptionId
from st_agent.contracts.ui_description import (
    COMPONENT_TYPES,
    DESCRIPTION_ID_PREFIX,
    IMPLEMENTED_COMPONENT_TYPES,
    RESERVED_COMPONENT_TYPES,
    ComponentType,
    UiDescription,
    checked_description,
    new_description_id,
)

TZ = timezone(timedelta(hours=8))


def _description(**overrides):
    fields = {
        "description_id": new_description_id(),
        "component_type": "report_card",
        "title": "示例",
        "slots": {"sections": [{"title": "概要", "lines": ["说明"]}]},
        "text_kinds": {"sections": "generated"},
    }
    fields.update(overrides)
    return fields


class TestComponentTypes:
    """§12 的组件类型登记表。"""

    def test_enum_and_tuple_are_single_sourced(self):
        """`COMPONENT_TYPES` 必须由 `ComponentType` 派生——两处不会漂移。"""
        assert COMPONENT_TYPES == get_args(ComponentType)

    def test_registered_types_partition_into_implemented_and_reserved(self):
        """登记表二分且不漏：已实现 6 型 + 预留 5 型 = 全部 11 型（§12）。"""
        assert set(IMPLEMENTED_COMPONENT_TYPES) | set(RESERVED_COMPONENT_TYPES) == set(
            COMPONENT_TYPES
        )
        assert not set(IMPLEMENTED_COMPONENT_TYPES) & set(RESERVED_COMPONENT_TYPES)
        assert len(IMPLEMENTED_COMPONENT_TYPES) == 6
        assert len(COMPONENT_TYPES) == 11

    def test_reserved_types_are_registered_but_not_implemented(self):
        """预留型是**已登记**的合法取值——提前登记使后续补形态属于实现而非改契约。"""
        assert set(RESERVED_COMPONENT_TYPES) == {
            "divergence_map",
            "heatmap",
            "trend_chart",
            "risk_badge",
            "pinned_board",
        }
        for component_type in RESERVED_COMPONENT_TYPES:
            description = UiDescription(**_description(component_type=component_type))
            assert description.component_type == component_type


class TestDescriptionShape:
    """§12 的字段与不变量。"""

    def test_new_description_id_matches_section1_form(self):
        """`description_id` 复用 §1 的单一真相源（不另造形态副本）。"""
        value = new_description_id()
        assert value.startswith(f"{DESCRIPTION_ID_PREFIX}_")
        assert DescriptionId.of(value).value == value

    def test_foreign_id_form_is_rejected(self):
        with pytest.raises(ValidationError, match="description_id 格式非法"):
            UiDescription(**_description(description_id="ev_0123456789abcdef0123"))

    def test_unregistered_component_type_is_rejected_at_construction(self):
        """未登记的类型在**构造期**即被拒——不是跑到渲染期才降级（§12）。"""
        with pytest.raises(ValidationError):
            UiDescription(**_description(component_type="no_such_component"))

    def test_component_type_is_a_key_not_code(self):
        """类型只能是登记表里的键：塞代码片段即拒。"""
        for payload in ("alert(1)", "<script>", "table; rm -rf /"):
            with pytest.raises(ValidationError):
                UiDescription(**_description(component_type=payload))

    @pytest.mark.parametrize(
        "bad",
        [object(), b"bytes", {1: "非字符串键"}, [object()], float("nan"), float("inf")],
        ids=["object", "bytes", "non-str-key", "nested-object", "nan", "inf"],
    )
    def test_slots_accept_only_json_values(self, bad):
        """`slots` 只承载已解析的 JSON 值——禁表达式 / 模板 / 可执行片段（§12）。"""
        with pytest.raises(ValidationError, match="不是合法 JSON 值|必须为字符串"):
            UiDescription(**_description(slots={"sections": bad}, text_kinds={"sections": "data"}))

    @pytest.mark.parametrize("bad_name", ["", "1abc", "a b", "a-b", "a" * 65])
    def test_slot_names_must_be_stable_identifiers(self, bad_name):
        """槽名是绑定键（渲染件按名取值），不是自由文本。"""
        with pytest.raises(ValidationError, match="槽名非法"):
            UiDescription(
                **_description(slots={bad_name: "x"}, text_kinds={bad_name: "data"})
            )

    def test_text_kinds_must_cover_every_slot_exactly(self):
        """逐槽显式标注、不留白也不多标（§12：留白即误伤用户数据）。"""
        with pytest.raises(ValidationError, match="缺标注"):
            UiDescription(**_description(slots={"sections": []}, text_kinds={}))
        with pytest.raises(ValidationError, match="多标注"):
            UiDescription(
                **_description(slots={"sections": []}, text_kinds={"sections": "data", "x": "data"})
            )

    def test_text_kinds_rejects_unknown_bucket(self):
        with pytest.raises(ValidationError):
            UiDescription(**_description(text_kinds={"sections": "maybe"}))

    def test_as_of_must_carry_timezone(self):
        with pytest.raises(ValidationError, match="必须带时区语义"):
            UiDescription(**_description(as_of=datetime(2026, 9, 29, 12, 0)))
        assert UiDescription(**_description(as_of=datetime(2026, 9, 29, 12, 0, tzinfo=TZ))).as_of

    def test_layout_is_optional_and_may_be_omitted(self):
        """`layout` 是可选且渲染器可忽略的声明式提示（§12）。"""
        assert UiDescription(**_description()).layout is None

    def test_model_is_frozen(self):
        description = UiDescription(**_description())
        with pytest.raises(ValidationError):
            description.component_type = "table"

    def test_checked_description_raises_the_layer_error(self):
        """非法形状经工厂还原为 `ContractViolation`（本层错误，不是 pydantic 裸错）。

        注意「必填槽缺失」**不属**契约层判定——那是注册表的事（见 `tests/ui/test_ui_registry.py`）：
        契约只管形状，空槽在形状上是合法的。
        """
        with pytest.raises(ContractViolation):
            checked_description(**_description(component_type="no_such_component"))
        assert checked_description(**_description(slots={}, text_kinds={})).slots == {}
