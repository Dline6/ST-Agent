"""L5 主动触达 Ambient Delivery（[07-主动触达](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

承载 [story-07](../../../docs/PRD-v2-Agent/story-07-ambient-delivery.md)：把副驾产出的信号送到用户面前，
核心是**注意力预算管理**——默认策略「少而准」，每日报告为主叙事、实时告警为插入项。

本包按 [07](../../../docs/技术架构-v2/07-L5-主动触达.md) 的段次分模块，当前交付前四段
（[`T-L5-001`](../../../项目管理/tasks/T-L5-001-信号模型注意力预算.md) + [`T-L5-002`](../../../项目管理/tasks/T-L5-002-渠道适配器投递编排与升级链.md)）：

- **信号模型与采纳**（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`signal`——
  `SignalEmitted` 负载的解释与校验（含发布端的 `signal_event` 组装面）、`signal_id` 的确定性铸造、
  内容载体（结论 + 逐视角摘要，非单视角结论）
- **注意力预算**（[07 §2](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`budget`——
  情境 × 时段 × 内容类型的格位判定与「汇总进日报」路由、五情境模式、注册进
  [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)（族适配器见 `:mod:`registry_adapter``）
- **渠道适配器与渠道偏好**（[07 §3](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`channels`——
  四类渠道的统一协议、离线标注与**即时降级**；`:mod:`channel_policy` / `:mod:`channel_registry``
  交付偏好（有序链 + 每级未读等待）与其 01 §7 族
- **投递编排与升级链**（[07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`delivery`（+ `:mod:`delivery_store``）——
  逐级升级与 `delivery_id` 留痕、已读结算、`routine` 汇总路由、离线补发；
  `:mod:`personalize`` 交付文案个性化（过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）
- **薄装配缝**：`:mod:`runtime`` 的 `build_l5`——各族注入点与订阅面
  （M3 的**跨层**组合根 `build_m3_runtime` 归关卡 [`T-INT-004`](../../../项目管理/tasks/T-INT-004-M3集成关卡主动触达投递闭环.md)）
- **每日报告**（[07 §5](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`daily_report`（+ `:mod:`daily_report_store``）——
  四段「给你的信」的组装、到点判定与模板注册（[T-L5-003.1](../../../项目管理/tasks/T-L5-003.1-每日报告本体与模板配置.md)）
- **去重与频控**（[07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`frequency`（+ `:mod:`frequency_store``）——
  同 `dedup_key` 合并 + 触发次数、格位节奏计数与「排队进日报」（[T-L5-003.2](../../../项目管理/tasks/T-L5-003.2-去重合并与频控计数.md)）
- **推送疲劳监控**（[07 §7](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`fatigue`（+ `:mod:`fatigue_store``）——
  连续忽略计数与阈值、询问信号、答复落值与静音清单（[T-L5-003.3](../../../项目管理/tasks/T-L5-003.3-推送疲劳监控与答复回流.md)）

层次：L5 在 L4 之上（[`tests/test_layering.py`](../../../tests/test_layering.py) 的 `LAYER_ORDER`），
只向下消费契约与各层，不被任何层依赖。
"""

from st_agent.l5.budget import (
    CONTENT_TYPES,
    DEFAULT_CELL,
    DEFAULT_CELLS,
    DEFAULT_MODE,
    DEFAULT_SLOTS,
    MATRIX_CONFIG_ID,
    MODE_CONFIG_ID,
    SITUATION_MODES,
    SLOTS_CONFIG_ID,
    AttentionBudget,
    BudgetCell,
    BudgetCellView,
    BudgetKey,
    BudgetMap,
    BudgetVerdict,
    ContentType,
    SituationMode,
    TimeSlot,
    default_budget,
)
from st_agent.l5.channel_policy import (
    LEVELS,
    RESEND_CONFIG_ID,
    ChannelPolicies,
    ChannelPolicy,
    policy_config_id,
)
from st_agent.l5.channel_registry import ChannelPolicyFamily, channel_policy_family
from st_agent.l5.channels import (
    CHANNEL_KINDS,
    CLOUD_CHANNELS,
    LOCAL_CHANNELS,
    ChannelDispatcher,
    ChannelResult,
)
from st_agent.l5.delivery import (
    DAILY_REPORT_CHANNEL,
    DELIVERY_EVENT,
    DeliveryDispatch,
    DeliveryOrchestrator,
    DeliveryRecord,
)
from st_agent.l5.daily_report import (
    NO_CONTENT_NOTE,
    SECTION_LABELS,
    SECTION_NAMES,
    DailyReport,
    DailyReportBuilder,
    ReportSection,
    ReportTemplate,
    ReportTrace,
)
from st_agent.l5.errors import (
    BudgetValidationError,
    ChannelNotWiredError,
    ChannelValidationError,
    DailyReportValidationError,
    DeliveryValidationError,
    FatigueValidationError,
    FrequencyValidationError,
    L5Error,
    SignalAdoptionError,
)
from st_agent.l5.fatigue import (
    ANSWERS,
    DEFAULT_THRESHOLD,
    Answer,
    AnswerResult,
    FatigueMonitor,
    FatigueState,
    Inquiry,
)
from st_agent.l5.frequency import (
    DEFAULT_WINDOW_MINUTES,
    DedupCount,
    FrequencyController,
    GateResult,
    RateDecision,
)
from st_agent.l5.personalize import DEFAULT_WORDING, Personalizer, WordingProfile
from st_agent.l5.registry_adapter import (
    AttentionBudgetFamily,
    DailyReportFamily,
    FatigueFamily,
    FrequencyFamily,
    attention_budget_family,
    daily_report_family,
    fatigue_family,
    frequency_family,
)
from st_agent.l5.runtime import L5Stack, build_l5
from st_agent.l5.signal import (
    SIGNAL_EVENT,
    Signal,
    SignalContent,
    SignalLensStance,
    SignalLevel,
    adopt_signal,
    signal_digest_key,
    signal_event,
)
from st_agent.l5.sources import (
    DELIBERATION_RULE,
    DEFAULT_MONITOR_RULES,
    MONITOR_LENS,
    DeliberationRule,
    MonitorRule,
    signal_event_for_analysis,
    signal_events_for_run,
)

__all__ = [
    "ANSWERS",
    "Answer",
    "AnswerResult",
    "CHANNEL_KINDS",
    "CLOUD_CHANNELS",
    "CONTENT_TYPES",
    "DAILY_REPORT_CHANNEL",
    "DEFAULT_CELL",
    "DEFAULT_CELLS",
    "DEFAULT_MODE",
    "DEFAULT_SLOTS",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW_MINUTES",
    "DEFAULT_WORDING",
    "DEFAULT_MONITOR_RULES",
    "DELIBERATION_RULE",
    "DELIVERY_EVENT",
    "DailyReport",
    "DailyReportBuilder",
    "DailyReportFamily",
    "DailyReportValidationError",
    "DeliberationRule",
    "DedupCount",
    "FEEDBACK_EVENT",
    "FatigueFamily",
    "FatigueMonitor",
    "FatigueState",
    "FatigueValidationError",
    "FrequencyController",
    "FrequencyFamily",
    "FrequencyValidationError",
    "GateResult",
    "Inquiry",
    "LEVELS",
    "LOCAL_CHANNELS",
    "MATRIX_CONFIG_ID",
    "MODE_CONFIG_ID",
    "MONITOR_LENS",
    "MonitorRule",
    "NO_CONTENT_NOTE",
    "RESEND_CONFIG_ID",
    "RateDecision",
    "ReportSection",
    "ReportTemplate",
    "ReportTrace",
    "SECTION_LABELS",
    "SECTION_NAMES",
    "SIGNAL_EVENT",
    "SITUATION_MODES",
    "SLOTS_CONFIG_ID",
    "AttentionBudget",
    "AttentionBudgetFamily",
    "BudgetCell",
    "BudgetCellView",
    "BudgetKey",
    "BudgetMap",
    "BudgetValidationError",
    "BudgetVerdict",
    "ChannelDispatcher",
    "ChannelNotWiredError",
    "ChannelPolicies",
    "ChannelPolicy",
    "ChannelPolicyFamily",
    "ChannelResult",
    "ChannelValidationError",
    "ContentType",
    "DeliveryDispatch",
    "DeliveryOrchestrator",
    "DeliveryRecord",
    "DeliveryValidationError",
    "L5Error",
    "L5Stack",
    "Personalizer",
    "Signal",
    "SignalAdoptionError",
    "SignalContent",
    "SignalEmissionError",
    "SignalLensStance",
    "SignalLevel",
    "SituationMode",
    "TimeSlot",
    "WordingProfile",
    "adopt_signal",
    "attention_budget_family",
    "build_l5",
    "channel_policy_family",
    "daily_report_family",
    "default_budget",
    "fatigue_family",
    "frequency_family",
    "policy_config_id",
    "signal_digest_key",
    "signal_event",
    "signal_event_for_analysis",
    "signal_events_for_run",
]
