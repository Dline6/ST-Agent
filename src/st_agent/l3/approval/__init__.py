"""能力安装 / 导入审批面（[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md) 逐项批准的 L3 双通道表现面；[03 §5.1](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) / [09 §3](../../../../docs/技术架构-v2/09-生态与分享.md)）。

**这是 [L1 册 D2](../../../../项目管理/遗留问题/L1-遗留问题.md) 点名的承接方**（决策 [D-064](../../../../项目管理/决策日志.md)）：
`SkillPermissionBook.declare / approve / reject` 此前**无调用方**，本包即其写入入口。

- [`panel`](panel.py) —— 审批面视图数据 + 双通道处置（逐条展示 / 逐项批准 / 逐项拒绝）
"""

from __future__ import annotations

from st_agent.l3.approval.panel import (
    APPROVAL_ABSENT_REASON,
    ApprovalItem,
    ApprovalItemState,
    ApprovalRequest,
    ApprovalSubmission,
    CapabilityApprovalPanel,
    CapabilityApprovalView,
)

__all__ = [
    "APPROVAL_ABSENT_REASON",
    "ApprovalItem",
    "ApprovalItemState",
    "ApprovalRequest",
    "ApprovalSubmission",
    "CapabilityApprovalPanel",
    "CapabilityApprovalView",
]
