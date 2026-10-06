"""L6 主动提案（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

两条出路，**同一个面**：

1. **周报第四段的候选生成**（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md) 第 4 段）——
   :meth:`ProposalEngine.proposals` 即 ``WeeklyReportBuilder(advisor=...)`` 的注入面：
   按**声明式规则表**从周报的观察（``evidence``）推出候选（`config_id` + 现值 + 建议值 +
   理由 + 依据锚点）。两处**同源**——不另造第二套规则，否则同一份观察会给出两份不同的建议。
2. **模式识别**（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md) 前半）——
   :meth:`ProposalEngine.detect` 从**注入的模式观察面**取观察，按阈值（``proposal.repeat-threshold``，
   缺省 5）判定「同类问题重复出现 > N 次」，产出**含 Skill 草稿的提案**
   （草稿形态＝[03 §4](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 的工作流草稿子对象），
   并按**频率上限**（``proposal.rate-limit``，每 ISO 周最多 N 条）放行——超限的**排队不丢**。

五条口径：

- **提案不生效**——一切产出都是候选 / 提案，**不产 `change_id`、不落变更历史**；生效 / 回滚
  一律经 [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)（[§7](../../../docs/技术架构-v2/08-L6-反思演进.md)
  红线：不存在静默调参路径）。故本面**只**在条目落值（01 §7 的统一落值面）时经存储产生变更留痕。
- **观察缺省 ⇒ 不产提案**（fail-closed）——「同类问题」的判定需要 L3 的对话史，而 L6 无该读面，
  故经**注入的模式观察面**取得；未注入时**不产任何提案**并显式说明，**不由计数 0 反推**
  「没有问题模式」（先例＝L5 的声明式规则 + 组合根接线，[D-083](../../../项目管理/决策日志.md)）。
- **现值读不到 ⇒ 跳过该条并记因**——规则命中但目标条目的现值不可读时，**不臆造**一个现值，
  该条以 :class:`RuleNote` 记因后跳过（读面 :meth:`ProposalEngine.last_run` 可查）。
- **窗口＝ ISO 周**——频率上限的窗口复用 [`.2`](weekly.py) 的 ``week_key``，**不另造**第二套
  时间口径（离线可复算、幂等）。
- **文案过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)**——规则理由与提案理由都是
  **生成性文案**（执行点 2）；草稿的名称 / 说明走**执行点 1**（命名中性，[03 §4](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
  的「保存时执行中性化命名校验」同一判据）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import ChangePolicy, ChangeRecord, ConfigEntry, PanelField
from st_agent.l6.errors import ProposalError
from st_agent.l6.proposal_store import (
    CONFIG_PREFIX,
    RATE_LIMIT_CONFIG_ID,
    REPEAT_CONFIG_ID,
    ProposalStore,
)
from st_agent.l6.weekly import ProposalCandidate, week_key

__all__ = [
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_REPEAT_THRESHOLD",
    "DEFAULT_RULES",
    "NOT_HIT_NOTE",
    "OBSERVER_ABSENT_REASON",
    "ProposalEngine",
    "ProposalRule",
    "ProposalRun",
    "DetectRun",
    "PatternObservation",
    "RuleNote",
    "SkillDraft",
    "SkillProposal",
]

OBSERVER_ABSENT_REASON = "未注入模式观察面，无法识别问题模式（08 §4）"

DEFAULT_REPEAT_THRESHOLD = 5
"""story-09 GWT-4「用户反复问某类问题超过 5 次」的缺省阈值（登记为 01 §7 条目）。"""

DEFAULT_RATE_LIMIT = 1
"""缺省的每 ISO 周放行条数（防骚扰；`0` 合法＝只排队不放行）。"""

NOT_HIT_NOTE = "未达判据"

_RuleMetric = Literal["ignored", "rejected", "week_deliveries"]


class SkillDraft(BaseModel):
    """**Skill 草稿**（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)「提议创建专门 Skill，附草稿」）。

    形态＝[03 §4](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 与
    [05 §5](../../docs/技术架构-v2/05-L3-对话主入口.md) 的 ``workflow_draft`` 子对象
    （名称 / 说明 / 节点 / 连线 / 分组 / 调度，可含 ASCII ``flow_name``）——**身份字段
    `flow_id` / `version` 不在草稿中**，由 Studio 侧「接受」派生。本层**不物化** Skill
    （物化归 Studio 的接受路径），故只承载形态、不 import L1。
    """

    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=1)]
    nodes: Annotated[tuple[Mapping[str, Any], ...], Field(min_length=1)]
    edges: tuple[Mapping[str, Any], ...] = ()
    groups: tuple[Mapping[str, Any], ...] = ()
    schedule: Mapping[str, Any] | None = None
    flow_name: str = ""
    """ASCII 注册名（缺省由展示名 slug 化；纯中文名时由产出方补，[03 §4](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 的派生口径）。"""


class PatternObservation(BaseModel):
    """**模式观察面**的一条观察（同类问题的聚合）。"""

    model_config = ConfigDict(frozen=True)

    key: str
    """同类键（同一类问题的稳定标识；提案标识由它确定性派生）。"""
    count: int
    """该类问题出现的次数。"""
    sample: str = ""
    """样本原话（**用户数据**，原样承载、不过 §6）。"""
    refs: tuple[str, ...] = ()
    """来源锚点（对话 trace / 记忆节点等，[01 §1](../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    draft: SkillDraft | None = None
    """拟创建的 Skill 草稿（**必备**——[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 要求提案「附草稿」）。"""
    reason: str = ""
    """中性陈述式理由（**生成性文案**，过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)）。"""


class SkillProposal(BaseModel):
    """一条**主动提案**（含草稿；无 `change_id`、不生效）。"""

    model_config = ConfigDict(frozen=True)

    proposal_id: str
    key: str
    count: int
    reason: str
    draft: SkillDraft
    sample: str = ""
    """样本原话（**用户数据**，原样承载、不过 §6——同训练对话把 `correction` 分字段的口径）。"""
    refs: tuple[str, ...] = ()
    created_at: datetime
    released_week: str = ""
    """放行所在的 ISO 周键；**空串＝仍在排队**（受频率上限约束尚未放行，[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md)）。"""

    @property
    def queued(self) -> bool:
        """是否仍在排队（未放行）。"""
        return not self.released_week


class ProposalRule(BaseModel):
    """一条**声明式规则**（[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 的候选生成规则）。

    判据只用周报注入面已给的 ``evidence`` 字段（`ignored` / `rejected` / `week_deliveries`
    / `feedback_total`），**不扩 `evidence` 结构**（那会改 [T-L6-001.2](../项目管理/tasks/T-L6-001.2-每周反思报告本体.md)
    的交付面）。
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    metric: _RuleMetric
    threshold: int
    majority: bool = False
    """为真时另要求该指标**占反馈多数**（`metric * 2 >= feedback_total`）。"""
    config_id: str
    delta: int
    """建议值的相对变化（现值 + `delta`）。"""
    floor: int
    """建议值的下界（避免给出无意义的负值 / 零阈值）。"""
    reason: str
    """中性陈述式理由（**生成性文案**，过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)）。"""


DEFAULT_RULES: tuple[ProposalRule, ...] = (
    ProposalRule(
        rule_id="ignored-majority", metric="ignored", threshold=3, majority=True,
        config_id="fatigue.ignore-threshold", delta=-1, floor=1,
        reason="本周忽略占反馈多数，疲劳阈值可更早触发以减少重复打扰",
    ),
    ProposalRule(
        rule_id="delivery-density", metric="week_deliveries", threshold=20,
        config_id="frequency.dedup-window-minutes", delta=5, floor=0,
        reason="本周触达条数达阈值，去重合并窗口可拉长以合并重复事件",
    ),
)
"""缺省规则表（**声明式数据**——换规则不动引擎；两条目标条目都是 L5 的既有标量条目）。"""


class RuleNote(BaseModel):
    """一条规则的本次结论（含**被跳过的**——不静默）。"""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    hit: bool
    note: str


class ProposalRun(BaseModel):
    """一次周报候选生成的全貌（候选 + 逐规则记因）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    candidates: tuple[ProposalCandidate, ...] = ()
    notes: tuple[RuleNote, ...] = ()


class DetectRun(BaseModel):
    """一次模式识别的全貌（**显式**说明「有没有观察面」——不由空结果反推）。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    reason: str = ""
    proposals: tuple[SkillProposal, ...] = ()


class ProposalEngine:
    """主动提案面（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（提案照常可读，但不落盘）
    :param observer: **模式观察面**鸭子端口（``observations() -> Sequence[PatternObservation | Mapping]``）；
        缺省 ``None`` ⇒ :meth:`detect` **不产提案**（fail-closed + 原因），**不由计数 0 反推**
    :param registry: 01 §7 的条目读面（鸭子类型 ``entry(config_id) -> ConfigEntry | None``）；
        缺省 ``None`` ⇒ 规则命中但现值不可读 ⇒ **跳过并记因**（不臆造现值）
    :param rules: 规则表（缺省 :data:`DEFAULT_RULES`；声明式，注入以便测试与后续扩展）
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        observer: Any = None,
        registry: Any = None,
        rules: Sequence[ProposalRule] | None = None,
        guard: NeutralityGuard | None = None,
        default_repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
        default_rate_limit: int = DEFAULT_RATE_LIMIT,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = ProposalStore(store, now=now) if store is not None else None
        self._observer = observer
        self._registry = registry
        self._rules = tuple(DEFAULT_RULES if rules is None else rules)
        self._guard = guard if guard is not None else NeutralityGuard()
        self._default_repeat = _require_positive(default_repeat_threshold, "重复阈值")
        self._default_rate = _require_non_negative(default_rate_limit, "频率上限")
        self._now = _system_now if now is None else now
        self._memory: dict[str, dict[str, Any]] = {}     # 纯内存态（无 Store 时）
        self._last: ProposalRun = ProposalRun()

    # ───────────────────────── 01 §7 条目 ─────────────────────────

    def repeat_threshold(self) -> int:
        """同类问题重复出现次数的阈值（落盘条目优先，缺省回落内置）。"""
        raw = self._default_repeat if self._store is None else self._store.current(
            REPEAT_CONFIG_ID, self._default_repeat,
        )
        try:
            return _require_positive(raw, "重复阈值")
        except ProposalError:
            return self._default_repeat

    def rate_limit(self) -> int:
        """每个 ISO 周最多放行的提案条数（落盘条目优先，缺省回落内置）。"""
        raw = self._default_rate if self._store is None else self._store.current(
            RATE_LIMIT_CONFIG_ID, self._default_rate,
        )
        try:
            return _require_non_negative(raw, "频率上限")
        except ProposalError:
            return self._default_rate

    def set_repeat_threshold(
        self, value: Any, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """设置重复阈值（正整数；越界即拒、不落盘不留痕）。"""
        target = _require_positive(value, "重复阈值")
        self._default_repeat = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(REPEAT_CONFIG_ID, target),
            previous=self._store.current(REPEAT_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def set_rate_limit(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """设置频率上限（非负整数；越界即拒、不落盘不留痕）。"""
        target = _require_non_negative(value, "频率上限")
        self._default_rate = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(RATE_LIMIT_CONFIG_ID, target),
            previous=self._store.current(RATE_LIMIT_CONFIG_ID, None), trace_ref=trace_ref,
        )

    def changes(self) -> tuple[ChangeRecord, ...]:
        """条目变更留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本层登记进 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的两个条目（含当前取值）。"""
        return (
            self._entry(REPEAT_CONFIG_ID, self.repeat_threshold()),
            self._entry(RATE_LIMIT_CONFIG_ID, self.rate_limit()),
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        """按 `config_id` 取条目登记形态；不属本面 → ``None``；**损坏 → 校验错**。"""
        if not config_id.startswith(CONFIG_PREFIX):
            return None
        if self._store is not None:
            stored = self._store.entry(config_id)
            if stored is not None:
                return stored
        for entry in self.entries():
            if entry.config_id == config_id:
                return entry
        return None

    # ───────────────────────── 周报第四段（advisor 注入面） ─────────────────────────

    def evaluate(self, evidence: Mapping[str, Any]) -> ProposalRun:
        """按规则表评估一次观察（**纯读**；候选 + 逐规则记因）。"""
        total = _as_int(evidence.get("feedback_total"))
        anchor = _anchor(evidence)
        candidates: list[ProposalCandidate] = []
        notes: list[RuleNote] = []
        for rule in self._rules:
            value = _as_int(evidence.get(rule.metric))
            if value < rule.threshold or (rule.majority and value * 2 < total):
                notes.append(RuleNote(rule_id=rule.rule_id, hit=False, note=NOT_HIT_NOTE))
                continue
            current = self._current_value(rule.config_id)
            if current is None:
                notes.append(RuleNote(
                    rule_id=rule.rule_id, hit=True,
                    note=f"命中但现值不可读（{rule.config_id}），已跳过——不臆造现值",
                ))
                continue
            suggested = max(rule.floor, current + rule.delta)
            if suggested == current:
                notes.append(RuleNote(
                    rule_id=rule.rule_id, hit=True,
                    note=f"命中但已达下界（{rule.config_id}），无建议值",
                ))
                continue
            self._require_neutral(rule.reason, f"规则「{rule.rule_id}」的理由")
            candidates.append(ProposalCandidate(
                config_id=rule.config_id, current=current, suggested=suggested,
                reason=rule.reason, trace_ref=anchor,
            ))
            notes.append(RuleNote(rule_id=rule.rule_id, hit=True, note="命中"))
        run = ProposalRun(candidates=tuple(candidates), notes=tuple(notes))
        self._last = run
        return run

    def proposals(self, evidence: Mapping[str, Any]) -> tuple[ProposalCandidate, ...]:
        """**周报第四段的注入面**（鸭子类型 ``proposals(evidence) -> Sequence[ProposalCandidate]``，
        正是 :class:`WeeklyReportBuilder` 的 `advisor` 端口——两处**同源**，故本引擎可**直接**
        作为 `advisor` 注入）。"""
        return self.evaluate(evidence).candidates

    def last_run(self) -> ProposalRun:
        """最近一次 :meth:`evaluate` 的全貌（含**被跳过的**规则与原因）。"""
        return self._last

    # ───────────────────────── 模式识别 ─────────────────────────

    def detect(self, *, now: datetime | None = None) -> DetectRun:
        """按阈值识别问题模式并产出提案（含放行配额的处理）。

        :return: `enabled=False` ＋原因 ⇒ **没有观察面**（不产提案、也不反推「无模式」）
        """
        moment = self._now() if now is None else now
        if self._observer is None:
            return DetectRun(enabled=False, reason=OBSERVER_ABSENT_REASON)
        threshold = self.repeat_threshold()
        produced: list[SkillProposal] = []
        for observation in self._observations():
            if observation.count <= threshold:
                continue
            proposal = self._proposal(observation, moment)
            self._persist(proposal)
            produced.append(proposal)
        self.release(now=moment)
        touched = tuple(
            self.get(p.proposal_id) or p for p in sorted(
                produced, key=lambda p: (p.created_at, p.proposal_id)
            )
        )
        return DetectRun(enabled=True, proposals=touched)

    def release(self, *, now: datetime | None = None) -> tuple[SkillProposal, ...]:
        """把排队中的提案按**当周余额**放行（先到先放；返回本次放行的）。"""
        moment = self._now() if now is None else now
        week = week_key(moment.date())
        existing = self.all()
        used = sum(1 for p in existing if p.released_week == week)
        slots = max(0, self.rate_limit() - used)
        if slots == 0:
            return ()
        queued = sorted(
            (p for p in existing if p.queued), key=lambda p: (p.created_at, p.proposal_id),
        )
        released: list[SkillProposal] = []
        for proposal in queued[:slots]:
            updated = proposal.model_copy(update={"released_week": week})
            self._persist(updated)
            released.append(updated)
        return tuple(released)

    def all(self) -> tuple[SkillProposal, ...]:
        """全部提案（按（创建时刻, 标识）升序；无 → 空集）。"""
        raws = self._store.all() if self._store is not None else tuple(self._memory.values())
        out: list[SkillProposal] = []
        for raw in raws:
            try:
                out.append(SkillProposal(**raw))
            except ValidationError as exc:
                raise ProposalError(f"提案形态损坏：{exc}") from exc
        return tuple(sorted(out, key=lambda p: (p.created_at, p.proposal_id)))

    def get(self, proposal_id: str) -> SkillProposal | None:
        """按标识取一条提案（不存在 → ``None``；损坏 → :class:`ProposalError`）。"""
        raw = self._store.get(proposal_id) if self._store is not None else self._memory.get(proposal_id)
        if raw is None:
            return None
        try:
            return SkillProposal(**raw)
        except ValidationError as exc:
            raise ProposalError(f"提案形态损坏（{proposal_id}）：{exc}") from exc

    # ───────────────────────── 内部 ─────────────────────────

    def _observations(self) -> tuple[PatternObservation, ...]:
        raw = self._observer.observations()
        out: list[PatternObservation] = []
        for item in raw or ():
            if isinstance(item, PatternObservation):
                if item.draft is None:
                    raise ProposalError(
                        f"观察 {item.key!r} 未附 Skill 草稿（08 §4：提案「附草稿」）"
                    )
                out.append(item)
                continue
            if not isinstance(item, Mapping):
                raise ProposalError(
                    f"观察面返回非法结构 {type(item).__name__}（须为 PatternObservation / 映射）"
                )
            try:
                out.append(PatternObservation(**dict(item)))
            except (ValidationError, TypeError, ValueError) as exc:
                raise ProposalError(f"观察不合 08 §4 形态：{exc}") from exc
        for observation in out:
            if observation.draft is None:
                raise ProposalError(
                    f"观察 {observation.key!r} 未附 Skill 草稿（08 §4：提案「附草稿」）"
                )
        return tuple(out)

    def _proposal(self, observation: PatternObservation, moment: datetime) -> SkillProposal:
        draft = observation.draft
        assert draft is not None                    # _observations 已保证
        verdict = self._guard.check_name(draft.name, description=draft.description)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise ProposalError(
                f"提案草稿的名称 / 说明未过 01 §6 中性化命名校验（{hits}）：铁律 2"
            )
        if observation.reason:
            self._require_neutral(observation.reason, "提案理由")
        proposal_id = digest_id("prop", observation.key)
        existing = self.get(proposal_id)
        return SkillProposal(
            proposal_id=proposal_id, key=observation.key, count=observation.count,
            reason=observation.reason, draft=draft, sample=observation.sample,
            refs=tuple(observation.refs), created_at=moment,
            released_week="" if existing is None else existing.released_week,
        )

    def _current_value(self, config_id: str) -> int | None:
        """目标条目的现值（读不到 → ``None``；非整数 → ``None``——**不臆造**）。"""
        if self._registry is None:
            return None
        try:
            entry = self._registry.entry(config_id)
        except Exception:
            return None
        if entry is None:
            return None
        value = getattr(entry, "default", None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _persist(self, proposal: SkillProposal) -> None:
        payload = proposal.model_dump(mode="json")
        if self._store is None:
            self._memory[proposal.proposal_id] = payload
            return
        self._store.put(proposal.proposal_id, payload)

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise ProposalError(
                f"{where} 未过 01 §6 中性化校验（{hits}）：文案须中性（铁律 2）"
            )

    def _entry(self, config_id: str, default: Any) -> ConfigEntry:
        display, schema, chat, panel = _ENTRY_SPECS[config_id]
        return ConfigEntry(
            config_id=config_id, display_name=display, value_schema=schema, default=default,
            description_for_chat=chat, panel_form_spec=panel, scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )


# ───────────────────────── 取值解释（越界一律显式拒） ─────────────────────────


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _as_int(value: Any) -> int:
    """观察值 → 非负整数（缺失 / 非数值 → ``0``；它只用于判据比较，不是落盘数据）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _anchor(evidence: Mapping[str, Any]) -> str:
    """候选所依据的**留痕锚点**（[01 §4](../../docs/技术架构-v2/01-平台共享契约.md)）——
    取观察里真实存在的第一条反馈 / 投递标识，**不臆造**。"""
    for field in ("feedback_ids", "delivery_ids"):
        values = evidence.get(field) or ()
        if isinstance(values, (list, tuple)) and values:
            return str(values[0])
    return ""


def _require_positive(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProposalError(f"{where}须为 ≥ 1 的正整数，收到 {value!r}")
    return value


def _require_non_negative(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProposalError(f"{where}须为非负整数，收到 {value!r}")
    return value


# ───────────────────────── 01 §7 条目的登记形态 ─────────────────────────

_ENTRY_SPECS: dict[str, tuple[str, dict[str, Any], str, PanelField]] = {
    REPEAT_CONFIG_ID: (
        "同类问题重复次数阈值",
        {"type": "integer", "minimum": 1},
        "同一类问题重复出现超过多少次时，主动提议为它创建一个专门 Skill",
        PanelField(widget="text", label="重复次数阈值", help_text="正整数，缺省 5"),
    ),
    RATE_LIMIT_CONFIG_ID: (
        "主动提案频率上限",
        {"type": "integer", "minimum": 0},
        "每周最多主动放行多少条提案（超出者排队等待，不丢弃）",
        PanelField(widget="text", label="每周提案上限", help_text="非负整数，缺省 1"),
    ),
}
