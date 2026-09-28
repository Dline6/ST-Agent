"""L3 · 冲突裁决包（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 第一条）。

只导出编排面与卡模型；实现细节见 [`adjudication`](adjudication.py)。
"""

from __future__ import annotations

from st_agent.l3.conflict.adjudication import (
    ADJUDICATION_HEADER,
    ADJUDICATION_QUESTION,
    ACTION_LABELS,
    QUEUE_ABSENT_REASON,
    STANCE_UNDECIDED_LABEL,
    AdjudicationAction,
    ConflictAdjudication,
    ConflictAdjudicator,
    ConflictSide,
)

__all__ = [
    "ADJUDICATION_HEADER",
    "ADJUDICATION_QUESTION",
    "ACTION_LABELS",
    "QUEUE_ABSENT_REASON",
    "STANCE_UNDECIDED_LABEL",
    "AdjudicationAction",
    "ConflictAdjudication",
    "ConflictAdjudicator",
    "ConflictSide",
]
