"""L5 主动触达 Ambient Delivery（[07-主动触达](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

承载 [story-07](../../../docs/PRD-v2-Agent/story-07-ambient-delivery.md)：把副驾产出的信号送到用户面前，
核心是**注意力预算管理**——默认策略「少而准」，每日报告为主叙事、实时告警为插入项。

本包按 [07](../../../docs/技术架构-v2/07-L5-主动触达.md) 的段次分模块，当前交付前两段
（[`T-L5-001`](../../../项目管理/tasks/T-L5-001-信号模型注意力预算.md)）：

- **信号模型与采纳**（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`signal`——
  `SignalEmitted` 负载的解释与校验、`signal_id` 的确定性铸造、内容载体（结论 + 逐视角摘要，
  非单视角结论）
- **注意力预算**（[07 §2](../../../docs/技术架构-v2/07-L5-主动触达.md)）：`:mod:`budget`——
  情境 × 时段 × 内容类型的格位判定与「汇总进日报」路由、五情境模式、注册进
  [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)（族适配器见 `:mod:`registry_adapter``）

后续段次归同层下游任务：渠道适配器与投递编排、升级链（[07 §3–§4](../../../docs/技术架构-v2/07-L5-主动触达.md)，
[`T-L5-002`](../../../项目管理/tasks/T-L5-002-渠道适配器投递编排与升级链.md)）；每日报告、去重频控、
推送疲劳监控（[07 §5–§7](../../../docs/技术架构-v2/07-L5-主动触达.md)，
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
from st_agent.l5.errors import BudgetValidationError, L5Error, SignalAdoptionError
from st_agent.l5.registry_adapter import AttentionBudgetFamily, attention_budget_family
from st_agent.l5.signal import (
    SIGNAL_EVENT,
    Signal,
    SignalContent,
    SignalLensStance,
    SignalLevel,
    adopt_signal,
    signal_digest_key,
)

__all__ = [
    "CONTENT_TYPES",
    "DEFAULT_CELL",
    "DEFAULT_CELLS",
    "DEFAULT_MODE",
    "DEFAULT_SLOTS",
    "MATRIX_CONFIG_ID",
    "MODE_CONFIG_ID",
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
    "ContentType",
    "L5Error",
    "Signal",
    "SignalAdoptionError",
    "SignalContent",
    "SignalLensStance",
    "SignalLevel",
    "SituationMode",
    "TimeSlot",
    "adopt_signal",
    "attention_budget_family",
    "default_budget",
    "signal_digest_key",
]
