"""L4 · 多视角推理（Multi-Lens Deliberation，[06](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

复杂决策进入 Deliberation：多个**中性视角**并行分析 → 结构化观点 → 交叉对照 →
**分歧图**。用户是唯一决策者；L4 **不存在「汇总结论」组件**（06 架构级红线）。

本包的落地进度（按 [06] 各节）：

- [`lens`](lens.py)——视角模型 Lens（[06 §1](../../../docs/技术架构-v2/06-L4-多视角推理.md)）：六字段定义 + 构造期不变量
- [`builtin`](builtin.py)——官方预置 7 视角常设阵容种子（[06 §1](../../../docs/技术架构-v2/06-L4-多视角推理.md) 阵容表）
- [`roster`](roster.py)——阵容门面（[`T-L4-001`](../../../项目管理/tasks/T-L4-001-视角模型Lens常设阵容用户可增删.md)）：播种 / 增删自定义 / 内置停用 / 落盘
- [`deliberation`](deliberation.py)——多视角执行编排（[06 §2](../../../docs/技术架构-v2/06-L4-多视角推理.md)，[`T-L4-002`](../../../项目管理/tasks/T-L4-002-多视角执行编排.md)）：并行执行 / 失败隔离 / 结构化观点 / 决策沉淀
- [`divergence`](divergence.py)——分歧图产物模型（[06 §3](../../../docs/技术架构-v2/06-L4-多视角推理.md)）与 Mini Debate 冲突记录（[§4](../../../docs/技术架构-v2/06-L4-多视角推理.md)，[`T-L4-003`](../../../项目管理/tasks/T-L4-003-交叉对照MiniDebate冲突质询.md)）：`DisagreementMap` + 维度目录 / 证据重评估两个鸭子端口
- [`crosscheck`](crosscheck.py)——交叉对照 + Mini Debate 引擎（[06 §3–§4](../../../docs/技术架构-v2/06-L4-多视角推理.md)，[`T-L4-003`](../../../项目管理/tasks/T-L4-003-交叉对照MiniDebate冲突质询.md)）：算法性比对（证据交集 / 差集 + 方向比对），**不引入 LLM 裁判**
- [`divergence_view`](divergence_view.py)——分歧图视图投影（[06 §5](../../../docs/技术架构-v2/06-L4-多视角推理.md)）与一致性语义（[§6](../../../docs/技术架构-v2/06-L4-多视角推理.md)，[`T-L4-004.1`](../../../项目管理/tasks/T-L4-004.1-L4侧分歧图视图投影与一致性语义.md)）：矩阵行（全部参与视角 × 立场 / 信心度 / 链锚）+ 四字段透传 + Mini Debate 段 + 一致提示；UI 描述件归 `.2`（[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md) 的产出方＝L3）
- [`ports`](ports.py)——三个鸭子端口的**真实现**（[`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)）：`LlmOpinionSynthesizer`（方向研判）· `MarketDimensionCatalog`（盲点维度目录，读本地市场库 + 官方 Pack 旁的维度声明）· `LlmEvidenceReviewer`（证据重研判）
- [`analyze`](analyze.py)——`analyze` 去向的 L4 装配件（[`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)）：编排 → 对照 → 视图三段的唯一衔接点 + `AnalyzeOutcome`

尚未落地：接进 L3 总线 / 组合根属 [`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)；[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md) 的触达联动属 M3/L5。

**中性视角（铁律 2）**：Lens 的 `name` 与 `description` 在保存时须过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
命名校验（唯一真相源 [`contracts.neutrality`](../contracts/neutrality.py)，无关闭开关）；跨层协作一律经 01-平台共享契约，只向下依赖（铁律 7）。
"""

from __future__ import annotations

from st_agent.l4.errors import (
    BuiltinLensError,
    L4Error,
    LensNotFoundError,
    LensValidationError,
    PortUnavailableError,
)

__all__ = [
    "BuiltinLensError",
    "L4Error",
    "LensNotFoundError",
    "LensValidationError",
    "PortUnavailableError",
]
