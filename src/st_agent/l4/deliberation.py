"""多视角执行编排（[06 §2](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

给定主题 + 模式（快速 / 深度），对**启用视角**逐个跑其 `skill_bundle`（经注入的
``SkillRunner``，**并行 + 失败隔离**）、注入 [`MemoryReader`](../l2/memory/reader.py)
的 `deliberation` 记忆切片，把每视角产出为结构化 [`LensOpinion`](../contracts/capability_types.py)
（**过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 输出中性化校验**）。

职责边界（任务范围）：
- 视角选择（快速 = `FAST_COMBO` 默认组合 / 深度 = 全部启用；显式 `lens_ids` 覆盖）
- 并行执行 + **失败隔离**（某视角 `skill_bundle` 全非 `ok` 或为空 → `insufficient-data`，不阻塞他者）
- 结构化观点收集（每视角一条 `LensOpinion` + 一条 `Trace`）
- 记忆沉淀（`record_decision`：§2.4 决策写 `history` 节点）

**不含**：交叉对照 / Mini Debate（`T-L4-003`）、分歧图可视化 / 边界情形（`T-L4-004`）；
`analyze` 去向接进 L3 总线 / 组合根属 `T-INT-003`（铁律 7，见任务 A2）。

**中性视角（铁律 2）**：观点 `key_reasons` 逐条过 `check_output`（唯一真相源
[`contracts.neutrality`](../contracts/neutrality.py)，无关闭开关）；命中拟人化措辞即降级
`insufficient-data`，不送达违规文案。L4 **不存在「汇总结论」组件**——本模块只并列产出
各视角观点，绝不合并、不给统一建议（06 架构级红线、铁律 4）。

**合成端点（任务 A1）**：方向研判（`positive`/`negative`）经注入的 `OpinionSynthesizer`
端口完成，真 LLM 合成端点在 `T-INT-003` 组合根接；本任务自带的默认合成器**确定性、不触网**——
有 `ok` 数据标 `neutral`（不臆断方向）、信心度按数据充分度经 `ConfidencePolicy` 分档。
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from st_agent.contracts import (
    EvidenceRef,
    LensOpinion,
    NeutralityGuard,
    ResultEnvelope,
    Trace,
    TraceId,
    TraceStep,
    digest_of,
)
from st_agent.contracts.capability_types import Stance
from st_agent.l2.memory import SliceQuery
from st_agent.l4.errors import LensNotFoundError
from st_agent.l4.lens import Lens

__all__ = [
    "Deliberation",
    "DeliberationResult",
    "FAST_COMBO",
    "OpinionSynthesizer",
    "SynthesizedView",
]

#: 快速模式默认组合（06 §2.1「如 机会+风险+基本面」，官方预置、用户可改）。
#: 按**视角名**取，执行时经阵容解析为 lens_id（内置视角名为确定性 lens_id 派生键）。
FAST_COMBO: tuple[str, ...] = ("机会视角", "风险视角", "基本面视角")

_TASK_TYPE = "deliberation"
"""记忆切片查询的任务类型（[04 §3.1] `deliberation` 亲和 thesis/attention/identity）。"""

_INITIATOR = "l4_deliberation"
"""编排调用的发起方标注（01 §5 用量归属 / L1 审计口径）。"""

_OUTPUT_CHECK = NeutralityGuard()
"""输出中性化校验件（01 §6 执行点 2；LensOpinion 渲染前必经）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _insufficient_reasons(lens: Lens) -> tuple[str, ...]:
    """`insufficient-data` 的中性兜底理由（陈述式、无第一人称，恒过 `check_output`）。

    `LensOpinion.key_reasons` 不得为空（01 §3），故降级路径也须给可呈现的中性说明。
    """
    return (f"视角「{lens.name}」未获有效的 Skill 数据输出，无法给出观点（数据不足）",)


def _check_opinion_text(op: LensOpinion) -> bool:
    """观点的全部理由逐条过 `check_output`；任一命中即 False（拟人化不得送达）。"""
    return all(_OUTPUT_CHECK.check_output(r).passed for r in op.key_reasons)


class SynthesizedView(BaseModel):
    """合成器对单视角的产出（方向 + 理由，均须中性；信心度另按充分度分档）。"""

    model_config = ConfigDict(frozen=True)

    stance: Stance
    """`positive` / `negative` / `neutral`（数据不足由编排判，不由合成器给）。"""
    key_reasons: tuple[str, ...]
    """陈述式理由（渲染前逐条过 `check_output`）。"""


class OpinionSynthesizer(Protocol):
    """观点合成端口（任务 A1：真 LLM 合成端点在 `T-INT-003` 组合根接）。

    鸭子类型 `synthesize(lens, ok_outputs, memory_slice_ids, sufficiency) -> SynthesizedView`：
    接收该视角**成功的 Skill 输出载荷**（`[envelope.data]`）、注入的**记忆节点 ID** 与
    **数据充分度**（0–1），产出方向与理由。**不得**产出 `insufficient-data`（由编排判定）。
    """

    def synthesize(
        self,
        lens: Lens,
        ok_outputs: list[Any],
        memory_slice_ids: tuple[str, ...],
        sufficiency: float,
    ) -> SynthesizedView: ...


class _DefaultSynthesizer:
    """确定性默认合成器——有 `ok` 数据标 `neutral`（不臆断方向、不触网，任务 A1）。

    真方向研判（`positive`/`negative`）属注入的合成端点；本件只保证「数据到位即有可呈现的
    中性观点卡」，把方向留给上游——这正合 06「系统永远不给统一建议」与铁律 4：编排不替用户
    判断涨跌，只并列呈现各视角所依据的数据充分度。
    """

    def synthesize(
        self,
        lens: Lens,
        ok_outputs: list[Any],
        memory_slice_ids: tuple[str, ...],
        sufficiency: float,
    ) -> SynthesizedView:
        reasons = [f"视角「{lens.name}」基于 {len(ok_outputs)} 项有效 Skill 数据形成中性观点"]
        if memory_slice_ids:
            reasons.append(f"参考 {len(memory_slice_ids)} 项个性化记忆切片（{lens.description}）")
        else:
            reasons.append(f"未取到相关记忆切片，观点仅基于 Skill 数据面（{lens.description}）")
        return SynthesizedView(stance="neutral", key_reasons=tuple(reasons))


class DeliberationResult(BaseModel):
    """一次 Deliberation 的产出（各视角观点 + 推理链，并列不合并）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    topic: str
    mode: str
    envelope: ResultEnvelope
    """编排整体结果信封：成功（`ok`，`data` 为 `lens_id` 列表）；主题空 → `validation_failed`。"""
    opinions: tuple[LensOpinion, ...] = ()
    """各视角的结构化观点（01 §3；**并列**，不含任何合并结论——铁律 4）。"""
    traces: tuple[Trace, ...] = ()
    """每视角一条推理链（01 §4；`SkillRunner` 已记 `skill_run` 步，编排补 `memory_read`）。"""
    lens_ids: tuple[str, ...] = ()
    """本次参与的视角（空阵容 → 空集，由上层渲染空态入口，06 §6 归 `T-L4-004`）。"""

    @property
    def opinions_by_lens(self) -> dict[str, LensOpinion]:
        """按 `lens_id` 索引观点（下游 `T-L4-003` 交叉对照的取数面）。"""
        return {o.lens_id: o for o in self.opinions}


class Deliberation:
    """多视角执行编排门面（[06 §2](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    :param roster: 视角阵容（鸭子类型 `list_enabled()` / `get(lens_id)`，即
        [`l4.roster.LensRoster`](roster.py)）——选视角用
    :param runner: Skill 执行面（鸭子类型 `run(...) -> RunOutcome`，即 L1 `SkillRunner`）
        ——跑各视角 skill_bundle；**不注入即拒绝编排**（`unavailable`，不臆测）
    :param reader: 记忆切片查询面（鸭子类型 `query(SliceQuery)`，即 L2 `MemoryReader`）；
        缺省 `None` → 不注入记忆（观点仅基于 Skill 数据面）
    :param writer: 记忆写入面（鸭子类型 `add_node(node)`，即 L2 `MemoryWriter`）；
        缺省 `None` → `record_decision` 返回 `unavailable` + 点名归属（不静默丢弃）
    :param synthesizer: 观点合成端口（[`OpinionSynthesizer`]）；缺省用确定性默认件
        （`neutral` + 分档信心度，不触网，任务 A1）
    :param executor: 并行执行器（鸭子类型 `map(fn, seq)`）；缺省 `ThreadPoolExecutor`——
        **测试可传同线程件保确定性**；任一视角异常被捕获为该视角 `insufficient-data`，不阻塞他者
    :param now: 当前时刻取法（缺省本机带时区时间；注入以测 `as_of`）
    """

    def __init__(
        self,
        *,
        roster: Any,
        runner: Any,
        reader: Any = None,
        writer: Any = None,
        synthesizer: OpinionSynthesizer | None = None,
        executor: Any = None,
        now: Any = None,
    ) -> None:
        self._roster = roster
        self._runner = runner
        self._reader = reader
        self._writer = writer
        self._synthesizer = synthesizer or _DefaultSynthesizer()
        self._executor = executor
        self._now = now or _now

    # ───────────────────────── 编排主入口（06 §2.2–§2.3） ─────────────────────────

    def deliberate(
        self,
        topic: str,
        *,
        mode: str = "deep",
        lens_ids: Mapping[str, Any] | None = None,
    ) -> DeliberationResult:
        """对选定视角并行执行编排，收集结构化观点（失败隔离）。

        :param mode: `quick`（[`FAST_COMBO`] 默认组合）/ `deep`（全部启用视角）
        :param lens_ids: **显式覆盖**默认/全部选择（按 lens_id 逐项 `roster.get` 寻址）；
            缺省 `None` → 按 mode 选（快速取默认组合名、深度取全部启用）
        """
        moment = self._now()
        if not (topic or "").strip():
            return DeliberationResult(
                topic=topic, mode=mode,
                envelope=ResultEnvelope.validation_failed(
                    "Deliberation 缺少主题（无法界定分析对象，06 §2）"),
            )
        if self._runner is None:
            return DeliberationResult(
                topic=topic, mode=mode,
                envelope=ResultEnvelope.unavailable(
                    "未注入 Skill 执行面，无法编排视角（06 §2；装配归 T-INT-003）",
                    last_updated_at=moment),
            )
        if mode not in ("quick", "deep"):
            return DeliberationResult(
                topic=topic, mode=mode,
                envelope=ResultEnvelope.validation_failed(
                    f"未知模式 {mode!r}（须为 quick / deep，06 §2.1）"),
            )

        selected = self._select(mode, lens_ids)
        if not selected:
            return DeliberationResult(
                topic=topic, mode=mode, lens_ids=(),
                envelope=ResultEnvelope.empty(
                    "无启用视角可参与编排（阵容为空或选定视角均已停用）", as_of=moment),
            )

        memory_ids, memory_read_at = self._read_memory(topic)
        ops, traces = self._run_lenses(selected, topic, memory_ids, memory_read_at)
        participated = tuple(o.lens_id for o in ops)
        envelope = ResultEnvelope.ok(
            list(participated), as_of=memory_read_at or moment,
            evidence_refs=tuple(EvidenceRef(kind="memory_node_id", ref=i)
                                 for i in memory_ids),
        )
        return DeliberationResult(
            topic=topic, mode=mode, envelope=envelope,
            opinions=tuple(ops), traces=tuple(traces), lens_ids=participated,
        )

    # ───────────────────────── 决策沉淀（06 §2.4） ─────────────────────────

    def record_decision(
        self,
        *,
        decision: str,
        reasoning: str = "",
        adopted_lens_ids: tuple[str, ...] = (),
        ignored_lens_ids: tuple[str, ...] = (),
    ) -> ResultEnvelope:
        """用户在 Deliberation 后的决策 + 理由 + 采纳/忽略视角 → 写 L2 `history` 节点。

        `source="user_stated"`（用户自陈，非副驾推断，同 [`app.py`](../app.py) `write_preference`
        先例，任务 A3）。L4 **不自校验决策内容**——写语义归 L2。未注入写入面 → `unavailable`
        + 点名归属（不静默丢弃，06 §2.4 消费面为 L6）。
        """
        moment = self._now()
        if not (decision or "").strip():
            return ResultEnvelope.validation_failed(
                "决策沉淀缺少决策文本（06 §2.4）")
        if self._writer is None:
            return ResultEnvelope.unavailable(
                "未注入记忆写入面，决策无法沉淀（06 §2.4；装配归 T-INT-003）",
                last_updated_at=moment)
        from st_agent.l2.memory import checked_node, new_node_id  # 局部导入：仅在写入路径需要
        event = decision
        if reasoning:
            event = f"{decision}｜理由：{reasoning}"
        if adopted_lens_ids or ignored_lens_ids:
            event += f"｜采纳视角：{list(adopted_lens_ids)}｜忽略视角：{list(ignored_lens_ids)}"
        try:
            node = checked_node(
                type="history", memory_node_id=new_node_id(), source="user_stated",
                privacy_level="private", event=event, confidence=1.0,
                created_at=moment, updated_at=moment,
            )
            self._writer.add_node(node)
        except Exception as exc:  # noqa: BLE001 —— 节点非法 / 写入失败一律显式失败，不静默
            return ResultEnvelope.failed(
                f"决策沉淀未落 L2 history 节点：{exc}", log_ref="l4/decision")
        return ResultEnvelope.ok(
            {"memory_node_id": node.memory_node_id}, as_of=moment,
            evidence_refs=(EvidenceRef(kind="memory_node_id", ref=node.memory_node_id),))

    # ───────────────────────── 内部：选视角 / 读记忆 / 跑视角 ─────────────────────────

    def _select(self, mode: str, lens_ids: Mapping[str, Any] | None) -> list[Lens]:
        """选定参与视角。显式 `lens_ids` 覆盖 mode 默认；`roster.get` 寻址失败即跳过该项。"""
        if lens_ids is not None:
            out: list[Lens] = []
            for lid in lens_ids:
                try:
                    out.append(self._roster.get(lid))
                except (LensNotFoundError, KeyError):
                    continue  # 寻址失败不臆测——跳过（06 §2 失败显式化在观点侧，选侧即不纳入）
            return out
        if mode == "quick":
            by_name = {l.name: l for l in self._roster.list_enabled()}
            return [by_name[n] for n in FAST_COMBO if n in by_name]
        return list(self._roster.list_enabled())

    def _read_memory(self, topic: str) -> tuple[tuple[str, ...], datetime | None]:
        """取 `deliberation` 记忆切片（注入 reader 时）；返回 (节点ID, 查询时刻)。"""
        if self._reader is None:
            return (), None
        moment = self._now()
        result = self._reader.query(SliceQuery(task_type=_TASK_TYPE, topic=topic))
        ids = tuple(s.node.memory_node_id for s in result.slices)
        return ids, moment

    def _run_lenses(
        self, selected: list[Lens], topic: str,
        memory_ids: tuple[str, ...], memory_read_at: datetime | None,
    ) -> tuple[list[LensOpinion], list[Trace]]:
        """并行跑各视角（失败隔离）；返回按**选定顺序**对齐的观点与链。"""
        def _one(lens: Lens) -> tuple[LensOpinion, Trace]:
            try:
                return self._run_one(lens, topic, memory_ids, memory_read_at)
            except Exception as exc:  # noqa: BLE001 —— 单视角抛穿不外溢：降级为数据不足（任务 A4）
                return self._insufficient(
                    lens, memory_ids, Trace(trace_id=TraceId.generate()),
                    f"视角「{lens.name}」执行抛出异常，按数据不足处理（失败隔离）：{exc}",
                )

        if self._executor is not None:
            pairs = list(self._executor.map(_one, selected))
        else:
            with ThreadPoolExecutor(max_workers=min(8, len(selected))) as pool:
                pairs = list(pool.map(_one, selected))
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def _run_one(
        self, lens: Lens, topic: str,
        memory_ids: tuple[str, ...], memory_read_at: datetime | None,
    ) -> tuple[LensOpinion, Trace]:
        """单视角：读记忆 → 跑 bundle → 分档信心度 → 合成 → 中性化校验（抛穿由调用方兜）。"""
        chain = Trace(trace_id=TraceId.generate())
        if memory_ids:  # 编排显式记一步 memory_read（SkillRunner 只记 skill_run 步，01 §4 五型）
            chain = chain.append_step(TraceStep(
                step_type="memory_read", ref=chain.trace_id.value,
                input_digest=digest_of({"task_type": _TASK_TYPE, "topic": topic}),
                output_digest=digest_of(memory_ids),
                duration_ms=0, timestamp=memory_read_at or self._now(),
            ))
        bundle = list(lens.skill_bundle)
        if not bundle:  # 空 bundle：无可跑 Skill → 直接数据不足（[T-L4-001] A1 留口的兜底）
            return self._insufficient(lens, memory_ids, chain,
                                       f"视角「{lens.name}」的 skill_bundle 为空，无可执行 Skill")

        ok_outputs: list[Any] = []
        run_ids: list[str] = []
        for sid in bundle:
            # 不向底层 Skill 塞记忆 inputs——官方 skill 的 input_schema 各异，注入臆测即
            # validation_failed；记忆注入落在**视角层**（合成上下文 + 观点证据面）。
            outcome = self._runner.run(
                sid, {}, trace=chain,
                initiator=_INITIATOR, purpose=f"deliberation:{topic}",
            )
            chain = outcome.trace  # 链式续接：SkillRunner 把 skill_run 步追加进来
            run_ids.append(outcome.skill_run_id)
            if outcome.envelope.status == "ok" and outcome.envelope.data is not None:
                ok_outputs.append(outcome.envelope.data)

        sufficiency = len(ok_outputs) / len(bundle)
        if not ok_outputs:  # 无有效数据（全失败态或合法空结果）→ 数据不足（失败隔离，任务 A4）
            return self._insufficient(lens, memory_ids, chain,
                                       f"视角「{lens.name}」的 {len(bundle)} 项 Skill 均无有效数据输出")

        view = self._synthesizer.synthesize(lens, ok_outputs, memory_ids, sufficiency)
        op = LensOpinion(
            lens_id=lens.lens_id, stance=view.stance, key_reasons=view.key_reasons,
            evidence_refs=self._refs(run_ids, memory_ids),
            confidence=lens.confidence_policy.evaluate(sufficiency),
            skills_triggered=tuple(run_ids), trace_id=chain.trace_id.value,
        )
        if not _check_opinion_text(op):  # 合成器产出违规 → 降级，不送达（01 §6 执行点 2）
            return self._insufficient(lens, memory_ids, chain,
                                       f"视角「{lens.name}」的合成观点理由命中中性化校验，降级为数据不足")
        chain = chain.append_step(TraceStep(
            step_type="lens_opinion", ref=chain.trace_id.value,
            input_digest=digest_of({"skill_runs": run_ids, "sufficiency": sufficiency}),
            output_digest=digest_of(op.model_dump(mode="json")),
            duration_ms=0, timestamp=self._now(),
        ))
        return op, chain

    @staticmethod
    def _refs(run_ids: list[str], memory_ids: tuple[str, ...]) -> tuple[str, ...]:
        """观点的证据引用面（01 §3：**字符串形态**的 §1 ID，非 ResultEnvelope 的 EvidenceRef）。"""
        return tuple(run_ids) + tuple(memory_ids)

    def _insufficient(
        self, lens: Lens, memory_ids: tuple[str, ...],
        chain: Trace, note: str,
    ) -> tuple[LensOpinion, Trace]:
        """数据不足观点（01 §3 `insufficient-data`；信心度 `low`）+ 记降级步的链。

        降级路径同样记入链（01 §4），`note` 为人可读中性原因。
        """
        c = chain.append_degraded(
            TraceStep(
                step_type="lens_opinion", ref=chain.trace_id.value,
                input_digest=digest_of({"lens": lens.name, "bundle": len(lens.skill_bundle)}),
                output_digest=digest_of("insufficient-data"),
                duration_ms=0, timestamp=self._now(),
            ),
            note=note,
        )
        op = LensOpinion(
            lens_id=lens.lens_id, stance="insufficient-data",
            key_reasons=_insufficient_reasons(lens),
            evidence_refs=self._refs([], memory_ids),
            confidence="low", skills_triggered=(), trace_id=c.trace_id.value,
        )
        return op, c
