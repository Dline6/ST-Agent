"""能力安装 / 导入审批面（[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)；[03 §5.1](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) / [09 §3](../../../../docs/技术架构-v2/09-生态与分享.md)）。

**这是 [L1 册 D2](../../../../项目管理/遗留问题/L1-遗留问题.md) 的写入入口承接方**（[D-064](../../../../项目管理/决策日志.md)）：
`SkillPermissionBook.declare / approve / reject` 早已是公开 API，但**无人调用**——本模块即那个调用方。

**双通道**（[铁律 3](../../../../项目管理/工程宪法.md)；口径同 [05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的 `dual_channel_view`）：
每条权限同时给出 `chat_text`（对话通道：中性措辞 + 当前状态）与 `panel_field`（面板通道：逐项批准控件）——
一个组件类型的两面，不各造一套记录。

**逐条、不合并**（[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)「这个能力想做什么」）：
每条声明单列，措辞取自 [`contracts.describe_permission`](../../contracts/permissions.py)（中性、单一口径），
**不**压成一句「该能力需要若干权限」。

**落账经注入的批准账本**（鸭子端口，同 [`l3.config.handling`](../config/handling.py) 的登记面端口）：
L3 **不自行落** `config/skill-permissions/`——批准事实归 L1 的账本，本模块只接线：

- `declare`（挂载 / 导入时登记声明，初态 ``pending``）
- `approve`（用户逐项批准）/ `reject`（逐项拒绝，**原因仅随返回值透传**，反馈池归
  [L6 `T-L6-001`](../../../../docs/技术架构-v2/08-L6-反思演进.md)）

**未注入账本即 ``unavailable`` + 点名，不假装已批准**（GWT-4）：批准态无从落账，
`fail-closed`（[03 §1.5](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）由下层沙箱保持——
「声明 ≠ 批准」的口径不因本面缺席而破。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.permissions import DECISION_LABELS, describe_permission
from st_agent.contracts.result_envelope import ResultEnvelope

__all__ = [
    "APPROVAL_ABSENT_REASON",
    "FEEDBACK_POOL_OWNER",
    "SOURCE_LABELS",
    "ApprovalItem",
    "ApprovalItemState",
    "ApprovalRequest",
    "ApprovalSubmission",
    "CapabilityApprovalPanel",
    "CapabilityApprovalView",
]

FEEDBACK_POOL_OWNER = "T-L6-001"
"""拒绝原因的反馈池归属任务（[08 §1](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。"""

APPROVAL_ABSENT_REASON = (
    "未注入权限批准账本，逐项批准无处落账——不假装已批准（01 §10；fail-closed 由 L1 沙箱保持）"
)
"""账本缺席时的显式原因（GWT-4：点名、不静默）。"""

SOURCE_LABELS: dict[str, str] = {
    "mcp_server": "MCP Server",
    "skill": "Skill",
    "imported": "导入物",
}
"""能力来源的中性标签（面板抬头「这个能力想做什么」的来源面）。"""

ApprovalItemState = Literal["pending", "approved", "rejected"]
"""一条权限的批准态（[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md) 三态；与 `contracts.PermissionDecision` 同取值）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class ApprovalItem(BaseModel):
    """一条权限声明的双通道审批项（01 §10 逐项）。"""

    model_config = ConfigDict(frozen=True)

    permission: str = Field(min_length=1)
    """权限声明原文（`local_read:<scope>` 形态）。"""

    description: str
    """中性措辞（`describe_permission` 的产物，即「这个能力想做什么」）。"""

    state: ApprovalItemState
    """当前批准态（未经批准恒为 `pending`——不预设已批准）。"""

    decided_at: datetime | None = None
    """批准 / 拒绝时刻（`pending` 时为 ``None``，逐项留痕）。"""

    chat_text: str
    """对话通道：措辞 + 状态标签（「… ｜ 待批准」）。"""

    panel_field: dict[str, Any]
    """面板通道：逐项批准控件（`{name, widget: "approval_toggle", options, value}`）。"""


class CapabilityApprovalView(BaseModel):
    """一台能力的权限审批面视图（逐条、不合并）。"""

    model_config = ConfigDict(frozen=True)

    key: str
    """账本键（Skill 取 base、MCP 取 server_id）。"""

    source: str
    """来源标识（`SOURCE_LABELS` 的键）。"""

    source_label: str
    """来源中性标签（面板抬头）。"""

    items: tuple[ApprovalItem, ...] = ()

    @property
    def pending(self) -> tuple[str, ...]:
        """待批准的权限声明（批准面据此知道还有几条要用户表态）。"""
        return tuple(i.permission for i in self.items if i.state == "pending")

    @property
    def approved(self) -> tuple[str, ...]:
        """已批准的权限声明（即沙箱 `fail-closed` 认的那一集）。"""
        return tuple(i.permission for i in self.items if i.state == "approved")


class ApprovalRequest(BaseModel):
    """打开审批面的入参：一台已声明权限的能力。

    声明清单（`permissions`）来自能力的描述体（Skill 的 `SkillDescriptor.permissions`
    或 MCP 的 `ServerRecord.permissions`）——本模块**不臆测**权限，只如实呈现传入的声明。
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    source: Literal["mcp_server", "skill", "imported"] = "skill"
    permissions: tuple[str, ...] = ()
    """该能力声明的权限（挂载 / 导入时即有；空元组 = 无权限声明）。"""


class ApprovalSubmission(BaseModel):
    """一次「提交全部决定」的结果（批准集 / 拒绝集 + 逐条原因透传）。"""

    model_config = ConfigDict(frozen=True)

    key: str
    approved: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    reject_reasons: dict[str, str] = {}
    """拒绝原因（仅随返回值透传；落库归 :data:`FEEDBACK_POOL_OWNER`）。"""
    feedback_owner: str = FEEDBACK_POOL_OWNER


class CapabilityApprovalPanel:
    """审批面门面：逐条展示 + 逐项批准 / 拒绝，落账经注入的批准账本。

    :param book: 权限批准账本端口（鸭子类型：`declare` / `approve` / `reject` /
        `approvals`）——即 [`SkillPermissionBook`](../../l1/skills/permissions.py) 或
        [`McpPermissionBook`](../../l1/mcp/permissions.py)。缺省不注入即 `fail-closed`：
        `unavailable` + 点名（GWT-4），**不假装已批准**。
    """

    def __init__(self, *, book: Any = None) -> None:
        self._book = book

    # ───────────────────────── 展示（先登记声明） ─────────────────────────

    def open(self, request: ApprovalRequest) -> ResultEnvelope:
        """打开审批面：挂载 / 导入时 `declare` 声明，逐条列出（GWT-1）。

        `declare` 的语义保「声明不变则既有决定不丢」（[D-042](../../../../项目管理/决策日志.md)），
        故重复打开同一能力不会把已批准的打回待批；新声明转 `pending`，已移除的丢弃。
        """
        if self._book is None:
            return ResultEnvelope.unavailable(APPROVAL_ABSENT_REASON, last_updated_at=_now())
        try:
            self._book.declare(request.key, request.permissions)
        except Exception as exc:  # 账本侧拒绝（键形态 / 记录损坏）→ 显式失败，不降级
            return ResultEnvelope.dependency_failed(f"权限声明登记失败：{exc}")
        return self._view(request)

    def view(self, request: ApprovalRequest) -> ResultEnvelope:
        """只读展示（不改动账本）。声明已由上游 `declare` 过时用此口。"""
        if self._book is None:
            return ResultEnvelope.unavailable(APPROVAL_ABSENT_REASON, last_updated_at=_now())
        return self._view(request)

    # ───────────────────────── 逐项批准 / 拒绝 ─────────────────────────

    def approve(self, request: ApprovalRequest, permission: str) -> ResultEnvelope:
        """批准一条**已声明**的权限（未声明 → 账本抛错，透传为 `dependency_failed`）。"""
        if self._book is None:
            return ResultEnvelope.unavailable(APPROVAL_ABSENT_REASON, last_updated_at=_now())
        try:
            self._book.approve(request.key, permission)
        except Exception as exc:
            return ResultEnvelope.dependency_failed(f"批准落账失败：{exc}")
        return self._view(request)

    def reject(self, request: ApprovalRequest, permission: str) -> ResultEnvelope:
        """拒绝一条已声明的权限（拒绝后不进入已批准集合；GWT-3）。

        原因透传走 `submit`（逐项拒绝本身只落 `rejected` 态，原因随提交返回，
        反馈池归 [L6](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。
        """
        if self._book is None:
            return ResultEnvelope.unavailable(APPROVAL_ABSENT_REASON, last_updated_at=_now())
        try:
            self._book.reject(request.key, permission)
        except Exception as exc:
            return ResultEnvelope.dependency_failed(f"拒绝落账失败：{exc}")
        return self._view(request)

    def submit(
        self, request: ApprovalRequest, *, reasons: dict[str, str] | None = None
    ) -> ResultEnvelope:
        """汇总当前账本的决定为一次提交结果（批准集 / 拒绝集 + 逐条原因透传）。

        本方法**不再改动账本**——批准 / 拒绝在逐项时已落账；提交只回显终态并把
        拒绝原因随返回值带走（不落 L3，反馈池归 :data:`FEEDBACK_POOL_OWNER`）。
        """
        if self._book is None:
            return ResultEnvelope.unavailable(APPROVAL_ABSENT_REASON, last_updated_at=_now())
        view = self._view(request).data
        return ResultEnvelope.ok(
            ApprovalSubmission(
                key=request.key,
                approved=view.approved,
                rejected=tuple(i.permission for i in view.items if i.state == "rejected"),
                reject_reasons=dict(reasons or {}),
            ),
            as_of=_now(),
        )

    # ───────────────────────── 内部：账本 → 双通道视图 ─────────────────────────

    def _view(self, request: ApprovalRequest) -> ResultEnvelope:
        try:
            approvals = tuple(self._book.approvals(request.key))
        except Exception as exc:
            return ResultEnvelope.dependency_failed(f"批准账本读取失败：{exc}")
        # 逐条、按声明顺序；措辞取 describe_permission（中性、单一口径），不合并成一句话
        items: list[ApprovalItem] = []
        for a in approvals:
            try:
                description = describe_permission(a.permission)
            except Exception as exc:  # 声明损坏（注册期应已校验）→ 单条显式标注，不吞整面
                description = f"{a.permission} —— 措辞解析失败（{exc}）"
            items.append(
                ApprovalItem(
                    permission=a.permission,
                    description=description,
                    state=a.decision,  # type: ignore[arg-type]
                    decided_at=a.decided_at,
                    chat_text=f"{description}｜{DECISION_LABELS[a.decision]}",
                    panel_field=_panel_field(a.permission, a.decision),
                )
            )
        return ResultEnvelope.ok(
            CapabilityApprovalView(
                key=request.key,
                source=request.source,
                source_label=SOURCE_LABELS.get(request.source, request.source),
                items=tuple(items),
            ),
            as_of=_now(),
        )


def _panel_field(permission: str, decision: str) -> dict[str, Any]:
    """面板通道的逐项批准控件（三态开关：批准 / 拒绝 / 待批准）。

    只给**形态**，不代用户表态：`value` 即当前态，控件把三态并列供逐项选择。
    """
    return {
        "name": permission,
        "widget": "approval_toggle",
        "options": ["approved", "rejected", "pending"],
        "value": decision,
    }
