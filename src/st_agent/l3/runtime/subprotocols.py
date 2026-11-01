"""L3 子协议动作的循环承接面（[T-AGT-030](../../../项目管理/tasks/T-AGT-030-子协议动作化.md)；
[05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

`configure` / `memory_op` / `analyze` / `train` / `explain` 在循环内以五个工具名出现，
由 L3 **拦截承接**（与计划 / 提问原语同型，不产 `skill_run` / Trace 步）。本模块是
五种承接的**唯一实现**：

- 只读 / 无副作用：配置草稿只合成、不落值；记忆卡只列 / 取、不裁决；训练只产未确认
  复述、不落账；分析只跑读取型视角链；推理链只展开当前链；
- 成功产卡的动作返回描述件（`SubprotocolResult.cards`），由循环以 `card_ready` 交还；
- 失败 / 无卡时返回 `note`（显式说明），由循环回灌工作上下文，模型可改选或作答；
- 端口一律由组合根注入（鸭子面）：L3 **不 import L4 / L6**（[铁律 7](../../../项目管理/工程宪法.md)）。

**卡片不是第二套渲染路径**：这里的描述件全部来自 `l3.render.describe` 的既有产出
（[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md) 的组件类型与槽），表现层仍走
同一个注册表。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.subprotocols import (
    ANALYZE_TOOL,
    CONFIGURE_TOOL,
    EXPLAIN_TOOL,
    MEMORY_OP_TOOL,
    TRAIN_TOOL,
)
from st_agent.l3.conflict.adjudication import ConflictAdjudication
from st_agent.l3.render.describe import (
    describe_adjudication,
    describe_divergence_map,
    describe_draft,
    describe_trace,
)
from st_agent.l3.runtime.truncation import json_text

__all__ = [
    "SUBPROTOCOL_TOOLS",
    "SubprotocolPorts",
    "SubprotocolResult",
    "handle",
]

SUBPROTOCOL_TOOLS: frozenset[str] = frozenset({
    CONFIGURE_TOOL, MEMORY_OP_TOOL, ANALYZE_TOOL, TRAIN_TOOL, EXPLAIN_TOOL,
})
"""循环内由 L3 拦截的五个子协议动作名（工具目录里的 `name`）。"""


@dataclass(frozen=True)
class SubprotocolPorts:
    """子协议动作的执行端口（组合根注入；缺省 ``None`` 即该动作显式不可用）。

    ``configs`` / ``adjudicator`` / ``deliberations`` / ``trainings`` 都是既有鸭子面：
    L3 只按属性 / 方法取值，不 import L4 / L6。
    """

    configs: Any = None
    adjudicator: Any = None
    deliberations: Any = None
    trainings: Any = None


@dataclass(frozen=True)
class SubprotocolResult:
    """一次子协议动作的承接结果。

    ``cards`` 非空即成功产出描述件（循环据此 `card_ready` 交还）；``note`` 是回灌给
    模型的显式说明（失败原因 / 训练复述 / 无卡说明）。两者互不替代。
    """

    envelope: ResultEnvelope
    cards: tuple[Any, ...] = ()
    note: str = ""

    @property
    def card_ready(self) -> bool:
        return bool(self.cards) and self.envelope.status == "ok"


@dataclass(frozen=True)
class _ActionInput:
    """鸭子型动作输入（``analyze`` / ``train`` 端口只读 ``.values`` / ``.target``）。"""

    values: dict[str, Any]
    target: str = ""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _failure(envelope: ResultEnvelope, note: str = "") -> SubprotocolResult:
    return SubprotocolResult(envelope=envelope, note=note or envelope.reason or "")


def _unavailable(reason: str) -> ResultEnvelope:
    return ResultEnvelope.unavailable(reason, last_updated_at=_now())


def _mapping(name: str, value: Any) -> tuple[dict[str, Any], str]:
    if value is None:
        return {}, ""
    if not isinstance(value, Mapping):
        return {}, f"{name} 须为对象，得到 {type(value).__name__}"
    return dict(value), ""


def _text(arguments: Mapping[str, Any], key: str) -> str:
    return str(arguments.get(key) or "").strip()


def handle(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    trace: Any = None,
    ports: SubprotocolPorts | None = None,
) -> SubprotocolResult:
    """承接一次子协议动作（循环内唯一入口）。

    :param tool_name: 五个动作名之一（目录外的名字按调用方缺陷显式拒绝）
    :param arguments: 模型给出的调用参数（未经循环改写）
    :param trace: 本次循环当前链（`sk_explain` 展开；其余动作用不到）
    :param ports: 组合根注入的执行端口
    """
    name = str(tool_name or "").strip()
    if name not in SUBPROTOCOL_TOOLS:
        return _failure(ResultEnvelope.validation_failed(
            f"{name!r} 不是子协议动作（应为 {' / '.join(sorted(SUBPROTOCOL_TOOLS))}）"
        ))
    args, error = _mapping("arguments", arguments)
    if error:
        return _failure(ResultEnvelope.validation_failed(error))
    if ports is None:
        return _failure(_unavailable(
            "未注入子协议动作端口，无法承接该动作"
            "（装配归组合根；05 §3.1）"
        ))
    try:
        if name == CONFIGURE_TOOL:
            return _configure(args, ports)
        if name == MEMORY_OP_TOOL:
            return _memory_op(args, ports)
        if name == ANALYZE_TOOL:
            return _analyze(args, ports)
        if name == TRAIN_TOOL:
            return _train(args, ports)
        return _explain(trace)
    except Exception as exc:  # noqa: BLE001 —— 注入端口的缺陷不外溢为循环崩溃
        return _failure(ResultEnvelope.dependency_failed(
            f"子协议动作 {name} 执行异常：{exc}"
        ))


# ───────────────────────── configure（05 §5） ─────────────────────────


def _configure(args: Mapping[str, Any], ports: SubprotocolPorts) -> SubprotocolResult:
    target = _text(args, "target")
    if not target:
        return _failure(ResultEnvelope.validation_failed(
            "sk_configure 缺少 target（目标 Skill / 工作流）"
        ))
    values, error = _mapping("values", args.get("values"))
    if error:
        return _failure(ResultEnvelope.validation_failed(error))
    structure, error = _mapping("structure", args.get("structure"))
    if error:
        return _failure(ResultEnvelope.validation_failed(error))
    if ports.configs is None:
        return _failure(_unavailable(
            "未注入配置草稿生成面（归属 T-AGT-030.1），无法产出配置草稿卡"
        ))
    envelope = ports.configs.compose(
        target=target, values=values, structure=structure or None,
    )
    if not isinstance(envelope, ResultEnvelope):
        return _failure(ResultEnvelope.dependency_failed(
            "配置草稿生成面返回非法类型（须为 ResultEnvelope）"
        ))
    if envelope.status != "ok":
        return _failure(envelope)
    described = describe_draft(envelope.data)
    if described.status != "ok":
        # 工作流草稿（WorkflowDraft）没有 chat 卡片形态：交 Studio 画布，卡不在此产出。
        return SubprotocolResult(
            envelope=envelope,
            note=(
                "配置草稿已产出，但该形态没有对话卡片（工作流草稿交 Studio 画布）；"
                f"{described.reason or '未产出 config_draft_card'}"
            ),
        )
    return SubprotocolResult(
        envelope=envelope,
        cards=(described.data,),
        note="配置草稿已产出，等待用户确认（接受仍经配置登记面落值）",
    )


# ───────────────────────── memory_op（05 §9） ─────────────────────────


def _memory_op(args: Mapping[str, Any], ports: SubprotocolPorts) -> SubprotocolResult:
    if ports.adjudicator is None:
        return _failure(_unavailable(
            "未注入冲突裁决面（归属 T-AGT-030.1），无法产出裁决卡"
        ))
    conflict_id = _text(args, "conflict_id")
    envelope = (
        ports.adjudicator.card_for(conflict_id)
        if conflict_id else ports.adjudicator.cards()
    )
    if not isinstance(envelope, ResultEnvelope):
        return _failure(ResultEnvelope.dependency_failed(
            "冲突裁决面返回非法类型（须为 ResultEnvelope）"
        ))
    if envelope.status != "ok":
        return _failure(envelope)
    cards = _as_cards(envelope.data)
    if cards is None:
        return _failure(ResultEnvelope.dependency_failed(
            "冲突裁决面返回的载荷不是裁决卡（须为 ConflictAdjudication 或其一序列）"
        ))
    descriptions: list[Any] = []
    for card in cards:
        described = describe_adjudication(card)
        if described.status != "ok":
            return _failure(described)
        descriptions.append(described.data)
    return SubprotocolResult(
        envelope=envelope,
        cards=tuple(descriptions),
        note=f"待裁决卡 {len(descriptions)} 张（首张随响应交还；裁决仍由用户提交）",
    )


def _as_cards(payload: Any) -> tuple[ConflictAdjudication, ...] | None:
    if isinstance(payload, ConflictAdjudication):
        return (payload,)
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        if all(isinstance(item, ConflictAdjudication) for item in payload):
            return tuple(payload)
    return None


# ───────────────────────── analyze（05 §3.1 / 06 §5） ─────────────────────────


def _analyze(args: Mapping[str, Any], ports: SubprotocolPorts) -> SubprotocolResult:
    topic = _text(args, "topic")
    if not topic:
        return _failure(ResultEnvelope.validation_failed(
            "sk_analyze 缺少 topic（待分析主题）"
        ))
    if ports.deliberations is None:
        return _failure(_unavailable(
            "未注入多视角编排面（归属 T-AGT-030.2），无法触发 L4 分析"
        ))
    mode = _text(args, "mode")
    values = {"topic": topic}
    if mode:
        values["mode"] = mode
    payload = ports.deliberations.analyze(
        _ActionInput(values=values), values=None, now=_now(),
    )
    envelope = getattr(payload, "envelope", None)
    if not isinstance(envelope, ResultEnvelope):
        return _failure(ResultEnvelope.dependency_failed(
            "多视角编排面返回非法结构（须含 envelope: ResultEnvelope）："
            f"{type(payload).__name__}"
        ))
    if envelope.status != "ok":
        return _failure(envelope)
    view = getattr(payload, "view", None)
    if view is None:
        return SubprotocolResult(
            envelope=envelope,
            note="多视角分析已运行，但视图件未装配，未产出分歧图（不伪造一张）",
        )
    described = describe_divergence_map(view)
    if described.status != "ok":
        return _failure(described)
    return SubprotocolResult(
        envelope=envelope,
        cards=(described.data,),
        note="分歧图已产出；各视角分歧并列呈现，不合并、不给统一建议",
    )


# ───────────────────────── train（08 §3） ─────────────────────────


def _train(args: Mapping[str, Any], ports: SubprotocolPorts) -> SubprotocolResult:
    correction = _text(args, "correction")
    feedback_id = _text(args, "feedback_id")
    if not correction and not feedback_id:
        return _failure(ResultEnvelope.validation_failed(
            "sk_train 需要 correction（修正原话）或 feedback_id（带原因的反馈）"
        ))
    if ports.trainings is None:
        return _failure(_unavailable(
            "未注入训练对话面（归属 T-AGT-030.2），无法走 L6 训练协议"
        ))
    target = _text(args, "target")
    values = {}
    if correction:
        values["correction"] = correction
    if feedback_id:
        values["feedback_id"] = feedback_id
    try:
        payload = ports.trainings.start(
            _ActionInput(values=values, target=target), values=None, now=_now(),
        )
    except Exception as exc:  # noqa: BLE001 —— 注入端口的拒绝不外溢为循环崩溃
        return _failure(ResultEnvelope.dependency_failed(
            f"训练对话面异常：{exc}"
        ))
    envelope = getattr(payload, "envelope", None)
    if not isinstance(envelope, ResultEnvelope):
        return _failure(ResultEnvelope.dependency_failed(
            "训练对话面返回非法结构（须含 envelope: ResultEnvelope）："
            f"{type(payload).__name__}"
        ))
    if envelope.status != "ok":
        return _failure(envelope)
    session = getattr(payload, "session", None)
    if session is not None and hasattr(session, "model_dump"):
        shown = session.model_dump(mode="json")
    else:
        shown = envelope.data
    return SubprotocolResult(
        envelope=envelope,
        note=(
            "训练协议已产出未确认会话（不落账；确认与落账仍是下一轮的事）："
            + json_text(shown)
        ),
    )


# ───────────────────────── explain（05 §7） ─────────────────────────


def _explain(trace: Any) -> SubprotocolResult:
    described = describe_trace(trace, now=_now())
    if described.status != "ok":
        return _failure(described)
    return SubprotocolResult(
        envelope=described,
        cards=(described.data,),
        note="本次运行的推理链已展开（只含动作步，不含模型中间推理）",
    )
