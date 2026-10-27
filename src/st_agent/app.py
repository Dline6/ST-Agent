"""生产组合根门面（[00 §5 反向流](../../docs/技术架构-v2/00-架构总览.md)）。

装配各部件的实现按「面 / 里程碑」拆在 :mod:`st_agent.composition`——本模块只做
**再导出**：`build_m1_runtime` … `build_m5_runtime` 与各层薄门面仍从这里取，生产入口
（`python -m st_agent`）与测试的 import 面不变。

**`app` 不是第七层**（同 `ui`，[D-060] ⑤）：它只**向下** import 各层做装配，任何层不得
反向 import 它——方向由 [`tests/test_layering.py`](../../tests/test_layering.py) 的
`test_app_is_composition_root_only` 钉住；`composition` 与 `app` 同属装配面，层不得依赖。
"""

from __future__ import annotations

from st_agent.composition.adapters import (
    LlmPatternObserver,
    LlmTrainingUnderstander,
)

from st_agent.composition.agent import (
    InvestigationOutcome,
)

from st_agent.composition.facades.chat import (
    DialogFacade,
    TurnResult,
)

from st_agent.composition.facades.capability import (
    McpFacade,
    SkillsFacade,
    StudioFacade,
    TrialSessions,
)

from st_agent.composition.facades.delivery import (
    DeliveryFacade,
)

from st_agent.composition.facades.eco import (
    EcoFacade,
)

from st_agent.composition.facades.memory import (
    GraphFacade,
    MemoryPositionsFacade,
)

from st_agent.composition.facades.reasoning import (
    DeliberationFacade,
)

from st_agent.composition.facades.reflection import (
    ReflectionFacade,
)

from st_agent.composition.facades.settings import (
    SettingsFacade,
)

from st_agent.composition.runtime.m1 import (
    M1Runtime,
    build_m1_runtime,
)

from st_agent.composition.runtime.m2 import (
    M2Runtime,
    build_m2_runtime,
)

from st_agent.composition.runtime.m3 import (
    M3Runtime,
    TickResult,
    build_m3_runtime,
)

from st_agent.composition.runtime.m4 import (
    M4Runtime,
    build_m4_runtime,
)

from st_agent.composition.runtime.m5 import (
    M5Runtime,
    build_m5_runtime,
)

__all__ = [
    "DeliberationFacade",
    "DeliveryFacade",
    "DialogFacade",
    "EcoFacade",
    "GraphFacade",
    "InvestigationOutcome",
    "LlmPatternObserver",
    "LlmTrainingUnderstander",
    "M1Runtime",
    "M2Runtime",
    "M3Runtime",
    "M4Runtime",
    "M5Runtime",
    "McpFacade",
    "MemoryPositionsFacade",
    "ReflectionFacade",
    "SettingsFacade",
    "SkillsFacade",
    "StudioFacade",
    "TickResult",
    "TrialSessions",
    "TurnResult",
    "build_m1_runtime",
    "build_m2_runtime",
    "build_m3_runtime",
    "build_m4_runtime",
    "build_m5_runtime",
]
