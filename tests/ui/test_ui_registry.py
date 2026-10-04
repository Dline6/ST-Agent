"""组件类型注册表：服务端镜像与前端注册表**不漂移**（[01 §12]；[05 §6]）。

真正维护注册表的是渲染方（前端 `web/js/registry.js`）；本组用例锁的是「两侧类型集合
一致」——一旦 Python 侧新增实现型而前端没跟上（或反之），这里立刻红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from st_agent.contracts.ui_description import (
    IMPLEMENTED_COMPONENT_TYPES,
    RESERVED_COMPONENT_TYPES,
    UiDescription,
    new_description_id,
)
from st_agent.ui.registry import REGISTRY, slot_gaps, spec_for

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_REGISTRY_JS = _UI / "web" / "js" / "registry.js"


def _frontend_component_types() -> set[str]:
    """从 `registry.js` 的 `RENDERERS` 字面量里取出已实现的类型键。"""
    text = _REGISTRY_JS.read_text(encoding="utf-8")
    block = text.split("export const RENDERERS = {", 1)[1].split("};", 1)[0]
    return set(re.findall(r"(\w+)\s*:", block))


def _description(component_type: str, slots: dict) -> UiDescription:
    return UiDescription(
        description_id=new_description_id(),
        component_type=component_type,
        slots=slots,
        text_kinds={name: "data" for name in slots},
    )


def test_every_registered_type_has_a_spec() -> None:
    """登记表与契约的枚举面一致（不含未登记项，也不漏登记项）。"""
    assert set(REGISTRY) == set(IMPLEMENTED_COMPONENT_TYPES) | set(RESERVED_COMPONENT_TYPES)


def test_implemented_types_declare_required_slots() -> None:
    for component_type in IMPLEMENTED_COMPONENT_TYPES:
        spec = spec_for(component_type)
        assert spec is not None and spec.implemented
        assert spec.required_slots, f"{component_type} 未声明必填槽"


def test_reserved_types_are_registered_but_not_implemented() -> None:
    for component_type in RESERVED_COMPONENT_TYPES:
        spec = spec_for(component_type)
        assert spec is not None and not spec.implemented
        assert spec.required_slots == ()


#: 本批（`T-L3-004.1` / `.2`）之后，四型渲染件已随注册表落地——`T-UI-001.3` 留下的
#: 「待补齐集」由此**收紧为空**：两侧必须**相等**，不再有豁免项（欠账由机器接住，不靠记性）。
PENDING_IN_FRONTEND: frozenset[str] = frozenset()


def test_frontend_registry_matches_the_python_side() -> None:
    """两侧不漂移：前端**不得**登记 Python 侧不认的类型，也不得漏掉任何已实现型。"""
    frontend = _frontend_component_types()
    assert frontend <= set(IMPLEMENTED_COMPONENT_TYPES), "前端登记了契约未实现的类型"
    assert set(IMPLEMENTED_COMPONENT_TYPES) - frontend == PENDING_IN_FRONTEND


def test_frontend_renderers_exist_as_files() -> None:
    """注册表里点名的渲染件必须真有对应模块（防悬空登记）。"""
    for relative in re.findall(r"from '\./([\w/]+)\.js'", _REGISTRY_JS.read_text(encoding="utf-8")):
        assert (_UI / "web" / "js" / f"{relative}.js").is_file(), relative


@pytest.mark.parametrize(
    "component_type,slots",
    [
        ("table", {"columns": [], "rows": []}),
        ("report_card", {"sections": []}),
        ("trace_timeline", {"steps": []}),
        ("context_card", {"sections": [], "labels": {}}),
        ("config_draft_card", {"target": "stock-watch", "panels": []}),
        ("conflict_adjudication_card", {"sides": [], "question": "选哪一条"}),
        ("permission_approval_card", {"items": [], "labels": {}}),
        # 06 §5 两视图均实现 → 矩阵与网络图同为必填槽
        ("divergence_map", {"matrix": [], "network": {"nodes": [], "edges": []}}),
    ],
)
def test_complete_descriptions_have_no_slot_gaps(component_type: str, slots: dict) -> None:
    assert slot_gaps(_description(component_type, slots)) == ()


def test_slot_gaps_reports_missing_required_slots() -> None:
    assert slot_gaps(_description("table", {"columns": []})) == ("rows",)
    assert slot_gaps(_description("trace_timeline", {})) == ("steps",)
    assert slot_gaps(_description("context_card", {"sections": []})) == ("labels",)
    assert slot_gaps(_description("config_draft_card", {"summary": "草稿"})) == ("target", "panels")
    assert slot_gaps(_description("divergence_map", {"matrix": []})) == ("network",)


def test_reserved_type_is_not_judged_here() -> None:
    """已登记未实现型交由**渲染面降级**，不该在服务端被判成「缺槽」。"""
    assert slot_gaps(_description("heatmap", {})) == ()


def test_unknown_type_has_no_spec() -> None:
    assert spec_for("no_such_component") is None
