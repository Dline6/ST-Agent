"""能力区 · Skill 库（[T-UI-015.1]）。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner.models import RUN_PREFIX
from st_agent.l1.skills.errors import SkillNotFoundError
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _ENVELOPE_STATE_LABELS,
    _brief_json,
    _contract_keys,
    _now,
    _required,
)


def _skill_row(descriptor: Any) -> dict[str, Any]:
    """一行 Skill 摘要（清单用）。"""
    return {
        "name": descriptor.name,
        "skill_id": descriptor.skill_id,
        "source": _SKILL_SOURCE_LABELS.get(descriptor.source, descriptor.source),
        "offline_level": _SKILL_OFFLINE_LABELS.get(
            descriptor.offline_level, descriptor.offline_level
        ),
        "version_policy": _SKILL_VERSION_POLICY_LABELS.get(
            descriptor.version_policy, descriptor.version_policy
        ),
        "parameter_count": len(descriptor.parameters),
        "dependencies": "、".join(descriptor.dependencies) or "（无）",
    }


def _skill_detail_sections(descriptor: Any) -> list[dict[str, Any]]:
    """详情页的五段（[11-sitemap §2.1]：`report_card` 多段式陈述）。"""
    return [
        {"title": "基本信息", "lines": [
            f"名称：{descriptor.name}",
            f"标识：{descriptor.skill_id}",
            f"来源：{_SKILL_SOURCE_LABELS.get(descriptor.source, descriptor.source)}",
            f"离线档：{_SKILL_OFFLINE_LABELS.get(descriptor.offline_level, descriptor.offline_level)}",
            f"版本策略：{_SKILL_VERSION_POLICY_LABELS.get(descriptor.version_policy, descriptor.version_policy)}",
            f"说明：{descriptor.description}",
        ]},
        {"title": "可配参数", "lines": [
            _skill_param_description(spec) for spec in descriptor.parameters
        ] or ["（无参数）"]},
        {"title": "依赖", "lines": list(descriptor.dependencies) or ["（无依赖）"]},
        # 官方 Pack 的 12 个 Skill 一律 `permissions=()`（03 §1.5 / §2：它们只经**注入的
        # 取数面**消费数据，自身不发文件 / 网络 / 命令行为）——故出厂态这一段就是「无声明」，
        # **如实出**而不是隐藏该段（隐藏会让「无声明」与「面缺失」不可分）。
        {"title": "权限声明", "lines": list(descriptor.permissions) or ["（无权限声明）"]},
        {"title": "输入 / 输出契约", "lines": [
            "输入：" + _contract_keys(descriptor.input_schema),
            "输出：" + _contract_keys(descriptor.output_schema),
        ]},
    ]


def _skill_param_description(spec: Any) -> str:
    """一条参数的说明（名称 + 说明 + 类型 + 取值面 / 范围）。"""
    bits = [f"{spec.name}：{spec.description}", f"类型 {spec.type}"]
    if spec.type == "enum":
        bits.append("取值 " + "、".join(spec.choices))
    elif spec.type in ("number", "integer") and (
        spec.min_value is not None or spec.max_value is not None
    ):
        low = "—" if spec.min_value is None else f"{spec.min_value:g}"
        high = "—" if spec.max_value is None else f"{spec.max_value:g}"
        bits.append(f"范围 {low} ~ {high}")
    if spec.default is not None:
        bits.append(f"默认 {spec.default}")
    return "；".join(bits)


def _skill_param_entry(skill_id: str, spec: Any) -> dict[str, Any]:
    """一个 `ParameterSpec` → `setting_panel` 的一条（取值面按 `type` 三态选）。

    三态口径见渲染件头注：`enum` / `boolean` 给 `options` 走下拉（布尔**刻意不走**
    `<select>` 之外的路——本件没有独立的布尔控件，两个候选取值是同一件事的最短表达），
    其余（string / number / integer）给 `input: "text"` 走自由文本，**值域仍由后端**
    `validate_param_values` 把关，前端不替它枚举也不替它判合法性。
    """
    default = spec.default
    if spec.type == "boolean":
        current = "—" if default is None else ("true" if default else "false")
        options = [{"value": "true"}, {"value": "false"}]
    elif spec.type == "enum":
        current = "—" if default is None else str(default)
        options = [{"value": str(choice)} for choice in spec.choices]
    else:
        current = "—" if default is None else str(default)
        options = []
    entry: dict[str, Any] = {
        "identifier": spec.name,
        "description": _skill_param_description(spec),
        "current": current,
        "options": options,
        "actions": ["apply"],
        "params": {"skill_id": skill_id, "name": spec.name},
    }
    if not options:
        entry["input"] = "text"
    return entry


def _coerce_param_value(spec: Any, raw: Any) -> Any:
    """表单值（恒为字符串）按 `ParameterSpec.type` **还原成声明类型**。

    类型还原是本层的（HTML 表单只给字符串），**值域校验不是**——超范围 / 类型不符由
    `validate_param_values` 拒。不还原则 `type=integer` 的参数会把 `"30"` 当字符串判非法
    （同 `_coerce_value` 在设置区的作用）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError(f"缺少参数 {spec.name!r} 的取值")
    if spec.type == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "是"):
            return True
        if text in ("false", "0", "否"):
            return False
        raise ValueError(f"参数 {spec.name!r} 是布尔型，取值须为 true / false（得到 {raw!r}）")
    if spec.type == "enum":
        return str(raw)
    if spec.type in ("number", "integer"):
        try:
            return int(str(raw)) if spec.type == "integer" else float(str(raw))
        except ValueError as exc:
            raise ValueError(f"参数 {spec.name!r} 是数值型，取值须为数（得到 {raw!r}）") from exc
    return str(raw)


def _skill_log_limit(raw: str) -> int:
    """执行日志的条数上限（缺省 :data:`_SKILL_LOG_LIMIT`；非法即拒**不夹取**）。

    上限之上再夹一层是**读取成本**的护栏而非语义——被夹时表格标题会写明「共 N 条、
    显示最近 M 条」，不静默截断。
    """
    text = (raw or "").strip()
    if not text:
        return _SKILL_LOG_LIMIT
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"limit 须为整数（得到 {raw!r}）") from exc
    if value < 1:
        raise ValueError(f"limit 须为正整数（得到 {raw!r}）")
    return min(value, _SKILL_LOG_LIMIT)


def _skill_run_rows(
    store: Any, skill_id: str, cap: int
) -> tuple[list[dict[str, Any]], int, int]:
    """`execution_log` 分区 `skill-run/` 下的记录 → 该 Skill 的日志行（新在前）。

    返回 `(行, 命中总数, 无法解析条数)`。无法解析的**计数并显式披露**，不当成「没有」
    （[01 §5]：不谎报为 0）。
    """
    rows: list[dict[str, Any]] = []
    unreadable = 0
    for name in store.list_files("execution_log"):
        if not (name.startswith(RUN_PREFIX) and name.endswith(".json")):
            continue
        try:
            payload = json.loads(store.get("execution_log", name).decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError):
            unreadable += 1
            continue
        if not isinstance(payload, Mapping):
            unreadable += 1
            continue
        if str(payload.get("skill_id", "")) != skill_id:
            continue
        rows.append(_skill_run_row(payload))
    rows.sort(key=lambda row: str(row.get("started_at", "")), reverse=True)
    return rows[:cap], len(rows), unreadable


def _skill_run_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    """一条 `SkillRun` 记录 → 表格行（[03 §1.2] 步骤 5 的留痕字段）。"""
    envelope = payload.get("envelope")
    status = str(envelope.get("status", "")) if isinstance(envelope, Mapping) else ""
    upstream = payload.get("upstream") or ()
    return {
        "started_at": str(payload.get("started_at", "") or "—"),
        "skill_run_id": str(payload.get("skill_run_id", "") or "—"),
        "status": _ENVELOPE_STATE_LABELS.get(status, status or "—"),
        "duration_ms": payload.get("duration_ms") or 0,
        "upstream": "、".join(str(item) for item in upstream) or "（无）",
        "params": _brief_json(payload.get("params")),
    }


SKILL_PARAMS_SURFACE = "skill-params"
"""Skill 参数配置面板的面键（`setting_panel` 的 `surface` 槽；[`T-UI-015.1`]）。

与 :data:`LENS_SURFACE` 同理：路由由**枚举键**在渲染件的固定表里解析（[01 §12]「动作不进描述」）。
[`T-UI-015.3`] 的「Studio Skill 参数面板」**复用本键**（同一屏语义两处宿主，不造第二套）。
"""


_SKILL_PARAM_LABELS: dict[str, str] = {
    "apply": "应用",
    "true": "是",
    "false": "否",
}
"""参数面板的标签表（生成文案槽 `labels`，过 [01 §6]）——**动作与候选值共用一张**
（渲染件头注：`labels` 同时按动作键与 `option.value` 取值）。布尔参数的候选取「是 / 否」
而不取「启用 / 停用」：参数语义由各 Skill 自述，此处只是取值面的中性词。"""


_SKILL_SOURCE_LABELS: dict[str, str] = {
    "official": "官方预置",
    "user-built": "自建",
    "mcp-mapped": "MCP 映射",
    "imported": "导入",
}
"""Skill 来源的中文名（系统文案；`SkillDescriptor.source` 的四个取值）。"""


_SKILL_OFFLINE_LABELS: dict[str, str] = {
    "full": "完全离线可用",
    "degraded": "降级可用",
    "none": "须联网",
}
"""Skill 离线档的中文名（系统文案；[02 §7] 的 `OfflineLevel` 三个取值）。"""


_SKILL_VERSION_POLICY_LABELS: dict[str, str] = {
    "follow-latest": "跟随最新",
    "locked": "锁定当前",
}
"""Skill 版本策略的中文名（系统文案；[01 §9] 的 `VersionPolicy` 两个取值）。"""


_SKILL_LOG_LIMIT = 50
"""Skill 执行日志页单次列出的上限（超出即如实标注「只显示最近 N 条」，不静默截断）。"""


class SkillsSurface(SurfaceBase):
    """能力区 · Skill 库（[T-UI-015.1]）。"""

    def api_skills_catalog(self) -> dict[str, Any]:
        """Skill 清单 → `table`（逐条并列；含**全部已注册版本**，如实不折叠）。"""
        return self._call_face(
            "skills-catalog", self.skills, "L1 能力（Skill）面", self._skill_catalog_envelope
        )

    def api_skills_detail(self, *, skill_id: str) -> dict[str, Any]:
        """单 Skill 详情 → `report_card`（元数据 / 参数 / 依赖 / 权限声明 / 输入输出契约）。"""
        if not skill_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Skill 标识（skill）"))
        return self._call_face(
            "skills-detail", self.skills, "L1 能力（Skill）面",
            lambda face: self._skill_detail_envelope(face, skill_id),
        )

    def api_skills_params(self, *, skill_id: str) -> dict[str, Any]:
        """参数配置面板 → `setting_panel`（**带动作**；面键 `skill-params`）。"""
        if not skill_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Skill 标识（skill）"))
        return self._call_face(
            "skills-params", self.skills, "L1 能力（Skill）面",
            lambda face: self._skill_params_envelope(face, skill_id),
        )

    def api_skills_params_set(self, body: dict[str, Any]) -> dict[str, Any]:
        """参数落值——经 `SkillRegistry.set_parameters`（[03 §1.1] 的唯一写口）。

        超范围 / 类型不符由 `validate_param_values` 拒（`SkillValidationError` →
        `validation_failed`），**不允许保存**（story-02 边缘情况）。回 `applied` 而**不回**
        `changed`：描述体的写入**不产生 `ChangeRecord`**（版本变更才留痕，见
        `publish_version`），故不能让渲染件打出「留痕已记」那句假话（同 `T-UI-016.1` 对
        `set_enabled` 的处理）。
        """
        return self._call_face(
            "skills-params-set", self.skills, "L1 能力（Skill）面",
            lambda face: self._skill_params_set_envelope(face, body),
        )

    def api_skills_logs(self, *, skill_id: str, limit: str = "") -> dict[str, Any]:
        """执行日志 → `table`（`skill-run/` 前缀的只读列表；[03 §1.2] 步骤 5 的既落盘）。"""
        if not skill_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Skill 标识（skill）"))
        return self._call_face(
            "skills-logs", self.skills, "L1 能力（Skill）面",
            lambda face: self._skill_logs_envelope(face, skill_id, limit),
        )

    def _skill_unavailable(self, exc: Any) -> ResultEnvelope:
        """「标识合法但未注册」的如实降级（与「参数非法」互不冒充）。"""
        return ResultEnvelope.unavailable(
            f"{exc}——标识形态合法但该 Skill 未注册（不是请求不合契约）", last_updated_at=_now()
        )

    def _skill_catalog_envelope(self, face: Any) -> ResultEnvelope:
        skills = tuple(face.registry.list_all())
        if not skills:
            return ResultEnvelope.empty(
                "能力池为空：尚未注册任何 Skill（03 §2 的官方 Pack 未装载）"
            )
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("name", "名称"),
                    Column("skill_id", "标识"),
                    Column("source", "来源"),
                    Column("offline_level", "离线档"),
                    Column("version_policy", "版本策略"),
                    Column("parameter_count", "参数数", "number", digits=0),
                    Column("dependencies", "依赖"),
                ),
                [_skill_row(descriptor) for descriptor in skills],
            ),
            text_kinds=table_text_kinds(),
            title="Skill 库",
        ))

    def _skill_detail_envelope(self, face: Any, skill_id: str) -> ResultEnvelope:
        try:
            descriptor = face.registry.get(skill_id)
        except SkillNotFoundError as exc:
            return self._skill_unavailable(exc)
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": _skill_detail_sections(descriptor)},
            text_kinds={"sections": "data"},
            title=f"{descriptor.name} · 详情",
        ))

    def _skill_params_envelope(self, face: Any, skill_id: str) -> ResultEnvelope:
        try:
            descriptor = face.registry.get(skill_id)
        except SkillNotFoundError as exc:
            return self._skill_unavailable(exc)
        if not descriptor.parameters:
            return ResultEnvelope.empty(
                f"「{descriptor.name}」未声明可配参数（03 §1.2：parameters 可为空，"
                "该 Skill 无参数可配）"
            )
        entries = [_skill_param_entry(descriptor.skill_id, spec) for spec in descriptor.parameters]
        return ResultEnvelope.ok(self._gated_description(
            "setting_panel",
            slots={
                "entries": entries,
                "labels": dict(_SKILL_PARAM_LABELS),
                "surface": SKILL_PARAMS_SURFACE,
            },
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title=f"{descriptor.name} · 参数",
        ))

    def _skill_params_set_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        skill_id = str(_required(body, "skill_id"))
        name = str(_required(body, "name"))
        try:
            descriptor = face.registry.get(skill_id)
        except SkillNotFoundError as exc:
            return self._skill_unavailable(exc)
        spec = next((item for item in descriptor.parameters if item.name == name), None)
        if spec is None:
            return ResultEnvelope.validation_failed(
                f"「{descriptor.name}」未声明参数 {name!r}，不允许保存（03 §1.2：未知参数名即拒）"
            )
        face.registry.set_parameters(skill_id, {name: _coerce_param_value(spec, body.get("value"))})
        return ResultEnvelope.ok({"applied": True})

    def _skill_logs_envelope(self, face: Any, skill_id: str, limit: str) -> ResultEnvelope:
        try:
            descriptor = face.registry.get(skill_id)
        except SkillNotFoundError as exc:
            return self._skill_unavailable(exc)
        if face.store is None:
            return ResultEnvelope.unavailable(
                "未接入存储面，Skill 执行日志不可读（装配归生产入口）", last_updated_at=_now()
            )
        rows, total, unreadable = _skill_run_rows(face.store, skill_id, _skill_log_limit(limit))
        if not rows:
            reason = (
                f"「{descriptor.name}」尚无执行留痕——执行日志是 03 §1.2 步骤 5 的落盘，"
                "该 Skill 未执行过即为空"
            )
            if unreadable:
                reason += f"（另 {unreadable} 条留痕无法解析，未计入）"
            return ResultEnvelope.empty(reason)
        title = f"{descriptor.name} · 执行日志"
        if total > len(rows):
            title += f"（共 {total} 条，显示最近 {len(rows)} 条）"
        if unreadable:
            title += f"（另 {unreadable} 条无法解析）"
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("started_at", "时刻"),
                    Column("skill_run_id", "留痕标识"),
                    Column("status", "状态"),
                    Column("duration_ms", "耗时(ms)", "number", digits=0),
                    Column("upstream", "依赖链"),
                    Column("params", "参数快照"),
                ),
                rows,
            ),
            text_kinds=table_text_kinds(),
            title=title,
        ))
