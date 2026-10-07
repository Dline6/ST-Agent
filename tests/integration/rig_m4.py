"""T-INT-005 · M4 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M3 的 ``rig_m3.py`` 同一取向：真实 ``Store`` + 真实 ``MarketDb`` + 真实官方 Pack +
真实 L2/L3/L4/L5 编排件 + **真实进程内事件总线**；只把**出网面**换成脚本化注入，使 CI
不随本机 ``.env`` 有无而变：

- 意图理解器（L3）→ :class:`DeterministicUnderstander`（复用 ``rig_m1``）
- 观点方向合成器 / 证据重研判（L4）→ ``rig_m2`` 的脚本件（真研判由 ``tests/live/`` 兜）
- 渠道（L5）→ :class:`RecordingChannel`（真渠道是「经 L0 网关出网」那条路）
- 训练理解端口（L6 §3）→ :class:`DeterministicTrainingUnderstander`
- 模式观察面（L6 §4）→ :class:`ScriptedObserver`

**本 rig 的调度目标是真跑的**（同 ``rig_m3``）：播种的最新交易日被改成「异动 + 高换手」，
故 ``sk_stock_watch`` 在首次 ``tick`` 时真的到期、真的执行，产出 ``triggered[]``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import CONFIGURE_QUERY, MEMORY_OP_QUERY, RISK_QUERY, DeterministicUnderstander
from rig_m2 import ANALYZE_QUERY, SameThreadExecutor, ScriptedReviewer, ScriptedSynthesizer
from rig_m3 import NOW, Clock, RecordingChannel, WATCH_SEED_SQL
from rig_m3 import DEFAULT_RULES as _M3_RULES

from st_agent.app import M4Runtime, build_m4_runtime
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.l3.intent import IntentDraft
from st_agent.l6 import PatternObservation, SkillDraft, SuggestionDraft, TrainingDraft

TRAIN_QUERY = IntentDraft(intent="train", target=None)
"""`train` 去向的意图草案（[05 §4] / [08 §3]）——M4 关卡新增，供训练对话接线用例触发。"""

DEFAULT_RULES: list[tuple[str, IntentDraft]] = [
    *_M3_RULES,
    ("训练", TRAIN_QUERY),
]
"""M3 的四条规则 **+** `训练` ——训练对话在 rig 里也能真经总线派发。"""

__all__ = [
    "ANALYZE_QUERY",
    "CONFIGURE_QUERY",
    "DEFAULT_RULES",
    "MEMORY_OP_QUERY",
    "NOW",
    "RISK_QUERY",
    "TRAIN_QUERY",
    "Clock",
    "DeterministicTrainingUnderstander",
    "M4Rig",
    "RecordingChannel",
    "ScriptedObserver",
    "observation",
    "seeded_m4",
    "skill_draft",
]

_LOCAL_CHANNELS = ("desktop", "email", "im_webhook", "tts")


def skill_draft(
    *,
    name: str = "同类问题专用技能",
    description: str = "把反复出现的同类问题整理为一个专门能力",
    skill_id: str = "sk_stock_watch_v1.0",
) -> SkillDraft:
    """一个合法的 Skill 草稿（模式观察面必备，[08 §4]「附草稿」）。"""
    return SkillDraft(
        name=name, description=description,
        nodes=({"node_id": "n1", "skill_id": skill_id, "params": {}},),
        flow_name="same_kind_helper",
    )


def observation(
    *,
    key: str = "repeat-question",
    count: int = 6,
    sample: str = "这个指标怎么算的？",
    reason: str = "同类问题重复出现，可建专门能力承接",
    draft: SkillDraft | None = None,
) -> PatternObservation:
    """一条模式观察（缺省 `count` 已过提案阈值 5 ⇒ 必然产提案）。"""
    return PatternObservation(
        key=key, count=count, sample=sample, refs=(),
        draft=skill_draft() if draft is None else draft, reason=reason,
    )


class DeterministicTrainingUnderstander:
    """训练理解端口的确定性替身（[08 §3]）。

    把用户原话包成一份**中性复述**；可选地附一条配置建议。同一输入恒得同一输出，
    故训练对话用例可离线复算（真理解由 ``tests/live/test_l6_adapters_live.py`` 兜）。
    """

    def __init__(self, *, suggestion: SuggestionDraft | None = None) -> None:
        self._suggestion = suggestion
        self.calls: list[tuple[str, str]] = []

    def understand(self, correction: str, *, target: str = "") -> TrainingDraft:
        self.calls.append((correction, target))
        return TrainingDraft(
            restatement=f"用户的修正要点：{correction}",
            pattern=f"该用户的偏好：{correction}",
            suggestion=self._suggestion,
        )


class ScriptedObserver:
    """模式观察面的确定性替身（`observations()`）。"""

    def __init__(self, items: tuple[PatternObservation, ...] = ()) -> None:
        self._items = tuple(items)

    def observations(self) -> tuple[PatternObservation, ...]:
        return self._items


@dataclass(frozen=True)
class M4Rig:
    """一次 M4 装配的全部句柄。"""

    root: Path
    m4: M4Runtime
    feed: MarketData
    sender: RecordingSender
    clock: Clock
    understander: DeterministicUnderstander
    training: DeterministicTrainingUnderstander
    observer: ScriptedObserver
    channels: dict[str, RecordingChannel] = field(default_factory=dict)


def _seed_watch_data(root: Path, passphrase: str) -> None:
    """播种市场数据面后，把最新交易日改成触发盯盘条件的那一组值（同 ``rig_m3``）。"""
    seed_market_db(root, passphrase)
    db = MarketDb(Store.open(root, passphrase))
    with db.transact() as con:
        con.execute(WATCH_SEED_SQL)
        con.commit()


def seeded_m4(
    root: Path,
    *,
    observations: tuple[PatternObservation, ...] = (),
    training_suggestion: SuggestionDraft | None = None,
    rules: list[tuple[str, Any]] | None = None,
    stances: dict[str, str] | None = None,
    channels: dict[str, RecordingChannel] | None = None,
    clock: Clock | None = None,
    **l1_kwargs: Any,
) -> M4Rig:
    """已播种数据面的 M4 全栈装配（生产组合根 `build_m4_runtime` + 脚本化出网面替身）。"""
    _seed_watch_data(root, PASS)
    feed = MarketData()
    sender = RecordingSender()
    moment = Clock() if clock is None else clock
    understander = DeterministicUnderstander(DEFAULT_RULES if rules is None else rules)
    training = DeterministicTrainingUnderstander(suggestion=training_suggestion)
    observer = ScriptedObserver(observations)
    built = channels if channels is not None else {
        kind: RecordingChannel(kind) for kind in _LOCAL_CHANNELS
    }
    m4 = build_m4_runtime(
        root, PASS, market_query=feed, sender=sender, llm_env={}, now=moment,
        understander=understander, synthesizer=ScriptedSynthesizer(stances),
        reviewer=ScriptedReviewer(), executor=SameThreadExecutor(), channels=built,
        training_understander=training, observer=observer, **l1_kwargs,
    )
    return M4Rig(
        root=root, m4=m4, feed=feed, sender=sender, clock=moment,
        understander=understander, training=training, observer=observer,
        channels=dict(built),
    )
