"""草稿的处置三态（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

草稿 → **接受**（配置生效并产生 `change_id`）/ **微调**（进参数面板）/
**拒绝**（丢弃并可选记录原因进反馈池）。三态的落点见 §5 的落地口径表：

- **接受**经 :class:`~st_agent.l3.config.registry.ConfigRegistryPort` 的写入端落值
  并产生 ``change_id``——L3 **不自行落** ``config`` 分区（否则绕过登记表，
  双通道会分叉）；未注入登记面即 ``unavailable`` + 点名，**不假装生效**。
- **微调**返回**参数面板视图**（同一条登记项的 ``panel_form_spec`` + 当前值），
  草稿态**不落盘**——与 [03 §4](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
  工作流面「编辑期不落盘」是同一条边界。
- **拒绝**丢弃、不落盘，``reason`` 仅随返回值透传；**反馈池归 L6**
  （[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)），本层不建反馈存储，
  但把归属任务 id 一并带回，使「原因去哪」不靠猜。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.registry_types import ChangeRecord
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.config.draft import ConfigDraft
from st_agent.l3.config.registry import (
    REGISTRY_ABSENT_REASON,
    ChannelParam,
    dual_channel_view,
)

__all__ = [
    "FEEDBACK_POOL_OWNER",
    "ConfigAcceptance",
    "ConfigDraftHandling",
    "DraftRejection",
    "PanelView",
]

FEEDBACK_POOL_OWNER = "T-L6-001"
"""拒绝原因的反馈池归属任务（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class ConfigAcceptance(BaseModel):
    """「接受」的结果（配置生效 + 变更留痕）。"""

    model_config = ConfigDict(frozen=True)

    target: str
    records: tuple[ChangeRecord, ...] = ()
    """逐条变更留痕（01 §7）；``change_id`` 即回滚单位。"""

    @property
    def change_ids(self) -> tuple[str, ...]:
        return tuple(r.change_id for r in self.records)


class PanelView(BaseModel):
    """「微调」的参数面板视图（同一处解析的双通道参数 + 草稿当前值）。"""

    model_config = ConfigDict(frozen=True)

    target: str
    params: tuple[ChannelParam, ...] = ()
    values: dict[str, Any] = {}
    """草稿当前取值（面板据此回显；草稿态**不落盘**）。"""

    def panel_field(self, name: str):
        """取某参数的面板字段（不存在即 ``None``）。"""
        for param in self.params:
            if param.name == name:
                return param.panel_field
        return None


class DraftRejection(BaseModel):
    """「拒绝」的结果（丢弃会话，不落盘）。"""

    model_config = ConfigDict(frozen=True)

    target: str
    rejected: bool = True
    reason: str | None = None
    """可选原因，仅随返回值透传；落库归 :data:`FEEDBACK_POOL_OWNER`。"""
    feedback_owner: str = FEEDBACK_POOL_OWNER


class ConfigDraftHandling:
    """草稿三态的编排面（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param descriptors: 描述体取数口（微调的面板视图按参数枚举时需要）
    :param registry: 登记面端口（:class:`ConfigRegistryPort`）；接受**必须**有它，
        缺省不注入即 fail-closed
    """

    def __init__(self, *, descriptors: Any = None, registry: Any = None) -> None:
        self._descriptors = descriptors
        self._registry = registry

    # ───────────────────────── 接受 ─────────────────────────

    def accept(self, draft: ConfigDraft, *, trace_id: str | None = None) -> ResultEnvelope:
        """接受草稿：经登记面写入端落值 + 产生 `change_id`（§5）。"""
        if self._registry is None:
            return ResultEnvelope.unavailable(
                REGISTRY_ABSENT_REASON, last_updated_at=_now()
            )
        if not draft.parameter_draft:
            return ResultEnvelope.empty(
                "草稿没有可落值的参数，无需接受（05 §5）", as_of=_now()
            )
        try:
            records = tuple(self._registry.apply(
                draft.target, dict(draft.parameter_draft), trace_id=trace_id
            ))
        except Exception as exc:  # 注入端口的实现缺陷 → 显式失败，不静默降级
            return ResultEnvelope.dependency_failed(f"配置登记面落值失败：{exc}")
        if not records:
            return ResultEnvelope.dependency_failed(
                "配置登记面未返回变更留痕（05 §5 要求接受产生 change_id）"
            )
        return ResultEnvelope.ok(
            ConfigAcceptance(target=draft.target, records=records), as_of=_now()
        )

    # ───────────────────────── 微调 ─────────────────────────

    def tune(self, draft: ConfigDraft) -> ResultEnvelope:
        """微调：返回参数面板视图（草稿态不落盘；§5）。"""
        view = dual_channel_view(
            draft.target, descriptors=self._descriptors, registry=self._registry
        )
        if view.status != "ok":
            return view
        return ResultEnvelope.ok(
            PanelView(
                target=draft.target,
                params=view.data.params,
                values=dict(draft.parameter_draft),
            ),
            as_of=_now(),
        )

    # ───────────────────────── 拒绝 ─────────────────────────

    def reject(self, draft: ConfigDraft, *, reason: str | None = None) -> ResultEnvelope:
        """拒绝：丢弃、不落盘，`reason` 仅透传（反馈池归 L6；§5）。"""
        return ResultEnvelope.ok(
            DraftRejection(target=draft.target, reason=reason), as_of=_now()
        )
