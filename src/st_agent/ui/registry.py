"""组件类型注册表的**服务端镜像**（[01 §12]；[05 §6]）。

真正的注册表（组件类型 → 渲染件）在渲染方，即前端 `web/js/registry.js`。本模块是它在
Python 侧的同源镜像，用来做两件前端做不了的事：

1. **出数据前校验**——一份描述在该型下**必填槽是否齐**；缺槽在这里就回
   `validation_failed`，而不是把一张残缺的卡送到浏览器；
2. **两处不漂移**——「已实现 / 已登记未实现」的划分在两侧各有一份，类型集合由
   `tests/ui/test_ui_registry.py` 断言一致。

**不做的事**：不执行描述里的任何内容。类型是枚举键、槽按名取值（§12 的不变量）。
"""

from __future__ import annotations

from dataclasses import dataclass

from st_agent.contracts.ui_description import (
    IMPLEMENTED_COMPONENT_TYPES,
    RESERVED_COMPONENT_TYPES,
    UiDescription,
)

__all__ = ["REGISTRY", "ComponentSpec", "spec_for", "slot_gaps"]

REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "table": ("columns", "rows"),
    "report_card": ("sections",),
    "trace_timeline": ("steps",),
    # 段名 / 原因在 `labels`（生成文案槽），段内容在 `sections`（数据槽）——两处按 `key` 并联，
    # 缺任一处渲染件都出不了完整卡片（[D-064]）。
    "context_card": ("sections", "labels"),
    # `ConfigDraft` 与 `PanelView` 共用本型，故必填槽取两者都在的 `target` / `panels`；
    # `summary` 只在草稿分支存在（面板视图无摘要），不能作必填（[D-064]）。
    "config_draft_card": ("target", "panels"),
    "conflict_adjudication_card": ("sides", "question"),
}
"""每型的必填槽——新增实现型时**必须**在此表态（下面的断言会拦住漏填）。"""

assert set(REQUIRED_SLOTS) == set(IMPLEMENTED_COMPONENT_TYPES), (
    "已实现的组件类型必须逐个声明必填槽（01 §12 / ui/registry.py）"
)


@dataclass(frozen=True)
class ComponentSpec:
    """一个组件类型在服务端的登记项。"""

    component_type: str
    implemented: bool
    required_slots: tuple[str, ...] = ()


REGISTRY: dict[str, ComponentSpec] = {
    **{
        component_type: ComponentSpec(component_type, True, REQUIRED_SLOTS[component_type])
        for component_type in IMPLEMENTED_COMPONENT_TYPES
    },
    **{
        component_type: ComponentSpec(component_type, False)
        for component_type in RESERVED_COMPONENT_TYPES
    },
}
"""§12 登记的全部组件类型（已实现者带必填槽；已登记未实现者交由渲染面降级）。"""


def spec_for(component_type: str) -> ComponentSpec | None:
    """按类型键取登记项；未登记返回 ``None``（调用方按降级处理，不猜测）。"""
    return REGISTRY.get(component_type)


def slot_gaps(description: UiDescription) -> tuple[str, ...]:
    """该描述在本型下的**缺失必填槽**。

    只对**已实现**型判定——已登记未实现型本就该由渲染面降级，不该在这里被判为「缺槽」。
    """
    spec = spec_for(description.component_type)
    if spec is None or not spec.implemented:
        return ()
    return tuple(slot for slot in spec.required_slots if slot not in description.slots)
