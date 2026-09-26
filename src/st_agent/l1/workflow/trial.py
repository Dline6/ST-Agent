"""工作流试跑（T-L1-003.3；03 §4 调试协议）。

任何工作流可**试跑**：按拓扑序逐节点执行并留快照，支持单步、输入注入、
暂停 / 继续 / 中止；每次试跑留存历史可对比。落地口径逐条对应 [03 §4] 的
「调试协议落地口径」表：

- **试跑门禁**：只拦**不可执行**类结构问题（成环 / 自环 / 重复节点标识 /
  连线或引用指向不存在的节点）；其余发现（Skill 未注册、`skill_id` 形态非法、
  连线契约不匹配、参数未声明）不拦，落到节点级以失败信封装载——调试期正是要暴露它们
- **会话驱动**：步进式状态机（非线程中断）。暂停与中止都生效在**节点边界**；
  进程被杀不在覆盖范围（崩溃恢复不属试跑语义）
- **逐节点执行**：经 ``l1.runner.SkillRunner.run`` 走 §1.2 完整流水线
  （同一 ``trace_id``，逐节点追加 ``skill_run`` 步），**不修改**该流水线
- **参数解析**：``literal`` 取字面值；``ref`` 从上游节点输出信封的 ``data``
  按 ``path`` 取值——路径不存在即该节点 ``validation_failed``，不静默置空
- **输入注入**：按节点注入，**覆盖**该节点绑定中的 ``literal``；对 ``ref``
  绑定或节点未声明的参数名**显式拒绝**（不静默丢弃）
- **失败短路**：节点信封非 ``ok``/``empty`` → 其**下游**不执行并标
  ``dependency_failed``（与节点无关的旁支照常执行）
- **输出复用面**：试跑**不登记**输出复用——故本模块要求传入的 ``SkillRunner``
  不带 ``OutputRegistry``（构造期显式拒绝，见 ``TrialRunner.__init__``）

布局（只经 ``Store`` 读写）：试跑记录 → ``execution_log`` 分区
``workflow-trial/<trial_id>.json``（与 §1.2 的 ``skill-run/`` 同分区不同前缀）。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.identifiers import TraceId, TrialId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l1.runner.errors import RunnerValidationError
from st_agent.l1.skills.errors import SkillNotFoundError, SkillValidationError
from st_agent.l1.workflow.errors import (
    TrialStateError,
    WorkflowValidationError,
)
from st_agent.l1.workflow.models import WorkflowDAG, WorkflowNode
from st_agent.l1.workflow.validate import (
    dependency_graph,
    topological_order,
    validate_dag,
)

__all__ = [
    "FATAL_ISSUE_CODES",
    "TRIAL_STATUSES",
    "TrialDiff",
    "TrialRecord",
    "TrialRunner",
    "TrialStatus",
    "TrialStep",
    "ValueSource",
    "WorkflowTrial",
]

TrialStatus = Literal["running", "paused", "completed", "failed", "aborted"]
TRIAL_STATUSES: tuple[str, ...] = ("running", "paused", "completed", "failed", "aborted")
"""会话状态取值（机器可读副本）。"""

ValueSource = Literal["injected", "upstream", "literal", "default"]
"""输入快照中单个参数的来源（03 §4 值来源标注）。"""

FATAL_ISSUE_CODES: frozenset[str] = frozenset(
    {"cycle", "self_loop", "duplicate_node", "unknown_node"}
)
"""试跑门禁拦下的「不可执行」类校验发现码（03 §4 落地口径）。"""

_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "aborted"})
_STATUS_CN = {"running": "运行中", "paused": "已暂停", "completed": "已完成",
              "failed": "已失败", "aborted": "已中止"}


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _dig(data: Any, path: str | None) -> tuple[bool, Any]:
    """按点号路径取值 → ``(是否取到, 值)``；``path=None`` 取整个对象。"""
    if path is None:
        return True, data
    cur = data
    for seg in path.split("."):
        if not isinstance(cur, Mapping) or seg not in cur:
            return False, None
        cur = cur[seg]
    return True, cur


def _descendants(node_id: str, adj: Mapping[str, set[str]]) -> tuple[str, ...]:
    """依赖图上从 ``node_id`` 可达的全部节点（不含自身，按字典序）。"""
    seen: set[str] = set()
    frontier = list(adj.get(node_id, ()))
    while frontier:
        cur = frontier.pop()
        if cur in seen or cur == node_id:
            continue
        seen.add(cur)
        frontier.extend(adj.get(cur, ()))
    return tuple(sorted(seen))


# ───────────────────────── 留痕模型 ─────────────────────────


class TrialStep(BaseModel):
    """一个节点的试跑留痕（输入快照 + 输出信封 + 耗时 + 值来源）。"""

    model_config = ConfigDict(frozen=True)

    order: int = Field(ge=0)
    """拓扑序位次（0 起）。"""
    node_id: str
    skill_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    """输入快照（实际生效的参数：引用 / 字面量 / 注入 + Skill 默认值）。"""
    value_sources: dict[str, ValueSource] = Field(default_factory=dict)
    """逐参数来源（``injected`` / ``upstream`` / ``literal`` / ``default``）。"""
    envelope: ResultEnvelope
    duration_ms: int = Field(default=0, ge=0)
    skill_run_id: str | None = None
    """本步对应的 SkillRun 定位串（``skill-run/<id>.json`` 的 id）；未执行则空。"""
    started_at: datetime
    skipped: bool = False
    """失败短路下未执行（信封为 ``dependency_failed``）。"""

    @property
    def status(self) -> str:
        """本步输出信封的状态码。"""
        return self.envelope.status


class TrialRecord(BaseModel):
    """一次试跑的留存记录（落 ``execution_log`` 分区 ``workflow-trial/<trial_id>.json``）。"""

    model_config = ConfigDict(frozen=True)

    trial_id: str
    flow_id: str
    status: TrialStatus
    started_at: datetime
    finished_at: datetime | None = None
    injections: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """本次试跑收到的全部注入（按节点分组，含尚未生效者）。"""
    steps: tuple[TrialStep, ...] = ()
    approved_permissions: tuple[str, ...] = ()
    """本次试跑实际携带的已批准权限集合（A5：试跑不放宽权限口径）。"""
    trace: Trace
    """整条试跑共用的推理链（01 §4：逐节点追加 ``skill_run`` 步）。"""

    def step_of(self, node_id: str) -> TrialStep | None:
        """按 ``node_id`` 取该节点的留痕（未推进到 → ``None``）。"""
        for s in self.steps:
            if s.node_id == node_id:
                return s
        return None


class TrialDiff(BaseModel):
    """两次试跑的逐节点输出差异（03 §4「历史对比」）。"""

    model_config = ConfigDict(frozen=True)

    trial_id_a: str
    trial_id_b: str
    flow_id_a: str
    flow_id_b: str
    added_nodes: tuple[str, ...] = ()
    """仅在 b 中出现的节点。"""
    removed_nodes: tuple[str, ...] = ()
    """仅在 a 中出现的节点。"""
    changed_nodes: tuple[str, ...] = ()
    """两侧都有但输出不同（状态码或载荷不同）的节点。"""

    def is_empty(self) -> bool:
        """两侧逐节点输出是否一致。"""
        return not (self.added_nodes or self.removed_nodes or self.changed_nodes)


# ───────────────────────── 试跑会话 ─────────────────────────


class WorkflowTrial:
    """一次试跑会话（步进式状态机；由 ``TrialRunner.start`` 创建）。"""

    def __init__(
        self,
        runner: "TrialRunner",
        *,
        trial_id: str,
        dag: WorkflowDAG,
        order: tuple[str, ...],
        approved_permissions: tuple[str, ...] = (),
        injected: Mapping[str, Mapping[str, Any]] | None = None,
        trace: Trace | None = None,
    ) -> None:
        self._runner = runner
        self._trial_id = trial_id
        self._dag = dag
        self._order = order
        self._approved = tuple(approved_permissions)
        self._injected: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in (injected or {}).items()}
        self._trace = trace if trace is not None else Trace(trace_id=TraceId.generate())
        self._steps: list[TrialStep] = []
        self._blocked: dict[str, str] = {}
        """失败短路标下的节点 → 其最近的失败上游节点。"""
        self._status: TrialStatus = "running"
        self._started_at = _now()
        self._finished_at: datetime | None = None

    # ───────────────────────── 只读视图 ─────────────────────────

    @property
    def trial_id(self) -> str:
        return self._trial_id

    @property
    def dag(self) -> WorkflowDAG:
        return self._dag

    @property
    def order(self) -> tuple[str, ...]:
        """本次试跑的拓扑序（节点 id）。"""
        return self._order

    @property
    def status(self) -> TrialStatus:
        return self._status

    @property
    def trace(self) -> Trace:
        return self._trace

    @property
    def approved_permissions(self) -> tuple[str, ...]:
        return self._approved

    @property
    def injected(self) -> dict[str, dict[str, Any]]:
        """当前全部注入（副本；按节点分组）。"""
        return {k: dict(v) for k, v in self._injected.items()}

    def snapshots(self) -> tuple[TrialStep, ...]:
        """已推进的逐步快照（按拓扑序）。"""
        return tuple(self._steps)

    def snapshot_of(self, node_id: str) -> TrialStep | None:
        """某节点已推进的快照（未推进到 → ``None``）。"""
        for s in self._steps:
            if s.node_id == node_id:
                return s
        return None

    def record(self) -> TrialRecord:
        """当前会话的留存记录（每次状态变更后即落盘此形态）。"""
        return TrialRecord(
            trial_id=self._trial_id, flow_id=self._dag.flow_id, status=self._status,
            started_at=self._started_at, finished_at=self._finished_at,
            injections=self.injected, steps=tuple(self._steps),
            approved_permissions=self._approved, trace=self._trace,
        )

    # ───────────────────────── 驱动 ─────────────────────────

    def step(self) -> TrialStep:
        """推进一步（一个节点）：执行或标短路 → 该步留痕。

        已暂停的会话仍可单步（调试语义：暂停只拦「自动跑到尾」）。
        """
        if self._status in _TERMINAL:
            raise TrialStateError(
                f"试跑 {self._trial_id} {_STATUS_CN[self._status]}，无法继续推进")
        if len(self._steps) >= len(self._order):
            raise TrialStateError(f"试跑 {self._trial_id} 的节点已全部推进完毕")
        step = self._runner._advance(self, self._order[len(self._steps)])
        self._steps.append(step)
        self._after_advance()
        return step

    def run_to_end(self) -> TrialRecord:
        """一路推进到结束（暂停 / 中止都会在节点边界停下）。"""
        if self._status != "running":
            raise TrialStateError(
                f"试跑 {self._trial_id} 当前为{_STATUS_CN[self._status]}，"
                "run_to_end 仅可对运行中的试跑调用（暂停态请先 resume）")
        if not self._order:
            self._after_advance()       # 零节点工作流：无步可推，当场完成
        while self._status == "running" and len(self._steps) < len(self._order):
            self.step()
        return self.record()

    def pause(self) -> None:
        """暂停（同态幂等；终态 → 显式拒绝）。"""
        self._require_live("暂停")
        if self._status == "paused":
            return
        self._status = "paused"
        self._persist()

    def resume(self) -> None:
        """继续（同态幂等；终态 → 显式拒绝）。"""
        self._require_live("继续")
        if self._status == "running":
            return
        self._status = "running"
        self._persist()

    def abort(self) -> None:
        """中止：后续节点一律不执行，记录标 ``aborted``（同态幂等）。"""
        if self._status == "aborted":
            return
        if self._status in _TERMINAL:
            raise TrialStateError(
                f"试跑 {self._trial_id} {_STATUS_CN[self._status]}，无法中止")
        self._status = "aborted"
        self._finished_at = _now()
        self._persist()

    def inject(self, node_id: str, values: Mapping[str, Any]) -> None:
        """为某节点注入输入（覆盖其 ``literal`` 绑定；仅当该节点尚未执行）。

        已执行节点 / 终态会话 → ``TrialStateError``；不在本工作流中的节点 →
        ``WorkflowValidationError``。注入是否可被接受（``ref`` 绑定、未声明参数）
        在该节点执行时显式判定，见 ``TrialRunner._resolve``。
        """
        self._require_live("注入")
        if self._dag.node(node_id) is None:
            raise WorkflowValidationError(
                f"节点 {node_id!r} 不在这条工作流（{self._dag.flow_id}）中，无法注入")
        if node_id in {s.node_id for s in self._steps}:
            raise TrialStateError(
                f"节点 {node_id!r} 已执行，注入不生效——拒绝静默丢弃")
        self._injected.setdefault(node_id, {}).update(dict(values))
        self._persist()

    # ───────────────────────── 内部 ─────────────────────────

    def _require_live(self, action: str) -> None:
        if self._status in _TERMINAL:
            raise TrialStateError(
                f"试跑 {self._trial_id} {_STATUS_CN[self._status]}，无法{action}")

    def _after_advance(self) -> None:
        """推进一步后的收尾：中止优先（执行期被中止即终态），否则看是否跑完。"""
        if self._status != "aborted" and len(self._steps) >= len(self._order):
            failed = any(not s.skipped and s.envelope.status not in ("ok", "empty")
                         for s in self._steps)
            self._status = "failed" if failed else "completed"
            self._finished_at = _now()
        self._persist()

    def _is_blocked(self, node_id: str) -> bool:
        return node_id in self._blocked

    def _blocked_by(self, node_id: str) -> str:
        return self._blocked[node_id]

    def _block_descendants(self, failed_node: str) -> None:
        """把 ``failed_node`` 在依赖图上的全部后代标为短路（首个失败者优先）。"""
        for node_id in _descendants(failed_node, dependency_graph(self._dag)):
            self._blocked.setdefault(node_id, failed_node)

    def _set_trace(self, trace: Trace) -> None:
        self._trace = trace

    def _persist(self) -> None:
        self._runner._save(self.record())


# ───────────────────────── 门面 ─────────────────────────


class TrialRunner:
    """工作流试跑门面（03 §4；逐节点经 ``SkillRunner`` 走完整流水线）。"""

    def __init__(self, store, registry, runner, *, history=None,
                 approved_permissions: tuple[str, ...] | list[str] = (),
                 initiator: str = "", purpose: str = "") -> None:
        if getattr(runner, "_outputs", None) is not None:
            # 03 §4 落地口径：试跑不登记输出复用（彩排产物不进生产复用面）
            raise WorkflowValidationError(
                "试跑不得使用带输出复用登记（OutputRegistry）的 SkillRunner"
                "——03 §4：试跑不登记输出复用")
        self._registry = registry
        self._runner = runner
        if history is None:
            from st_agent.l1.workflow.trial_history import TrialHistory
            history = TrialHistory(store)
        self._history = history
        self._approved = tuple(approved_permissions)
        self._initiator = initiator
        self._purpose = purpose

    @property
    def history(self):
        """试跑留存门面（``TrialHistory``）。"""
        return self._history

    def start(self, dag: WorkflowDAG,
              values: Mapping[str, Mapping[str, Any]] | None = None, *,
              trace: Trace | None = None) -> WorkflowTrial:
        """开一次试跑会话（门禁通过才起跑；会话已就位落盘一条 ``running`` 记录）。

        :param values: 起跑时的输入注入（``{node_id: {参数名: 值}}``）
        :param trace: 调用方已有推理链（``None`` 即新建一条，整场试跑共用）
        """
        guard = validate_dag(dag, self._registry)
        fatal = tuple(i for i in guard.issues if i.code in FATAL_ISSUE_CODES)
        if fatal:
            raise WorkflowValidationError(
                "工作流不可执行，拒绝试跑：" + "；".join(str(i) for i in fatal))
        trial = WorkflowTrial(
            self, trial_id=TrialId.generate().value, dag=dag,
            order=topological_order(dag), approved_permissions=self._approved,
            injected=values, trace=trace,
        )
        trial._persist()            # 首条记录（会话就位即留痕）
        return trial

    # ───────────────────────── 单节点推进（会话调用） ─────────────────────────

    def _advance(self, trial: WorkflowTrial, node_id: str) -> TrialStep:
        started = time.monotonic()
        node = trial.dag.node(node_id)
        if node is None:                                  # 门禁已排除，防御性兜底
            raise WorkflowValidationError(f"节点 {node_id!r} 不在工作流拓扑序中")
        order = len(trial.snapshots())

        if trial._is_blocked(node_id):
            return TrialStep(
                order=order, node_id=node_id, skill_id=node.skill_id,
                envelope=ResultEnvelope.dependency_failed(
                    f"上游节点 {trial._blocked_by(node_id)!r} 未产出可用结果，"
                    f"下游 {node_id!r} 不执行（03 §4 失败短路）"),
                started_at=_now(), skipped=True,
            )

        params, sources, failure = self._resolve(trial, node)
        if failure is not None:
            return TrialStep(
                order=order, node_id=node_id, skill_id=node.skill_id,
                params=params, value_sources=sources,
                envelope=ResultEnvelope.validation_failed(failure),
                duration_ms=int((time.monotonic() - started) * 1000),
                started_at=_now(),
            )

        try:
            outcome = self._runner.run(
                node.skill_id, params, trace=trial.trace,
                approved_permissions=trial.approved_permissions,
                initiator=self._initiator,
                purpose=self._purpose or f"试跑 {trial.trial_id}",
            )
        except RunnerValidationError as exc:              # skill_id 形态非法等
            return TrialStep(
                order=order, node_id=node_id, skill_id=node.skill_id,
                params=params, value_sources=sources,
                envelope=ResultEnvelope.validation_failed(str(exc)),
                duration_ms=int((time.monotonic() - started) * 1000),
                started_at=_now(),
            )
        trial._set_trace(outcome.trace)
        step = TrialStep(
            order=order, node_id=node_id, skill_id=node.skill_id,
            params=params, value_sources=sources, envelope=outcome.envelope,
            duration_ms=int((time.monotonic() - started) * 1000),
            skill_run_id=outcome.skill_run_id, started_at=_now(),
        )
        if step.envelope.status not in ("ok", "empty"):
            trial._block_descendants(node_id)
        return step

    def _resolve(self, trial: WorkflowTrial, node: WorkflowNode
                 ) -> tuple[dict[str, Any], dict[str, ValueSource], str | None]:
        """解析一个节点的输入 → ``(输入快照, 逐参数来源, 显式失败原因)``。

        失败原因非空即该节点不经流水线、直接 ``validation_failed``
        （绑定取值路径缺失 / 注入打在 ``ref`` 绑定上——两者都不静默置空）。
        """
        values: dict[str, Any] = {}
        sources: dict[str, ValueSource] = {}
        for pname in sorted(node.params):
            binding = node.params[pname]
            if binding.kind == "literal":
                values[pname] = binding.value
                sources[pname] = "literal"
                continue
            up = trial.snapshot_of(binding.from_node)
            if up is None or up.envelope.status != "ok":
                return values, sources, (
                    f"参数 {pname!r} 引用的上游节点 {binding.from_node!r} 无可取结果"
                    f"（{up.envelope.status if up is not None else '未执行'}）")
            found, value = _dig(up.envelope.data, binding.path)
            if not found:
                return values, sources, (
                    f"参数 {pname!r} 的取值路径 {binding.path!r} 在 "
                    f"{binding.from_node!r} 的输出中不存在")
            values[pname] = value
            sources[pname] = "upstream"
        for pname, value in sorted(trial.injected.get(node.node_id, {}).items()):
            binding = node.params.get(pname)
            if binding is not None and binding.kind == "ref":
                return values, sources, (
                    f"参数 {pname!r} 是上游引用绑定，不接受注入（不静默丢弃）")
            values[pname] = value
            sources[pname] = "injected"
        try:
            effective = self._registry.validate_call_params(node.skill_id, values)
        except (SkillNotFoundError, SkillValidationError):
            return values, sources, None      # 交流水线产出失败信封（同一语义不重复造）
        for pname, value in effective.items():
            if pname not in sources:
                sources[pname] = "default"
            values[pname] = value
        return values, sources, None

    def _save(self, record: TrialRecord) -> TrialRecord:
        return self._history.save(record)
