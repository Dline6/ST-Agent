"""对话即配置 Config Draft（[05 §5](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

「一句话 → Skill/工作流 配置草稿」的契约、生成、双通道登记面消费与处置三态：

- :mod:`~st_agent.l3.config.draft` —— 四字段契约与生成（含 `configure` 去向的产出面）
- :mod:`~st_agent.l3.config.registry` —— [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
  登记面的**消费端口**与**双通道同源**视图
- :mod:`~st_agent.l3.config.handling` —— 接受 / 微调 / 拒绝三态
- :mod:`~st_agent.l3.config.workflow` —— 工作流分支：产出 + 交 [03 §4](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
  Studio 接收面

本包只做再导出；实现见各模块文档。
"""

from __future__ import annotations

from st_agent.l3.config.draft import (
    CONFIG_DRAFT_ABSENT_REASON,
    OPEN_QUESTION_LABELS,
    WORKFLOW_BRANCH_ABSENT_REASON,
    ConfigDraft,
    ConfigDraftProtocol,
    OpenQuestion,
    OpenQuestionSource,
    checked_draft,
    has_workflow_structure,
    open_questions,
    summarise,
)
from st_agent.l3.config.handling import (
    FEEDBACK_POOL_OWNER,
    ConfigAcceptance,
    ConfigDraftHandling,
    DraftRejection,
    PanelView,
)
from st_agent.l3.config.registry import (
    DESCRIPTORS_ABSENT_REASON,
    REGISTRY_ABSENT_REASON,
    ChannelParam,
    ConfigRegistryPort,
    DualChannelView,
    dual_channel_view,
    panel_field_for,
)
from st_agent.l3.config.workflow import (
    IDENTITY_FIELDS,
    WORKFLOW_DRAFT_ABSENT_REASON,
    WorkflowDraftBuilder,
    handoff,
    is_workflow_draft,
)

__all__ = [
    "CONFIG_DRAFT_ABSENT_REASON",
    "DESCRIPTORS_ABSENT_REASON",
    "FEEDBACK_POOL_OWNER",
    "IDENTITY_FIELDS",
    "OPEN_QUESTION_LABELS",
    "REGISTRY_ABSENT_REASON",
    "WORKFLOW_BRANCH_ABSENT_REASON",
    "WORKFLOW_DRAFT_ABSENT_REASON",
    "ChannelParam",
    "ConfigAcceptance",
    "ConfigDraft",
    "ConfigDraftHandling",
    "ConfigDraftProtocol",
    "ConfigRegistryPort",
    "DraftRejection",
    "DualChannelView",
    "OpenQuestion",
    "OpenQuestionSource",
    "PanelView",
    "WorkflowDraftBuilder",
    "checked_draft",
    "dual_channel_view",
    "handoff",
    "has_workflow_structure",
    "is_workflow_draft",
    "open_questions",
    "panel_field_for",
    "summarise",
]
