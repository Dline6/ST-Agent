"""主动提案落 Studio 草稿接收面（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)
「衔接 [story-06](../../../docs/PRD-v2-Agent/story-06-skill-studio.md)」）。

[`ProposalEngine`](proposal.py) 检出的 :class:`~st_agent.l6.proposal.SkillProposal` 携一份
:class:`~st_agent.l6.proposal.SkillDraft`（形态＝[03 §4](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
的 ``workflow_draft`` 子对象），而落地点是 L1 Skill Studio 的草稿接收面
[`DraftIntake.receive(WorkflowDraft)`](../l1/studio/draft.py)。两端**形态不同**，
故本模块交付两件事：

1. :func:`to_workflow_draft`——**唯一**的 ``SkillDraft`` → ``WorkflowDraft`` 翻译
   （纯函数，不落盘、不 import L6 之外的业务面）。``SkillDraft`` 的字段与
   ``workflow_draft`` 子对象**同形**（[05 §5](../../../docs/技术架构-v2/05-L3-对话主入口.md)），
   故翻译只做**载体切换**：``target`` 取草稿名、``understanding_summary`` 取提案理由
   （缺省回落草稿说明，两者都已在提案检出时过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)，
   本模块**不新造**生成性文案）；**身份字段** `flow_id` / `version` **不入**（由 Studio
   「接受」派生）。
2. :class:`StudioHandoff`——交接受体：按 ``proposal_id`` 取提案、翻译、交
   [`DraftIntake`](../l1/studio/draft.py) 落画布、**持会话**（``proposal_id → DraftSession``），
   三态（接受 / 微调 / 拒绝）**转交** L1，**不复制**其判定、不另造第二套落盘或变更留痕。

**为什么住 L6**：L6 > L1，可向下 import L1（[铁律 7](../../../项目管理/工程宪法.md)）；
先例 [`AgentFamily`](registry_adapter.py)（上层 owner 经组合根注入 L1 门面）。
``SkillDraft`` 本体（[`proposal.py`](proposal.py)）**保持不 import L1**——L1 依赖
只落在本适配器模块。

**会话持在内存**：草稿态**一律不落盘**（[03 §4](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
的编辑期不落盘，「接受」是唯一落盘点），故会话无需持久化；重启丢会话＝丢草稿，
重新交 Studio 即可（幂等）。**无 Studio 画布页**时「微调」只到 :meth:`StudioHandoff.tune`
返回的 ``CanvasEditor`` 句柄——画布渲染是 story-06 的独立表现层任务。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.l1.studio import (
    AcceptResult,
    CanvasEditor,
    DraftIntake,
    DraftSession,
    RejectResult,
    WorkflowDraft,
)
from st_agent.l6.errors import StudioHandoffError
from st_agent.l6.proposal import SkillProposal

__all__ = ["StudioDraftView", "StudioHandoff", "to_workflow_draft"]


class StudioDraftView(BaseModel):
    """一次「交 Studio」的画布视图（表现层取数就绪；**不**即会话本体）。"""

    model_config = ConfigDict(frozen=True)

    proposal_id: str
    """来源提案标识。"""
    base: str
    """由草稿派生的工作流 base（``wf_<注册名>``）——「接受」落盘时用它拼 `flow_id`。"""
    flow_id: str
    """画布期的**临时**标识（``wf_<base>_v0.0``，表「未发布」；接受时换 ``v1.0``）。"""
    node_count: int
    """草稿节点数。"""
    violations: tuple[str, ...] = ()
    """首轮**语义**校验的违规清单（成环 / 契约不匹配 / 命名未中性化 / Skill 未注册 …）——
    语义问题**不阻断**编辑（照开画布），随视图带回供用户看（[03 §4](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。"""


def to_workflow_draft(proposal: SkillProposal) -> WorkflowDraft:
    """一条提案 → L1 的工作流草稿载体（**唯一**的 ``SkillDraft`` → ``WorkflowDraft`` 翻译）。

    ``SkillDraft`` 的字段与 ``workflow_draft`` 子对象同形，故只做载体切换：
    ``nodes`` 恒携，``edges`` / ``groups`` / ``schedule`` / ``flow_name`` **空者不携**
    （不落空键）；身份字段 `flow_id` / `version` **不入**——携即由下游 `DraftIntake` 拒。
    """
    draft = proposal.draft
    payload: dict[str, Any] = {
        "name": draft.name,
        "description": draft.description,
        "nodes": [dict(node) for node in draft.nodes],
    }
    if draft.edges:
        payload["edges"] = [dict(edge) for edge in draft.edges]
    if draft.groups:
        payload["groups"] = [dict(group) for group in draft.groups]
    if draft.schedule is not None:
        payload["schedule"] = dict(draft.schedule)
    if draft.flow_name:
        payload["flow_name"] = draft.flow_name
    return WorkflowDraft(
        target=draft.name,
        parameter_draft={},
        understanding_summary=proposal.reason or draft.description,
        open_questions=(),
        workflow_draft=payload,
    )


class StudioHandoff:
    """主动提案 → Studio 草稿接收面的交接受体（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

    :param intake: L1 的 :class:`~st_agent.l1.studio.DraftIntake`（**经组合根注入**，
        已持 ``store`` + ``SkillRegistry``）；缺省 ``None`` ⇒ :meth:`handoff`
        **fail-closed 并点名**（不假装已交 Studio）
    :param proposals: 提案读面（鸭子类型 ``get(proposal_id) -> SkillProposal | None``，
        如 :class:`~st_agent.l6.proposal.ProposalEngine`）；缺省 ``None`` ⇒ 同样 fail-closed
    """

    def __init__(self, *, intake: Any = None, proposals: Any = None) -> None:
        self._intake = intake
        self._proposals = proposals
        self._sessions: dict[str, DraftSession] = {}

    @property
    def available(self) -> bool:
        """接收面与提案读面**都已注入**（缺一 ⇒ 交 Studio 不可用，表现层据此回 `unavailable`）。"""
        return self._intake is not None and self._proposals is not None

    # ───────────────────────── 交 Studio ─────────────────────────

    def handoff(self, proposal_id: str) -> StudioDraftView:
        """把一条提案的 Skill 草稿交 Studio 落画布（**幂等**）。

        已有**未闭**会话 ⇒ 返回同一视图（不新开、不重置）；已接受 / 已否决 ⇒ 重新开
        会话（接受过则重申会因 base 已存在而由 L1 显式拒）。
        **形状**非法（草稿无法构造 ``WorkflowDAG``）⇒ ``DraftShapeError`` 上升、
        会话**不登记**（画布不开）。
        """
        proposal = self._require_proposal(proposal_id)
        intake = self._require_intake()
        existing = self._sessions.get(proposal_id)
        if existing is not None and existing.status == "editing":
            return self._view(proposal_id, existing)
        session = intake.receive(to_workflow_draft(proposal))
        self._sessions[proposal_id] = session
        return self._view(proposal_id, session)

    def session(self, proposal_id: str) -> DraftSession | None:
        """该提案的画布会话（未交过 ⇒ ``None``）。"""
        return self._sessions.get(proposal_id)

    def opened(self) -> tuple[str, ...]:
        """仍在编辑中的会话所属 ``proposal_id``（升序）——「交过 Studio 但未处置」的面。"""
        return tuple(sorted(
            pid for pid, session in self._sessions.items() if session.status == "editing"
        ))

    # ───────────────────────── 三态（委托 L1） ─────────────────────────

    def accept(self, proposal_id: str, *, trace_id: str | None = None) -> AcceptResult:
        """接受：经 L1 :meth:`DraftIntake.accept` 落 v1.0 并产生 `change_id`（本层不重包）。"""
        return self._require_intake().accept(
            self._require_session(proposal_id), trace_id=trace_id,
        )

    def reject(self, proposal_id: str, *, reason: str | None = None) -> RejectResult:
        """拒绝：经 L1 :meth:`DraftIntake.reject` 丢弃会话、**不落盘**。"""
        return self._require_intake().reject(
            self._require_session(proposal_id), reason=reason,
        )

    def tune(self, proposal_id: str) -> CanvasEditor:
        """微调：返回**同一个** :class:`~st_agent.l1.studio.CanvasEditor` 句柄（会话不分裂）。

        **画布渲染归 story-06 的独立表现层任务**——本模块只交句柄，无 UI。
        """
        return self._require_intake().tune(self._require_session(proposal_id))

    # ───────────────────────── 内部 ─────────────────────────

    def _view(self, proposal_id: str, session: DraftSession) -> StudioDraftView:
        return StudioDraftView(
            proposal_id=proposal_id,
            base=session.base,
            flow_id=session.dag.flow_id,
            node_count=len(session.dag.nodes),
            violations=tuple(str(issue) for issue in session.validation.issues),
        )

    def _require_intake(self) -> DraftIntake:
        if self._intake is None:
            raise StudioHandoffError(
                "未接入 Studio 草稿接收面（DraftIntake），无法交 Studio（08 §4）"
            )
        return self._intake

    def _require_proposal(self, proposal_id: str) -> SkillProposal:
        if self._proposals is None:
            raise StudioHandoffError(
                "未接入提案读面，无法交 Studio（08 §4）"
            )
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise StudioHandoffError(f"提案不存在：{proposal_id!r}（08 §4）")
        return proposal

    def _require_session(self, proposal_id: str) -> DraftSession:
        self._require_intake()
        session = self._sessions.get(proposal_id)
        if session is None:
            raise StudioHandoffError(
                f"该提案尚未交 Studio，无可处置会话：{proposal_id!r}（08 §4）"
            )
        return session
