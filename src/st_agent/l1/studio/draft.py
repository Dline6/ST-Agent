"""草稿接收面（T-L1-003.4；03 §4 双通道的对话通道接收端）。

L3（``T-L3-003``）产出「工作流草稿对象」——[05-L3 §5] 四字段（``target`` /
``parameter_draft`` / ``understanding_summary`` / ``open_questions``）**另携**
``workflow_draft`` 子对象承载结构（``name`` / ``description`` / ``nodes`` /
``edges`` / ``groups`` / ``schedule``，可含 ASCII ``flow_name``）。身份字段
``flow_id`` / ``version`` **不在草稿中**，由本模块派生（03 §4 编辑面落地口径）。

落地口径（[03 §4] 编辑面落地口径表）：

- **非法草稿边界**：**形状**非法（无法构造 ``WorkflowDAG``——缺必填字段 /
  非法 ``node_id`` / 无法派生注册名 / 违规携带身份字段 / 含无法识别的字段）→
  ``DraftShapeError``、**画布不开**；**语义**不合规（成环 / 契约不匹配 / 命名
  未中性化 / Skill 未注册 / 参数未声明）→ **照开画布**，违规清单随
  ``DraftSession.validation`` 带回供用户看
- **接受**：base 取 ``flow_name``（缺省时由 ``name`` slug 化；slug 为空则拒）→
  ``flow_id = wf_<base>_v1.0``，经 ``WorkflowStore.save`` 落 ``config`` 分区
  ``workflow/<flow_id>.json``；base 已存在 → **拒**（改既有工作流走
  ``WorkflowStore.publish_version``，不属本流程）
- **变更留痕**：接受产生一条 ``ChangeRecord``（01 §7）并落 ``execution_log``
  分区 ``workflow-change/<change_id>.json``——与 §5.3 的 ``mcp-hub-change/``
  同构（同分区不同前缀），``change_id`` 即回滚单位
- **微调 / 拒绝**：微调返回**同一个** ``CanvasEditor`` 句柄（会话不分裂）；
  拒绝丢弃会话、**不落盘**，``reason`` 仅随返回值透传（反馈采集归 L6）

草稿态与落盘态的边界只有一条：编辑期一律不落盘，「接受」是唯一落盘点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.identifiers import ChangeId
from st_agent.contracts.registry_types import ChangeRecord, SemVer
from st_agent.l1.studio.canvas import CanvasEditor
from st_agent.l1.studio.errors import (
    DraftAcceptError,
    DraftSessionError,
    DraftShapeError,
)
from st_agent.l1.studio.ids import workflow_base
from st_agent.l1.workflow.errors import WorkflowValidationError
from st_agent.l1.workflow.ids import flow_id_for
from st_agent.l1.workflow.models import WorkflowDAG, checked_dag
from st_agent.l1.workflow.store import WorkflowStore
from st_agent.l1.workflow.validate import WorkflowValidation

__all__ = [
    "WORKFLOW_CHANGE_PREFIX",
    "AcceptResult",
    "DraftIntake",
    "DraftSession",
    "DraftStatus",
    "RejectResult",
    "WorkflowDraft",
]

WORKFLOW_CHANGE_PREFIX = "workflow-change/"
"""``execution_log`` 分区内工作流变更留痕的目录前缀（与 ``workflow-trial/`` 同分区
不同前缀，互不串扫）。"""

DraftStatus = Literal["editing", "accepted", "rejected"]

_FIRST_VERSION = SemVer(major=1, minor=0)
"""「接受」落盘的首版版本（03 §4：落盘为 v1.0）。"""

_PROVISIONAL_VERSION = SemVer(major=0, minor=0)
"""画布期的**临时**身份版本——草稿尚未发布，``0.0`` 表「未发布」；「接受」时
被替换为首版 ``1.0``。"""

_DAG_FIELDS: tuple[str, ...] = ("name", "description", "nodes", "edges",
                                "groups", "schedule")
"""草稿允许携带的 ``WorkflowDAG`` 字段（其余为身份字段或无法识别字段）。"""

_IDENTITY_FIELDS: tuple[str, ...] = ("flow_id", "version")
"""身份字段——不在草稿中，由「接受」派生；草稿携带即拒。"""

_FLOW_NAME_KEY = "flow_name"
"""草稿可选携带的 ASCII 注册名（缺省时由 ``name`` slug 化派生）。"""


class WorkflowDraft(BaseModel):
    """L3 对话通道产出的工作流草稿（[05-L3 §5] 四字段 + ``workflow_draft``）。"""

    model_config = ConfigDict(frozen=True)

    target: Annotated[str, Field(min_length=1)]
    """目标 Skill / 工作流（[05-L3 §5]）。"""
    parameter_draft: dict[str, Any] = Field(default_factory=dict)
    """从对话提取的参数值（对齐 ``SkillDescriptor.parameters``）。"""
    understanding_summary: Annotated[str, Field(min_length=1)]
    """结构化理解摘要（「我理解到 N 件事」的内容源）。"""
    open_questions: tuple[str, ...] = ()
    """未澄清参数（供追问或标注默认值来源）。"""
    workflow_draft: dict[str, Any] | None = None
    """工作流专用子对象（``name`` / ``description`` / ``nodes`` / ``edges`` /
    ``groups`` / ``schedule``，可含 ASCII ``flow_name``）；``None`` 即非工作流草稿。"""


class AcceptResult(BaseModel):
    """「接受」的结果（落盘 v1.0 + 变更留痕）。"""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    dag: WorkflowDAG
    """落盘后的工作流定义（身份已换为首版 ``v1.0``）。"""
    change: ChangeRecord
    """01 §7 变更留痕（``change_id`` 即回滚单位）。"""

    @property
    def change_id(self) -> str:
        return self.change.change_id


class RejectResult(BaseModel):
    """「拒绝」的结果（丢弃会话，不落盘）。"""

    model_config = ConfigDict(frozen=True)

    rejected: bool = True
    reason: str | None = None
    """可选原因，仅随返回值透传（反馈采集归 L6）。"""


class DraftSession:
    """一次草稿的编辑会话（``DraftIntake.receive`` 创建）。"""

    def __init__(self, draft: WorkflowDraft, editor: CanvasEditor,
                 validation: WorkflowValidation, base: str) -> None:
        self._draft = draft
        self._editor = editor
        self._validation = validation
        self._base = base
        self._status: DraftStatus = "editing"

    @property
    def draft(self) -> WorkflowDraft:
        """来源草稿对象（不可变）。"""
        return self._draft

    @property
    def editor(self) -> CanvasEditor:
        """编辑面句柄（微调用；增删节点 / 连线 / 分组都经它）。"""
        return self._editor

    @property
    def dag(self) -> WorkflowDAG:
        """当前画布状态（随编辑变化）。"""
        return self._editor.dag

    @property
    def validation(self) -> WorkflowValidation:
        """落画布时的**首轮**校验结论（违规清单供用户看；语义问题不阻断编辑）。"""
        return self._validation

    @property
    def base(self) -> str:
        """由草稿派生的工作流 base（``wf_<注册名>``）——「接受」落盘时用它拼 flow_id。"""
        return self._base

    @property
    def status(self) -> DraftStatus:
        return self._status

    def _close(self, status: DraftStatus) -> None:
        self._status = status


class DraftIntake:
    """Studio 侧草稿接收面（L3 对话通道 → 画布 → 接受 / 微调 / 拒绝）。"""

    def __init__(self, store, registry, *, name_check=None, clock=None) -> None:
        """
        :param store: L0 ``Store``（草稿态不落盘；「接受」才写 ``config`` 与
            ``execution_log`` 两个分区）
        :param registry: ``SkillRegistry``（供画布与保存期校验核 Skill 存在性与契约）
        :param name_check: 命名校验回调（缺省接 01 §6 ``NeutralityGuard``）
        :param clock: 取当前时刻的回调（缺省用户本地时区；测试可注入固定时钟）
        """
        self._store = store
        self._registry = registry
        self._name_ok = name_check
        self._clock = clock or _now
        self._workflows = WorkflowStore(store, registry,
                                        name_neutrality_check=name_check)

    # ───────────────────────── 落画布 ─────────────────────────

    def receive(self, draft: WorkflowDraft) -> DraftSession:
        """接收工作流草稿 → 落画布（形状非法即拒、画布不开）。"""
        payload = draft.workflow_draft
        if not payload:
            raise DraftShapeError(
                f"草稿未携 workflow_draft 子对象（target={draft.target!r}），"
                "无法落到画布")
        stowaways = [k for k in _IDENTITY_FIELDS if k in payload]
        if stowaways:
            raise DraftShapeError(
                f"草稿不得携带身份字段（{'、'.join(stowaways)}）——"
                "flow_id / version 由「接受」派生（03 §4）")
        unknown = sorted(set(payload) - set(_DAG_FIELDS) - {_FLOW_NAME_KEY})
        if unknown:
            raise DraftShapeError(
                f"草稿含无法识别的字段：{'、'.join(unknown)}"
                f"（允许：{'、'.join((*_DAG_FIELDS, _FLOW_NAME_KEY))}）")
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise DraftShapeError("草稿缺展示名 name（03 §4 工作流草稿子对象）")
        try:
            base = workflow_base(name, payload.get(_FLOW_NAME_KEY))
        except DraftAcceptError as exc:
            raise DraftShapeError(f"草稿无法派生注册名：{exc}") from exc

        fields = {k: payload[k] for k in _DAG_FIELDS if k in payload}
        fields["flow_id"] = flow_id_for(base, _PROVISIONAL_VERSION)
        fields["version"] = _PROVISIONAL_VERSION
        try:
            dag = checked_dag(**fields)
        except WorkflowValidationError as exc:
            raise DraftShapeError(f"草稿结构非法（画布不开）：{exc}") from exc

        editor = CanvasEditor(dag, self._registry, name_check=self._name_ok)
        return DraftSession(draft, editor, editor.validate_current(), base)

    # ───────────────────────── 三出口 ─────────────────────────

    def tune(self, session: DraftSession) -> CanvasEditor:
        """微调：返回**可继续编辑的编辑面句柄**（同一个会话，不分裂）。"""
        self._require_editing(session, "微调")
        return session.editor

    def accept(self, session: DraftSession, *, trace_id: str | None = None
               ) -> AcceptResult:
        """接受：落盘为首版 ``v1.0`` 并产生 ``change_id``。"""
        self._require_editing(session, "接受")
        base = session.base
        if self._workflows.get_latest(base) is not None:
            raise DraftAcceptError(
                f"工作流 base {base!r} 已存在——改既有工作流请走 "
                "WorkflowStore.publish_version；新建请另取名"
                "（或由调用方提供不同的 flow_name）")
        final = checked_dag(**{
            **session.dag.model_dump(),
            "flow_id": flow_id_for(base, _FIRST_VERSION),
            "version": _FIRST_VERSION,
        })
        try:
            saved = self._workflows.save(final)
        except WorkflowValidationError as exc:
            raise DraftAcceptError(f"工作流校验未通过，未落盘：{exc}") from exc
        change = ChangeRecord(
            change_id=ChangeId.generate().value,
            config_id=saved.flow_id,
            old_value=None,
            new_value=saved.flow_id,
            applied_at=self._clock().isoformat(),
            trace_ref=trace_id,
        )
        self._store.put(
            "execution_log", f"{WORKFLOW_CHANGE_PREFIX}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"))
        session._close("accepted")
        return AcceptResult(flow_id=saved.flow_id, dag=saved, change=change)

    def reject(self, session: DraftSession, *, reason: str | None = None
               ) -> RejectResult:
        """拒绝：丢弃会话、**不落盘**；``reason`` 仅随返回值透传。"""
        self._require_editing(session, "拒绝")
        session._close("rejected")
        return RejectResult(reason=reason)

    # ───────────────────────── 内部 ─────────────────────────

    @staticmethod
    def _require_editing(session: DraftSession, act: str) -> None:
        if session.status != "editing":
            raise DraftSessionError(
                f"会话已处于 {session.status}，不可再{act}")


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()
