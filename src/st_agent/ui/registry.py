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
    # 逐条权限在 `items`（数据槽：声明原文 + 批准态 + 面板控件），中性措辞 / 对话文案在
    # `labels`（生成文案槽，按 `permission` 与 `items` 并联）——缺任一处出不了完整审批面。
    "permission_approval_card": ("items", "labels"),
    # 反馈按钮组：`target`（数据槽：反馈对象）与 `actions`（数据槽：提供的动作键）；
    # 中文标签在 `labels`（生成文案槽，按动作键与 `actions` 并联）——缺任一处出不了按钮组。
    "feedback_capture": ("target", "actions", "labels"),
    # 提案卡：`proposals`（数据槽：**一组**建议，每项含类型 / 配置项 / 现值 / 建议值 / 理由 /
    # trace 依据 / Skill 草稿 / 待批准项 id / **该项可用的动作**）与 `actions` 标签面——
    # 动作标签在 `labels`（生成文案槽，按动作键并联）。一组而非一条：周报第四段与待批准队列都是多条。
    "proposal_card": ("proposals", "labels"),
    # 变更历史时间线：`changes`（数据槽：逐条变更 + 回滚状态 + 可用动作）与动作标签 `labels`
    # （生成文案槽，按动作键并联）。逐条时间线的生成文案在 L6 侧落盘时已过 §6，故整槽按 data。
    "change_timeline": ("changes", "labels"),
    # 逐条设置 / 处置面板：`entries`（数据槽：标识 / 当前值 / 候选值 / 可用动作）· `labels`
    # （生成文案槽：动作与候选值的中文标签）· `surface`（数据槽：**面键**，渲染件据此在
    # 固定表里解析路由——描述**不接受** URL / 方法）。**带动作**——只读呈现走 `config_draft_card`。
    "setting_panel": ("entries", "labels", "surface"),
    # 越界行为警示：`record`（数据槽：能力 / 越界类别 / 时刻 / trace 锚点）与 `labels`
    # （生成文案槽：警示措辞与「禁用该能力」等动作标签）。整条警示由系统产出，逐条不合并。
    "violation_alert": ("record", "labels"),
    # 06 §5 要求**两视图均实现**：矩阵视图取 `matrix`（视角 × 结论逐行）、证据网络图取
    # `network`（节点 + 引用边）——缺任一处都出不了分歧图，故两者同为必填。
    "divergence_map": ("matrix", "network"),
    # Studio 画布：`canvas`（数据槽：session / nodes / edges / groups / violations /
    # issues / validation_ok，编辑后附 `edit` 小结）与 `labels`（生成文案槽：动作与标题标签，
    # 按动作键并联）——缺任一处出不了画布页（T-UI-005.1）。
    "studio_canvas": ("canvas", "labels"),
    # 图谱视图：`nodes`（数据槽：节点 id / 类型 / 标签 / 置信度 / 隐私分级）· `edges`
    # （数据槽：起止节点 id + 关系）· `labels`（生成文案槽：节点类型 / 隐私分级 / 关系的中文
    # 标签与图例措辞，按类型键与关系键并联）。**中性边界**：节点标签与置信度是数据展示（可能
    # 含用户原话），作 `data`；类型名 / 图例是系统文案，作 `generated`（T-UI-010.1）。
    "graph_view": ("nodes", "edges", "labels"),
    # 热力块：`cells`（数据槽：逐格坐标 + 数值）与 `labels`（生成文案槽：轴名 / 图例 / 单位）。
    # **中性边界**：数值是数据（`data`），轴名与图例是系统文案（`generated`）；色阶由渲染件按
    # 数值一次产出，不靠产出方拼色（同 13-visual-design §5.1 涨跌编码的取向）。
    "heatmap": ("cells", "labels"),
    # 趋势图：`series`（数据槽：逐条序列的点）与 `labels`（生成文案槽：轴名 / 图例 / 单位）。
    # **中性边界**同 `heatmap`——**不判方向、不拼成品文案**，方向与涨跌编码归渲染面（§5.1）。
    "trend_chart": ("series", "labels"),
    # 通用时序视图：`entries`（数据槽：逐条——标识 / 时刻 / 内容 / 类型 / 状态）与
    # `labels`（生成文案槽：类型名与状态名的中文标签，按键并联）。**只读**——本型不承载
    # 写动作（修正历史按 story-03 只「保留旧值供审计」），故描述里不接受动作键、渲染件不接
    # 任何回环路由。**中性边界**：条目内容是**数据展示**（记忆本体可能含用户原话），作 `data`；
    # 类型名与状态名是系统文案，作 `generated`。承载记忆区时间线 / 修正历史与触达区推送历史。
    "timeline_view": ("entries", "labels"),
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
