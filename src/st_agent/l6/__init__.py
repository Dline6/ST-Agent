"""L6 · 反思演进（Reflection Loop）——[08](../../../docs/技术架构-v2/08-L6-反思演进.md)。

本层首批（[T-L6-001](../../../项目管理/tasks/T-L6-001-反馈采集FeedbackEvent每周反思报告.md)）交付
[08 §1–§2](../../../docs/技术架构-v2/08-L6-反思演进.md)：**反思数据池**（反馈落 `reflection`
分区、本机不外发）与**每周反思报告**（四段式 · 完整 Trace · 两处空态 · 经 L5 渠道分发）。

两个面各自独立可用，由 [`runtime.build_l6`](runtime.py) 接成一层；跨层组合根归关卡
[T-INT-005](../../../项目管理/tasks/T-INT-005-M4集成关卡反思演进与生态闭环.md)。

**只向下依赖**（[铁律 7](../../../项目管理/工程宪法.md)）：本层消费 L0 的 `reflection` 分区、
[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的事件面、[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
的配置门面、L2 的记忆读取面、L3 的反馈事件模型与 L5 的投递 / 渠道面——**不反向**。
"""

from __future__ import annotations

from st_agent.l6.errors import FeedbackPoolError, L6Error, WeeklyReportError
from st_agent.l6.pool import (
    FEEDBACK_EVENT,
    SOURCE,
    FeedbackEntry,
    FeedbackPool,
    PoolCounts,
)
from st_agent.l6.pool_store import FEEDBACK_PREFIX, FeedbackPoolStore
from st_agent.l6.registry_adapter import (
    TEMPLATE_WRITE_OWNER,
    WeeklyReportFamily,
    weekly_report_family,
)
from st_agent.l6.runtime import L6Stack, build_l6
from st_agent.l6.weekly import (
    DEFAULT_CHANNELS,
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_MIN_FEEDBACK,
    DEFAULT_TIME,
    INSUFFICIENT_NOTE,
    NO_ADVISOR_NOTE,
    NO_TOUCH_NOTE,
    SECTION_LABELS,
    WEEK_SECTIONS,
    ProposalCandidate,
    ReportLine,
    ReportSection,
    ReportTemplate,
    ReportTrace,
    WeeklyReport,
    WeeklyReportBuilder,
    due_on,
    week_key,
    week_window,
)
from st_agent.l6.weekly_store import (
    CHANGE_PREFIX,
    CONFIG_PREFIX,
    DAY_CONFIG_ID,
    MIN_FEEDBACK_CONFIG_ID,
    REPORT_PREFIX,
    TEMPLATE_CONFIG_ID,
    TIME_CONFIG_ID,
    WeeklyReportStore,
)

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "DEFAULT_CHANNELS",
    "DEFAULT_DAY_OF_WEEK",
    "DEFAULT_MIN_FEEDBACK",
    "DEFAULT_TIME",
    "DAY_CONFIG_ID",
    "FEEDBACK_EVENT",
    "FEEDBACK_PREFIX",
    "INSUFFICIENT_NOTE",
    "L6Stack",
    "MIN_FEEDBACK_CONFIG_ID",
    "NO_ADVISOR_NOTE",
    "NO_TOUCH_NOTE",
    "REPORT_PREFIX",
    "SECTION_LABELS",
    "SOURCE",
    "TEMPLATE_CONFIG_ID",
    "TEMPLATE_WRITE_OWNER",
    "TIME_CONFIG_ID",
    "WEEK_SECTIONS",
    "FeedbackEntry",
    "FeedbackPool",
    "FeedbackPoolError",
    "FeedbackPoolStore",
    "L6Error",
    "PoolCounts",
    "ProposalCandidate",
    "ReportLine",
    "ReportSection",
    "ReportTemplate",
    "ReportTrace",
    "WeeklyReport",
    "WeeklyReportBuilder",
    "WeeklyReportError",
    "WeeklyReportFamily",
    "WeeklyReportStore",
    "build_l6",
    "due_on",
    "week_key",
    "week_window",
    "weekly_report_family",
]
