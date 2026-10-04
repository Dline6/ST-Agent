"""交叉对照 + Mini Debate 冲突质询（[06 §3–§4](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

接 [`T-L4-002`](../../../项目管理/tasks/T-L4-002-多视角执行编排.md) 的 `DeliberationResult`，
对并列的各视角 [`LensOpinion`](../contracts/capability_types.py) 做**算法性**比对，产出
[`DisagreementMap`](divergence.py)（一致点 / 分歧点 / 盲点 / 证据网络）；当两视角结论方向冲突时
触发 **Mini Debate**，产出结构化的冲突原因分析（哪条证据被 A 引用被 B 忽略 / 前提是否不同）。

**架构级红线（06 §3）**：对照是**算法性比对**（证据引用交集 / 差集 + 结论方向比对），
**不引入额外 LLM「裁判」**——任何带裁判的实现在架构上违规。本模块不 import 任何 LLM / 出网面。
L4 **不存在「汇总结论」组件**：本模块只并列产出对照记录，绝不合并分歧、不给统一建议（铁律 4）。

**两处外部依赖以鸭子端口声明（真实现归组合根 `T-INT-003`）**：

- [`DimensionCatalog`](divergence.py)——盲点的相关维度枚举（任务 A2）；未注入 → 盲点为空 + 标注
- [`EvidenceReviewer`](divergence.py)——Mini Debate 的证据重评估（任务 A3）；缺省为确定性件

职责边界（任务范围）：§5 分歧图可视化、§6 边界与特殊情形归 `T-L4-004`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from st_agent.contracts import (
    ConclusionRef,
    NeutralityGuard,
    Trace,
    TraceId,
    TraceStep,
    digest_of,
)
from st_agent.contracts.identifiers import new_id
from st_agent.l4.divergence import (
    Agreement,
    BlindSpot,
    ConflictAnalysis,
    Disagreement,
    DisagreementMap,
    DisagreementSide,
    EvidenceEdge,
    EvidenceNetwork,
    EvidenceNode,
    EvidenceReview,
    evidence_kind_of,
)

__all__ = ["CrossExaminer"]

_DIRECTIONAL: frozenset[str] = frozenset({"positive", "negative"})
"""方向性立场（06 §4 的「方向冲突」只在二者之间判定）。"""

_INSUFFICIENT = "insufficient-data"

_OUTPUT_CHECK = NeutralityGuard()
"""生成文案的中性化校验件（01 §6 执行点 2；`reasons` / `notes` 渲染前必经）。"""

_MAP_ID_PREFIX = "dvg"
"""分歧图锚点 id 前缀（01 §4 `ConclusionRef(kind="divergence_map")` 的取值面）。

01 §1 未单列 `divergence_map_id` 类型，故本层以局部前缀生成**有界字符串**作锚点——
**已知边界**（非欠账）：若日后要把它纳入 §1 标识体系，属契约变更（铁律 8）。
"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class _DefaultEvidenceReviewer:
    """确定性缺省重评估件——三态皆 `unknown`、权重不判，**不触网、不臆断**（任务 A3）。

    「证据是否成立 / 时效 / 权重」的**真研判**是本端口的语义（须懂证据内容），
    故缺省件如实留 `unknown`，把判断交给注入的重评估端点（`T-INT-003` 组合根）。
    编辑层不替用户判断证据可信度——这与铁律 4「不合并分歧、不给统一建议」同源。
    """

    def review(
        self, ref: str, *, lens_ids: tuple[str, ...], as_of: datetime | None,
    ) -> EvidenceReview:
        return EvidenceReview(
            ref=ref,
            note="缺省重评估件不做成立 / 时效 / 权重判断（真研判端点归 T-INT-003）",
        )


def _checked_lines(lines: list[str]) -> tuple[str, ...]:
    """逐条过 §6，剔除命中拟人化者；有剔除即补一条中性说明（不静默丢）。"""
    kept: list[str] = []
    dropped = 0
    for line in lines:
        if _OUTPUT_CHECK.check_output(line).passed:
            kept.append(line)
        else:
            dropped += 1
    if dropped:
        kept.append(f"{dropped} 条生成文案命中中性化校验，已从对照记录中剔除（01 §6 执行点 2）")
    return tuple(kept)


def _safe_note(note: str) -> str:
    """重评估说明过 §6；命中即降级为中性表述（不送达违规文案）。"""
    if not note or _OUTPUT_CHECK.check_output(note).passed:
        return note
    return "重评估说明命中中性化校验，已降级为中性表述（01 §6 执行点 2）"


def _safe_review(review: EvidenceReview, ref: str) -> EvidenceReview:
    """把重评估产出的 `ref` 钉回被评证据，并给其说明过 §6。"""
    return review.model_copy(update={"ref": ref, "note": _safe_note(review.note)})


class CrossExaminer:
    """交叉对照 + Mini Debate 门面（[06 §3–§4](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    :param roster: 视角阵容（鸭子类型 `get(lens_id) -> Lens`，即
        [`l4.roster.LensRoster`](roster.py)）——取 `skill_bundle` 判盲点覆盖与前提相关性；
        缺省 `None` → 该两项均**如实不判**（不臆断）
    :param catalog: 相关维度枚举端口（[`DimensionCatalog`](divergence.py)）；缺省 `None` →
        `blind_spots` 为空 + 降级标注（任务 A2）
    :param reviewer: 证据重评估端口（[`EvidenceReviewer`](divergence.py)）；缺省用确定性件
        （三态 `unknown`、不触网，任务 A3）
    :param now: 当前时刻取法（缺省本机带时区时间；注入以测对照链时间戳）
    """

    def __init__(
        self,
        *,
        roster: Any = None,
        catalog: Any = None,
        reviewer: Any = None,
        now: Any = None,
    ) -> None:
        self._roster = roster
        self._catalog = catalog
        self._reviewer = reviewer or _DefaultEvidenceReviewer()
        self._now = now or _now

    # ───────────────────────── 主入口（06 §3） ─────────────────────────

    def cross_examine(self, result: Any) -> DisagreementMap:
        """对一次 Deliberation 结果做交叉对照（含 §4 Mini Debate），返 `DisagreementMap`。

        `result` 为鸭子类型（消费 `.topic` / `.opinions` / `.envelope.as_of`），
        即 [`DeliberationResult`](deliberation.py)。
        """
        moment = self._now()
        topic = str(getattr(result, "topic", "") or "")
        opinions = tuple(getattr(result, "opinions", ()) or ())
        as_of = getattr(getattr(result, "envelope", None), "as_of", None)
        map_id = new_id(_MAP_ID_PREFIX)

        if not opinions:
            return DisagreementMap(
                topic=topic, map_id=map_id,
                notes=_checked_lines(["本次编排无任何视角观点，无可对照（06 §3）"]),
            )

        stance_of = {o.lens_id: o.stance for o in opinions}
        refs_of = {o.lens_id: tuple(dict.fromkeys(o.evidence_refs)) for o in opinions}

        network = self._network(refs_of)
        agreements = self._agreements(stance_of, refs_of)
        disagreements = self._disagreements(topic, stance_of, refs_of)
        conflicts, conflict_notes = self._mini_debates(
            topic, disagreements, stance_of, refs_of, as_of,
        )
        blind_spots, blind_notes = self._blind_spots(topic, opinions)

        notes = _checked_lines([
            *blind_notes,
            *conflict_notes,
            *([f"阵容未注入，盲点与前提相关性不可判（06 §3–§4）"] if self._roster is None else []),
        ])
        base = DisagreementMap(
            topic=topic, agreements=agreements, disagreements=disagreements,
            blind_spots=blind_spots, evidence_network=network, conflicts=conflicts,
            notes=notes, map_id=map_id,
        )
        trace = (
            Trace(trace_id=TraceId.generate())
            .append_step(TraceStep(
                step_type="aggregation", ref=map_id,
                input_digest=digest_of({"stances": stance_of,
                                        "evidence": {k: list(v) for k, v in refs_of.items()}}),
                output_digest=digest_of({
                    "agreements": len(agreements), "disagreements": len(disagreements),
                    "blind_spots": len(blind_spots), "conflicts": len(conflicts),
                }),
                duration_ms=0, timestamp=moment,
            ))
            .conclude(ConclusionRef(kind="divergence_map", ref=map_id))
        )
        return base.model_copy(update={"trace": trace})

    # ───────────────────────── 06 §3：一致点 / 分歧点 / 证据网络 ─────────────────────────

    @staticmethod
    def _network(refs_of: dict[str, tuple[str, ...]]) -> EvidenceNetwork:
        """全部被引证据的节点 + `lens_id → ref` 引用边（按 id 排序，稳定可复算）。"""
        nodes = tuple(
            EvidenceNode(ref=ref, kind=evidence_kind_of(ref))
            for ref in sorted({r for refs in refs_of.values() for r in refs})
        )
        edges = tuple(
            EvidenceEdge(lens_id=lid, ref=ref)
            for lid in sorted(refs_of) for ref in refs_of[lid]
        )
        return EvidenceNetwork(nodes=nodes, edges=edges)

    @staticmethod
    def _agreements(
        stance_of: dict[str, str], refs_of: dict[str, tuple[str, ...]],
    ) -> tuple[Agreement, ...]:
        """被 ≥2 个**有结论**视角引用、且这些视角立场同向的证据。"""
        citing: dict[str, list[str]] = {}
        for lid, refs in refs_of.items():
            if stance_of.get(lid) == _INSUFFICIENT:
                continue  # 数据不足者未给结论，不参与一致点（06 §3「同向结论」）
            for ref in refs:
                citing.setdefault(ref, []).append(lid)
        out: list[Agreement] = []
        for ref in sorted(citing):
            lenses = sorted(set(citing[ref]))
            if len(lenses) < 2:
                continue
            stances = {stance_of[lid] for lid in lenses}
            if len(stances) != 1:
                continue  # 立场不同向 → 不是一致点（其冲突面归分歧点）
            out.append(Agreement(
                ref=ref, kind=evidence_kind_of(ref),
                stance=next(iter(stances)), lens_ids=tuple(lenses),
            ))
        return tuple(out)

    @staticmethod
    def _disagreements(
        topic: str, stance_of: dict[str, str], refs_of: dict[str, tuple[str, ...]],
    ) -> tuple[Disagreement, ...]:
        """**方向相反**的两两视角（`positive` vs `negative`），各含冲突证据差集。"""
        directional = sorted(lid for lid, s in stance_of.items() if s in _DIRECTIONAL)
        out: list[Disagreement] = []
        for i, a in enumerate(directional):
            for b in directional[i + 1:]:
                if {stance_of[a], stance_of[b]} != _DIRECTIONAL:
                    continue
                out.append(Disagreement(topic=topic, sides=(
                    _side(a, set(refs_of.get(b, ())), stance_of, refs_of),
                    _side(b, set(refs_of.get(a, ())), stance_of, refs_of),
                )))
        return tuple(out)

    # ───────────────────────── 06 §4：Mini Debate ─────────────────────────

    def _mini_debates(
        self,
        topic: str,
        disagreements: tuple[Disagreement, ...],
        stance_of: dict[str, str],
        refs_of: dict[str, tuple[str, ...]],
        as_of: datetime | None,
    ) -> tuple[tuple[ConflictAnalysis, ...], list[str]]:
        """对方向冲突对做一轮交叉质询；不满足触发条件者记明原因（GWT-4）。"""
        conflicts: list[ConflictAnalysis] = []
        notes: list[str] = []
        for d in disagreements:
            a, b = d.sides[0].lens_id, d.sides[1].lens_id
            refs_a, refs_b = set(refs_of.get(a, ())), set(refs_of.get(b, ()))
            shared = tuple(sorted(refs_a & refs_b))
            shared_skills = self._shared_skills(a, b)
            premise_related = bool(shared_skills)
            if not shared and not premise_related:
                notes.append(
                    f"视角「{self._label(a)}」与「{self._label(b)}」结论方向相反，"
                    "但无共同引用证据、亦无共同 Skill，未触发 Mini Debate（06 §4 触发条件）"
                )
                continue
            reviews = tuple(
                _safe_review(self._reviewer.review(ref, lens_ids=(a, b), as_of=as_of), ref)
                for ref in sorted(refs_a | refs_b)
            )
            conflicts.append(ConflictAnalysis(
                topic=topic, sides=d.sides, shared_refs=shared,
                premise_related=premise_related, shared_skills=shared_skills,
                reviews=reviews,
                reasons=_checked_lines(self._conflict_reasons(
                    a, b, d.sides, shared, premise_related, shared_skills,
                )),
            ))
        return tuple(conflicts), notes

    def _conflict_reasons(
        self,
        a: str, b: str,
        sides: tuple[DisagreementSide, DisagreementSide],
        shared: tuple[str, ...], premise_related: bool, shared_skills: tuple[str, ...],
    ) -> list[str]:
        """中性陈述式冲突原因（06 §4「哪条证据被 A 引用被 B 忽略、哪个前提假设不同」）。"""
        na, nb = self._label(a), self._label(b)
        lines = [
            f"视角「{na}」与「{nb}」结论方向相反（{sides[0].stance} / {sides[1].stance}）",
            f"被「{na}」引用而被「{nb}」忽略的证据 {len(sides[0].only_refs)} 条，"
            f"反向 {len(sides[1].only_refs)} 条",
        ]
        if shared:
            lines.append(f"双方共同引用证据 {len(shared)} 条，冲突源于对同一证据的解读不同")
        if premise_related:
            lines.append(f"两视角共享 Skill {len(shared_skills)} 项，前提相关")
        else:
            lines.append("两视角无共同 Skill，冲突源于前提或口径不同")
        return lines

    def _shared_skills(self, a: str, b: str) -> tuple[str, ...]:
        """两视角 `skill_bundle` 的交集（任务 A4：共享数据来源 ⇒ 前提相关的算法代理）。"""
        if self._roster is None:
            return ()
        try:
            sa = set(self._roster.get(a).skill_bundle)
            sb = set(self._roster.get(b).skill_bundle)
        except Exception:  # noqa: BLE001 —— 阵容寻址失败即如实不判（不臆断）
            return ()
        return tuple(sorted(sa & sb))

    def _label(self, lens_id: str) -> str:
        """视角的中性名（阵容可取则用 `name`，否则退回 `lens_id`——中性且不臆造）。"""
        if self._roster is None:
            return lens_id
        try:
            return self._roster.get(lens_id).name
        except Exception:  # noqa: BLE001
            return lens_id

    # ───────────────────────── 06 §3：盲点 ─────────────────────────

    def _blind_spots(
        self, topic: str, opinions: tuple[Any, ...],
    ) -> tuple[tuple[BlindSpot, ...], list[str]]:
        """相关维度中、未被任何参与视角 `skill_bundle` 覆盖者（任务 A2）。"""
        if self._catalog is None:
            return (), [
                "未注入维度目录，盲点未判定（06 §3；组合根按 MarketDb 表结构 + 指标字典接线，"
                "归 T-INT-003）"
            ]
        if self._roster is None:
            return (), ["已注入维度目录但未注入阵容，维度覆盖不可判，盲点未判定（06 §3）"]

        covered: set[str] = set()
        for o in opinions:
            try:
                covered.update(self._roster.get(o.lens_id).skill_bundle)
            except Exception:  # noqa: BLE001
                continue
        try:
            dimensions = tuple(self._catalog.dimensions_for(topic))
        except Exception as exc:  # noqa: BLE001 —— 目录失败显式标注，不静默当无盲点
            return (), [f"维度目录查询失败，盲点未判定：{exc}"]

        spots: list[BlindSpot] = []
        unknown = 0
        for dim in dimensions:
            if not dim.skill_ids:
                unknown += 1  # 覆盖不可判 → 不臆断为盲点
                continue
            if covered.isdisjoint(dim.skill_ids):
                spots.append(BlindSpot(key=dim.key, label=dim.label))
        notes = (
            [f"{unknown} 个维度未声明产出 Skill，覆盖不可判，未纳入盲点"] if unknown else []
        )
        return tuple(spots), notes


def _side(
    lens_id: str, other_refs: set[str],
    stance_of: dict[str, str], refs_of: dict[str, tuple[str, ...]],
) -> DisagreementSide:
    """构造分歧的一侧：本方引用、对方未引用的证据（差集，排序稳定）。"""
    mine = set(refs_of.get(lens_id, ()))
    return DisagreementSide(
        lens_id=lens_id, stance=stance_of[lens_id],
        only_refs=tuple(sorted(mine - other_refs)),
    )
