"""Skill Studio 编辑面（T-L1-003.4；03 §4）。

交付双通道的后端编辑面：

- :mod:`st_agent.l1.studio.canvas` —— ``CanvasEditor`` 内存编辑会话（增删节点 /
  连线断开 / 分组解组），每次写操作返回 ``EditResult``（新 DAG 视图 + 校验结论
  + 人可读提示）；连线即时校验契约与成环，不过则不生效、画布不变
- :mod:`st_agent.l1.studio.draft` —— ``WorkflowDraft``（[05-L3 §5] 四字段 +
  ``workflow_draft`` 子对象）与 ``DraftIntake``（``receive`` / ``tune`` /
  ``accept`` / ``reject``）；「接受」落 ``config`` 分区 ``workflow/<flow_id>.json``
  并留痕 ``execution_log`` 分区 ``workflow-change/<change_id>.json``

草稿态一律不落盘——「接受」是唯一落盘点。画布渲染与交互 UI 归 L3（仓库无前端）。
上层入口一律 ``from st_agent.l1.studio import ...``。
"""

from st_agent.l1.studio.canvas import CanvasEditor, EditResult
from st_agent.l1.studio.draft import (
    WORKFLOW_CHANGE_PREFIX,
    AcceptResult,
    DraftIntake,
    DraftSession,
    DraftStatus,
    RejectResult,
    WorkflowDraft,
)
from st_agent.l1.studio.errors import (
    DraftAcceptError,
    DraftSessionError,
    DraftShapeError,
    StudioError,
)
from st_agent.l1.studio.ids import SLUG_PATTERN, slugify, workflow_base

__all__ = [
    "SLUG_PATTERN",
    "WORKFLOW_CHANGE_PREFIX",
    "AcceptResult",
    "CanvasEditor",
    "DraftAcceptError",
    "DraftIntake",
    "DraftSession",
    "DraftSessionError",
    "DraftShapeError",
    "DraftStatus",
    "EditResult",
    "RejectResult",
    "StudioError",
    "WorkflowDraft",
    "slugify",
    "workflow_base",
]
