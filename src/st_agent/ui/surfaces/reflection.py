"""反思中心与 Studio 画布（[T-UI-004.2] / [T-UI-005.1]）。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _FEEDBACK_ACTIONS,
    _FEEDBACK_LABELS,
    _FEEDBACK_REASON_REQUIRED,
    _now,
    _required,
)
from st_agent.ui.surfaces.studio import (
    _STUDIO_LABELS,
    _studio_envelope,
)


def _compact(value: Any) -> Any:
    """表格单元格的取值：容器压成一行 JSON、其余原样（**不臆造摘要**）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _anchor_text(item: Any) -> str:
    """一个留痕锚点的展示文本（证据引用是 ``{kind, ref}`` 对象，其余为 ID 字符串）。"""
    if isinstance(item, Mapping):
        return f"{item.get('kind')}:{item.get('ref')}"
    return str(item)


_STUDIO_CANVAS_TITLE = "Studio 画布"
"""画布描述标题（生成文案，过 [01 §6] 执行点 2）。"""


_EXPERIMENT_COLUMNS = (
    Column("hypothesis", "假设"),
    Column("scope", "范围"),
    Column("sample", "样本"),
    Column("result", "结果"),
    Column("decision", "决策"),
    Column("status", "状态"),
)
"""A/B 实验日志的表（[08 §4]）。"""


_TRAINING_COLUMNS = (
    Column("training_id", "训练 id"),
    Column("correction", "用户的修正（原话）"),
    Column("restatement", "复述"),
    Column("pattern", "模式"),
    Column("confirmed", "已确认"),
    Column("created_at", "时刻"),
)
"""训练对话留痕的表（[08 §1]）；`correction` 是**用户原话**，整行按 `data` 原样呈现（[D-053]）。"""


def _training_row(item: Mapping[str, Any]) -> dict[str, Any]:
    """训练留痕的一行（`session` 段可能缺席，逐字段取值、不抛）。"""
    session = item.get("session") or {}
    return {
        "training_id": session.get("training_id"),
        "correction": session.get("correction"),
        "restatement": session.get("restatement"),
        "pattern": _compact(session.get("pattern")),
        "confirmed": "是" if session.get("confirmed") else "否",
        "created_at": session.get("created_at"),
    }


def _as_datetime(value: Any) -> datetime | None:
    """把 ISO 串还原成**带时区**的 ``datetime``（[01 §8]：`as_of` 须带时区语义）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class ReflectionSurface(SurfaceBase):
    """反思中心与 Studio 画布（[T-UI-004.2] / [T-UI-005.1]）。"""

    def api_reflection_report(self, *, week: str = "") -> dict[str, Any]:
        """每周反思报告：四段式 `report_card`；两种空态**分开判定、皆不硬凑**（[08 §2]）。"""
        return self._call_face(
            "reflection-report", self.reflection, "L6 反思演进面",
            lambda face: self._report_envelope(face, week),
        )

    def api_reflection_trace(self, *, week: str = "") -> dict[str, Any]:
        """报告的**留痕锚点**（`trace_timeline`）——兑现「报告生成走完整 Trace」（[08 §2]）。"""
        return self._call_face(
            "reflection-trace", self.reflection, "L6 反思演进面",
            lambda face: self._trace_envelope(face, week),
        )

    def api_reflection_feedback_capture(self) -> dict[str, Any]:
        """反馈采集组件：对**本周这次触达**采集反馈（目标取自最近一期报告的投递留痕）。

        写面在 :meth:`api_reflection_feedback`；本端点只把「可反馈的对象」喂给组件——
        没有可反馈对象时给 `empty`，**不硬凑**一个假目标。
        """
        return self._call_face(
            "reflection-feedback-capture", self.reflection, "L6 反思演进面",
            lambda face: self._feedback_envelope(face),
        )

    def _feedback_envelope(self, face: Any) -> ResultEnvelope:
        report = self._latest_report(face, "")
        if isinstance(report, ResultEnvelope):
            return report
        deliveries = (report.get("trace") or {}).get("delivery_ids") or []
        if not deliveries:
            return ResultEnvelope.empty("本周没有可反馈的触达（报告无投递留痕）")
        return ResultEnvelope.ok(
            self._gated_description(
                "feedback_capture",
                slots={
                    "target": {"kind": "delivery", "ref": str(deliveries[-1])},
                    "actions": list(_FEEDBACK_ACTIONS),
                    "reason_required": list(_FEEDBACK_REASON_REQUIRED),
                    "labels": dict(_FEEDBACK_LABELS),
                    "prompt": "否定类反馈必须说明原因",
                },
                text_kinds={
                    "target": "data",
                    "actions": "data",
                    "reason_required": "data",
                    "labels": "generated",
                    "prompt": "generated",
                },
                title=f"对本周触达的反馈 · {report.get('week')}",
            )
        )

    def api_reflection_proposals(self) -> dict[str, Any]:
        """待处置的建议：待批准队列 + 最近一期周报的候选 + 主动提案（`proposal_card`）。"""
        return self._call_face(
            "reflection-proposals", self.reflection, "L6 反思演进面",
            lambda face: self._proposals_envelope(face),
        )

    def api_reflection_feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        """反馈采集（写面）——**产生方是交互层**，本层只把请求转给注入的采集面（[01 §1]）。

        `feedback_id` 由采集面铸造、`rejected` 的 `reason` 由它校验必填；本层**不预判、
        也不落盘**（[05 §9]：L3 只产事件、反馈池归 L6）。
        """
        return self._call_face(
            "reflection-feedback", self.reflection, "L6 反思演进面",
            lambda face: face.record_feedback(
                target=_required(body, "target"),
                action=_required(body, "action"),
                reason=body.get("reason"),
                context=body.get("context"),
            ),
        )

    def api_reflection_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """逐条处置提案（接受 / 否决 / 延后）——**唯一通道是变更流**（[08 §5] / §7 红线）。"""
        return self._call_face(
            "reflection-decide", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                face.decide(
                    action=_required(body, "action"),
                    proposal=body.get("proposal"),
                    pending_id=str(body.get("pending_id") or ""),
                )
            ),
        )

    def api_reflection_studio_handoff(self, body: dict[str, Any]) -> dict[str, Any]:
        """主动提案的 Skill 草稿**交 Studio**（落画布）——[08 §4]「衔接 story-06」。

        经组合根把 L6 的 `SkillDraft` 翻成 L1 的 `WorkflowDraft` 并转交 Studio 接收面；
        表现层**不 import** 各层类型。子面未接线 → `unavailable` + 点名；输入类失败 →
        `validation_failed`。
        """
        return self._call_face(
            "reflection-studio-handoff", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(
                face.studio_handoff(str(_required(body, "proposal_id"))), "Studio",
            ),
        )

    def api_reflection_studio_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """处置一条**已交 Studio** 的提案：接受（落 v1.0）/ 否决——两个动作都委托 L1。"""
        return self._call_face(
            "reflection-studio-decide", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(
                face.studio_decide(
                    proposal_id=str(_required(body, "proposal_id")),
                    action=str(_required(body, "action")),
                ),
                "Studio",
            ),
        )

    def api_studio_sessions(self) -> dict[str, Any]:
        """已交 Studio 未处置的会话一览——画布页左栏（`{available, sessions}`）。"""
        return self._call_face(
            "studio-sessions", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(face.studio_sessions(), "Studio"),
        )

    def api_studio_skills(self) -> dict[str, Any]:
        """画布页「加节点」的可选 Skill 清单（01 §2 描述体的投影；不新造第二份登记）。"""
        return self._call_face(
            "studio-skills", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(face.studio_skills(), "Studio"),
        )

    def api_studio_canvas(self, *, proposal_id: str) -> dict[str, Any]:
        """某会话的画布——出站为 `studio_canvas` 描述（过必填槽 + 中性化两道闸）。"""
        return self._call_face(
            "studio-canvas", self.reflection, "L6 反思演进面",
            lambda face: self._studio_canvas_envelope(face.studio_canvas(proposal_id)),
        )

    def api_studio_edit(self, body: dict[str, Any]) -> dict[str, Any]:
        """一次画布结构编辑（增删节点 / 连线断线 / 建分组解散）——回 `studio_canvas` 描述，
        附本次 `edit` 小结（`applied` / `message` / `blocked_by`）与**编辑后的画布**。"""
        return self._call_face(
            "studio-edit", self.reflection, "L6 反思演进面",
            lambda face: self._studio_canvas_envelope(
                face.studio_edit(
                    proposal_id=str(_required(body, "proposal_id")),
                    op=str(_required(body, "op")),
                    args=body.get("args"),
                )
            ),
        )

    def _studio_canvas_envelope(self, payload: dict[str, Any]) -> Any:
        """画布载荷 → 信封：子面未接线 ⇒ `unavailable` + 点名；否则出 `studio_canvas` 描述。

        `canvas` 槽按 **data** 承载（节点 / 连线 / 分组是**数据展示**，含用户填的标识与
        绑定值，不整串复检）；`labels` 槽是本层生成文案，过 [01 §6] 执行点 2。
        """
        if not payload.get("available"):
            return ResultEnvelope.unavailable(
                payload.get("reason") or "未接入 Studio 草稿接收面（08 §4）",
                last_updated_at=_now(),
            )
        return ResultEnvelope.ok(
            self._gated_description(
                "studio_canvas",
                slots={"canvas": payload["canvas"], "labels": dict(_STUDIO_LABELS)},
                text_kinds={"canvas": "data", "labels": "generated"},
                title=_STUDIO_CANVAS_TITLE,
            )
        )

    def api_reflection_experiments(self) -> dict[str, Any]:
        """A/B 实验日志（`table`）——五字段 + 状态与决策；判定**保守**，不产 p 值（[08 §4]）。"""
        return self._call_face(
            "reflection-experiments", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                self._gated_description(
                    "table",
                    slots=table_slots(
                        _EXPERIMENT_COLUMNS,
                        [
                            {
                                "hypothesis": item.get("hypothesis"),
                                "scope": item.get("scope"),
                                "sample": _compact(item.get("sample")),
                                "result": _compact(item.get("result")),
                                "decision": item.get("decision"),
                                "status": item.get("status"),
                            }
                            for item in face.experiments()
                        ],
                    ),
                    text_kinds=table_text_kinds(),
                    title="A/B 实验日志",
                )
            ),
        )

    def api_reflection_training(self) -> dict[str, Any]:
        """训练会话与待回访项（`table`）——用户原话按 `data` **原样呈现**（[D-053]）。"""
        return self._call_face(
            "reflection-training", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                self._gated_description(
                    "table",
                    slots=table_slots(_TRAINING_COLUMNS, map(_training_row, face.trainings())),
                    text_kinds=table_text_kinds(),
                    title="训练对话留痕",
                )
            ),
        )

    def _report_envelope(self, face: Any, week: str) -> ResultEnvelope:
        """周报信封：无报告 ⇒ `empty`；**无触达 / 数据不足两态由 L6 的正文原话承载**
        （本层不自行推断——按反馈数为 0 反推「无触达」正是 [08 §4] 明禁的那种误判）。"""
        report = self._latest_report(face, week)
        if isinstance(report, ResultEnvelope):
            return report
        if report.get("empty") or report.get("insufficient"):
            return ResultEnvelope.empty(str(report.get("body") or ""))
        sections = [
            {
                "title": section.get("label"),
                "lines": [line.get("text") for line in section.get("lines") or []],
            }
            for section in report.get("sections") or []
        ]
        return ResultEnvelope.ok(
            self._gated_description(
                "report_card",
                slots={"sections": sections},
                text_kinds={"sections": "data"},
                title=f"反思报告 · {report.get('week')}",
                as_of=_as_datetime(report.get("generated_at")),
            )
        )

    def _trace_envelope(self, face: Any, week: str) -> ResultEnvelope:
        """留痕锚点信封——每一环 = 一类留痕（投递 / 信号 / 反馈 / 记忆 / 证据 / 推理链）。"""
        report = self._latest_report(face, week)
        if isinstance(report, ResultEnvelope):
            return report
        trace = report.get("trace") or {}
        window = f"{report.get('period_start')}–{report.get('period_end')}"
        plan = (
            ("投递留痕", trace.get("delivery_ids") or []),
            ("信号留痕", trace.get("signal_ids") or []),
            ("反馈留痕", trace.get("feedback_ids") or []),
            ("记忆读取", trace.get("memory_node_ids") or []),
            ("证据引用", trace.get("evidence_refs") or []),
            ("推理链引用", trace.get("trace_ids") or []),
        )
        steps = [
            {
                "step_type": name,
                "ref": "、".join(_anchor_text(item) for item in anchors) or "—",
                "input_digest": f"取材窗口 {window}",
                "output_digest": f"{len(anchors)} 条",
                "duration_ms": 0,
                "timestamp": report.get("generated_at"),
                "degraded": not anchors,
            }
            for name, anchors in plan
        ]
        return ResultEnvelope.ok(
            self._gated_description(
                "trace_timeline",
                slots={"steps": steps},
                text_kinds={"steps": "generated"},
                title=(
                    f"留痕锚点 · {report.get('week')}"
                    "（报告生成不逐环计时，耗时恒记 0；degraded 标记该类留痕为空）"
                ),
            )
        )

    def _proposals_envelope(self, face: Any) -> ResultEnvelope:
        """建议信封：待批准队列（三动作）+ 周报候选（可接受）+ 主动提案（**信息面**）。"""
        items: list[dict[str, Any]] = []
        for pending in face.pending():
            if pending.get("status") not in ("pending", "deferred"):
                continue
            proposal = pending.get("proposal") or {}
            items.append({
                "kind": "change",
                "pending_id": pending.get("pending_id"),
                "status": pending.get("status"),
                "deferrals": pending.get("deferrals", 0),
                "config_id": proposal.get("config_id"),
                "current": proposal.get("current"),
                "suggested": proposal.get("suggested"),
                "reason": proposal.get("reason"),
                "trace_ref": proposal.get("trace_ref"),
                "source": proposal.get("source"),
                "actions": ["accept", "reject", "defer"],
            })
        report = self._latest_report(face, "")
        if not isinstance(report, ResultEnvelope):
            for candidate in report.get("proposals") or []:
                items.append({
                    "kind": "change",
                    "pending_id": "",
                    "config_id": candidate.get("config_id"),
                    "current": candidate.get("current"),
                    "suggested": candidate.get("suggested"),
                    "reason": candidate.get("reason"),
                    "trace_ref": candidate.get("trace_ref"),
                    "source": f"周报 {report.get('week')}",
                    "actions": ["accept"],
                })
        for proposal in face.proposals():
            draft = proposal.get("draft") or {}
            items.append({
                "kind": "skill",
                "proposal_id": proposal.get("proposal_id"),
                "key": proposal.get("key"),
                "count": proposal.get("count"),
                "reason": proposal.get("reason"),
                "sample": proposal.get("sample"),
                "draft": {
                    "name": draft.get("name"),
                    "description": draft.get("description"),
                    "nodes": len(draft.get("nodes") or []),
                    "queued": not proposal.get("released_week"),
                },
                "actions": ["studio"],
                "note": "交 Studio 落画布后可接受 / 否决；画布微调待 Studio 页面（08 §4）",
            })
        return ResultEnvelope.ok(
            self._gated_description(
                "proposal_card",
                slots={
                    "proposals": items,
                    "labels": {"accept": "接受", "reject": "否决", "defer": "延后",
                               "studio": "交 Studio"},
                },
                text_kinds={"proposals": "data", "labels": "generated"},
                title="建议与提案",
            )
        )

    def _latest_report(self, face: Any, week: str) -> Any:
        """取目标周的报告；无周可读 / 尚未生成 ⇒ 一条 `empty` 信封（**不是**空报告）。"""
        weeks = face.report_weeks()
        target = week or (weeks[-1] if weeks else "")
        if not target:
            return ResultEnvelope.empty("尚无任何一期反思报告（到点后由常驻循环生成）")
        report = face.report(target)
        if report is None:
            return ResultEnvelope.empty(f"{target} 的反思报告尚未生成")
        return report
