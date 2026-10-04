"""T-INT-003 · M2 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M1 的 ``rig_m1.py`` 同一取向：真实 ``Store`` + 真实 ``MarketDb`` + 真实官方 Pack +
真实 L2/L3/L4 编排件；只把三处**出网面**换成脚本化注入，使 CI 不随本机 ``.env`` 有无而变
（同 [`T-INT-002`](../项目管理/tasks/T-INT-002-M1集成关卡首次可对话.md) A4 的口径）：

- 意图理解器（L3）→ :class:`DeterministicUnderstander`（复用 `rig_m1`）
- 观点方向合成器（L4）→ :class:`ScriptedSynthesizer`——**真方向研判经 LLM，由
  `tests/live/` 兜**（GWT-9）
- 证据重研判（L4）→ :class:`ScriptedReviewer`

装配入口是**生产组合根** :func:`st_agent.app.build_m2_runtime`——本关卡要证明的正是
这个根把 L0→L1→L2→L3 **+ L4** 装到同一个 `Store` 上，并令 `analyze` 去向成为真链路。

`SameThreadExecutor` 让编排的并行执行退化为**同线程顺序**：并行本身由 `T-L4-002` 的
用例覆盖，关卡要的是**确定可复算**的对照结果（06 §4 的对照是集合运算，与执行顺序无关）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import (
    CONFIGURE_QUERY,
    MEMORY_OP_QUERY,
    RISK_QUERY,
    DeterministicUnderstander,
)

from st_agent.app import M2Runtime, build_m2_runtime
from st_agent.l3.intent import IntentDraft
from st_agent.l4.deliberation import SynthesizedView
from st_agent.l4.divergence import EvidenceReview

__all__ = [
    "ANALYZE_QUERY",
    "DEFAULT_STANCES",
    "M2Rig",
    "SCRIPTED_NOTE",
    "SameThreadExecutor",
    "ScriptedReviewer",
    "ScriptedSynthesizer",
    "seeded_m2",
]

ANALYZE_QUERY = IntentDraft(
    intent="analyze", target=None,
    understood={"topic": "sh.600000 是否值得关注"},
)
"""`analyze` 意图的脚本化理解产物——**主题**经 `understood` 流入确认卡取值。

`mode` 不在此给出：它由 [05 §3.2](../docs/技术架构-v2/05-L3-对话主入口.md) 的澄清协议
按**意图级参数声明**生成问项（`T-INT-003` 交付），跳过即取默认值。
"""

DEFAULT_STANCES: dict[str, str] = {
    "风险视角": "negative",
    "合规视角": "positive",
    "宏观视角": "positive",
}
"""脚本化的视角方向表（离线关卡用；未列者 `neutral`）。

配置依据是 [06 §4](../../docs/技术架构-v2/06-L4-多视角推理.md) 的 **Mini Debate 触发条件**
（方向相反 **且**（证据重叠 **或** 前提相关）），使两种情形同批可测：

- **触发**：「风险视角」与「合规视角」的 `skill_bundle` **完全相同**
  （`sk_risk_alert` + `sk_delisting_risk_scan`，见 [`l4/builtin.py`](../src/st_agent/l4/builtin.py)），
  方向相反 ⇒ 前提相关成立 ⇒ 出 `ConflictAnalysis`。
- **不触发**：「风险视角」与「宏观视角」的 bundle 无交集（`sk_sector_heatmap` +
  `sk_portfolio_stress_test`），且本关卡的种子数据未注入个性化记忆（无共同 `mn_` 证据）
  ⇒ 方向相反也不触发 ⇒ 原因**显式记入** `notes`（不静默略过，GWT-4）。
"""

SCRIPTED_NOTE = "脚本件按本地数据存在即判成立与新鲜（离线关卡；真研判经端点）"
"""脚本重研判件的中性说明（生成文案，仍由对照件的 §6 门复核）。"""

_LABELS = {"positive": "看好", "negative": "看空", "neutral": "中性"}


class SameThreadExecutor:
    """同线程执行器（鸭子类型 `map(fn, seq)`）——离线关卡保确定性。"""

    def map(self, fn: Any, seq: Any) -> list[Any]:
        return [fn(item) for item in seq]


class ScriptedSynthesizer:
    """按**视角名**给方向的确定性方向合成器（离线关卡；真研判经 LLM，GWT-9）。

    产出理由为中性陈述句（编排侧仍逐条过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)
    `check_output`，命中即降级——本件不自带第二道门）。
    """

    def __init__(self, stances: dict[str, str] | None = None) -> None:
        self._stances = dict(DEFAULT_STANCES if stances is None else stances)

    def synthesize(
        self,
        lens: Any,
        ok_outputs: list[Any],
        memory_slice_ids: tuple[str, ...],
        sufficiency: float,
    ) -> SynthesizedView:
        name = str(getattr(lens, "name", ""))
        stance = self._stances.get(name, "neutral")
        reason = (
            f"视角「{name}」基于 {len(ok_outputs)} 项有效 Skill 数据形成"
            f"{_LABELS[stance]}观点（脚本合成器：离线关卡按视角名给方向）"
        )
        return SynthesizedView(stance=stance, key_reasons=(reason,))


class ScriptedReviewer:
    """确定性证据重研判件——**三态皆有判定**（离线关卡；真研判经 LLM，GWT-9）。

    与缺省件（三态 `unknown`）的区别：本件真的落判定值，从而证明对照件**消费**了
    重研判结果，而非把注入端口当摆设。
    """

    def review(
        self, ref: str, *, lens_ids: tuple[str, ...], as_of: Any,
    ) -> EvidenceReview:
        return EvidenceReview(
            ref=ref, validity="valid", freshness="fresh", weight=0.5,
            note=SCRIPTED_NOTE,
        )


@dataclass(frozen=True)
class M2Rig:
    """一次 M2 装配的全部句柄。"""

    root: Path
    m2: M2Runtime
    feed: MarketData
    sender: RecordingSender
    understander: DeterministicUnderstander
    synthesizer: ScriptedSynthesizer
    reviewer: ScriptedReviewer


def seeded_m2(
    root: Path,
    *,
    rules: list[tuple[str, IntentDraft]] | None = None,
    stances: dict[str, str] | None = None,
) -> M2Rig:
    """已播种数据面的 M2 全栈装配（生产组合根 `build_m2_runtime` + 脚本化出网面替身）。"""
    seed_market_db(root, PASS)
    feed = MarketData()
    sender = RecordingSender()
    understander = DeterministicUnderstander(
        rules if rules is not None
        else [
            ("退市", RISK_QUERY),
            ("阈值", CONFIGURE_QUERY),
            ("分析", ANALYZE_QUERY),
            ("冲突", MEMORY_OP_QUERY),
        ]
    )
    synthesizer = ScriptedSynthesizer(stances)
    reviewer = ScriptedReviewer()
    m2 = build_m2_runtime(
        root, PASS, market_query=feed, sender=sender, llm_env={},
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        executor=SameThreadExecutor(),
    )
    return M2Rig(
        root=root, m2=m2, feed=feed, sender=sender, understander=understander,
        synthesizer=synthesizer, reviewer=reviewer,
    )
