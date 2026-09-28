"""工作流草稿的产出与 Studio 交接（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 末段 + [03 §4](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。

``target`` 为工作流时，草稿在四字段之外**另携** ``workflow_draft`` 子对象
（``name`` / ``description`` / ``nodes`` / ``edges`` / ``groups`` / ``schedule``，
可含 ASCII ``flow_name``）承载结构与暂定名；**身份字段 `flow_id` / `version`
不在草稿中**——由 Studio 侧「接受」派生。

**载体复用 L1 的模型**：[`WorkflowDraft`](../../../l1/studio/draft.py)（[`T-L1-003.4`](../../../项目管理/tasks/done/M0/T-L1-003.4-编辑面与草稿落画布.md)
交付，形状已对齐 [05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)），**不另造平行模型**
——单一真相源，避免两处形状漂移。该任务的假设 A3 即「L3 开工时若需调整须回改」。

**三态委托 L1**：接受 / 微调 / 拒绝由 L1 的 [`DraftIntake`](../../../l1/studio/draft.py)
承载（[03 §4](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 编辑面落地口径表：
接受落 v1.0 并产生 `change_id`、微调返回同一个 `CanvasEditor` 句柄、拒绝不落盘）。
本模块**不复制**其判定、不另造第二套工作流落盘或变更留痕。

**分支判定只有一处**：:func:`is_workflow_draft` 即 [`.1`](draft.py) 的
:func:`~st_agent.l3.config.draft.has_workflow_structure` 的**同一份**实现
（判据＝调用方随派发提供了结构载荷），此处只做再导出。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.studio import DraftIntake, DraftSession, WorkflowDraft
from st_agent.l3.config.draft import (
    has_workflow_structure,
    open_questions,
    summarise,
)
from st_agent.l3.errors import ConfigValidationError
from st_agent.l3.intent.protocol import IntentConfirmation

__all__ = [
    "IDENTITY_FIELDS",
    "WORKFLOW_DRAFT_ABSENT_REASON",
    "WorkflowDraftBuilder",
    "handoff",
    "is_workflow_draft",
]

IDENTITY_FIELDS: tuple[str, ...] = ("flow_id", "version")
"""工作流草稿**不得**携带的身份字段（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。"""

WORKFLOW_DRAFT_ABSENT_REASON = (
    "该草稿不是工作流草稿（未携 workflow_draft 子对象），不进工作流通道（05 §5）"
)

is_workflow_draft = has_workflow_structure
"""工作流分支的**唯一**判据（[`.1`](draft.py) 的同一份实现）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class WorkflowDraftBuilder:
    """工作流草稿的产出面（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 末段）。

    :param descriptors: 描述体取数口（未澄清项的候选源）；缺省 ``None`` 时不产
        未澄清项（不臆测）
    :param guard: 中性规则库守卫（缺省用默认规则库；**无**关闭开关）
    """

    def __init__(self, *, descriptors: Any = None, guard: Any = None) -> None:
        self._descriptors = descriptors
        self._guard = guard

    def build(
        self,
        confirmation: IntentConfirmation,
        structure: Mapping[str, Any],
    ) -> ResultEnvelope:
        """已确认的确认卡 + 结构载荷 → 工作流草稿（载荷即 :class:`WorkflowDraft`）。

        **形状非法即显式拒**：身份字段出现在结构载荷中、或载体构造失败，一律
        ``validation_failed``——**不静默丢弃**违规字段（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。
        """
        if not confirmation.confirmed:
            return ResultEnvelope.validation_failed(
                "意图确认卡未经用户确认，不得生成配置草稿（05 §5）"
            )
        target = (confirmation.target or "").strip()
        if not target:
            return ResultEnvelope.validation_failed(
                "配置草稿缺少目标工作流，无法生成（05 §5）"
            )
        fields = dict(structure)
        forbidden = tuple(sorted(set(fields) & set(IDENTITY_FIELDS)))
        if forbidden:
            return ResultEnvelope.validation_failed(
                f"工作流草稿不得携带身份字段 {'、'.join(forbidden)}"
                "——身份由 Studio 侧「接受」派生（05 §5）"
            )
        try:
            questions = open_questions(
                confirmation, self._specs(target), guard=self._guard
            )
        except ConfigValidationError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        try:
            draft = WorkflowDraft(
                target=target,
                parameter_draft=dict(confirmation.values),
                understanding_summary=summarise(confirmation),
                open_questions=tuple(q.param for q in questions),
                workflow_draft=fields,
            )
        except ValidationError as exc:
            return ResultEnvelope.validation_failed(f"工作流草稿非法：{exc}")
        return ResultEnvelope.ok(draft, as_of=_now())

    def _specs(self, target: str) -> tuple[Any, ...]:
        if self._descriptors is None:
            return ()
        try:
            descriptor = self._descriptors.get(target)
        except Exception:
            return ()
        return tuple(getattr(descriptor, "parameters", ()) or ())


def handoff(intake: DraftIntake, draft: WorkflowDraft) -> DraftSession:
    """把工作流草稿交 Studio 接收面（[03 §4](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。

    形状非法（无法构造 ``WorkflowDAG``）→ ``DraftShapeError`` **显式上升**、画布不开；
    语义不合规 → 画布**照开**，违规清单随会话带回。两条口径均在 L1 侧判定，
    本函数只做转交、不改判。
    """
    if getattr(draft, "workflow_draft", None) is None:
        raise ConfigValidationError(WORKFLOW_DRAFT_ABSENT_REASON)
    return intake.receive(draft)
