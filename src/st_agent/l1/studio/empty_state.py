"""Studio 首页状态（T-L1-003.5；03 §4 空状态）。

story-06 边缘情况：「新用户未创建任何工作流 → 显示模板库 + 『用一句话描述你
想要的工作流』引导入口」。

落地口径（[03 §4] 模板库落地口径表）：

- **判据**：``WorkflowStore.list_all()`` 为空（**未创建任何工作流**），不是
  「未打开过 Studio」——与 story-06 字面一致；由 ``StudioHome.is_empty`` 承载
- **恒返回**：模板清单**恒返回**（非空时亦然，供「新建」入口复用同一数据源）；
  不外抛异常、不返回空对象
- **入口文案**：``one_line_entry`` 逐字取自 story-06 的引导语，供 L3 渲染
  输入框占位与提交目标
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from st_agent.l1.studio.templates import TemplateLibrary, TemplateSummary
from st_agent.l1.workflow.store import WorkflowStore

__all__ = [
    "STUDIO_ONE_LINE_ENTRY",
    "StudioHome",
    "studio_empty_state",
]

STUDIO_ONE_LINE_ENTRY = "用一句话描述你想要的工作流"
"""一句话入口的引导文案（story-06 空状态原文）。"""


class StudioHome(BaseModel):
    """Studio 首页状态（template 清单 + 一句话入口 + 是否为空）。"""

    model_config = ConfigDict(frozen=True)

    is_empty: bool
    """用户是否**尚未创建任何工作流**（``WorkflowStore.list_all()`` 为空）。"""
    templates: tuple[TemplateSummary, ...]
    """官方模板库清单（恒返回，不因 ``is_empty`` 为假而缺省）。"""
    one_line_entry: str
    """一句话入口的引导文案。"""


def studio_empty_state(store, registry) -> StudioHome:
    """查询 Studio 首页状态（空状态 = 模板库 + 一句话入口）。

    :param store: ``T-L0-001`` 的 ``Store``（判空 + 传给模板库做 fork 去重）
    :param registry: ``SkillRegistry``（模板依赖提示走 §3.2 同一判定件）
    """
    library = TemplateLibrary(store, registry)
    return StudioHome(
        is_empty=not WorkflowStore(store, registry).list_all(),
        templates=library.list(),
        one_line_entry=STUDIO_ONE_LINE_ENTRY,
    )
