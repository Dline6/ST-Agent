"""任务派发总线（[05 §4](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

见 [`bus`](bus.py) 的模块文档；本包只做再导出。
"""

from __future__ import annotations

from st_agent.l3.dispatch.bus import (
    ANALYZE_ABSENT_REASON,
    DISPATCH_INITIATOR,
    DISPATCH_PURPOSE,
    INVESTIGATE_ABSENT_REASON,
    LLM_DEGRADED_NOTICE,
    RENDER_SEMANTICS,
    ROUTE_BY_INTENT,
    ROUTE_SPECS,
    TRAIN_ABSENT_REASON,
    DispatchBus,
    DispatchOutcome,
    RouteSpec,
    UiSemantics,
    render_semantics,
)

__all__ = [
    "ANALYZE_ABSENT_REASON",
    "DISPATCH_INITIATOR",
    "DISPATCH_PURPOSE",
    "INVESTIGATE_ABSENT_REASON",
    "LLM_DEGRADED_NOTICE",
    "RENDER_SEMANTICS",
    "ROUTE_BY_INTENT",
    "ROUTE_SPECS",
    "TRAIN_ABSENT_REASON",
    "DispatchBus",
    "DispatchOutcome",
    "RouteSpec",
    "UiSemantics",
    "render_semantics",
]
