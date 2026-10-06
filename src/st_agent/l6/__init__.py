"""L6 · 反思演进（Reflection Loop）——[08](../../../docs/技术架构-v2/08-L6-反思演进.md)。

本层首批（[T-L6-001](../../../项目管理/tasks/T-L6-001-反馈采集FeedbackEvent每周反思报告.md)）交付
[08 §1–§2](../../../docs/技术架构-v2/08-L6-反思演进.md)：**反思数据池**（反馈落 `reflection`
分区、本机不外发）与**每周反思报告**（四段式 · 完整 Trace · 两处空态 · 经 L5 渠道分发）。
第二批（[T-L6-002](../../../项目管理/tasks/T-L6-002-训练对话协议主动提案与AB实验.md)）交付
[08 §3–§4](../../../docs/技术架构-v2/08-L6-反思演进.md)：**训练对话协议**（概念性理解 · 确认门 ·
记忆 `pattern` 写入 · 提案候选 · 回访留痕）、**主动提案**（模式识别 · Skill 草稿 · 频率上限 ·
周报第四段候选生成）与 **A/B 实验**（五字段 · 留痕 · 保守判定 · 授权面预留）。

三个面各自独立可用，由 [`runtime.build_l6`](runtime.py) 接成一层；跨层组合根归关卡
[T-INT-005](../../../项目管理/tasks/T-INT-005-M4集成关卡反思演进与生态闭环.md)。

**只向下依赖**（[铁律 7](../../../项目管理/工程宪法.md)）：本层消费 L0 的 `reflection` 分区、
[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的事件面、[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
的配置门面、L2 的记忆读取面、L3 的反馈事件模型与 L5 的投递 / 渠道面——**不反向**。
"""

from __future__ import annotations

from st_agent.l6.experiment import (
    AUTHORIZER_ABSENT_REASON,
    DEFAULT_MIN_SAMPLE,
    LOW_RISK_SCOPES,
    NOT_PERMITTED_REASON,
    Experiment,
    ExperimentConsole,
    ExperimentStart,
    judge,
)
from st_agent.l6.experiment_store import EXPERIMENT_PREFIX, ExperimentStore
from st_agent.l6.errors import (
    ExperimentError,
    FeedbackPoolError,
    L6Error,
    ProposalError,
    TrainingError,
    WeeklyReportError,
)
from st_agent.l6.pool import (
    FEEDBACK_EVENT,
    SOURCE,
    FeedbackEntry,
    FeedbackPool,
    PoolCounts,
)
from st_agent.l6.pool_store import FEEDBACK_PREFIX, FeedbackPoolStore
from st_agent.l6.proposal import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_REPEAT_THRESHOLD,
    DEFAULT_RULES,
    NOT_HIT_NOTE,
    OBSERVER_ABSENT_REASON,
    DetectRun,
    PatternObservation,
    ProposalEngine,
    ProposalRule,
    ProposalRun,
    RuleNote,
    SkillDraft,
    SkillProposal,
)
from st_agent.l6.proposal_store import (
    PROPOSAL_PREFIX,
    RATE_LIMIT_CONFIG_ID,
    REPEAT_CONFIG_ID,
    ProposalStore,
)
from st_agent.l6.registry_adapter import (
    TEMPLATE_WRITE_OWNER,
    ProposalFamily,
    WeeklyReportFamily,
    proposal_family,
    weekly_report_family,
)
from st_agent.l6.runtime import L6Stack, build_l6
from st_agent.l6.training import (
    CALLBACK_NOTICE,
    MEMORY_ABSENT_NOTE,
    NO_SUGGESTION_NOTE,
    TRAINING_CONFIDENCE,
    TRAINING_PRIVACY,
    UNDERSTANDER_ABSENT_REASON,
    CallbackNote,
    SuggestionDraft,
    TrainingDraft,
    TrainingOutcome,
    TrainingProtocol,
    TrainingSession,
    TrainingTurn,
)
from st_agent.l6.training_store import TRAINING_PREFIX, TrainingStore
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
    "AUTHORIZER_ABSENT_REASON",
    "CALLBACK_NOTICE",
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "DEFAULT_CHANNELS",
    "DEFAULT_DAY_OF_WEEK",
    "DEFAULT_MIN_FEEDBACK",
    "DEFAULT_MIN_SAMPLE",
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_REPEAT_THRESHOLD",
    "DEFAULT_RULES",
    "DEFAULT_TIME",
    "DAY_CONFIG_ID",
    "EXPERIMENT_PREFIX",
    "FEEDBACK_EVENT",
    "FEEDBACK_PREFIX",
    "INSUFFICIENT_NOTE",
    "L6Stack",
    "LOW_RISK_SCOPES",
    "MEMORY_ABSENT_NOTE",
    "MIN_FEEDBACK_CONFIG_ID",
    "NO_ADVISOR_NOTE",
    "NO_SUGGESTION_NOTE",
    "NO_TOUCH_NOTE",
    "NOT_HIT_NOTE",
    "NOT_PERMITTED_REASON",
    "OBSERVER_ABSENT_REASON",
    "PROPOSAL_PREFIX",
    "RATE_LIMIT_CONFIG_ID",
    "REPORT_PREFIX",
    "REPEAT_CONFIG_ID",
    "SECTION_LABELS",
    "SOURCE",
    "TEMPLATE_CONFIG_ID",
    "TEMPLATE_WRITE_OWNER",
    "TIME_CONFIG_ID",
    "TRAINING_CONFIDENCE",
    "TRAINING_PREFIX",
    "TRAINING_PRIVACY",
    "UNDERSTANDER_ABSENT_REASON",
    "WEEK_SECTIONS",
    "CallbackNote",
    "DetectRun",
    "Experiment",
    "ExperimentConsole",
    "ExperimentError",
    "ExperimentStart",
    "ExperimentStore",
    "FeedbackEntry",
    "FeedbackPool",
    "FeedbackPoolError",
    "FeedbackPoolStore",
    "L6Error",
    "PatternObservation",
    "PoolCounts",
    "ProposalCandidate",
    "ProposalEngine",
    "ProposalError",
    "ProposalFamily",
    "ProposalRule",
    "ProposalRun",
    "ProposalStore",
    "ReportLine",
    "ReportSection",
    "ReportTemplate",
    "ReportTrace",
    "RuleNote",
    "SkillDraft",
    "SkillProposal",
    "SuggestionDraft",
    "TrainingDraft",
    "TrainingError",
    "TrainingOutcome",
    "TrainingProtocol",
    "TrainingSession",
    "TrainingStore",
    "TrainingTurn",
    "WeeklyReport",
    "WeeklyReportBuilder",
    "WeeklyReportError",
    "WeeklyReportFamily",
    "WeeklyReportStore",
    "build_l6",
    "due_on",
    "judge",
    "proposal_family",
    "week_key",
    "week_window",
    "weekly_report_family",
]
