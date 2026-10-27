"""L1 能力门面：Skill 库、MCP Hub、Studio 工作流与试跑。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillsFacade:
    """L1 能力（Skill）面：持 `SkillRegistry`（工具目录＝描述体投影的唯一来源）。

    `store` 是**执行日志读面**的取数口（[`T-UI-015.1`]）：`SkillRun` 记录落在
    `execution_log` 分区 `skill-run/` 前缀下（[03 §1.2]），属 L1 自己的落盘，表现层不自建
    第二份留痕——故此处只把 `Store` 与注册表并列透出，读面归功能叶。
    """

    registry: Any
    store: Any = None


@dataclass(frozen=True)
class McpFacade:
    """L1 能力（MCP）面：持 Server 注册表 / 权限簿 / Skill 映射 / Hub 状态机。

    `skills` 是**派生 Skill 的描述体取数口**（[`T-UI-015.2`] 的 tool schema 浏览器：映射
    记录只带指纹，逐 tool 的参数面要经它取派生描述体）；`remove_server` 是
    [`L1Runtime.remove_mcp_server`] 的引用——**移除必须走它**（先 `disable` 再
    `remove_server`，触发组合根接的回收回调），表现层不得自行拼那两步（[03 §5.2]）。
    """

    servers: Any
    permissions: Any = None
    mapper: Any = None
    machine: Any = None
    skills: Any = None
    remove_server: Any = None


class TrialSessions:
    """进程内的**试跑会话表**（`trial_id → WorkflowTrial`；[`T-UI-015.3`] 的调试控制台）。

    试跑是**步进式活对象**（[03 §4]：单步 / 暂停 / 继续 / 中止都作用在**节点边界**上），
    `TrialHistory` 只落每次状态变更后的**快照**，故跨 HTTP 请求的续跑只能由进程持有——
    这是「真试跑」与「只读试跑历史」的结构差别，不是实现细节。

    会话是**临时态**：进程重启即丢。已跑过的留痕仍在 `execution_log` 分区
    ``workflow-trial/`` 下（试跑记录不因会话表丢失而消失）。
    """

    def __init__(self) -> None:
        self._items: dict[str, Any] = {}

    def put(self, trial: Any) -> None:
        self._items[trial.trial_id] = trial

    def has(self, trial_id: str) -> bool:
        return trial_id in self._items

    def get(self, trial_id: str) -> Any:
        return self._items[trial_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


@dataclass(frozen=True)
class StudioFacade:
    """L1 Studio 面（[`T-UI-015.3`]）：工作流 / 模板库 / 试跑与**进程内会话表**。

    `open_template` 是「模板 fork → 交既有画布会话」的那一跳（`template_id` → 画布视图）：
    fork 产的是 L1 的 ``WorkflowDAG``，交编辑面要经 L6 的 ``StudioHandoff.open_draft``——
    与主动提案**同一条**落画布路径，不另开旁路（[03 §4] 模板库落地口径：fork 不落盘、
    「接受」才是落盘点）。
    """

    workflows: Any
    templates: Any
    trials: Any
    sessions: TrialSessions
    skills: Any = None
    open_template: Any = None
    home: Any = None
    """Studio 首页状态（`StudioHome`：`is_empty` ＋ 模板清单 ＋ 一句话入口）。

    在**组合根**算一次（`studio_empty_state(store, registry)`）而不在表现层现算——现算会
    再列一遍模板（同一份种子两次求值），且要求表现层同时够到 store 与 registry 两处句柄；
    「空状态判据只有一处」与 [03 §4] 的空状态口径同源。"""
