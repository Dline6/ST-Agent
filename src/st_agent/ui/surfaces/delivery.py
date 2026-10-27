"""触达区：日报、推送历史、配置、反馈与疲劳（[T-UI-017]）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from st_agent.contracts.identifiers import DeliveryId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l5.budget import LEVELS as SIGNAL_LEVELS
from st_agent.l5.budget import BudgetKey
from st_agent.l5.fatigue import ANSWERS as FATIGUE_ANSWERS
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _FEEDBACK_ACTIONS,
    _FEEDBACK_LABELS,
    _FEEDBACK_REASON_REQUIRED,
    _now,
    _required,
)
from st_agent.ui.surfaces.delivery_shared import (
    DELIVERY_CONFIG_SURFACE,
    _CHANNEL_LABELS,
    _FATIGUE_ANSWER_ACTION,
    _FATIGUE_ANSWER_LABELS,
    _HISTORY_LIMIT,
    _REPORT_LIST_LIMIT,
    _as_envelope,
    _bounded_limit,
    _cell_entries,
    _change_payload,
    _clock,
    _delivery_config_labels,
    _fatigue_threshold_entry,
    _history_entry,
    _l5_envelope,
    _mode_entry,
    _parse_policy_value,
    _policy_entries,
    _positive_int,
    _report_day,
    _report_sections,
    _report_time_entry,
    _resend_entry,
    _stored_report_days,
)


class DeliverySurface(SurfaceBase):
    """触达区：日报、推送历史、配置、反馈与疲劳（[T-UI-017]）。"""

    def api_delivery_report(self, *, day: str) -> dict[str, Any]:
        """每日报告页 → `report_card`（四段 + 投出态；`?day=` 缺省＝今日）。"""
        return self._call_face(
            "delivery-report", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_report_envelope(face, day),
        )

    def api_delivery_reports(self, *, limit: str) -> dict[str, Any]:
        """已生成日报清单 → `table`（`?limit=` 列出最近 N 天）。"""
        return self._call_face(
            "delivery-reports", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_reports_envelope(face, limit),
        )

    def api_delivery_history(self, *, limit: str) -> dict[str, Any]:
        """推送历史时间线 → `timeline_view`（逐条投递留痕；`?limit=` 列出最近 N 条）。"""
        return self._call_face(
            "delivery-history", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_history_envelope(face, limit),
        )

    def api_delivery_report_build(self, body: dict[str, Any]) -> dict[str, Any]:
        """生成某日报告——对同一纯函数 `build(day)` 的**显式重放**（[07 §5]）。

        日报生成本是常驻循环 `tick` 内 `due` 判定的职责；`build` 以「显式时刻 / 留痕状态」
        为唯一输入、写同一路径（`execution_log/daily_report/<day>.json`），故页面触发生成
        **不新增第二条生成逻辑**、也不会产生重复报告。
        """
        return self._call_face(
            "delivery-report-build", self.delivery, "L5 主动触达面",
            lambda face: self._build_report_envelope(face, body),
        )

    def _delivery_report_envelope(self, face: Any, day: str) -> ResultEnvelope:
        reports = self._require_handle(face, "reports", "L5 日报面")
        if isinstance(reports, ResultEnvelope):
            return reports
        target = _report_day(day)
        report = _l5_envelope(lambda: reports.stored(target))
        if isinstance(report, ResultEnvelope):
            return report
        if report is None:
            return ResultEnvelope.empty(
                f"{target.isoformat()} 尚无报告——到点由常驻循环生成；"
                "「生成」是对同一纯函数的显式重放，可立即补出"
            )
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": _report_sections(report)},
            text_kinds={"sections": "data"},
            title=f"日报 · {report.day.isoformat()}",
            as_of=report.generated_at,
        ))

    def _delivery_reports_envelope(self, face: Any, limit: str) -> ResultEnvelope:
        store = self._require_handle(face, "store", "本地存储面")
        if isinstance(store, ResultEnvelope):
            return store
        reports = self._require_handle(face, "reports", "L5 日报面")
        if isinstance(reports, ResultEnvelope):
            return reports
        cap = _bounded_limit(limit, default=_REPORT_LIST_LIMIT, cap=_REPORT_LIST_LIMIT,
                             what="条数（limit）")
        stored = _l5_envelope(lambda: _stored_report_days(store))
        if isinstance(stored, ResultEnvelope):
            return stored
        if not stored:
            return ResultEnvelope.empty(
                "尚无已生成的日报——到点由常驻循环生成，或在日报页点「生成」"
            )
        rows: list[dict[str, Any]] = []
        unreadable = 0
        for target in stored[:cap]:
            report = _l5_envelope(lambda: reports.stored(target))
            if isinstance(report, ResultEnvelope):
                return report
            if report is None:
                unreadable += 1
                continue
            channel = str(getattr(report, "delivered_channel", "") or "")
            rows.append({
                "day": report.day.isoformat(),
                "generated_at": _clock(report.generated_at),
                "content": "无内容（今天没有需要打扰你的事）" if report.empty else "有内容",
                "delivered": _CHANNEL_LABELS.get(channel, channel) if channel else "（未投出）",
            })
        title = f"已生成的日报 · 共 {len(stored)} 天"
        if len(stored) > cap:
            title += f"，显示最近 {cap} 天"
        if unreadable:
            title += f"（另 {unreadable} 天留痕无法解析，未计入）"
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("day", "日期"),
                    Column("generated_at", "生成时刻"),
                    Column("content", "内容"),
                    Column("delivered", "投出"),
                ),
                rows,
            ),
            text_kinds=table_text_kinds(),
            title=title,
        ))

    def _delivery_history_envelope(self, face: Any, limit: str) -> ResultEnvelope:
        orchestrator = self._require_handle(face, "delivery", "L5 投递编排面")
        if isinstance(orchestrator, ResultEnvelope):
            return orchestrator
        cap = _bounded_limit(limit, default=_HISTORY_LIMIT, cap=_HISTORY_LIMIT,
                             what="条数（limit）")
        ledger = _l5_envelope(lambda: tuple(orchestrator.ledger()))
        if isinstance(ledger, ResultEnvelope):
            return ledger
        if not ledger:
            return ResultEnvelope.empty("尚无推送留痕——信号经 L5 投出后在此逐条呈现")
        ordered = sorted(ledger, key=lambda record: record.created_at, reverse=True)
        title = f"推送历史 · 共 {len(ordered)} 条"
        if len(ordered) > cap:
            title += f"，显示最近 {cap} 条"
        return ResultEnvelope.ok(self._gated_description(
            "timeline_view",
            slots={
                "entries": [_history_entry(record) for record in ordered[:cap]],
                "labels": {
                    "kinds": dict(_CHANNEL_LABELS),
                    "states": {
                        "ok": "已投出", "empty": "已汇总",
                        "delayed": "未确认", "failed": "失败",
                    },
                },
                "empty_hint": "尚无推送留痕",
            },
            text_kinds={"entries": "data", "labels": "generated", "empty_hint": "generated"},
            title=title,
        ))

    def _build_report_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        reports = self._require_handle(face, "reports", "L5 日报面")
        if isinstance(reports, ResultEnvelope):
            return reports
        target = _report_day(str(body.get("day") or ""))
        report = _l5_envelope(lambda: reports.build(target))
        if isinstance(report, ResultEnvelope):
            return report
        return ResultEnvelope.ok({
            "day": report.day.isoformat(),
            "empty": report.empty,
            "sections": len(report.sections),
        })

    def api_delivery_config(self) -> dict[str, Any]:
        """触达配置面板 → `setting_panel`（面键 `delivery-config`）。

        预算格位 / 渠道链的取值是**对象 / 列表** ⇒ 写面归 L5 自有 API（[01 §7]「逐条目一次
        落值」）；日报时刻与疲劳阈值是**标量**（本可用统一落值面），本页把它们也走 L5 自有
        写面——同一面板的落点只此一处，不为标量另开一条注册表路径（[`T-UI-017.2`] 假设 A2）。
        """
        return self._call_face(
            "delivery-config", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_config_envelope(face),
        )

    def api_delivery_config_apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """触达配置落值——按 `action` 分派到 L5 自有写面（**留痕经 [01 §7] 的 `ChangeRecord`**）。"""
        return self._call_face(
            "delivery-config-apply", self.delivery, "L5 主动触达面",
            lambda face: _as_envelope(self._apply_delivery_config(face, body)),
        )

    def _delivery_config_envelope(self, face: Any) -> ResultEnvelope:
        for name, label in (("budget", "注意力预算面"), ("policies", "渠道偏好面"),
                            ("reports", "L5 日报面"), ("fatigue", "疲劳监控面")):
            missing = self._require_handle(face, name, label)
            if isinstance(missing, ResultEnvelope):
                return missing
        budget_map = _l5_envelope(lambda: face.budget.map())
        if isinstance(budget_map, ResultEnvelope):
            return budget_map
        policies = _l5_envelope(lambda: tuple(face.policies.map()))
        if isinstance(policies, ResultEnvelope):
            return policies
        report_time = _l5_envelope(lambda: face.reports.report_time())
        if isinstance(report_time, ResultEnvelope):
            return report_time
        threshold = _l5_envelope(lambda: face.fatigue.threshold())
        if isinstance(threshold, ResultEnvelope):
            return threshold
        resend = _l5_envelope(lambda: face.policies.resend_on_reconnect)
        if isinstance(resend, ResultEnvelope):
            return resend
        entries = [
            _mode_entry(budget_map),
            *_cell_entries(budget_map),
            *_policy_entries(policies),
            _resend_entry(bool(resend)),
            _report_time_entry(report_time),
            _fatigue_threshold_entry(int(threshold)),
        ]
        return ResultEnvelope.ok(self._gated_description(
            "setting_panel",
            slots={
                "entries": entries,
                "labels": _delivery_config_labels(),
                "surface": DELIVERY_CONFIG_SURFACE,
            },
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title="触达配置",
        ))

    def _apply_delivery_config(self, face: Any, body: dict[str, Any]) -> Any:
        action = str(body.get("action") or "")
        if action == "switch-mode":
            budget = self._require_handle(face, "budget", "注意力预算面")
            if isinstance(budget, ResultEnvelope):
                return budget
            mode = str(_required(body, "value"))
            got = _l5_envelope(lambda: budget.switch_mode(mode))
            return got if isinstance(got, ResultEnvelope) else _change_payload(got)
        if action == "set-cell":
            budget = self._require_handle(face, "budget", "注意力预算面")
            if isinstance(budget, ResultEnvelope):
                return budget
            key = BudgetKey(
                mode=str(_required(body, "mode")),
                slot_id=str(_required(body, "slot_id")),
                content_type=str(_required(body, "content_type")),
            )
            field = str(_required(body, "field"))
            value = str(_required(body, "value"))
            current = _l5_envelope(lambda: budget.cell(key))
            if isinstance(current, ResultEnvelope):
                return current
            if field == "levels":
                cell: dict[str, Any] = {
                    "allowed_levels": [lv.strip() for lv in value.split(",") if lv.strip()],
                    "max_per_slot": current.max_per_slot,
                }
            elif field == "max":
                cell = {
                    "allowed_levels": [lv for lv in SIGNAL_LEVELS if lv in current.allowed_levels],
                    "max_per_slot": _positive_int(value, "每格上限"),
                }
            else:
                raise ValueError(f"未知的格位字段：{field!r}（只认 levels / max）")
            got = _l5_envelope(lambda: budget.set_cell(key, cell))
            return got if isinstance(got, ResultEnvelope) else _change_payload(got)
        if action == "set-policy":
            policies = self._require_handle(face, "policies", "渠道偏好面")
            if isinstance(policies, ResultEnvelope):
                return policies
            level = str(_required(body, "level"))
            channels, waits = _parse_policy_value(str(_required(body, "value")))
            before = len(policies.changes())
            got = _l5_envelope(
                lambda: policies.set_policy(level, channels, waits_minutes=waits)
            )
            if isinstance(got, ResultEnvelope):
                return got
            # `set_policy` 回的是策略本体（非 `ChangeRecord`）⇒ 用留痕条数判「是否真落值」；
            # 取值未变时 L5 不写盘、不留痕（[01 §7]）。
            return {"changed": len(policies.changes()) > before}
        if action in ("resend-on", "resend-off"):
            policies = self._require_handle(face, "policies", "渠道偏好面")
            if isinstance(policies, ResultEnvelope):
                return policies
            got = _l5_envelope(
                lambda: policies.set_resend_on_reconnect(action == "resend-on")
            )
            return got if isinstance(got, ResultEnvelope) else _change_payload(got)
        if action == "set-report-time":
            reports = self._require_handle(face, "reports", "L5 日报面")
            if isinstance(reports, ResultEnvelope):
                return reports
            got = _l5_envelope(lambda: reports.set_time(str(_required(body, "value"))))
            return got if isinstance(got, ResultEnvelope) else _change_payload(got)
        if action == "set-fatigue-threshold":
            fatigue = self._require_handle(face, "fatigue", "疲劳监控面")
            if isinstance(fatigue, ResultEnvelope):
                return fatigue
            value = _positive_int(str(_required(body, "value")), "阈值")
            got = _l5_envelope(lambda: fatigue.set_threshold(value))
            return got if isinstance(got, ResultEnvelope) else _change_payload(got)
        raise ValueError(
            f"未知的触达配置动作：{action!r}"
            "（只认 switch-mode / set-cell / set-policy / resend-on / resend-off / "
            "set-report-time / set-fatigue-threshold）"
        )

    def api_delivery_feedback(self, *, delivery_id: str) -> dict[str, Any]:
        """推送疲劳反馈对话框 → `feedback_capture`（五动作 + 否定类原因入口）。

        写面是**既有** `POST /api/reflection/feedback`（渲染件的固定回环路由，[01 §12]）——
        本叶只把「可反馈的对象」（某条推送留痕的 `delivery_id`）喂给组件，**不另造写通道**。
        """
        return self._call_face(
            "delivery-feedback", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_feedback_envelope(face, delivery_id),
        )

    def api_delivery_fatigue(self) -> dict[str, Any]:
        """疲劳面 → `report_card`（逐类计数 / 阈值 / 静音 / 最近答复 + 待答复询问）。"""
        return self._call_face(
            "delivery-fatigue", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_fatigue_envelope(face),
        )

    def api_delivery_fatigue_answer(self, body: dict[str, Any]) -> dict[str, Any]:
        """答复疲劳调整询问（减少 / 关闭 / 保持）。

        经 **L3 反馈采集面**铸 `FeedbackEvent`（`feedback_id` 产生方＝交互层，[01 §1]）并以
        `context.fatigue_choice` 发布 `FeedbackRecorded`，由 L5 `FatigueMonitor.consume` 落值
        ——L5 与 L6 同经总线收到该反馈（[07 §7]）。**不**直调 `FatigueMonitor.answer`（那是
        无 UI 时的第二通道，见 [`T-L5-003.3`] 假设 A1）。
        """
        return self._call_face(
            "delivery-fatigue-answer", self.delivery, "L5 主动触达面",
            lambda face: self._answer_fatigue(face, body),
        )

    def _delivery_feedback_envelope(self, face: Any, delivery_id: str) -> ResultEnvelope:
        if not delivery_id:
            return ResultEnvelope.validation_failed("缺少推送标识（delivery）")
        try:
            DeliveryId.of(delivery_id)
        except ValueError as exc:
            return ResultEnvelope.validation_failed(f"推送标识不合 01 §1 形态：{exc}")
        orchestrator = getattr(face, "delivery", None)
        target_title = f"对推送的反馈 · {delivery_id}"
        if orchestrator is not None:
            record = _l5_envelope(lambda: orchestrator.get(delivery_id))
            if isinstance(record, ResultEnvelope):
                return record
            if record is None:
                return ResultEnvelope.unavailable(
                    f"投递留痕里没有 {delivery_id}——标识形态合法但无此留痕（不是请求不合契约）",
                    last_updated_at=_now(),
                )
            target_title = f"对推送的反馈 · {record.title}"
        return ResultEnvelope.ok(self._gated_description(
            "feedback_capture",
            slots={
                "target": {"kind": "delivery", "ref": delivery_id},
                "actions": list(_FEEDBACK_ACTIONS),
                "reason_required": list(_FEEDBACK_REASON_REQUIRED),
                "labels": dict(_FEEDBACK_LABELS),
                "prompt": "否定类反馈必须说明原因",
            },
            text_kinds={
                "target": "data", "actions": "data", "reason_required": "data",
                "labels": "generated", "prompt": "generated",
            },
            title=target_title,
        ))

    def _delivery_fatigue_envelope(self, face: Any) -> ResultEnvelope:
        fatigue = self._require_handle(face, "fatigue", "疲劳监控面")
        if isinstance(fatigue, ResultEnvelope):
            return fatigue
        states = _l5_envelope(lambda: tuple(fatigue.counts()))
        if isinstance(states, ResultEnvelope):
            return states
        threshold = _l5_envelope(lambda: fatigue.threshold())
        if isinstance(threshold, ResultEnvelope):
            return threshold
        if not states:
            return ResultEnvelope.empty(
                "尚无疲劳计数——某类推送被连续忽略达阈值、或用户答复过之后才会在此列出"
            )
        lines: list[str] = []
        for state in states:
            bits = [f"连续忽略 {state.ignored_streak} 次（阈值 {state.threshold}）",
                    "已静音（不再单独触达，改汇总进日报）" if state.muted else "未静音"]
            if state.last_answer:
                bits.append(
                    f"最近答复：{_FATIGUE_ANSWER_LABELS.get(state.last_answer, state.last_answer)}"
                )
            if state.answered_at:
                bits.append(f"答复于 {_clock(state.answered_at)}")
            if state.as_of:
                bits.append(f"计数截至 {_clock(state.as_of)}")
            if state.asked_delivery_id:
                bits.append("有未答复的调整询问（见「待答复的询问」表）")
            lines.append(f"{state.dedup_key}：{' · '.join(bits)}")
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "阈值", "lines": [f"连续忽略 {threshold} 次即发问（可改）"]},
                {"title": "逐类疲劳状态", "lines": lines},
                {"title": "可答复", "lines": [
                    f"{choice}：{label}" for choice, label in _FATIGUE_ANSWER_LABELS.items()
                ]},
            ]},
            text_kinds={"sections": "data"},
            title="推送疲劳",
        ))

    def api_delivery_inquiries(self) -> dict[str, Any]:
        """待答复的调整询问 → `table`（逐条给 `delivery_id`——页面据此就地造答复按钮）。

        **不调 `sweep`**：那是「发问」的动作（有副作用），本端点是**只读**——只列
        L5 已发出且尚未答复的询问（判据是 `asked_delivery_id` 非空，[`T-UI-017.3`] 假设 A5）。
        """
        return self._call_face(
            "delivery-inquiries", self.delivery, "L5 主动触达面",
            lambda face: self._delivery_inquiries_envelope(face),
        )

    def _delivery_inquiries_envelope(self, face: Any) -> ResultEnvelope:
        fatigue = self._require_handle(face, "fatigue", "疲劳监控面")
        if isinstance(fatigue, ResultEnvelope):
            return fatigue
        states = _l5_envelope(lambda: tuple(fatigue.counts()))
        if isinstance(states, ResultEnvelope):
            return states
        waiting = [state for state in states if state.asked_delivery_id]
        if not waiting:
            return ResultEnvelope.empty(
                "当前没有待答复的调整询问——某类被连续忽略达阈值时 L5 会发问"
            )
        orchestrator = getattr(face, "delivery", None)
        rows: list[dict[str, Any]] = []
        for state in waiting:
            question = ""
            if orchestrator is not None:
                record = _l5_envelope(lambda: orchestrator.get(state.asked_delivery_id))
                if isinstance(record, ResultEnvelope):
                    return record
                if record is not None:
                    question = str(getattr(record, "body", "") or getattr(record, "title", ""))
            rows.append({
                "delivery_id": state.asked_delivery_id,
                "dedup_key": state.dedup_key,
                "streak": state.ignored_streak,
                "question": question or "（询问留痕不可读；答复仍可按推送标识提交）",
            })
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("delivery_id", "推送标识"),
                    Column("dedup_key", "类"),
                    Column("streak", "连续忽略", "number", digits=0),
                    Column("question", "问句"),
                ),
                rows,
            ),
            text_kinds=table_text_kinds(),
            title=f"待答复的调整询问 · {len(rows)} 条",
        ))

    def _answer_fatigue(self, face: Any, body: dict[str, Any]) -> Any:
        delivery_id = str(_required(body, "delivery_id"))
        choice = str(_required(body, "choice"))
        if choice not in FATIGUE_ANSWERS:
            raise ValueError(
                f"答复须为 {' / '.join(FATIGUE_ANSWERS)} 之一，收到 {choice!r}"
            )
        fatigue = self._require_handle(face, "fatigue", "疲劳监控面")
        if isinstance(fatigue, ResultEnvelope):
            return fatigue
        feedback = self._require_handle(face, "feedback", "交互层反馈采集面")
        if isinstance(feedback, ResultEnvelope):
            return feedback
        states = _l5_envelope(lambda: tuple(fatigue.counts()))
        if isinstance(states, ResultEnvelope):
            return states
        waiting = {state.asked_delivery_id for state in states if state.asked_delivery_id}
        if delivery_id not in waiting:
            raise ValueError(
                f"{delivery_id!r} 不是待答复的调整询问——疲劳面没有以它发问且尚未答复的类"
                "（答复别的推送不属本面，且不会落进 L5 的答复面）"
            )
        result = feedback(
            target={"kind": "delivery", "ref": delivery_id},
            action=_FATIGUE_ANSWER_ACTION,
            context={"fatigue_choice": choice},
        )
        if not isinstance(result, ResultEnvelope):
            result = ResultEnvelope.ok(result)
        if result.status != "ok":
            return result
        delivery = getattr(result.data, "delivery", None)
        delivered = bool(getattr(delivery, "delivered", False))
        note = str(getattr(delivery, "note", "") or "")
        label = _FATIGUE_ANSWER_LABELS.get(choice, choice)
        return ResultEnvelope.ok({
            "choice": choice,
            "delivered": delivered,
            "applied": f"答复「{label}」已送达 L5 与 L6" if delivered
                       else f"答复未送达：{note or '事件接收面未接通（装配归组合根）'}",
        })
