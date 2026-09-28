"""L3 · 反馈采集点包（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 第二条）。

只导出采集面与事件模型；实现细节见 [`collector`](collector.py)。
"""

from __future__ import annotations

from st_agent.l3.feedback.collector import (
    ACTIONS,
    SINK_ABSENT_NOTE,
    TARGET_ID_TYPES,
    FeedbackAction,
    FeedbackCollector,
    FeedbackDelivery,
    FeedbackEvent,
    FeedbackRecord,
    FeedbackTarget,
    FeedbackTargetKind,
)

__all__ = [
    "ACTIONS",
    "SINK_ABSENT_NOTE",
    "TARGET_ID_TYPES",
    "FeedbackAction",
    "FeedbackCollector",
    "FeedbackDelivery",
    "FeedbackEvent",
    "FeedbackRecord",
    "FeedbackTarget",
    "FeedbackTargetKind",
]
