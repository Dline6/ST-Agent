"""输出复用子系统（T-L1-001.4；03 §1.2 步骤 6 + §1.3）。

- :mod:`st_agent.l1.reuse.models` —— 登记索引与复用视图的数据形态
- :mod:`st_agent.l1.reuse.freshness` —— L0 §05 口径适配器 + 超龄兜底
- :mod:`st_agent.l1.reuse.registry` —— ``OutputRegistry`` 门面

对外统一从 ``st_agent.l1.reuse`` import。
"""

from __future__ import annotations

from st_agent.l1.reuse.errors import (
    OutputNotRegisteredError,
    ReuseError,
    ReuseValidationError,
)
from st_agent.l1.reuse.freshness import (
    DEFAULT_MAX_AGE,
    FreshnessOracle,
    MarketFreshnessOracle,
    age_based_verdict,
    unavailable_envelope,
)
from st_agent.l1.reuse.models import (
    OUTPUT_PREFIX,
    REUSABLE_STATUSES,
    ReusableOutput,
    ReuseView,
)
from st_agent.l1.reuse.registry import OutputRegistry

__all__ = [
    "DEFAULT_MAX_AGE",
    "OUTPUT_PREFIX",
    "REUSABLE_STATUSES",
    "FreshnessOracle",
    "MarketFreshnessOracle",
    "OutputNotRegisteredError",
    "OutputRegistry",
    "ReusableOutput",
    "ReuseError",
    "ReuseValidationError",
    "ReuseView",
    "age_based_verdict",
    "unavailable_envelope",
]
