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
- **薄装配缝**：`:mod:`runtime`` 的 `build_l5`——两个 01 §7 族的注入点与订阅面
  （M3 的**跨层**组合根 `build_m3_runtime` 归关卡 [`T-INT-004`](../../../项目管理/tasks/T-INT-004-M3集成关卡主动触达投递闭环.md)）

后续段次归同层下游任务：每日报告、去重频控、推送疲劳监控
（[07 §5–§7](../../../docs/技术架构-v2/07-L5-主动触达.md)，
[`T-L5-003`](../../../项目管理/tasks/T-L5-003-每日报告去重频控推送疲劳监控.md)）。

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
from st_agent.l5.errors import (
    BudgetValidationError,
    ChannelNotWiredError,
    ChannelValidationError,
    DeliveryValidationError,
    L5Error,
    SignalAdoptionError,
)
from st_agent.l5.personalize import DEFAULT_WORDING, Personalizer, WordingProfile
from st_agent.l5.registry_adapter import AttentionBudgetFamily, attention_budget_family
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

__all__ = [
    "CHANNEL_KINDS",
    "CLOUD_CHANNELS",
    "CONTENT_TYPES",
    "DAILY_REPORT_CHANNEL",
    "DEFAULT_CELL",
    "DEFAULT_CELLS",
    "DEFAULT_MODE",
    "DEFAULT_SLOTS",
    "DEFAULT_WORDING",
    "DELIVERY_EVENT",
    "LEVELS",
    "LOCAL_CHANNELS",
    "MATRIX_CONFIG_ID",
    "MODE_CONFIG_ID",
    "RESEND_CONFIG_ID",
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
    "SignalLensStance",
    "SignalLevel",
    "SituationMode",
    "TimeSlot",
    "WordingProfile",
    "adopt_signal",
    "attention_budget_family",
    "build_l5",
    "channel_policy_family",
    "default_budget",
    "policy_config_id",
    "signal_digest_key",
    "signal_event",
]
