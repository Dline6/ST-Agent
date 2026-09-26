"""Skill Studio（T-L1-003.4 / .5；03 §4）。

交付双通道的后端编辑面与模板库：

- :mod:`st_agent.l1.studio.canvas` —— ``CanvasEditor`` 内存编辑会话（增删节点 /
  连线断开 / 分组解组），每次写操作返回 ``EditResult``（新 DAG 视图 + 校验结论
  + 人可读提示）；连线即时校验契约与成环，不过则不生效、画布不变
- :mod:`st_agent.l1.studio.draft` —— ``WorkflowDraft``（[05-L3 §5] 四字段 +
  ``workflow_draft`` 子对象）与 ``DraftIntake``（``receive`` / ``tune`` /
  ``accept`` / ``reject``）；「接受」落 ``config`` 分区 ``workflow/<flow_id>.json``
  并留痕 ``execution_log`` 分区 ``workflow-change/<change_id>.json``
- :mod:`st_agent.l1.studio.templates` —— ``OFFICIAL_TEMPLATES`` 纯数据种子与
  ``TemplateLibrary``（``list`` / ``get`` / ``load`` / ``fork``）；fork 产用户
  自有副本、**不落盘**（交编辑面，「接受」才是落盘点）
- :mod:`st_agent.l1.studio.empty_state` —— ``StudioHome`` / ``studio_empty_state``
  （模板库清单 + 一句话入口 + ``is_empty``）

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
from st_agent.l1.studio.empty_state import (
    STUDIO_ONE_LINE_ENTRY,
    StudioHome,
    studio_empty_state,
)
from st_agent.l1.studio.errors import (
    DraftAcceptError,
    DraftSessionError,
    DraftShapeError,
    StudioError,
    TemplateError,
)
from st_agent.l1.studio.ids import SLUG_PATTERN, slugify, workflow_base
from st_agent.l1.studio.templates import (
    OFFICIAL_TEMPLATES,
    TemplateLibrary,
    TemplateSummary,
)

__all__ = [
    "OFFICIAL_TEMPLATES",
    "SLUG_PATTERN",
    "STUDIO_ONE_LINE_ENTRY",
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
    "StudioHome",
    "TemplateError",
    "TemplateLibrary",
    "TemplateSummary",
    "WorkflowDraft",
    "slugify",
    "studio_empty_state",
    "workflow_base",
]
