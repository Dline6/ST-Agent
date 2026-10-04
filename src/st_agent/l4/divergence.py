"""分歧图产物模型（[06 §3 交叉对照](../../../docs/技术架构-v2/06-L4-多视角推理.md)）与
Mini Debate 冲突记录（[06 §4](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

`DisagreementMap` 是 [06 §3] 表列的四字段产物——一致点 / 分歧点 / 盲点 / 证据网络；
另带 §4 Mini Debate 段的 `conflicts` 与中性降级 `notes`、对照链 `trace`（**加性字段**，
四字段的口径与 §3 原文一致）。它是**算法性比对**的结果（证据引用交集 / 差集 + 结论方向比对，
见 [`crosscheck`](crosscheck.py)），**不含任何合并结论**——L4 不存在「汇总结论」组件（06 架构级红线、
铁律 4）。

两处外部依赖以**鸭子端口**形态声明（真实现在组合根 `T-INT-003` 接，本包只定形态）：

- `DimensionCatalog`——盲点的「数据缓存中与主题相关的数据维度」可枚举集来源
  （真读 [数据库设计 §05 指标字典](../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)
  的 `indicator_dictionary` 与表结构）；**未注入 → 盲点为空 + 降级标注**（任务 A2）
- `EvidenceReviewer`——Mini Debate 中「对冲突证据重新评估（成立 / 时效 / 权重）」的研判端点；
  缺省为确定性件（三态皆 `unknown`、不触网），真研判端点归组合根（任务 A3）

**中性形态约束（铁律 2 / 01 §6）**：`ConflictAnalysis.reasons` 与 `DisagreementMap.notes` 为
**生成文案**，渲染前须逐条过 `check_output`（唯一真相源 [`contracts.neutrality`](../contracts/neutrality.py)）；
其余字段为数据引用（§1 ID / 枚举），原样呈现不过 §6。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.capability_types import Stance
from st_agent.contracts.trace import Trace

__all__ = [
    "Agreement",
    "BlindSpot",
    "ConflictAnalysis",
    "Dimension",
    "DimensionCatalog",
    "Disagreement",
    "DisagreementMap",
    "DisagreementSide",
    "EvidenceEdge",
    "EvidenceNetwork",
    "EvidenceNode",
    "EvidenceReview",
    "EvidenceReviewer",
    "evidence_kind_of",
]

# ───────────────────────── 证据引用分类（§1 四类 ID 的字符串形态） ─────────────────────────

_EVIDENCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ann_", "announcement_id"),
    ("snap_", "dataset_snapshot_id"),
    ("run_", "skill_run_id"),
    ("mn_", "memory_node_id"),
)
"""§1 四类证据 ID 的前缀（[`contracts.identifiers`](../contracts/identifiers.py)）。

[01 §3](../../../docs/技术架构-v2/01-平台共享契约.md) 规定 `LensOpinion.evidence_refs` 只承载
这四类 ID 的**字符串形态**（不含显式 kind），故对照件按前缀还原类型；无法识别的引用落到
``"unknown"``——**如实标注，不猜测**（认不出可能是契约外的引用，不该被当成已知类型渲染）。
"""


def evidence_kind_of(ref: str) -> str:
    """按 §1 前缀还原证据引用的 ID 类型；认不出返 ``"unknown"``。"""
    for prefix, kind in _EVIDENCE_PREFIXES:
        if ref.startswith(prefix):
            return kind
    return "unknown"


# ───────────────────────── 06 §3 DisagreementMap ─────────────────────────


class EvidenceNode(BaseModel):
    """证据网络节点（06 §3 `evidence_network`）：一条被引用的证据。"""

    model_config = ConfigDict(frozen=True)

    ref: Annotated[str, Field(min_length=3, max_length=128)]
    """被引证据的 §1 ID 字符串。"""
    kind: str = "unknown"
    """ID 类型（`announcement_id` / … / `unknown`），由 [`evidence_kind_of`] 还原。"""


class EvidenceEdge(BaseModel):
    """证据网络引用边（06 §3 `evidence_network`）：某视角 → 某证据。"""

    model_config = ConfigDict(frozen=True)

    lens_id: Annotated[str, Field(min_length=3, max_length=128)]
    ref: Annotated[str, Field(min_length=3, max_length=128)]


class EvidenceNetwork(BaseModel):
    """证据节点 + 引用关系（06 §3 `evidence_network`；分歧图网络视图的数据源）。"""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()


class Agreement(BaseModel):
    """一致点（06 §3）：被 **≥2 视角**引用、且引用它的视角**结论同向**的同一证据。"""

    model_config = ConfigDict(frozen=True)

    ref: Annotated[str, Field(min_length=3, max_length=128)]
    kind: str = "unknown"
    stance: Stance
    """引用该证据的各视角的**共同立场方向**（同向才成一致点）。"""
    lens_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _at_least_two_lenses(self) -> "Agreement":
        if len(self.lens_ids) < 2:
            raise ValueError("一致点须由 ≥2 个视角共同引用（06 §3）")
        return self


class DisagreementSide(BaseModel):
    """分歧的一侧（一个视角）：立场 + 本方引用而对方未引用的证据（差集）。"""

    model_config = ConfigDict(frozen=True)

    lens_id: Annotated[str, Field(min_length=3, max_length=128)]
    stance: Stance
    only_refs: tuple[str, ...] = ()
    """本方引用、对方**未引用**的证据——「冲突证据对」的取值面。"""


class Disagreement(BaseModel):
    """分歧点（06 §3）：**方向相反**（`positive` vs `negative`）的两个视角。

    :attr:`topic` 为争议对象＝本轮编排主题（任务 A1：算法性对照不生成子话题标签——
    那会引入 LLM 裁判，违 06 §3 红线）；子话题粒度留后续。
    """

    model_config = ConfigDict(frozen=True)

    topic: str
    sides: tuple[DisagreementSide, DisagreementSide]
    """方向相反的两方（顺序稳定，见 [`crosscheck`](crosscheck.py) 的排序口径）。"""


class BlindSpot(BaseModel):
    """盲点（06 §3）：与主题相关、但**无任何参与视角覆盖**的数据维度。"""

    model_config = ConfigDict(frozen=True)

    key: Annotated[str, Field(min_length=1, max_length=128)]
    """维度键（如 `indicator_dictionary.indicator_key` 或表名）。"""
    label: Annotated[str, Field(min_length=1)]
    """中性标签（如字典 `name_cn`）。"""
    source: str = ""
    """维度来源（表名 / 域），可空。"""


class Dimension(BaseModel):
    """一份「与主题相关的数据维度」枚举项（由 [`DimensionCatalog`] 给出）。"""

    model_config = ConfigDict(frozen=True)

    key: Annotated[str, Field(min_length=1, max_length=128)]
    label: Annotated[str, Field(min_length=1)]
    skill_ids: tuple[str, ...] = ()
    """产出该维度的 Skill（引用 `skill_id`）。对照件据此判「是否被视角覆盖」；
    为空表示**覆盖不可判**——如实跳出不报，不臆断为盲点。"""


class EvidenceReview(BaseModel):
    """对单条冲突证据的一轮重评估（06 §4：「证据是否成立 / 时效 / 权重」）。"""

    model_config = ConfigDict(frozen=True)

    ref: Annotated[str, Field(min_length=3, max_length=128)]
    validity: Literal["valid", "invalid", "unknown"] = "unknown"
    """证据是否成立（缺省件不判，留 `unknown`）。"""
    freshness: Literal["fresh", "stale", "unknown"] = "unknown"
    """时效（缺省件不判，留 `unknown`）。"""
    weight: float | None = None
    """权重（缺省件不判，留 `None`）。"""
    note: str = ""
    """中性说明（生成文案，渲染前过 §6）。"""


class ConflictAnalysis(BaseModel):
    """Mini Debate 的冲突原因分析（06 §4；**结构化对照记录，非拟人化对话**）。

    「哪条证据被 A 引用被 B 忽略」＝ :attr:`sides` 的 `only_refs` 差集；
    「哪个前提假设不同」＝ :attr:`premise_related` 与 :attr:`shared_skills`。
    """

    model_config = ConfigDict(frozen=True)

    topic: str
    sides: tuple[DisagreementSide, DisagreementSide]
    shared_refs: tuple[str, ...] = ()
    """双方**共同引用**的证据（触发条件之一「引用证据重叠」的取值面）。"""
    premise_related: bool = False
    """前提是否相关（任务 A4：以两方 `skill_bundle` **有交集**为算法代理）。"""
    shared_skills: tuple[str, ...] = ()
    """共享的 `skill_id`（`premise_related` 的取值面）。"""
    reviews: tuple[EvidenceReview, ...] = ()
    """对冲突证据（双方引用面并集）的一轮重评估。"""
    reasons: tuple[str, ...] = ()
    """中性陈述式冲突原因（生成文案，逐条过 §6）。"""


class DisagreementMap(BaseModel):
    """06 §3 对照产物（四字段 + §4 Mini Debate 段 + 对照链，**并列不合并**）。"""

    model_config = ConfigDict(frozen=True)

    topic: str
    agreements: tuple[Agreement, ...] = ()
    disagreements: tuple[Disagreement, ...] = ()
    blind_spots: tuple[BlindSpot, ...] = ()
    evidence_network: EvidenceNetwork = Field(default_factory=EvidenceNetwork)
    conflicts: tuple[ConflictAnalysis, ...] = ()
    """06 §4 段：Mini Debate 触发的冲突对及其原因分析（未触发即空）。"""
    notes: tuple[str, ...] = ()
    """中性降级 / 标注（如未注入维度目录、维度覆盖不可判的条数；生成文案，过 §6）。"""
    map_id: str = ""
    """本分歧图的锚点 id（`dvg_<hex20>`，[`crosscheck`](crosscheck.py) 生成；
    01 §4 `ConclusionRef(kind="divergence_map")` 的取值面）。"""
    trace: Trace | None = None
    """对照链（`aggregation` 步 + `divergence_map` 结论锚，01 §4）。"""


# ───────────────────────── 鸭子端口（真实现归 T-INT-003 组合根） ─────────────────────────


class DimensionCatalog(Protocol):
    """盲点的「与主题相关的数据维度」可枚举集来源（任务 A2）。

    鸭子类型 ``dimensions_for(topic) -> tuple[Dimension, ...]``：返回该主题下
    缓存中**相关**的数据维度（真实现按 [数据库设计 §05](../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)
    的表结构与 `indicator_dictionary` 取，归 [`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)
    组合根）。**未注入 → 盲点为空 + 降级标注**，不静默丢弃。
    """

    def dimensions_for(self, topic: str) -> tuple[Dimension, ...]: ...


class EvidenceReviewer(Protocol):
    """Mini Debate 中证据重评估的研判端点（任务 A3；真研判端点归 `T-INT-003`）。

    鸭子类型 ``review(ref, *, lens_ids, as_of) -> EvidenceReview``：对一条冲突证据给出
    成立 / 时效 / 权重判断。缺省件（[`crosscheck._DefaultEvidenceReviewer`](crosscheck.py)）
    一律留 `unknown`、**不触网**。
    """

    def review(
        self, ref: str, *, lens_ids: tuple[str, ...], as_of: datetime | None,
    ) -> EvidenceReview: ...
