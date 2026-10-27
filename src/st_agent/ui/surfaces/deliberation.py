"""推理区：视角阵容、编排触发、进度与决策留痕。"""

from __future__ import annotations

import json
import uuid
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.l3.render.describe import STANCE_LABELS, describe_divergence_map, describe_trace
from st_agent.l4.errors import BuiltinLensError, LensNotFoundError
from st_agent.ui.envelope import envelope_payload

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _CONFIDENCE_LABELS,
    _LOG,
    _id_list,
    _iso_day,
    _mapping,
    _now,
    _required,
    _skill_bundle,
)


_DELIBERATION_MODE_LABELS: dict[str, str] = {
    "quick": "快速模式",
    "deep": "深度模式",
}
"""两种分析模式的中文名（[06 §2.1]：快速 ＝ 核心少数视角；深度 ＝ 全部启用视角）。"""


LENS_SURFACE = "deliberation-lens"
"""视角阵容面板的面键（`setting_panel` 的 `surface` 槽）。

渲染件据此在**固定表**里解析回环路由——描述**不接受** URL / 方法（[01 §12]「动作不进描述」）。
"""


_LENS_ACTION_LABELS: dict[str, str] = {
    "enable": "启用",
    "disable": "停用",
    "remove": "删除",
}
"""阵容面板的动作标签（生成文案槽 `labels`，过 [01 §6]）。"""


_LENS_KIND_LABELS: dict[str, str] = {
    "builtin": "官方预置",
    "custom": "自定义",
}
"""视角来源的中文名（[06 §1] 的 `kind`；系统文案）。"""


class DeliberationSurface(SurfaceBase):
    """推理区：视角阵容、编排触发、进度与决策留痕。"""

    def api_deliberation_analyze(self, body: dict[str, Any]) -> dict[str, Any]:
        """触发一次编排全链（编排 → 对照 → 视图投影），回**分多段**载荷。

        `reply` 是编排信封（六态如实，含主题空 / 模式非法 / 未注入执行面 / 空阵容各态）；
        `progress` 是**逐视角进度面**（`report_card`，非 `ok` 视角显式原因，**并列不合并**）。
        `divergence` / `traces` 由同批的 [`.2`] / [`.3`] 填充（本叶先占键，读取方按具名键取）。

        **不臆造**：面缺席即 `unavailable` + 点名；`topic` 缺失即 `validation_failed`——
        两者都在**编排之前**判定（[06 §2.1] 的模式校验在 L4 侧、同样先于编排）。
        """
        def _fail(envelope: ResultEnvelope) -> dict[str, Any]:
            return {"reply": envelope_payload(envelope), "progress": None,
                    "divergence": None, "lenses": []}

        face = self.deliberation
        if face is None or getattr(face, "analyze", None) is None:
            return _fail(ResultEnvelope.unavailable(
                "未接入多视角编排面（analyze 去向），该端点不可用（装配归组合根）",
                last_updated_at=_now(),
            ))
        try:
            topic = _required(body, "topic")
        except ValueError as exc:
            return _fail(ResultEnvelope.validation_failed(str(exc)))
        mode = str(body.get("mode") or "")
        try:
            outcome = face.run_analysis(topic=str(topic), mode=mode)
        except (ValueError, KeyError) as exc:
            _LOG.info("编排请求被拒（%s）：%s", type(exc).__name__, exc)
            return _fail(ResultEnvelope.validation_failed(str(exc) or "编排请求不合契约"))
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/deliberation-analyze-{uuid.uuid4().hex[:12]}"
            _LOG.exception("编排面未预期失败（log_ref=%s）：%s", log_ref, exc)
            return _fail(ResultEnvelope.failed("编排未预期失败，详见服务端日志", log_ref=log_ref))

        envelope = getattr(outcome, "envelope", None)
        if not isinstance(envelope, ResultEnvelope):
            return _fail(ResultEnvelope.failed(
                "编排面返回非法结构（缺 ResultEnvelope）", log_ref="ui/deliberation-analyze",
            ))
        payload: dict[str, Any] = {
            "reply": envelope_payload(envelope), "progress": None,
            "divergence": None, "lenses": [],
        }
        if envelope.status == "ok":  # 非 ok 时无「逐视角」可言：各段留空，由 reply 的 reason 承载
            try:
                payload["progress"] = envelope_payload(
                    ResultEnvelope.ok(self._deliberation_progress_description(face, outcome))
                )
            except ValueError as exc:  # 描述不过闸（缺槽 / 中性化）——阻断渲染并明说
                payload["progress"] = envelope_payload(
                    ResultEnvelope.validation_failed(str(exc))
                )
            payload["divergence"] = self._described_part(
                describe_divergence_map(getattr(outcome, "view", None), now=_now())
            )
            payload["lenses"] = self._deliberation_lens_parts(face, outcome)
        return payload

    def _deliberation_lens_parts(self, face: Any, outcome: Any) -> list[dict[str, Any]]:
        """逐视角段：`{lens_id, trace_id, opinion, trace}`（视角详情与追问的取值面）。

        观点卡与链**同出**——两者都随本次响应内联（[D-112] ② 无会话态），页面上按 `trace_id`
        对位（矩阵行的锚点即此键，[06 §5]）。任一缺失给 `None`，渲染面据此显式标注，不伪造。
        """
        result = getattr(outcome, "result", None)
        by_trace = {
            getattr(getattr(trace, "trace_id", None), "value", None): trace
            for trace in tuple(getattr(outcome, "traces", ()) or ())
        }
        parts: list[dict[str, Any]] = []
        for opinion in tuple(getattr(result, "opinions", ()) or ()):
            trace_id = str(getattr(opinion, "trace_id", "") or "")
            trace = by_trace.get(trace_id)
            parts.append({
                "lens_id": str(getattr(opinion, "lens_id", "") or ""),
                "trace_id": trace_id,
                "opinion": self._lens_opinion_part(face, opinion),
                "trace": (
                    self._described_part(describe_trace(trace, now=_now()))
                    if trace is not None else None
                ),
            })
        return parts

    def _lens_opinion_part(self, face: Any, opinion: Any) -> dict[str, Any] | None:
        """一个视角的结构化观点 → `report_card`（[01 §3] 的立场 / 理由 / 证据 / 信心度 / 触发 Skill）。"""
        lens_id = str(getattr(opinion, "lens_id", "") or "")
        stance = str(getattr(opinion, "stance", "") or "")
        confidence = str(getattr(opinion, "confidence", "") or "")
        reasons = [str(item) for item in tuple(getattr(opinion, "key_reasons", ()) or ())]
        skills = [str(item) for item in tuple(getattr(opinion, "skills_triggered", ()) or ())]
        evidence = [str(item) for item in tuple(getattr(opinion, "evidence_refs", ()) or ())]
        try:
            description = self._gated_description(
                "report_card",
                slots={"sections": [
                    {"title": "观点", "lines": [
                        f"视角：{self._lens_name(face, lens_id)}",
                        f"立场：{STANCE_LABELS.get(stance, stance or '—')}",
                        f"信心度：{_CONFIDENCE_LABELS.get(confidence, confidence or '—')}",
                    ]},
                    {"title": "关键理由", "lines": reasons or ["（无）"]},
                    {"title": "触发的 Skill / 证据引用", "lines": (skills + evidence) or ["（无）"]},
                    {"title": "推理链锚点", "lines": [str(getattr(opinion, "trace_id", "") or "—")]},
                ]},
                text_kinds={"sections": "data"},
                title=f"{self._lens_name(face, lens_id)} · 观点",
            )
        except ValueError as exc:
            return envelope_payload(ResultEnvelope.validation_failed(str(exc)))
        return envelope_payload(ResultEnvelope.ok(description))

    def _deliberation_progress_description(self, face: Any, outcome: Any) -> UiDescription:
        """逐视角进度面 → `report_card`（[11-sitemap §2.1] 的「逐视角状态并列」）。

        整卡作 `data` 槽：段内含**用户给出的主题**（[D-053]：用户数据不过输出校验），且逐条
        理由在 L4 侧已过 [01 §6] 执行点 2（`_check_opinion_text`），此处不复检、也不改述。
        视角名取阵容（寻址失败退回 `lens_id`——**不臆造名字**，同 [06 §5] 视图投影的口径）。
        """
        opinions = tuple(getattr(getattr(outcome, "result", None), "opinions", ()) or ())
        topic = str(getattr(outcome, "topic", "") or "")
        mode = str(getattr(outcome, "mode", "") or "")
        lines: list[str] = []
        for opinion in opinions:
            lens_id = str(getattr(opinion, "lens_id", "") or "")
            stance = str(getattr(opinion, "stance", "") or "")
            confidence = str(getattr(opinion, "confidence", "") or "")
            stance_label = STANCE_LABELS.get(stance, stance or "—")
            confidence_label = _CONFIDENCE_LABELS.get(confidence, confidence or "—")
            lines.append(
                f"{self._lens_name(face, lens_id)}：{stance_label} · 信心度 {confidence_label}"
            )
            for reason in tuple(getattr(opinion, "key_reasons", ()) or ()):
                lines.append(f"　· {reason}")
        mode_label = _DELIBERATION_MODE_LABELS.get(mode, mode or "—")
        section_lines = lines or ["（本次无参与视角）"]
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "各视角状态", "lines": section_lines},
                {"title": "说明", "lines": [
                    f"主题：{topic or '—'} · 模式：{mode_label} · 参与视角 {len(opinions)} 个",
                    "各视角状态**并列**呈现，不合并、不给出统一结论（06 架构级红线）。",
                ]},
            ]},
            text_kinds={"sections": "data"},
            title="多视角并行执行进度",
        )

    @staticmethod
    def _lens_name(face: Any, lens_id: str) -> str:
        """视角的中性名（阵容寻址失败退回 `lens_id`——中性且不臆造）。"""
        roster = getattr(face, "roster", None)
        if roster is None or not lens_id:
            return lens_id
        try:
            return str(roster.get(lens_id).name)
        except Exception:  # noqa: BLE001 —— 取名不可得退回 id 而非报错（同 DivergenceViewer）
            return lens_id

    def api_deliberation_lenses(self) -> dict[str, Any]:
        """视角阵容读面（[06 §1] 常设阵容·用户可增删）→ `setting_panel`（逐条启用态 + 动作）。

        **内置只给停用动作**——[06 §1] 的 `builtin` 只能停用不可删，故不摆一个必然失败的按钮
        （`remove` 对内置仍在后端显式拒，见 :meth:`api_deliberation_lens_update`）。
        """
        return self._call_face(
            "deliberation-lenses", self.deliberation, "L4 多视角推理面",
            lambda face: self._lens_panel_envelope(face),
        )

    def _lens_panel_envelope(self, face: Any) -> ResultEnvelope:
        lenses = tuple(face.roster.list_all())
        if not lenses:
            return ResultEnvelope.empty(
                "视角阵容为空：既无官方预置视角，也未创建自定义视角（06 §6 的空状态）"
            )
        entries = []
        for lens in lenses:
            enabled = bool(lens.enabled)
            actions = ["disable" if enabled else "enable"]
            if lens.kind != "builtin":
                actions.append("remove")
            entries.append({
                "identifier": lens.name,
                "description": lens.description,
                "current": "启用" if enabled else "停用",
                "options": [],
                "actions": actions,
                "params": {"lens_id": lens.lens_id},
            })
        return ResultEnvelope.ok(self._gated_description(
            "setting_panel",
            slots={"entries": entries, "labels": dict(_LENS_ACTION_LABELS),
                   "surface": LENS_SURFACE},
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title="视角阵容",
        ))

    def api_deliberation_lens_create(self, body: dict[str, Any]) -> dict[str, Any]:
        """新建自定义视角（[06 §1]：一组 Skill + 一套评判准则 + 一个中性名字）。

        命名 / 描述的唯一真相源是 `LensRoster.add_custom` → [01 §6] `check_name`——本端点只做
        **形态**归一（列表 / 字典），命中拟人化由后端拒并回 `validation_failed`（不落盘）。
        """
        return self._call_face(
            "deliberation-lens-create", self.deliberation, "L4 多视角推理面",
            lambda face: ResultEnvelope.ok({
                "lens_id": face.roster.add_custom(
                    name=_required(body, "name"),
                    description=_required(body, "description"),
                    skill_bundle=_skill_bundle(body.get("skill_bundle")),
                    judging_criteria=_mapping(body.get("judging_criteria"), "judging_criteria"),
                    confidence_policy=_mapping(
                        body.get("confidence_policy"), "confidence_policy", allow_none=True
                    ),
                ).lens_id,
            }),
        )

    def api_deliberation_lens_update(self, body: dict[str, Any]) -> dict[str, Any]:
        """阵容写面：停用 / 启用 / 删除（[06 §1]；删除对内置显式拒）。"""
        action = _required(body, "action")
        lens_id = _required(body, "lens_id")

        def _apply(face: Any) -> ResultEnvelope:
            if action == "remove":
                try:
                    face.roster.remove(lens_id)
                except BuiltinLensError as exc:  # 内置只能停用不可删（06 §1）
                    raise ValueError(str(exc)) from exc
                return ResultEnvelope.ok({"removed": lens_id})
            if action in ("enable", "disable"):
                lens = face.roster.set_enabled(lens_id, action == "enable")
                # **不设 `changed`**：`set_enabled` 只改视角自身的启用位、不落变更留痕，
                # 而渲染件对 `changed` 的措辞是「已应用（留痕已记）」——那会是一句假话。
                return ResultEnvelope.ok({"lens_id": lens.lens_id, "enabled": bool(lens.enabled)})
            raise ValueError(f"未知动作 {action!r}（须为 enable / disable / remove）")

        return self._call_face(
            "deliberation-lens-update", self.deliberation, "L4 多视角推理面", _apply,
        )

    def api_deliberation_lens(self, *, lens_id: str) -> dict[str, Any]:
        """视角定义读面（`?lens=<id>`，[06 §1]）→ `report_card`。

        三种缺口**互不冒充**（同 [`T-UI-014.2`] 的单节点详情口径）：缺 `id` ⇒ `validation_failed`；
        格式非法 / 不存在 ⇒ 分别由 `LensRoster.get` 的 `LensNotFoundError`（`KeyError` 族，本处
        显式转 `unavailable` + 点名）与 `LensId` 形态校验（`ValueError` 族 ⇒ `validation_failed`）承担。
        """
        if not lens_id:
            return envelope_payload(
                ResultEnvelope.validation_failed("缺少视角标识 id（06 §1）")
            )

        def _build(face: Any) -> ResultEnvelope:
            try:
                lens = face.roster.get(lens_id)
            except LensNotFoundError as exc:
                return ResultEnvelope.unavailable(
                    f"视角 {lens_id!r} 不在阵容中（06 §1）", last_updated_at=_now()
                )
            criteria = getattr(lens, "judging_criteria", None)
            natural = str(getattr(criteria, "natural", "") or "")
            rule = getattr(criteria, "rule", None) or {}
            policy = getattr(lens, "confidence_policy", None)
            return ResultEnvelope.ok(self._gated_description(
                "report_card",
                slots={"sections": [
                    {"title": "基本信息", "lines": [
                        f"名称：{lens.name}",
                        f"标识：{lens.lens_id}",
                        f"类型：{_LENS_KIND_LABELS.get(str(lens.kind), str(lens.kind))}",
                        f"当前：{'启用' if lens.enabled else '停用'}",
                    ]},
                    {"title": "描述", "lines": [lens.description]},
                    {"title": "Skill 组合", "lines": list(lens.skill_bundle) or ["（未指定）"]},
                    {"title": "评判准则", "lines": (
                        [natural] if natural else []
                    ) + ([f"规则表达式：{json.dumps(dict(rule), ensure_ascii=False, sort_keys=True)}"]
                         if rule else []) or ["（未指定）"]},
                    {"title": "信心度判定", "lines": [
                        f"充分度 ≥ {getattr(policy, 'high_at', '—')} → 高；"
                        f"≥ {getattr(policy, 'medium_at', '—')} → 中；否则低",
                    ]},
                ]},
                text_kinds={"sections": "data"},
                title=f"视角 · {lens.name}",
            ))

        return self._call_face(
            "deliberation-lens", self.deliberation, "L4 多视角推理面", _build,
        )

    def api_deliberation_decisions(self) -> dict[str, Any]:
        """决策留痕读面（[06 §2.4] 写下的 L2 `history` 节点）→ `report_card`（逐条并列）。

        空留痕 ⇒ `empty` + 原因（**不静默留空**）；读面缺席 ⇒ `unavailable` + 点名。
        """
        return self._call_face(
            "deliberation-decisions", self.deliberation, "L4 多视角推理面",
            lambda face: self._decisions_envelope(face),
        )

    def _decisions_envelope(self, face: Any) -> ResultEnvelope:
        rows = list(face.decisions())
        if not rows:
            return ResultEnvelope.empty(
                "还没有决策记录（[06 §2.4] 的决策沉淀尚未发生过，或读面未接线）"
            )
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": f"决策留痕（{len(rows)} 条）", "lines": [
                    f"[{_iso_day(str(row.get('updated_at') or ''))}] "
                    f"{row.get('event') or '（无内容）'}"
                    for row in rows
                ]},
                {"title": "说明", "lines": [
                    "逐条**并列**呈现用户自陈的决策与理由；系统不给统一建议（06 §6）。",
                ]},
            ]},
            text_kinds={"sections": "data"},
            title="决策记录",
        ))

    def api_deliberation_decision(self, body: dict[str, Any]) -> dict[str, Any]:
        """决策沉淀写面（[06 §2.4]）：决策 + 理由 + 采纳视角 + 忽略视角 → L2 `history` 节点。

        **未采纳的参与视角不静默丢弃**（[`T-UI-016.3`] GWT-3）：由页面表单保证每条参与视角
        都在「采纳 / 忽略」之一；本端点只做**形态**归一（字符串列表）与转发，语义归 L2。
        """
        def _record(face: Any) -> ResultEnvelope:
            envelope = face.record_decision(
                decision=_required(body, "decision"),
                reasoning=str(body.get("reasoning") or ""),
                adopted_lens_ids=_id_list(body.get("adopted_lens_ids"), "adopted_lens_ids"),
                ignored_lens_ids=_id_list(body.get("ignored_lens_ids"), "ignored_lens_ids"),
            )
            return envelope if isinstance(envelope, ResultEnvelope) else ResultEnvelope.ok(envelope)

        return self._call_face(
            "deliberation-decision", self.deliberation, "L4 多视角推理面", _record,
        )
