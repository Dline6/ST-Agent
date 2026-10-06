"""上游信号产生规则与产生点（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md) 的产生规则面）。

[00 §5](../../../docs/技术架构-v2/00-架构总览.md) 步 4 的「信号生成」与
[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md) 的「触达联动」指向同一件事：把上游产出
变成一条 `SignalEmitted`。[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 登记了该事件的
**负载结构**，但「上游产出的形态」→「负载」之间**没有既定映射**——L1 交出的是一次
:class:`~st_agent.l1.scheduler.models.ScheduledRun`（`envelope` / `trace_id` / `skill_run_id`），
L4 交出的是一个 `AnalyzeOutcome`。本模块是那张**显式规则表**与两个**纯函数**求值面：

- :func:`signal_events_for_run`——一次调度运行 ⇒ 0..n 条信号（逐条目一条；条目重复投递得
  同一 `signal_id`，故重放 / 补发安全）。
- :func:`signal_event_for_analysis`——一次多视角分析 ⇒ 0..1 条信号（**多视角摘要**形态）。

三条口径（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md) 产生规则段，2026-10-06 定案）：

- **规则住 L5**——负载结构本就由订阅方（L5）先行登记并由
  :func:`~st_agent.l5.signal.adopt_signal` 校验，规则放同处才保「一种形态一份真相」；
  L1 / L4 **零改动**（只交出既有对象，本模块按鸭子面读）。
- **求值是纯函数**——只吃传进来的对象、不读系统时钟，同一输入恒得同一事件序列；
  **不发布**：投递面（[01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的 `publish`）由组合根接。
- **文本过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2**——生成的结论与观察摘要
  立即校验，命中即抛 :class:`~st_agent.l5.errors.SignalEmissionError`（**不把不合规文案投出去**）。

**无 L4 阵容参与的产生点**（L1 定时监控）以**观察视角**承载摘要——`lens_id` 由本层铸造，
[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md) 只校验其形态、不解析阵容（同
[§7](../../../docs/技术架构-v2/07-L5-主动触达.md) 疲劳询问的既有做法）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from string import Formatter
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.capability_types import Stance
from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l1.skills.errors import SkillValidationError
from st_agent.l1.skills.ids import base_of
from st_agent.l5.errors import SignalEmissionError
from st_agent.l5.signal import (
    SignalContent,
    SignalLensStance,
    SignalLevel,
    signal_event,
)

__all__ = [
    "DELIBERATION_RULE",
    "DEFAULT_MONITOR_RULES",
    "MONITOR_LENS",
    "DeliberationRule",
    "MonitorRule",
    "signal_event_for_analysis",
    "signal_events_for_run",
]

MONITOR_LENS = digest_id("lens", "l1:monitor-observer")
"""定时监控的**观察视角**（摘要的载体）——形态合 [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md)，
**不解析** L4 阵容（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md) 只校验 `lens_id` 形态）。"""

MONITOR_STANCE: Stance = "neutral"
"""监控观察的固有方向——监控只陈述**事实**（触及了什么条件），不替用户下方向判断。"""

_EMPTY_STANCE_NOTE = "监控观察摘要的载体视角"
"""观察视角的性质说明（写进摘要文本，使「这是谁的观察」在载荷里可读）。"""

_FORMATTER = Formatter()


def _template_fields(template: str) -> tuple[str, ...]:
    """模板里引用的字段名（`{name}` 形态；`{}` / `{0}` 一律拒——规则面只认具名取值）。"""
    names: list[str] = []
    for _literal, field, _spec, _conv in _FORMATTER.parse(template):
        if field is None:
            continue
        if not field.isidentifier():
            raise SignalEmissionError(f"规则模板只允许具名字段，收到 {{{field}}}")
        names.append(field)
    return tuple(names)


class MonitorRule(BaseModel):
    """「某 Skill 的某类产出 → 哪一级信号」的声明式规则（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

    :param skill_id: 产出方 Skill（`run.skill_id`）；**同表内唯一**
    :param level: 信号级别（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md) 三档）
    :param items_field: 信封载荷里**逐条目列表**的字段名（如 `alerts`）——逐条目产一条信号
    :param key_fields: 去重键的取值字段（[07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md)：同一标的
        同一事件类型）——按声明顺序用 `:` 串接，前缀取 :attr:`dedup_prefix`
    :param dedup_prefix: 去重键前缀（标明「哪一类」）
    :param conclusion: 逐条目结论模板（**中性陈述**，过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）
    :param summary: 逐条目观察摘要模板（同上；承载在观察视角下）
    """

    model_config = ConfigDict(frozen=True)

    skill_id: str
    level: SignalLevel
    items_field: str
    key_fields: tuple[str, ...]
    dedup_prefix: str
    conclusion: str
    summary: str

    @property
    def required_fields(self) -> tuple[str, ...]:
        """逐条目必须存在的字段（去重键 + 两个模板引用到的字段，去重后按名升序）。"""
        names = set(self.key_fields)
        names.update(_template_fields(self.conclusion))
        names.update(_template_fields(self.summary))
        return tuple(sorted(names))


DEFAULT_MONITOR_RULES: tuple[MonitorRule, ...] = (
    MonitorRule(
        skill_id="sk_stock_watch",
        level="important",
        items_field="triggered",
        key_fields=("code", "condition"),
        dedup_prefix="l1:stock-watch",
        conclusion="{code} 触发{condition}条件：{detail}",
        summary="{code} 的{condition}观察（定时盯盘的观察摘要）",
    ),
    MonitorRule(
        skill_id="sk_risk_alert",
        level="emergency",
        items_field="alerts",
        key_fields=("code", "dimension"),
        dedup_prefix="l1:risk-alert",
        conclusion="{code} 触及{dimension}条件：{detail}",
        summary=(
            "{code} 的{dimension}观察：距阈值剩余 {remaining_days} 个交易日"
            "（定时监控的观察摘要）"
        ),
    ),
)
"""内置的监控规则表。

**按 `skill_id` 命中**——凡是调度跑到的 Skill 都在覆盖内（规则不是「白名单」，
而是「这类产出怎么读」的声明）：

- `sk_stock_watch`（官方 Pack 的**可调度**监控：带 ``frequency_minutes``）
  ⇒ `important`。「同一标的同一事件类型」正好落在 `code` × `condition`
  （[07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md) 的 `dedup_key` 语义）。
- `sk_risk_alert` ⇒ `emergency`（[00 §5](../../../docs/技术架构-v2/00-架构总览.md) 步 4 的样例即
  「退市风险公告 → 紧急级」）。该 Skill **不带** ``frequency_minutes``，故只在被工作流调度
  时才会跑到；规则先立在此，不因当前触发路径少而省。
"""


class DeliberationRule(BaseModel):
    """「一次多视角分析 → 一条多视角摘要信号」的声明式规则（[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    :param level: 信号级别——取 `important`（[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md) 称
        「紧急信号」，其语义是**不经 `routine` 路由、即时触达**；真正的 `emergency` 档留给
        L1 风险监控的逐标的告警，免得每次分析都把桌面通知打满）
    :param dedup_prefix: 去重键前缀（同一主题的同一分析为一条）
    :param trigger_stance: 触发方向（[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md) 的
        「如风险视角高危」落为「存在该方向的视角」）
    :param min_triggering: 触发所需的最少视角数
    """

    model_config = ConfigDict(frozen=True)

    level: SignalLevel = "important"
    dedup_prefix: str = "l4:deliberation"
    trigger_stance: Stance = "negative"
    min_triggering: int = 1


DELIBERATION_RULE = DeliberationRule()
"""默认的触达联动规则（[06 §6](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。"""


def signal_events_for_run(
    run: Any,
    *,
    rules: Sequence[MonitorRule] = DEFAULT_MONITOR_RULES,
    guard: NeutralityGuard | None = None,
) -> tuple[PlatformEvent, ...]:
    """一次调度运行 ⇒ 0..n 条 `SignalEmitted`（纯函数；**不发布**）。

    :raises SignalEmissionError: 规则声明与实际载荷不符（缺字段 / 条目非映射 /
        时间戳非带时区 / 生成的文案未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）——
        逐类点名，**不静默略过**（[00 §6](../../../docs/技术架构-v2/00-架构总览.md) 失败显式化）

    未命中规则的 Skill、非 `ok` 的运行、载荷里没有逐条目列表 ⇒ 返回空元组
    （**不产信号**，也不编一条出来）。
    """
    if str(getattr(run, "status", "")) != "ok":
        return ()
    rule = _rule_for(getattr(run, "skill_id", None), rules)
    if rule is None:
        return ()
    envelope = getattr(run, "envelope", None)
    data = getattr(envelope, "data", None)
    if not isinstance(data, Mapping):
        return ()
    items = data.get(rule.items_field)
    if not _is_items(items):
        return ()

    moment = _require_aware(getattr(run, "ran_at", None), f"{rule.skill_id} 的运行时刻")
    trace_id = str(getattr(run, "trace_id", "") or "").strip()
    if not trace_id:
        raise SignalEmissionError(
            f"{rule.skill_id} 的运行缺 trace_id——信号须可溯源（01 §11）"
        )
    evidence = _evidence_of(envelope, getattr(run, "skill_run_id", None))
    check = guard if guard is not None else NeutralityGuard()

    events: list[PlatformEvent] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise SignalEmissionError(
                f"{rule.skill_id}.{rule.items_field}[{index}] 须为映射，"
                f"收到 {type(item).__name__}"
            )
        values = _values_of(rule, item, index)
        conclusion = rule.conclusion.format_map(values)
        summary = rule.summary.format_map(values)
        _require_neutral(check, conclusion, f"{rule.skill_id} 结论")
        _require_neutral(check, summary, f"{rule.skill_id} 观察摘要")
        events.append(signal_event(
            level=rule.level,
            content_ref=SignalContent(
                conclusion=conclusion,
                lens_stances=(SignalLensStance(
                    lens_id=MONITOR_LENS, stance=MONITOR_STANCE,
                    summary=f"{summary}（{_EMPTY_STANCE_NOTE}）",
                ),),
            ),
            evidence_refs=evidence,
            dedup_key=":".join([
                rule.dedup_prefix,
                *(f"{item[name]}" for name in rule.key_fields),
            ]),
            source_trace_id=trace_id,
            occurred_at=moment,
        ))
    return tuple(events)


def signal_event_for_analysis(
    outcome: Any,
    *,
    rule: DeliberationRule = DELIBERATION_RULE,
    guard: NeutralityGuard | None = None,
) -> PlatformEvent | None:
    """一次多视角分析 ⇒ 0..1 条**多视角摘要**形态的 `SignalEmitted`（纯函数；**不发布**）。

    逐视角摘要**并列保留、不合并不裁决**（[铁律 4](../../../项目管理/工程宪法.md)），
    结论是中性陈述（参与者数与方向分布）——**不写成单一视角的结论**。

    :raises SignalEmissionError: 生成的文案未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)、
        或该视角的理由列表为空（[01 §3](../../../docs/技术架构-v2/01-平台共享契约.md) 要求非空）

    分析未跑成（信封非 `ok`）、无参与视角、触发方向不足 ⇒ ``None``（**不硬凑**）。
    """
    envelope = getattr(outcome, "envelope", None)
    if str(getattr(envelope, "status", "")) != "ok":
        return None
    opinions = tuple(_opinions_of(outcome))
    if not opinions:
        return None
    triggering = tuple(
        op for op in opinions if str(getattr(op, "stance", "")) == rule.trigger_stance
    )
    if len(triggering) < max(1, rule.min_triggering):
        return None

    topic = str(getattr(outcome, "topic", "") or "本次多视角分析")
    conclusion = (
        f"多视角分析（{topic}）：{len(opinions)} 个视角参与，"
        f"其中 {len(triggering)} 个方向为{rule.trigger_stance}"
    )
    check = guard if guard is not None else NeutralityGuard()
    _require_neutral(check, conclusion, "多视角分析结论")

    stances: list[SignalLensStance] = []
    for opinion in opinions:
        lens_id = str(getattr(opinion, "lens_id", "") or "")
        reasons = tuple(getattr(opinion, "key_reasons", ()) or ())
        if not reasons:
            raise SignalEmissionError(
                f"视角 {lens_id!r} 的理由列表为空——逐视角摘要无从承载（01 §3）"
            )
        summary = str(reasons[0])
        _require_neutral(check, summary, f"视角 {lens_id} 的摘要")
        stances.append(SignalLensStance(
            lens_id=lens_id,
            stance=getattr(opinion, "stance", MONITOR_STANCE),
            summary=summary,
        ))

    evidence = _analysis_evidence(outcome, opinions)
    trace_id = _analysis_trace_id(outcome)
    return signal_event(
        level=rule.level,
        content_ref=SignalContent(conclusion=conclusion, lens_stances=tuple(stances)),
        evidence_refs=evidence,
        dedup_key=f"{rule.dedup_prefix}:{topic}",
        source_trace_id=trace_id,
        occurred_at=_analysis_moment(outcome),
    )


# ───────────────────────── 内部 ─────────────────────────


def _rule_for(skill_id: Any, rules: Sequence[MonitorRule]) -> MonitorRule | None:
    """按 `skill_id` 取规则——规则声明**注册名（base）**，运行结果给的是**带版本的拼接名**。

    版本是能力身份之外的实现细节（同一 base 升版不该让规则失效），故匹配前剥版本
    （`base_of` 是 L1 对该 ID 形态的**单一真相源**；非常规形态〔如工作流复合名〕原样比）。
    """
    name = str(skill_id or "")
    if not name:
        return None
    try:
        plain = base_of(name)
    except SkillValidationError:
        plain = name
    for rule in rules:
        if rule.skill_id in (name, plain):
            return rule
    return None


def _is_items(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) > 0
    )


def _values_of(rule: MonitorRule, item: Mapping[str, Any], index: int) -> dict[str, Any]:
    missing = [name for name in rule.required_fields if name not in item]
    if missing:
        raise SignalEmissionError(
            f"{rule.skill_id}.{rule.items_field}[{index}] 缺规则所需的字段："
            + " / ".join(missing)
        )
    return {name: item[name] for name in rule.required_fields}


def _evidence_of(holder: Any, skill_run_id: Any = None) -> tuple[str, ...]:
    """证据引用（沿用 [01 §3](../../../docs/技术架构-v2/01-平台共享契约.md) 的字符串形态）。

    取被持有对象的 `evidence_refs[].ref`（信封 / 分析结果各有该字段），并补上本次执行
    的 `skill_run_id`（四类合法证据之一）——去重后升序（与采纳侧的规范化同序）。
    """
    refs = [
        str(getattr(ref, "ref", ""))
        for ref in (getattr(holder, "evidence_refs", ()) or ())
    ]
    if skill_run_id:
        refs.append(str(skill_run_id))
    return tuple(sorted({ref for ref in refs if ref}))


def _opinions_of(outcome: Any) -> Sequence[Any]:
    raw = getattr(getattr(outcome, "result", None), "opinions", ())
    if not _is_items(raw):
        return ()
    return tuple(raw)


def _analysis_evidence(outcome: Any, opinions: Sequence[Any]) -> tuple[str, ...]:
    """多视角分析的证据引用——逐视角的 `evidence_refs` 并上对照件自身的引用，去重升序。"""
    refs = [
        str(ref)
        for opinion in opinions
        for ref in (getattr(opinion, "evidence_refs", ()) or ())
    ]
    refs += [
        str(getattr(ref, "ref", ""))
        for ref in (getattr(getattr(outcome, "map", None), "evidence_refs", ()) or ())
    ]
    return tuple(sorted({ref for ref in refs if ref}))


def _analysis_trace_id(outcome: Any) -> str:
    """溯源链锚点：优先取对照链（[06 §3](../../../docs/技术架构-v2/06-L4-多视角推理.md)），
    退而取首条视角链——都取不到即显式失败（**不编一个**）。"""
    candidates = [
        getattr(getattr(outcome, "map", None), "trace", None),
        *(getattr(outcome, "traces", ()) or ()),
    ]
    for trace in candidates:
        value = str(getattr(getattr(trace, "trace_id", None), "value", "") or "").strip()
        if value:
            return value
    raise SignalEmissionError(
        "多视角分析结果未携任何推理链锚点——信号须可溯源（01 §11）"
    )


def _analysis_moment(outcome: Any) -> datetime:
    """分析的时刻——先取信封的 `as_of`，退而取首条链上首步的时戳。"""
    stamped = getattr(getattr(outcome, "envelope", None), "as_of", None)
    if isinstance(stamped, datetime):
        return _require_aware(stamped, "多视角分析的时刻")
    for trace in (getattr(outcome, "traces", ()) or ()):
        for step in (getattr(trace, "steps", ()) or ()):
            moment = getattr(step, "timestamp", None)
            if isinstance(moment, datetime):
                return _require_aware(moment, "多视角分析的时刻")
    raise SignalEmissionError("多视角分析未携任何时刻锚点（信封 as_of / 链上步时戳皆缺）")


def _require_aware(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise SignalEmissionError(f"{label}须为带时区的 datetime，收到 {value!r}")
    if value.tzinfo is None:
        raise SignalEmissionError(f"{label}须带时区（01 §8）")
    return value


def _require_neutral(guard: NeutralityGuard, text: str, where: str) -> None:
    verdict = guard.check_output(text)
    if not verdict.passed:
        hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
        raise SignalEmissionError(
            f"{where}未过 01 §6 中性化校验（{hits}）：生成文案须中性（铁律 2）"
        )
