"""L3 描述件（[05 §6–§7](../../../docs/技术架构-v2/05-L3-对话主入口.md)；[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md)）。

把本层已交付的**结构化视图数据**转成符合 [01 §12](../../../docs/技术架构-v2/01-平台共享契约.md)
的 UI 描述，供表现层的组件注册表渲染。五个描述件都是**纯函数**：不落盘、不起服务、
**不 import `st_agent.ui`**（[铁律 7](../../../项目管理/工程宪法.md)；`ui` 是客户端组合根，
L3 与它之间不引入反向依赖）。

**槽词汇表**（决策 [D-064](../../../项目管理/决策日志.md)）：[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md)
只登记**通用槽形状**，每型有哪些槽由本模块与前端渲染件共同约定，服务端的机器可读副本
在 [`ui/registry.py`](../../ui/registry.py) 的 ``REQUIRED_SLOTS``。因 ``text_kinds`` 是
**槽级**标签而校验门会递归扫槽内全部字符串，故每型一律**按来源分槽**——「本层生成文案」
与「用户记忆 / 数据」各占其槽，渲染件按 ``key`` 并联，**不做同槽内标注**。
"""

from __future__ import annotations

from st_agent.l3.render.describe import (
    DIVERGENCE_ABSENT_REASON,
    DIVERGENCE_EMPTY_REASON,
    DIVERGENCE_TITLE,
    DRAFT_TITLES,
    ORIGIN_LABELS,
    SIDE_EXISTING_LABEL,
    SIDE_PROPOSED_LABEL,
    STANCE_LABELS,
    TRACE_ABSENT_REASON,
    TRACE_EMPTY_REASON,
    describe_adjudication,
    describe_approval,
    describe_context_card,
    describe_divergence_map,
    describe_draft,
    describe_trace,
)

__all__ = [
    "DIVERGENCE_ABSENT_REASON",
    "DIVERGENCE_EMPTY_REASON",
    "DIVERGENCE_TITLE",
    "DRAFT_TITLES",
    "ORIGIN_LABELS",
    "SIDE_EXISTING_LABEL",
    "SIDE_PROPOSED_LABEL",
    "STANCE_LABELS",
    "TRACE_ABSENT_REASON",
    "TRACE_EMPTY_REASON",
    "describe_adjudication",
    "describe_approval",
    "describe_context_card",
    "describe_divergence_map",
    "describe_draft",
    "describe_trace",
]
