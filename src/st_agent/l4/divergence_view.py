"""分歧图视图投影（[06 §5 分歧图可视化](../../../docs/技术架构-v2/06-L4-多视角推理.md)）与
[§6 边界与特殊情形](../../../docs/技术架构-v2/06-L4-多视角推理.md) 的一致性语义。

[06 §5] 要两视图——**矩阵视图**（视角 × 结论）为主、**证据网络图**为辅；[§6] 要「全部视角
结论一致 → 明确提示罕见的高度一致」。这两件事的**取数面只有 L4 有**：矩阵要**全部参与视角**
的立场与信心度，而 [`DisagreementMap`](divergence.py) 只带**方向冲突的两方**（一致点更是
要求 ≥2 视角同向，`insufficient-data` 视角被排除），故本模块把
[`DeliberationResult`](deliberation.py) 与 `DisagreementMap` 合成一份**视图就绪**的
[`DivergenceView`]。

**它只搬不判**：四字段（一致点 / 分歧点 / 盲点 / 证据网络）与 Mini Debate 段**原样承载**，
本模块**不做**任何比对、不引入 LLM（06 §3 红线），也不产出「汇总结论」——`DivergenceView`
无 `verdict` / `recommendation` / `summary` 字段（06 架构级红线、[铁律 4](../../../项目管理/工程宪法.md)）。

**UI 描述不在本层**：[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md) 规定描述由
**产出方（L3）生成**，故 `divergence_map` 描述件属 [`T-L4-004.2`](../../../项目管理/tasks/T-L4-004.2-divergence_map描述件与组件升真.md)；
L3 **不得 import L4**（[铁律 7](../../../项目管理/工程宪法.md)，`LAYER_ORDER` 为 `l3 < l4`），
故 `.2` 按**属性取值**收本模块产物——两个约束叠起来只能这么落（任务 `A1`）。

**不落盘**：视图是计算产物（同 [`crosscheck`](crosscheck.py) 的对照产物）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.capability_types import Confidence, Stance
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l4.divergence import (
    Agreement,
    BlindSpot,
    ConflictAnalysis,
    Disagreement,
    DisagreementMap,
    EvidenceNetwork,
)

__all__ = [
    "CONFLICT_KEY_PREFIX",
    "UNANIMITY_NOTICE",
    "ConflictEntry",
    "DivergenceView",
    "DivergenceViewer",
    "LensRow",
]

UNANIMITY_NOTICE = "罕见的高度一致，请警惕群体思维"
"""[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md) 的全部一致提示（本层自有固定文案）。

陈述句、无人称、无情感词——构造期过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
输出校验（[`_notice`]），命中就不送达（唯一真相源 [`contracts.neutrality`](../contracts/neutrality.py)）。
"""

CONFLICT_KEY_PREFIX = "dfv-conflict"
"""Mini Debate 段在视图里的稳定键前缀（`dfv-conflict-<n>`，按段序）。

键由**本层生成、只生成一次**：`.2` 的生成文案槽（`conflict_reasons`）按此键与 `conflicts`
并联——两处各造一次键就会漂移（任务 `A2`）。
"""

_INSUFFICIENT = "insufficient-data"
_OUTPUT_CHECK = NeutralityGuard()


class LensRow(BaseModel):
    """矩阵视图的一行（[06 §5]「视角 × 结论」）。

    `insufficient-data` 视角**照常成行**——06 §2.3 的失败隔离是「不阻塞他者」，
    不是「从图里消失」；把数据不足的视角抹掉才是对用户的误导。
    """

    model_config = ConfigDict(frozen=True)

    lens_id: str
    name: str
    """视角的中性名（阵容可取则取 `Lens.name`，否则退回 `lens_id`——不臆造）。"""
    stance: Stance
    confidence: Confidence
    trace_id: str
    """该视角的推理链锚点（[06 §5] 追问接口的取值面；展开渲染复用 05 §7）。"""


class ConflictEntry(BaseModel):
    """Mini Debate 段在视图里的承载（键 + 分析，见 [`CONFLICT_KEY_PREFIX`]）。"""

    model_config = ConfigDict(frozen=True)

    key: str
    analysis: ConflictAnalysis


class DivergenceView(BaseModel):
    """分歧图的视图数据面（[06 §5–§6]；矩阵 + 网络 + 摘要 + 一致性标志）。

    **无「汇总结论」字段**（06 架构级红线、[铁律 4]）——`agreements` / `disagreements`
    / `blind_spots` / `conflicts` **并列**，不合并、不给统一建议。
    """

    model_config = ConfigDict(frozen=True)

    topic: str
    map_id: str
    """对照产物的锚点 id（[`DisagreementMap.map_id`](divergence.py)）——下钻回对照链用。"""
    as_of: datetime | None = None
    rows: tuple[LensRow, ...] = ()
    """**矩阵视图**的取数面（全部参与视角，含数据不足者）。"""
    agreements: tuple[Agreement, ...] = ()
    disagreements: tuple[Disagreement, ...] = ()
    blind_spots: tuple[BlindSpot, ...] = ()
    evidence_network: EvidenceNetwork = Field(default_factory=EvidenceNetwork)
    """**证据网络图**的取数面（节点 + 引用边）。"""
    conflicts: tuple[ConflictEntry, ...] = ()
    notes: tuple[str, ...] = ()
    """对照件的降级 / 标注（原文承载；`.2` 标为生成文案槽、渲染前过 §6）。"""
    unanimous: bool = False
    """[06 §6] 全部参与视角**均有结论**且立场唯一。"""
    uniform_stance: Stance | None = None
    """一致的那个立场（`unanimous=False` 时为 `None`）。"""
    unanimity_notice: str = ""
    """一致提示（`unanimous=False` 时为空串）；见 [`UNANIMITY_NOTICE`]。"""


class DivergenceViewer:
    """分歧图视图投影门面（[06 §5–§6]）。

    :param roster: 视角阵容（鸭子类型 `get(lens_id) -> Lens`，即
        [`l4.roster.LensRoster`](roster.py)）——只为取**中性名**；缺省 `None`（或寻址失败）
        退回 `lens_id`，**不臆造名字**（任务 `A3`）
    """

    def __init__(self, *, roster: Any = None) -> None:
        self._roster = roster

    def build(self, result: Any, map: DisagreementMap) -> DivergenceView:
        """合成视图：矩阵行取自编排结果，四字段 + Mini Debate 段取自对照产物。

        `result` 为鸭子类型（消费 `.topic` / `.opinions` / `.envelope.as_of`），
        即 [`DeliberationResult`](deliberation.py)；`map` 为 [`DisagreementMap`](divergence.py)
        （**本层类型**——两者的最终装配在组合根 `T-INT-003`）。
        """
        opinions = tuple(getattr(result, "opinions", ()) or ())
        rows = tuple(self._row(o) for o in opinions)
        unanimous, uniform_stance = _uniform(opinions)
        return DivergenceView(
            topic=str(getattr(result, "topic", "") or map.topic),
            map_id=map.map_id,
            as_of=getattr(getattr(result, "envelope", None), "as_of", None),
            rows=rows,
            agreements=map.agreements,
            disagreements=map.disagreements,
            blind_spots=map.blind_spots,
            evidence_network=map.evidence_network,
            conflicts=tuple(
                ConflictEntry(key=f"{CONFLICT_KEY_PREFIX}-{index}", analysis=analysis)
                for index, analysis in enumerate(map.conflicts)
            ),
            notes=map.notes,
            unanimous=unanimous,
            uniform_stance=uniform_stance,
            unanimity_notice=_notice(UNANIMITY_NOTICE) if unanimous else "",
        )

    def _row(self, opinion: Any) -> LensRow:
        """一个视角 → 矩阵的一行（名取阵容，缺省退回 `lens_id`）。"""
        lens_id = str(opinion.lens_id)
        return LensRow(
            lens_id=lens_id,
            name=self._name(lens_id),
            stance=opinion.stance,
            confidence=opinion.confidence,
            trace_id=opinion.trace_id,
        )

    def _name(self, lens_id: str) -> str:
        """视角的中性名（阵容寻址失败即退回 `lens_id`——中性且不臆造，任务 `A3`）。"""
        if self._roster is None:
            return lens_id
        try:
            return self._roster.get(lens_id).name
        except Exception:  # noqa: BLE001 —— 寻址失败＝取名不可得，退回 id 而非报错
            return lens_id


def _uniform(opinions: tuple[Any, ...]) -> tuple[bool, Stance | None]:
    """[06 §6] 一致性判据（任务 `A1`）：**全部参与视角均有结论**且立场唯一。

    `insufficient-data` 视角**不参与**该判定——它的「立场」是「没有立场」，
    把它算进「高度一致」等于把数据缺失误报成共识（[06 §2.3] 的降级语义）。
    另要求 ≥2 个有结论的视角：单视角无「一致」可言。
    """
    stances = {o.stance for o in opinions if o.stance != _INSUFFICIENT}
    if len(stances) != 1:
        return False, None
    if sum(1 for o in opinions if o.stance != _INSUFFICIENT) < 2:
        return False, None
    return True, next(iter(stances))


def _notice(text: str) -> str:
    """提示文案过 [01 §6]；命中即不送达（返回空串），不降级为用户可见的替代文案。"""
    return text if _OUTPUT_CHECK.check_output(text).passed else ""


assert _notice(UNANIMITY_NOTICE) == UNANIMITY_NOTICE, (
    "一致提示须过 01 §6 输出校验（[06 §6] 的固定文案由本模块自持）"
)
