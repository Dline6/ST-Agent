"""L5 信号模型与采纳面（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

上游（L1 Skill 定时监控、L4 Deliberation）经 [`SignalEmitted`](../../../docs/技术架构-v2/01-平台共享契约.md)
事件投递信号；本模块是它的**唯一解释方**——把负载按 [01 §11](../../../docs/技术架构-v2/01-平台共享契约.md)
登记的五个字段读成一条 :class:`Signal`。

三条口径（[D-081](../../../项目管理/决策日志.md)）：

- **`signal_id` 由本层铸造**（[01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 登记「产生方＝L5」）——
  形态是**投递内容的确定性摘要**（[`digest_id`](../../contracts/identifiers.py)），故同一信号
  重复投递得**同一 ID**（幂等锚点），发布方无需也无从分配 ID。
- **内容载体是内联结构**（`:class:`SignalContent``）——`conclusion` + 逐视角摘要，
  「**非单视角结论**」由此**结构可查**（[铁律 4](../../../项目管理/工程宪法.md)：多视角并列保留、
  不合并分歧）；证据引用另置 `evidence_refs`（沿用 [01 §3](../../../docs/技术架构-v2/01-平台共享契约.md)
  的字符串形态，不造第二种表示）。
- **采纳不落任何东西**——纯函数，不写分区；投递留痕是 `delivery_id` 的事（[07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

负载不合契约一律**显式拒绝**（:class:`~st_agent.l5.errors.SignalAdoptionError`，逐类点名），
不臆补缺失字段、不产出半截信号；结论与逐视角摘要在采纳时过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
执行点 2 的输出校验（推送文案不得拟人化——中性化是本层边界上不开的口子）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.capability_types import Stance
from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import (
    AnnouncementId,
    DatasetSnapshotId,
    LensId,
    MemoryNodeId,
    SignalId,
    SkillRunId,
    TraceId,
    digest_id,
)
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l5.errors import SignalAdoptionError

__all__ = [
    "SIGNAL_EVENT",
    "Signal",
    "SignalContent",
    "SignalLensStance",
    "SignalLevel",
    "adopt_signal",
    "signal_digest_key",
    "signal_event",
]

SIGNAL_EVENT = "SignalEmitted"
"""本层订阅的事件名（01 §11）。"""

SignalLevel = Literal["emergency", "important", "routine"]
"""信号级别（07 §1）——`emergency` / `important` / `routine`。"""

_EVIDENCE_TYPES = (AnnouncementId, DatasetSnapshotId, SkillRunId, MemoryNodeId)
"""证据引用的四类 §1 ID（01 §3 与 §5 同一口径）。"""

_PAYLOAD_FIELDS = ("level", "content_ref", "evidence_refs", "dedup_key", "source_trace_id")
"""01 §11 登记的 `SignalEmitted` 负载字段（`signal_id` **不在**其中——由本层铸造）。"""

_OUTPUT_CHECK = NeutralityGuard()
"""01 §6 执行点 2 的守卫（规则库**取用时**解析，故官方 Pack 注入对它一样生效）。"""


class SignalLensStance(BaseModel):
    """一条逐视角摘要（多视角并列的一项；**不含**任何合并后的统一结论）。"""

    model_config = ConfigDict(frozen=True)

    lens_id: Annotated[str, Field(min_length=3, max_length=128)]
    """产生该摘要的视角（§1 `lens_id` 形态；本层只校验形态，**不解析 L4 阵容**）。"""
    stance: Stance
    """该视角的方向（01 §3 同一值域：`positive` / `negative` / `neutral` / `insufficient-data`）。"""
    summary: Annotated[str, Field(min_length=1)]
    """该视角的陈述式摘要（采纳前过 §6 输出校验）。"""

    @model_validator(mode="after")
    def _lens_id_form(self) -> "SignalLensStance":
        _check_id_form(LensId, self.lens_id, "lens_id")   # 只验形态，不解析 L4 阵容
        return self


class SignalContent(BaseModel):
    """信号的内容载体（07 §1 的 `content_ref`）。

    `conclusion` 是**非单一视角的措辞**——它是可送达的结论陈述，不代表某个视角的立场；
    各视角的立场一律并行留在 :attr:`lens_stances` 里（铁律 4：分歧并列、不合并）。
    """

    model_config = ConfigDict(frozen=True)

    conclusion: Annotated[str, Field(min_length=1)]
    """中性陈述式结论（采纳前过 §6 输出校验）。"""
    lens_stances: tuple[SignalLensStance, ...]
    """逐视角摘要（**非空**；按 `lens_id` 升序规范化，同一视角不得出现两次）。"""

    @model_validator(mode="after")
    def _stances_present_and_unique(self) -> "SignalContent":
        if not self.lens_stances:
            raise ContractViolation(
                "lens_stances 不得为空——信号的内容是「非单视角结论」，"
                "至少要有一条逐视角摘要（07 §1 / 铁律 4）"
            )
        lens_ids = [s.lens_id for s in self.lens_stances]
        if len(set(lens_ids)) != len(lens_ids):
            raise ContractViolation(f"同一视角不得出现两次：{sorted(lens_ids)}")
        return self

    def digest_parts(self) -> tuple[str, ...]:
        """参与 `signal_id` 摘要的内容片段（视角顺序不影响结果）。"""
        return (
            self.conclusion,
            *(f"{s.lens_id}\u001f{s.stance}\u001f{s.summary}" for s in self.lens_stances),
        )


class Signal(BaseModel):
    """一条已采纳的待触达信号（07 §1 的六字段）。"""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    """01 §1 `signal_id`（本层按投递内容的确定性摘要铸造；形态 `sig_` + 20 位十六进制）。"""
    level: SignalLevel
    """信号级别——触达的**分级依据**（注意力预算按级别放行，07 §2）。"""
    content_ref: SignalContent
    """内容载体（结论不可分割地带着逐视角摘要）。"""
    evidence_refs: tuple[str, ...]
    """证据引用（**非空**；四类 §1 ID；按字符串升序去重规范化）。"""
    dedup_key: str
    """去重合并键（同一标的同一事件类型；合并动作归 [07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md)）。"""
    source_trace_id: str
    """溯源推理链（§1 `trace_id` 形态）。"""

    @model_validator(mode="after")
    def _ids_and_refs(self) -> "Signal":
        _check_id_form(SignalId, self.signal_id, "signal_id")
        _check_id_form(TraceId, self.source_trace_id, "source_trace_id")
        if not self.evidence_refs:
            raise ContractViolation("evidence_refs 不得为空（结论须可追溯，01 §3 / §5）")
        for ref in self.evidence_refs:
            if _evidence_kind(ref) is None:
                raise ContractViolation(
                    f"evidence_refs 只接受 {_evidence_kinds()} 四类引用，收到 {ref!r}"
                )
        return self

    def digest_key(self) -> tuple[str, ...]:
        """`signal_id` 的摘要输入（与 :func:`signal_digest_key` 同一份推导）。"""
        return signal_digest_key(
            level=self.level,
            content=self.content_ref,
            evidence_refs=self.evidence_refs,
            dedup_key=self.dedup_key,
            source_trace_id=self.source_trace_id,
        )


def signal_event(
    *,
    level: str,
    content_ref: SignalContent | Mapping[str, Any],
    evidence_refs: Sequence[str],
    dedup_key: str,
    source_trace_id: str,
    occurred_at: datetime,
) -> PlatformEvent:
    """构造一条 `SignalEmitted` 事件（01 §11 的负载结构）——**发布端的调用面**。

    上游（L1 定时监控 / L4 Deliberation）据此填负载并发布到
    [01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的投递面（鸭子端口
    ``publish(event)``）。本函数只**组装**，不发布、不校验（校验在采纳侧
    :func:`adopt_signal`：发布端填错即被显式拒收，见 07 §1）。

    形态与 [01 §11](../../../docs/技术架构-v2/01-平台共享契约.md) 的字段表一一对应；
    ``signal_id`` **不在此处**——它由 L5 采纳时按投递内容的确定性摘要铸造。
    """
    return PlatformEvent(
        event=SIGNAL_EVENT,
        payload={
            "level": level,
            "content_ref": (
                content_ref
                if isinstance(content_ref, Mapping)
                else content_ref.model_dump(mode="json")
            ),
            "evidence_refs": list(evidence_refs),
            "dedup_key": dedup_key,
            "source_trace_id": source_trace_id,
        },
        trace_id=source_trace_id,
        occurred_at=occurred_at,
    )


def signal_digest_key(
    *,
    level: str,
    content: SignalContent,
    evidence_refs: tuple[str, ...],
    dedup_key: str,
    source_trace_id: str,
) -> tuple[str, ...]:
    """`signal_id` 的摘要输入——**全层唯一**一份推导。

    `signal_id` 只由这些片段决定，故「同一信号重复投递 → 同一 ID」；片段里不含
    采纳时刻，故同一条信号在何时被读到都指向同一个 ID（去重、留痕、回溯共用它）。
    """
    return (
        level,
        dedup_key,
        source_trace_id,
        *content.digest_parts(),
        *evidence_refs,
    )


def adopt_signal(event: PlatformEvent, *, guard: NeutralityGuard | None = None) -> Signal:
    """把一条 `SignalEmitted` 事件采纳为 :class:`Signal`（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

    :param event: 01 §11 的事件信封（负载按该节登记的字段表解释）
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :raises SignalAdoptionError: 事件名不符 / 缺 `trace_id` / 负载缺字段 / 取值非法 /
        内容未过 §6 输出校验——**逐类点名字段或命中原文**

    规范化（使「同一条信号」与「同一个 `signal_id`」一一对应）：`lens_stances` 按
    `lens_id` 升序、`evidence_refs` 去重后升序；**两者的原始顺序不影响结果**。
    """
    if not isinstance(event, PlatformEvent):
        raise SignalAdoptionError(f"须传入 01 §11 的事件信封，收到 {type(event).__name__}")
    if event.event != SIGNAL_EVENT:
        raise SignalAdoptionError(f"事件名须为 {SIGNAL_EVENT}，收到 {event.event!r}")
    trace_id = _text(event.trace_id, "事件 trace_id（01 §11 强制）")

    payload = event.payload
    if not isinstance(payload, Mapping):
        raise SignalAdoptionError(f"负载须为映射，收到 {type(payload).__name__}")
    missing = [name for name in _PAYLOAD_FIELDS if name not in payload]
    if missing:
        raise SignalAdoptionError("负载缺字段：" + " / ".join(missing))

    level = _level(payload["level"])
    content = _content(payload["content_ref"])
    evidence_refs = _evidence_refs(payload["evidence_refs"])
    dedup_key = _text(payload["dedup_key"], "dedup_key")
    source_trace_id = _require_id_form(
        TraceId, _text(payload["source_trace_id"], "source_trace_id"), "source_trace_id"
    )

    check = guard if guard is not None else _OUTPUT_CHECK
    _require_neutral(check, content.conclusion, "content_ref.conclusion")
    for stance in content.lens_stances:
        _require_neutral(check, stance.summary, f"content_ref.lens_stances[{stance.lens_id}].summary")

    # trace_id 在信封上（01 §11），source_trace_id 在负载里——两者同源，不一致即上游自相矛盾。
    if trace_id != source_trace_id:
        raise SignalAdoptionError(
            f"事件 trace_id（{trace_id!r}）与负载 source_trace_id（{source_trace_id!r}）"
            "须同源（01 §11）"
        )

    signal = Signal(
        signal_id=SignalId.of(
            digest_id(
                "sig",
                *signal_digest_key(
                    level=level,
                    content=content,
                    evidence_refs=evidence_refs,
                    dedup_key=dedup_key,
                    source_trace_id=source_trace_id,
                ),
            )
        ).value,
        level=level,
        content_ref=content,
        evidence_refs=evidence_refs,
        dedup_key=dedup_key,
        source_trace_id=source_trace_id,
    )
    return signal


# ───────────────────────── 负载字段的解释 ─────────────────────────


def _text(value: Any, field: str) -> str:
    """非空字符串字段（缺 / 空 / 非字符串一律显式拒）。"""
    if not isinstance(value, str) or not value.strip():
        raise SignalAdoptionError(f"{field} 须为非空字符串，收到 {value!r}")
    return value


def _level(value: Any) -> SignalLevel:
    if value not in ("emergency", "important", "routine"):
        raise SignalAdoptionError(
            f"level 须为 emergency / important / routine 之一，收到 {value!r}"
        )
    return value  # type: ignore[return-value]


def _content(value: Any) -> SignalContent:
    if not isinstance(value, Mapping):
        raise SignalAdoptionError(f"content_ref 须为映射，收到 {type(value).__name__}")
    for name in ("conclusion", "lens_stances"):
        if name not in value:
            raise SignalAdoptionError(f"content_ref 缺字段：{name}")
    conclusion = _text(value["conclusion"], "content_ref.conclusion")

    raw_stances = value["lens_stances"]
    if not isinstance(raw_stances, Sequence) or isinstance(raw_stances, (str, bytes)):
        raise SignalAdoptionError(
            f"content_ref.lens_stances 须为列表，收到 {type(raw_stances).__name__}"
        )
    if not raw_stances:
        raise SignalAdoptionError("content_ref.lens_stances 不得为空（非单视角结论，铁律 4）")

    stances: list[SignalLensStance] = []
    for index, item in enumerate(raw_stances):
        where = f"content_ref.lens_stances[{index}]"
        if not isinstance(item, Mapping):
            raise SignalAdoptionError(f"{where} 须为映射，收到 {type(item).__name__}")
        for name in ("lens_id", "stance", "summary"):
            if name not in item:
                raise SignalAdoptionError(f"{where} 缺字段：{name}")
        stances.append(
            SignalLensStance(
                lens_id=_require_id_form(LensId, _text(item["lens_id"], f"{where}.lens_id"),
                                         f"{where}.lens_id"),
                stance=_stance(item["stance"], where),
                summary=_text(item["summary"], f"{where}.summary"),
            )
        )
    try:
        return SignalContent(
            conclusion=conclusion, lens_stances=tuple(sorted(stances, key=lambda s: s.lens_id))
        )
    except ValidationError as exc:
        raise SignalAdoptionError(f"content_ref 不合约：{exc}") from exc


def _stance(value: Any, where: str) -> Stance:
    if value not in ("positive", "negative", "neutral", "insufficient-data"):
        raise SignalAdoptionError(
            f"{where}.stance 须为 positive / negative / neutral / insufficient-data 之一，"
            f"收到 {value!r}"
        )
    return value  # type: ignore[return-value]


def _evidence_refs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SignalAdoptionError(f"evidence_refs 须为列表，收到 {type(value).__name__}")
    if not value:
        raise SignalAdoptionError("evidence_refs 不得为空（结论须可追溯，01 §3 / §5）")
    refs: list[str] = []
    for index, item in enumerate(value):
        ref = _text(item, f"evidence_refs[{index}]")
        if _evidence_kind(ref) is None:
            raise SignalAdoptionError(
                f"evidence_refs[{index}] 只接受 {_evidence_kinds()} 四类引用，收到 {ref!r}"
            )
        refs.append(ref)
    return tuple(sorted(set(refs)))


def _evidence_kinds() -> str:
    """四类证据引用的 `id_kind`（错误文案与判据共用一份，不各写一遍）。"""
    return " / ".join(t.id_kind for t in _EVIDENCE_TYPES)  # type: ignore[attr-defined]


def _evidence_kind(ref: str) -> str | None:
    """证据引用的 §1 类型名；不属四类即 ``None``（不猜、不回落）。"""
    for typ in _EVIDENCE_TYPES:
        try:
            typ.of(ref)
        except ValidationError:
            continue
        return typ.id_kind  # type: ignore[attr-defined]
    return None


def _check_id_form(typ: Any, value: str, field: str) -> None:
    """**值对象层面**的形态校验：抛 :class:`ContractViolation`，由 pydantic 包装为
    ``ValidationError``（与契约层其余模型的取向一致）。"""
    try:
        typ.of(value)
    except ValidationError as exc:
        raise ContractViolation(f"{field} 形态非法（不符合 01 §1）：{value!r}") from exc


def _require_id_form(typ: Any, value: str, field: str) -> str:
    """**采纳面**的形态校验：抛 :class:`SignalAdoptionError`（携字段名，供调用方分流）。"""
    try:
        return typ.of(value).value
    except ValidationError as exc:
        raise SignalAdoptionError(f"{field} 形态非法（不符合 01 §1）：{value!r}") from exc


def _require_neutral(guard: NeutralityGuard, text: str, where: str) -> None:
    """01 §6 执行点 2：命中即拒收并点名命中的原文。"""
    verdict = guard.check_output(text)
    if not verdict.passed:
        hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
        raise SignalAdoptionError(f"{where} 未过 01 §6 中性化校验（{hits}）：{text!r}")
