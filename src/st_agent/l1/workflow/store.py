"""Workflow 持久化与版本（T-L1-003.1；03 §3.1）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 工作流定义 → ``config`` 分区 ``workflow/<flow_id>.json``（整文件加密落盘）
- 激活指针 → ``config`` 分区 ``workflow-active/<base>.json``（当前生效版本）

版本语义（与 ``SkillRegistry`` 同构，01 §9）：
- 同 base 多版本**共存**（各一文件，老版本保留），``get_latest`` 取最高版本
- ``active`` / ``rollback`` 用**激活指针**表达「当前生效版本」——版本文件本身
  不可变（§1「生成后不可变」），回滚是换指针而非改内容，可再换回去
- 引用方「待检查」是**派生结论**：某节点引用的 Skill base 有主版本待检查且该
  节点版本落后于 ``to_version`` 即标；``follow_latest`` 跟上后自动消失
  （不另存一份标记，避免与 ``SkillRegistry`` 的两处真相漂移）
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import ChangeId
from st_agent.contracts.registry_types import ChangeRecord, SemVer
from st_agent.l1.skills.ids import base_of as skill_base_of
from st_agent.l1.skills.ids import parse_skill_id
from st_agent.l1.workflow.errors import (
    WorkflowExistsError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from st_agent.l1.workflow.ids import base_of, check_flow_id, flow_id_for, parse_flow_id
from st_agent.l1.workflow.models import WorkflowDAG, checked_dag
from st_agent.l1.workflow.validate import validate_dag

__all__ = [
    "ACTIVE_PREFIX",
    "WORKFLOW_PREFIX",
    "ActivePointer",
    "WorkflowDiff",
    "WorkflowPendingCheck",
    "WorkflowStore",
]

WORKFLOW_PREFIX = "workflow/"
"""``config`` 分区内工作流定义的目录前缀。"""

ACTIVE_PREFIX = "workflow-active/"
"""``config`` 分区内激活指针的目录前缀（不以 ``workflow/`` 开头，故不串扫）。"""

NeutralityCheck = Callable[[str], bool]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class ActivePointer(BaseModel):
    """某 base 当前生效版本的指针（``rollback`` 换的就是它）。"""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    activated_at: datetime


class WorkflowDiff(BaseModel):
    """两个版本之间的结构化差异（03 §3.1 ``version`` 的 diff 能力）。"""

    model_config = ConfigDict(frozen=True)

    flow_id_a: str
    flow_id_b: str
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    changed_nodes: tuple[str, ...] = ()
    """同 ``node_id`` 但引用的 Skill 或参数绑定不同。"""
    added_edges: tuple[str, ...] = ()
    removed_edges: tuple[str, ...] = ()
    name_changed: bool = False
    description_changed: bool = False
    schedule_changed: bool = False

    def is_empty(self) -> bool:
        """两侧是否逐字段一致。"""
        return not (self.added_nodes or self.removed_nodes or self.changed_nodes
                    or self.added_edges or self.removed_edges or self.name_changed
                    or self.description_changed or self.schedule_changed)


class WorkflowPendingCheck(BaseModel):
    """一条「引用方待检查」（01 §9：主版本变更不自动破坏下游）。"""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    base: str
    """工作流的 base（激活与版本归集单位）。"""
    node_id: str
    skill_id: str
    """节点**当前**引用的版本（落后于 ``to_version``）。"""
    to_version: str
    """待检查的新主版本（``<主>.<次>``）。"""
    changelog: str
    impact: str


class WorkflowStore:
    """工作流库门面（03 §3.1；持久化只经 ``Store`` 的 ``config`` 分区）。"""

    def __init__(self, store, registry, *,
                 name_neutrality_check: NeutralityCheck | None = None) -> None:
        self._store = store
        self._registry = registry
        if name_neutrality_check is None:
            from st_agent.contracts.neutrality import NeutralityGuard
            guard = NeutralityGuard()
            name_neutrality_check = lambda n: guard.check_name(n).passed  # noqa: E731
        self._name_ok = name_neutrality_check

    # ───────────────────────── 写入：首版 / 新版本 / 激活 ─────────

    def save(self, dag: WorkflowDAG) -> WorkflowDAG:
        """保存工作流首版（校验通过才落盘；同 flow_id 已存在 → ``WorkflowExistsError``）。"""
        result = validate_dag(dag, self._registry, name_check=self._name_ok)
        if not result.ok:
            raise WorkflowValidationError(f"工作流校验未通过：{result.describe()}")
        path = self._path(dag.flow_id)
        if path in self._store.list_files("config"):
            raise WorkflowExistsError(
                f"工作流 {dag.flow_id!r} 已存在；新版本请用 publish_version，"
                "不得静默覆盖")
        self._put(dag)
        return dag

    def publish_version(self, base: str, version: SemVer | str,
                        dag: WorkflowDAG | None = None, *,
                        changelog: str = "", impact: str = "") -> WorkflowDAG:
        """发布同 base 新版本；``dag=None`` 即以上一版本为底稿。

        主版本变更须给出 ``changelog`` 与 ``impact``（更新面板展示用，01 §9）。
        """
        new_ver = version if isinstance(version, SemVer) else SemVer.parse(version)
        latest = self.get_latest(base)
        if latest is None:
            raise WorkflowNotFoundError(f"base {base!r} 无已发布版本，首版请用 save")
        _, old_ver = parse_flow_id(latest.flow_id)
        if not (new_ver.major > old_ver.major
                or (new_ver.major == old_ver.major and new_ver.minor > old_ver.minor)):
            raise WorkflowValidationError(
                f"新版本 {new_ver.major}.{new_ver.minor} 须高于当前 "
                f"{old_ver.major}.{old_ver.minor}（主.次递增）")
        if new_ver.major != old_ver.major and (not changelog.strip()
                                               or not impact.strip()):
            raise WorkflowValidationError(
                "主版本变更须给出 changelog 与 impact（更新面板展示用）")
        content = latest if dag is None else dag
        new_dag = checked_dag(**{
            **content.model_dump(),
            "flow_id": flow_id_for(base, new_ver),
            "version": new_ver,
        })
        return self.save(new_dag)

    def activate(self, base: str, flow_id: str) -> WorkflowDAG:
        """把某 base 的生效版本切到指定版本（版本文件不动）。"""
        dag = self.get(flow_id)
        if base_of(flow_id) != base:
            raise WorkflowValidationError(
                f"{flow_id!r} 不属于 base {base!r}，不得跨工作流激活")
        self._store.put(
            "config", self._active_path(base),
            ActivePointer(flow_id=flow_id, activated_at=_now())
            .model_dump_json().encode("utf-8"))
        return dag

    def rollback(self, base: str, flow_id: str) -> ChangeRecord:
        """一键回滚：把生效版本切回旧版本，返回变更留痕（01 §7 ``change_id``）。"""
        current = self.active(base)
        self.activate(base, flow_id)
        return ChangeRecord(
            change_id=ChangeId.generate().value,
            config_id=base,
            old_value=(current.flow_id if current is not None else None),
            new_value=flow_id,
            applied_at=_now().isoformat(),
        )

    def follow_latest(self, flow_id: str, *, changelog: str = "",
                      impact: str = "") -> WorkflowDAG:
        """把该版本中落后于待检查的节点引用改指各 base 的最新版本，落为新版本。

        无落后引用 → ``WorkflowValidationError``（不静默产出空变更）。
        """
        dag = self.get(flow_id)
        stale = self._stale_nodes(dag)
        if not stale:
            raise WorkflowValidationError(
                f"工作流 {flow_id!r} 无落后于待检查的 Skill 引用，无需跟进")
        nodes = tuple(
            n.model_copy(update={
                "skill_id": self._registry.get_latest(
                    skill_base_of(n.skill_id)).skill_id})
            if n.node_id in stale else n
            for n in dag.nodes
        )
        bumped = checked_dag(**{**dag.model_dump(), "nodes": nodes})
        return self.publish_version(
            base_of(flow_id), dag.version.bump("minor"), bumped,
            changelog=changelog, impact=impact)

    # ───────────────────────── 读取：单个 / 最新 / 列表 / 生效 ─────────

    def get(self, flow_id: str) -> WorkflowDAG:
        """读取某个版本（不存在 → ``WorkflowNotFoundError``）。"""
        check_flow_id(flow_id)
        try:
            raw = self._store.get("config", self._path(flow_id))
        except KeyError as exc:
            raise WorkflowNotFoundError(f"工作流 {flow_id!r} 不存在") from exc
        try:
            return checked_dag(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise WorkflowNotFoundError(
                f"工作流 {flow_id!r} 记录损坏无法解析：{exc}") from exc

    def get_latest(self, base: str) -> WorkflowDAG | None:
        """取同 base 下最高版本（无 → ``None``）。"""
        versions = self.list_versions(base)
        return versions[-1] if versions else None

    def list_versions(self, base: str) -> tuple[WorkflowDAG, ...]:
        """列出同 base 全部版本（按 ``(主, 次)`` 升序）。"""
        out: list[tuple[tuple[int, int], WorkflowDAG]] = []
        for fid in self._flow_ids():
            if base_of(fid) != base:
                continue
            _, ver = parse_flow_id(fid)
            out.append(((ver.major, ver.minor), self.get(fid)))
        return tuple(d for _, d in sorted(out, key=lambda t: t[0]))

    def list_all(self) -> tuple[WorkflowDAG, ...]:
        """列出全部工作流版本（按 ``(base, 主, 次)`` 升序）。"""
        out: list[tuple[tuple[str, int, int], WorkflowDAG]] = []
        for fid in self._flow_ids():
            base, ver = parse_flow_id(fid)
            out.append(((base, ver.major, ver.minor), self.get(fid)))
        return tuple(d for _, d in sorted(out, key=lambda t: t[0]))

    def active(self, base: str) -> WorkflowDAG | None:
        """某 base 当前生效版本（无激活指针 → 取最高版本；全无 → ``None``）。"""
        try:
            raw = self._store.get("config", self._active_path(base))
        except KeyError:
            return self.get_latest(base)
        pointer = ActivePointer.model_validate_json(raw.decode("utf-8"))
        return self.get(pointer.flow_id)

    # ───────────────────────── 派生：diff / 待检查 ─────────

    def diff(self, flow_id_a: str, flow_id_b: str) -> WorkflowDiff:
        """两个版本的结构化差异（节点 / 边 / 名称 / 描述 / 触发方式）。"""
        a, b = self.get(flow_id_a), self.get(flow_id_b)
        na = {n.node_id: n for n in a.nodes}
        nb = {n.node_id: n for n in b.nodes}
        ea = {e.edge_id for e in a.edges}
        eb = {e.edge_id for e in b.edges}
        return WorkflowDiff(
            flow_id_a=flow_id_a, flow_id_b=flow_id_b,
            added_nodes=tuple(sorted(set(nb) - set(na))),
            removed_nodes=tuple(sorted(set(na) - set(nb))),
            changed_nodes=tuple(sorted(
                nid for nid in set(na) & set(nb) if na[nid] != nb[nid])),
            added_edges=tuple(sorted(eb - ea)),
            removed_edges=tuple(sorted(ea - eb)),
            name_changed=a.name != b.name,
            description_changed=a.description != b.description,
            schedule_changed=a.schedule != b.schedule,
        )

    def pending_checks(self) -> tuple[WorkflowPendingCheck, ...]:
        """全部「引用方待检查」（01 §9）。

        按**各 base 的生效版本**派生（``active``）——「下游对象」指的是一条工作流
        当前在用的那一版，而非它累积的全部历史版本；故历史版本不误报，
        显式回滚到旧版本后该版本才被标（读 ``SkillRegistry.pending_updates``，
        不另存标记，避免两处真相漂移）。
        """
        out: list[WorkflowPendingCheck] = []
        for base in sorted({base_of(fid) for fid in self._flow_ids()}):
            dag = self.active(base)
            if dag is not None:
                out.extend(self._stale_nodes(dag).values())
        return tuple(sorted(out, key=lambda c: (c.base, c.node_id)))

    def _stale_nodes(self, dag: WorkflowDAG) -> dict[str, WorkflowPendingCheck]:
        """该版本内落后于「待检查主版本」的节点（``{node_id: check}``）。"""
        updates = {u.base: u for u in self._registry.pending_updates()}
        if not updates:
            return {}
        out: dict[str, WorkflowPendingCheck] = {}
        flow_base = base_of(dag.flow_id)
        for n in dag.nodes:
            if not n.skill_id.startswith("sk_"):
                continue
            info = updates.get(skill_base_of(n.skill_id))
            if info is None:
                continue
            _, ver = parse_skill_id(n.skill_id)
            to_ver = SemVer.parse(info.to_version)
            if (ver.major, ver.minor) < (to_ver.major, to_ver.minor):
                out[n.node_id] = WorkflowPendingCheck(
                    flow_id=dag.flow_id, base=flow_base, node_id=n.node_id,
                    skill_id=n.skill_id, to_version=info.to_version,
                    changelog=info.changelog, impact=info.impact)
        return out

    # ───────────────────────── 内部工具 ─────────────────────────

    def _flow_ids(self) -> tuple[str, ...]:
        return tuple(
            name[len(WORKFLOW_PREFIX):-len(".json")]
            for name in self._store.list_files("config")
            if name.startswith(WORKFLOW_PREFIX) and name.endswith(".json"))

    def _put(self, dag: WorkflowDAG) -> None:
        self._store.put(
            "config", self._path(dag.flow_id),
            dag.model_dump_json().encode("utf-8"))

    @staticmethod
    def _path(flow_id: str) -> str:
        check_flow_id(flow_id)
        return f"{WORKFLOW_PREFIX}{flow_id}.json"

    @staticmethod
    def _active_path(base: str) -> str:
        if not base.startswith("wf_"):
            raise WorkflowValidationError(f"非法工作流 base {base!r}（须为 wf_ 前缀）")
        return f"{ACTIVE_PREFIX}{base}.json"
