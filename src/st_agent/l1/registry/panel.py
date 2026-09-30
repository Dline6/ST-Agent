"""声明参数 → 面板字段（01 §7 ``panel_form_spec`` 的派生）。

[05 §5](../../../docs/技术架构-v2/05-L3-对话主入口.md) 要求「同一配置项对话可改、
面板可改、**效果一致**」：登记项未落值时，面板通道的控件由该参数的声明类型
派生——本模块是这份映射的**唯一实现**，L3 的
[`l3.config.registry`](../../l3/config/registry.py) 由此再导出（层间只向下依赖，
[铁律 7](../../../项目管理/工程宪法.md)），不各造一套。
"""

from __future__ import annotations

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.registry_types import PanelField

__all__ = ["panel_field_for"]

_WIDGET_BY_TYPE: dict[str, str] = {
    "string": "text",
    "number": "number",
    "integer": "number",
    "boolean": "toggle",
    "enum": "select",
}
"""``ParameterSpec.type`` → ``PanelField.widget``（01 §2 → 01 §7）。"""


def panel_field_for(spec: ParameterSpec) -> PanelField:
    """声明参数 → 面板字段。

    映射取 ``type``：string→text · number / integer→number · boolean→toggle ·
    enum→select（携 ``choices``）。``help_text`` 取该参数的 ``description``
    （与对话通道同一份说明，只是通道不同）。
    """
    return PanelField(
        widget=_WIDGET_BY_TYPE[spec.type],  # type: ignore[arg-type]
        label=spec.name,
        help_text=spec.description,
        choices=tuple(spec.choices),
    )
