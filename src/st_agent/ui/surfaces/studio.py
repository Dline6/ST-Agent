"""能力区 · Studio 附加面：自建工作流、模板与试跑（[T-UI-015.3]）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.studio.errors import StudioError
from st_agent.l1.workflow.errors import TrialError, WorkflowNotFoundError
from st_agent.l1.workflow.ids import base_of
from st_agent.l3.render.describe import STANCE_LABELS, describe_divergence_map, describe_trace
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _ENVELOPE_STATE_LABELS,
    _LOG,
    _TIMELINE_STATE_LABELS,
    _brief_json,
    _fmt_value,
    _mapping,
    _now,
    _required,
)


def _drive_trial(trial: Any, action: str) -> None:
    """按动作推进试跑会话（未知动作即拒，**不静默 no-op**）。

    暂停只拦「自动跑到尾」，故暂停态**仍可单步**（[03 §4] 的调试语义）；中止与暂停都生效
    在**节点边界**上。`start` 不在本函数内——它要先造会话、再登记进会话表。
    """
    drivers = {
        "step": trial.step,
        "run_to_end": trial.run_to_end,
        "pause": trial.pause,
        "resume": trial.resume,
        "abort": trial.abort,
    }
    driver = drivers.get(action)
    if driver is None:
        raise ValueError(
            f"未知动作 {action!r}（可选：start / step / run_to_end / pause / resume / abort）"
        )
    driver()


def _workflow_row(
    face: Any, dag: Any, checks: Mapping[tuple[str, str], Any]
) -> dict[str, Any]:
    """一行工作流摘要（清单用；`生效` 看激活指针是否指向本版本）。"""
    base = base_of(dag.flow_id)
    active = face.workflows.active(base)
    pending = [item for (item_base, _), item in checks.items() if item_base == base]
    return {
        "name": dag.name,
        "flow_id": dag.flow_id,
        "version": f"v{dag.version.major}.{dag.version.minor}",
        "node_count": len(dag.nodes),
        "active": "生效" if active is not None and active.flow_id == dag.flow_id else "—",
        "pending": f"{len(pending)} 项" if pending else "—",
    }


def _schedule_text(schedule: Any) -> str:
    """调度的一句话（[03 §3.1] `schedule` 四种 mode）。"""
    return {
        "manual": "手动",
        "interval": f"每 {schedule.interval_minutes} 分钟",
        "cron": f"cron：{schedule.cron}",
        "event": f"事件：{schedule.event}",
    }.get(schedule.mode, schedule.mode)


def _workflow_sections(
    dag: Any, base: str, active: Any, checks: tuple[Any, ...]
) -> list[dict[str, Any]]:
    """属性面板的五段（[11-sitemap §2.1]：`report_card` 多段式陈述）。"""
    return [
        {"title": "基本信息", "lines": [
            f"名称：{dag.name}",
            f"说明：{dag.description}",
            f"注册名：{base}",
            f"版本：v{dag.version.major}.{dag.version.minor}",
            f"节点 / 连线 / 分组：{len(dag.nodes)} / {len(dag.edges)} / {len(dag.groups)}",
            "当前生效：" + (active.flow_id if active is not None else "（无激活版本）"),
        ]},
        {"title": "调度", "lines": [_schedule_text(dag.schedule)]},
        {"title": "节点", "lines": [
            f"{node.node_id} → {node.skill_id}" for node in dag.nodes
        ] or ["（无节点）"]},
        {"title": "连线", "lines": [
            f"{edge.edge_id}：{edge.from_node} → {edge.to_node}" for edge in dag.edges
        ] or ["（无连线）"]},
        # 引用方待检查（[01 §9]：主版本变更不自动破坏下游）——逐条给 changelog 与影响范围。
        {"title": "引用方待检查", "lines": [
            f"{item.node_id} 引用的 {item.skill_id} 已有新主版本 {item.to_version}"
            f"（变更日志：{item.changelog}；影响：{item.impact}）"
            for item in checks
        ] or ["（无）"]},
    ]


def _version_entries(versions: tuple[Any, ...], active: Any) -> list[dict[str, Any]]:
    """版本历史的时间线条目。

    `at` 槽给的是**版本号**而非日期——`WorkflowDAG` 不携创建时刻，而版本号本身即排序依据；
    摆一个编出来的日期才是失真。当前生效的那版在 `content` 里显式标注。
    """
    current = active.flow_id if active is not None else ""
    return [
        {
            "id": dag.flow_id,
            "at": f"v{dag.version.major}.{dag.version.minor}",
            "kind": "version",
            "content": (
                f"{dag.name} · 节点 {len(dag.nodes)} · 连线 {len(dag.edges)}"
                + ("（当前生效）" if dag.flow_id == current else "")
            ),
            "state": "ok",
        }
        for dag in versions
    ]


def _template_row(summary: Any) -> dict[str, Any]:
    """一行模板摘要（[03 §4] 浏览面：名称 / 说明 / 节点数 / 引用 Skill / **缺失 Skill**）。"""
    return {
        "name": summary.name,
        "template_id": summary.template_id,
        "description": summary.description,
        "node_count": summary.node_count,
        "skills": "、".join(summary.skill_ids) or "（无）",
        "missing": "、".join(summary.missing_skill_ids) or "—",
    }


def _trial_row(record: Any) -> dict[str, Any]:
    """一行试跑摘要。"""
    return {
        "trial_id": record.trial_id,
        "flow_id": record.flow_id,
        "status": _TRIAL_STATUS_LABELS.get(record.status, record.status),
        "steps": len(record.steps),
        "started_at": record.started_at.isoformat(),
        "finished_at": record.finished_at.isoformat() if record.finished_at else "—",
    }


def _trial_sections(record: Any) -> list[dict[str, Any]]:
    """试跑小结（状态 / 步数 / 已批准权限 / 注入）——**并列**，不含「建议」。"""
    return [
        {"title": "试跑", "lines": [
            f"标识：{record.trial_id}",
            f"工作流：{record.flow_id}",
            f"状态：{_TRIAL_STATUS_LABELS.get(record.status, record.status)}",
            f"已推进：{len(record.steps)} 步",
            f"起跑：{record.started_at.isoformat()}",
            "结束：" + (record.finished_at.isoformat() if record.finished_at else "（未结束）"),
        ]},
        # 试跑不放宽权限口径（[03 §4]）——实际携带的已批准权限如实列出。
        {"title": "本次携带的已批准权限",
         "lines": list(record.approved_permissions) or ["（无）"]},
        {"title": "输入注入", "lines": [
            f"{node_id}：{_fmt_value(values)}"
            for node_id, values in record.injections.items()
        ] or ["（无注入）"]},
    ]


def _trial_step_row(step: Any) -> dict[str, Any]:
    """一个节点的试跑快照（[03 §4]：逐参数标注值来源）。"""
    sources = "、".join(
        f"{name}={_VALUE_SOURCE_LABELS.get(source, source)}"
        for name, source in step.value_sources.items()
    )
    return {
        "order": step.order,
        "node_id": step.node_id,
        "skill_id": step.skill_id,
        "status": _ENVELOPE_STATE_LABELS.get(step.status, step.status),
        "duration_ms": step.duration_ms,
        "sources": sources or "—",
        "params": _brief_json(step.params),
    }


def _studio_envelope(payload: dict[str, Any], label: str) -> Any:
    """Studio 面的返回 → 信封：`available: false` ⇒ `unavailable` + 点名；否则 `ok`。"""
    if not payload.get("available"):
        return ResultEnvelope.unavailable(
            payload.get("reason") or f"未接入{label}面（装配归组合根）",
            last_updated_at=_now(),
        )
    return ResultEnvelope.ok(payload)


_STUDIO_LABELS = {
    "accept": "接受",
    "reject": "否决",
    "edit": "编辑",
    "add_node": "添加节点",
    "remove_node": "删除节点",
    "connect": "连线",
    "disconnect": "断线",
    "group": "建分组",
    "ungroup": "解散分组",
}
"""Studio 画布的控件文案（**本层生成文案**，过 [01 §6] 执行点 2；[`T-UI-005.1`]）。

动作键与组合根 `_CANVAS_EDIT_OPS` / `_STUDIO_ACTIONS` 同集——**动作不进描述**（01 §12）：
描述只放数据与标签，发起写请求的路由（`/api/studio/edit` 等）是渲染件已知的固定回环路由。
"""


_TRIAL_STATUS_LABELS: dict[str, str] = {
    "running": "运行中",
    "paused": "已暂停",
    "completed": "已完成",
    "failed": "已失败",
    "aborted": "已中止",
}
"""试跑会话状态的中文名（[03 §4] 步进式状态机的五个状态）。"""


_VALUE_SOURCE_LABELS: dict[str, str] = {
    "injected": "注入",
    "upstream": "上游引用",
    "literal": "字面量",
    "default": "Skill 默认值",
}
"""逐参数**值来源**的中文名（[03 §4]：每个节点输入快照逐参数标注来源）。"""


class StudioSurface(SurfaceBase):
    """能力区 · Studio 附加面：自建工作流、模板与试跑（[T-UI-015.3]）。"""

    def api_studio_workflows(self) -> dict[str, Any]:
        """自建工作流清单 → `table`（逐版本一行；含「引用方待检查」标记）。"""
        return self._studio_call("studio-workflows", self._studio_workflows_envelope)

    def api_studio_workflow(self, *, flow_id: str) -> dict[str, Any]:
        """工作流属性面板 → `report_card`（基本信息 / 调度 / 节点 / 连线 / 激活 / 待检查）。"""
        if not flow_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少工作流标识（flow）"))
        return self._studio_call(
            "studio-workflow", lambda face: self._studio_workflow_envelope(face, flow_id)
        )

    def api_studio_versions(self, *, flow_id: str, a: str = "", b: str = "") -> dict[str, Any]:
        """版本历史 → 两段：`versions`（`timeline_view`，按版本序）＋ `diff`（`report_card`）。"""
        if not flow_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少工作流标识（flow）"))
        return self._studio_parts(
            "studio-versions", ("versions", "diff"),
            lambda face: self._studio_versions_parts(face, flow_id, a, b),
        )

    def api_studio_version_set(self, body: dict[str, Any]) -> dict[str, Any]:
        """版本动作（激活 / 回滚）——经 `WorkflowStore` 真落值并产生变更留痕。"""
        return self._studio_call(
            "studio-version-set", lambda face: self._studio_version_set_envelope(face, body)
        )

    def api_studio_templates(self) -> dict[str, Any]:
        """模板库 → 两段：`home`（空状态与一句话入口）＋ `templates`（逐条模板）。"""
        return self._studio_parts(
            "studio-templates", ("home", "templates"), self._studio_templates_parts
        )

    def api_studio_template(self, *, template_id: str) -> dict[str, Any]:
        """模板只读预览 → `report_card`（身份取临时位 `v0.0`，表「未发布」，[03 §4]）。"""
        if not template_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少模板标识（id）"))
        return self._studio_call(
            "studio-template", lambda face: self._studio_template_envelope(face, template_id)
        )

    def api_studio_template_fork(self, body: dict[str, Any]) -> dict[str, Any]:
        """模板 fork → 交**既有画布会话**（不落盘；「接受」才是落盘点，[03 §4]）。"""
        return self._studio_call(
            "studio-template-fork", lambda face: self._studio_template_fork_envelope(face, body)
        )

    def api_studio_trials(self, *, flow_id: str, a: str = "", b: str = "") -> dict[str, Any]:
        """试跑历史 → 两段：`trials`（`table`）＋ `diff`（`report_card`，逐节点输出差异）。"""
        return self._studio_parts(
            "studio-trials", ("trials", "diff"),
            lambda face: self._studio_trials_parts(face, flow_id, a, b),
        )

    def api_studio_trial(self, body: dict[str, Any]) -> dict[str, Any]:
        """调试控制台的一次动作（起跑 / 单步 / 跑到结束 / 暂停 / 继续 / 中止）。"""
        return self._studio_parts(
            "studio-trial", ("summary", "steps", "trace"),
            lambda face: self._studio_trial_parts(face, body),
        )

    def _studio_call(self, where: str, fn: Callable[[Any], Any]) -> dict[str, Any]:
        """调一次 Studio 面操作（先翻 Studio / 试跑异常族，再出信封，同 `_mcp_call`）。"""

        def guarded(face: Any) -> Any:
            try:
                return fn(face)
            except (TrialError, StudioError) as exc:
                return ResultEnvelope.validation_failed(str(exc) or "Studio 请求不合契约")

        return self._call_face(where, self.studio, "L1 Studio 面", guarded)

    def _studio_parts(
        self, where: str, keys: tuple[str, ...], fn: Callable[[Any], dict[str, Any]]
    ) -> dict[str, Any]:
        """多段 Studio 端点：**任一失败，各段都是同一支错误信封**（同 `_settings_parts`）。"""
        def blank(envelope: ResultEnvelope) -> dict[str, Any]:
            payload = envelope_payload(envelope)
            return {key: payload for key in keys}

        if self.restart_gate.reason:
            return blank(ResultEnvelope.unavailable(
                self.restart_gate.reason, last_updated_at=_now()
            ))
        face = self.studio
        if face is None:
            return blank(ResultEnvelope.unavailable(
                "未接入 L1 Studio 面，该端点不可用（装配归生产入口）", last_updated_at=_now()
            ))
        try:
            return fn(face)
        except (TrialError, StudioError) as exc:
            return blank(ResultEnvelope.validation_failed(str(exc) or "Studio 请求不合契约"))
        except (ValueError, KeyError) as exc:
            _LOG.info("表现层端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
            return blank(ResultEnvelope.validation_failed(str(exc) or "请求不合契约"))
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/{where}-{uuid.uuid4().hex[:12]}"
            _LOG.exception("表现层端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
            return blank(ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref))

    def _studio_workflows_envelope(self, face: Any) -> ResultEnvelope:
        flows = tuple(face.workflows.list_all())
        if not flows:
            return ResultEnvelope.empty(
                "尚未创建任何工作流——在上方「模板库」段 fork 一份，或从对话里用一句话"
                "描述你想要的工作流（[03 §4] 空状态）"
            )
        checks = {
            (item.base, item.node_id): item for item in tuple(face.workflows.pending_checks())
        }
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("name", "名称"),
                    Column("flow_id", "版本标识"),
                    Column("version", "版本"),
                    Column("node_count", "节点数", "number", digits=0),
                    Column("active", "生效"),
                    Column("pending", "引用方待检查"),
                ),
                [_workflow_row(face, flow, checks) for flow in flows],
            ),
            text_kinds=table_text_kinds(),
            title="自建工作流",
        ))

    def _studio_workflow_envelope(self, face: Any, flow_id: str) -> ResultEnvelope:
        dag = face.workflows.get(flow_id)
        base = base_of(flow_id)
        active = face.workflows.active(base)
        checks = tuple(
            item for item in tuple(face.workflows.pending_checks()) if item.base == base
        )
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": _workflow_sections(dag, base, active, checks)},
            text_kinds={"sections": "data"},
            title=f"{dag.name} · 属性",
        ))

    def _studio_versions_parts(self, face: Any, flow_id: str, a: str, b: str) -> dict[str, Any]:
        base = base_of(flow_id)
        versions = tuple(face.workflows.list_versions(base))
        if not versions:
            raise WorkflowNotFoundError(f"工作流 base {base!r} 无任何版本")
        active = face.workflows.active(base)
        return {
            "versions": envelope_payload(ResultEnvelope.ok(self._gated_description(
                "timeline_view",
                slots={
                    "entries": _version_entries(versions, active),
                    "labels": {"kinds": {"version": "版本"}, "states": dict(_TIMELINE_STATE_LABELS)},
                    "empty_hint": "该工作流没有版本记录",
                },
                text_kinds={"entries": "data", "labels": "generated",
                            "empty_hint": "generated"},
                title=f"{base} · 版本历史",
            ))),
            "diff": self._diff_segment(
                None if not (a and b) else face.workflows.diff(a, b)
            ),
        }

    def _studio_version_set_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        flow_id = str(_required(body, "flow_id"))
        action = str(_required(body, "action"))
        base = base_of(flow_id)
        if action == "activate":
            dag = face.workflows.activate(base, flow_id)
            return ResultEnvelope.ok({"flow_id": dag.flow_id, "active": True})
        if action == "rollback":
            change = face.workflows.rollback(base, flow_id)
            return ResultEnvelope.ok({
                "flow_id": flow_id, "change_id": change.change_id,
                "changelog": getattr(change, "changelog", ""),
            })
        return ResultEnvelope.validation_failed(
            f"未知动作 {action!r}（可选：activate / rollback）"
        )

    def _studio_templates_parts(self, face: Any) -> dict[str, Any]:
        templates = tuple(face.templates.list())
        home = face.home
        return {
            "home": envelope_payload(ResultEnvelope.ok(self._gated_description(
                "report_card",
                slots={"sections": [
                    {"title": "工作流", "lines": [
                        "尚未创建任何工作流" if home.is_empty else "已有自建工作流",
                    ]},
                    {"title": "一句话入口", "lines": [home.one_line_entry]},
                ]},
                text_kinds={"sections": "data"},
                title="Studio 首页",
            ))),
            "templates": envelope_payload(ResultEnvelope.ok(self._gated_description(
                "table",
                slots=table_slots(
                    (
                        Column("name", "名称"),
                        Column("template_id", "标识"),
                        Column("description", "说明"),
                        Column("node_count", "节点数", "number", digits=0),
                        Column("skills", "引用 Skill"),
                        Column("missing", "缺失 Skill"),
                    ),
                    [_template_row(item) for item in templates],
                ),
                text_kinds=table_text_kinds(),
                title="官方模板库",
            ))),
        }

    def _studio_template_envelope(self, face: Any, template_id: str) -> ResultEnvelope:
        summary = face.templates.get(template_id)
        dag = face.templates.load(template_id)
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "基本信息", "lines": [
                    f"名称：{summary.name}",
                    f"标识：{summary.template_id}",
                    f"说明：{summary.description}",
                    f"节点数：{summary.node_count}",
                    f"临时身份：{dag.flow_id}（0.0 表未发布，[03 §4] 只读预览）",
                ]},
                {"title": "引用 Skill", "lines": list(summary.skill_ids) or ["（无）"]},
                {"title": "缺失 Skill", "lines": list(summary.missing_skill_ids) or ["（无缺失）"]},
                {"title": "节点", "lines": [
                    f"{node.node_id} → {node.skill_id}" for node in dag.nodes
                ] or ["（无节点）"]},
                {"title": "连线", "lines": [
                    f"{edge.edge_id}：{edge.from_node} → {edge.to_node}" for edge in dag.edges
                ] or ["（无连线）"]},
            ]},
            text_kinds={"sections": "data"},
            title=f"{summary.name} · 模板预览",
        ))

    def _studio_template_fork_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        template_id = str(_required(body, "template_id"))
        if face.open_template is None:
            return ResultEnvelope.unavailable(
                "未接入画布会话面，fork 后无法开画布（装配归生产入口）", last_updated_at=_now()
            )
        view = face.open_template(template_id)
        return ResultEnvelope.ok({
            "session_key": view.proposal_id,
            "base": view.base,
            "flow_id": view.flow_id,
            "node_count": view.node_count,
            "violations": list(view.violations),
        })

    def _studio_trials_parts(self, face: Any, flow_id: str, a: str, b: str) -> dict[str, Any]:
        history = face.trials.history
        records = (
            tuple(history.list_for_flow(flow_id)) if flow_id else tuple(history.list_all())
        )
        diff = None
        if a and b:
            diff = history.compare(a, b)
        return {
            "trials": envelope_payload(
                ResultEnvelope.empty(
                    f"工作流 {flow_id!r} 还没有试跑记录——在调试控制台起跑一次即留痕"
                    if flow_id else "还没有任何试跑记录"
                ) if not records else ResultEnvelope.ok(self._gated_description(
                    "table",
                    slots=table_slots(
                        (
                            Column("trial_id", "试跑标识"),
                            Column("flow_id", "工作流"),
                            Column("status", "状态"),
                            Column("steps", "步数", "number", digits=0),
                            Column("started_at", "起跑时刻"),
                            Column("finished_at", "结束时刻"),
                        ),
                        [_trial_row(record) for record in records],
                    ),
                    text_kinds=table_text_kinds(),
                    title="试跑历史",
                )),
            ),
            "diff": self._trial_diff_segment(diff),
        }

    def _studio_trial_parts(self, face: Any, body: dict[str, Any]) -> dict[str, Any]:
        action = str(_required(body, "action"))
        if action == "start":
            flow_id = str(_required(body, "flow_id"))
            dag = face.workflows.get(flow_id)
            trial = face.trials.start(dag, _mapping(body.get("values"), "values", allow_none=True))
            face.sessions.put(trial)
        else:
            trial_id = str(_required(body, "trial_id"))
            if not face.sessions.has(trial_id):
                # 会话表是**进程内**的：重启后旧 id 已无可续跑的活对象——如实说，不冒充。
                reason = (
                    f"试跑 {trial_id!r} 不在本进程的会话表里（进程重启后试跑会话不保留，"
                    "须重新起跑；已跑过的留痕仍在试跑历史里）"
                )
                return {key: envelope_payload(ResultEnvelope.unavailable(
                    reason, last_updated_at=_now()
                )) for key in ("summary", "steps", "trace")}
            trial = face.sessions.get(trial_id)
            _drive_trial(trial, action)
        record = trial.record()
        return {
            # 平键（不是描述）：页面要拿它续跑后续动作，靠解析描述文本取 id 是脆的。
            "trial_id": record.trial_id,
            "summary": envelope_payload(ResultEnvelope.ok(self._gated_description(
                "report_card",
                slots={"sections": _trial_sections(record)},
                text_kinds={"sections": "data"},
                title=f"试跑 · {record.trial_id}",
            ))),
            "steps": envelope_payload(
                ResultEnvelope.ok(self._gated_description(
                    "table",
                    slots=table_slots(
                        (
                            Column("order", "序", "number", digits=0),
                            Column("node_id", "节点"),
                            Column("skill_id", "Skill"),
                            Column("status", "状态"),
                            Column("duration_ms", "耗时(ms)", "number", digits=0),
                            Column("sources", "值来源"),
                            Column("params", "输入快照"),
                        ),
                        [_trial_step_row(step) for step in record.steps],
                    ),
                    text_kinds=table_text_kinds(),
                    title="逐节点快照",
                ))
            ),
            "trace": self._described_part(describe_trace(trial.trace, now=_now())),
        }

    def _diff_segment(self, diff: Any) -> dict[str, Any]:
        """版本差异一段：未给两版 / 两版一致，都**如实**说明，不摆一张空表。"""
        if diff is None:
            return envelope_payload(ResultEnvelope.empty(
                "未指定对比的两版（`a` 与 `b`），未计算差异"
            ))
        if diff.is_empty():
            return envelope_payload(ResultEnvelope.empty(
                f"{diff.flow_id_a} 与 {diff.flow_id_b} 逐字段一致（无差异）"
            ))
        return envelope_payload(ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": f"差异 · {diff.flow_id_a} → {diff.flow_id_b}", "lines": [
                    f"新增节点：{'、'.join(diff.added_nodes) or '（无）'}",
                    f"移除节点：{'、'.join(diff.removed_nodes) or '（无）'}",
                    f"变化节点：{'、'.join(diff.changed_nodes) or '（无）'}",
                    f"新增连线：{'、'.join(diff.added_edges) or '（无）'}",
                    f"移除连线：{'、'.join(diff.removed_edges) or '（无）'}",
                    f"名称变更：{'是' if diff.name_changed else '否'}",
                    f"说明变更：{'是' if diff.description_changed else '否'}",
                    f"调度变更：{'是' if diff.schedule_changed else '否'}",
                ]},
            ]},
            text_kinds={"sections": "data"},
            title="版本差异",
        )))

    def _trial_diff_segment(self, diff: Any) -> dict[str, Any]:
        """两次试跑的逐节点输出差异（[03 §4]「历史对比」）；未给两次即如实说明。"""
        if diff is None:
            return envelope_payload(ResultEnvelope.empty(
                "未指定对比的两次试跑（`a` 与 `b`），未计算差异"
            ))
        if diff.is_empty():
            return envelope_payload(ResultEnvelope.empty(
                f"{diff.trial_id_a} 与 {diff.trial_id_b} 逐节点输出一致（无差异）"
            ))
        return envelope_payload(ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": f"差异 · {diff.trial_id_a} → {diff.trial_id_b}", "lines": [
                    f"新增节点：{'、'.join(diff.added_nodes) or '（无）'}",
                    f"消失节点：{'、'.join(diff.removed_nodes) or '（无）'}",
                    f"输出不同：{'、'.join(diff.changed_nodes) or '（无）'}",
                ]},
            ]},
            text_kinds={"sections": "data"},
            title="试跑对比",
        )))
