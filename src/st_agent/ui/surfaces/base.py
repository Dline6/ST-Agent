"""各面共用的取数 / 信封助手；本身不注册任何端点。"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.l1.skills.errors import SkillNotFoundError
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.registry import slot_gaps


_LOG = logging.getLogger("st_agent.ui.app")


def _now() -> datetime:
    """带时区的当前时刻（01 §8：内部传输时间锚点带时区）。"""
    return datetime.now().astimezone()


def _required(body: Mapping[str, Any], key: str) -> Any:
    """取请求体的必填项；缺失 / 空白即 ``ValueError``（表现层据此回 `validation_failed`）。"""
    value = body.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"请求体缺少必填项 {key!r}")
    return value


def _skill_bundle(value: Any) -> tuple[str, ...]:
    """`skill_bundle` 请求值 → `skill_id` 元组（仅做**形态**归一；存在性由 `LensRoster` 校验）。"""
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("skill_bundle 须为 skill_id 列表（06 §1）")
    return tuple(str(item) for item in value)


def _contract_keys(schema: Any) -> str:
    """schema 的顶层键清单（键是字段名，不算展示文本，故直接列）。"""
    if not isinstance(schema, Mapping) or not schema:
        return "（未声明）"
    properties = schema.get("properties")
    keys = list(properties) if isinstance(properties, Mapping) else list(schema)
    return "、".join(str(key) for key in keys) or "（未声明）"


def _brief_json(value: Any, *, limit: int = 60) -> str:
    """一份 JSON 可序列化值的**一行摘要**（超长即截断并标注，不静默丢内容）。"""
    try:
        text = json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "（无法序列化）"
    return text if len(text) <= limit else text[:limit] + "…"


def _skill_descriptor_or_none(registry: Any, skill_id: str) -> Any:
    """取派生 Skill 的描述体；取不到给 `None`。

    一行取不到**不能**让整张表失败——映射记录仍如实列出，参数面那一格显式写「描述体不在」。
    """
    if registry is None:
        return None
    try:
        return registry.get(skill_id)
    except (SkillNotFoundError, ValueError, KeyError):
        return None


def _mapping(value: Any, field: str, *, allow_none: bool = False) -> Any:
    """请求值 → 字典（缺省空字典；给了非字典即 ``ValueError``，不静默丢字段）。"""
    if value is None or value == "":
        if allow_none:
            return None
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} 须为对象（06 §1）")
    return dict(value)


def _id_list(value: Any, field: str) -> tuple[str, ...]:
    """请求值 → 标识元组（缺省空；给了非列表即 ``ValueError``，不静默丢条目）。"""
    if value is None or value == "":
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} 须为标识列表（06 §2.4）")
    return tuple(str(item) for item in value if str(item))


_FEEDBACK_ACTIONS = ("adopted", "ignored", "rejected", "queried", "liked")
"""反馈的五类动作（[08 §1]；与 L3 采集面的 `ACTIONS` 同序同集）。"""


_FEEDBACK_REASON_REQUIRED = ("rejected",)
"""其中**必填原因**的一类——「否定 + 说明原因」是 story-09 的原始要求。"""


_FEEDBACK_LABELS = {
    "adopted": "采纳",
    "ignored": "忽略",
    "rejected": "否定",
    "queried": "追问",
    "liked": "点赞",
}
"""动作键 → 中性中文标签（**本层生成文案**，过 [01 §6] 执行点 2）。"""


_TIMELINE_STATE_LABELS: dict[str, str] = {
    "ok": "生效",
    "empty": "空",
    "delayed": "待复核",
    "failed": "失败",
}
"""时间线视图的状态名（系统文案；与 `timeline_view.js` 的 `STATE_CLASS` 逐值对应）。"""


_CONFIDENCE_LABELS: dict[str, str] = {
    "high": "高",
    "medium": "中",
    "low": "低",
}
"""信心度档位的中文名（系统文案，过 [01 §6]）。

与 `divergence_map` 渲染件的三档（`CONFIDENCE_LABELS`）同词汇、同粒度——分档只是**呈现
词汇**，不是第二套语义（同 `_confidence_bucket` 的口径）。立场标签不自持：直接用 L3 的
[`STANCE_LABELS`][st_agent.l3.render.describe.STANCE_LABELS]（唯一真相源，[D-064]）。"""


_ENVELOPE_STATE_LABELS: dict[str, str] = {
    "ok": "完成",
    "empty": "空结果",
    "delayed": "延后",
    "unavailable": "不可用",
    "failed": "失败",
    "validation_failed": "不合契约",
}
"""[01 §5] 六态的中文名（系统文案）——执行日志按原值取此表，**不合并**（六态互不冒充）。"""


def _iso_day(value: Any) -> str:
    """ISO 串 → 日期串（时间线视图的 `at` 槽；取日期部分，坏值原样给，不抛）。"""
    if not isinstance(value, str) or not value:
        return ""
    return value[:10]


def _fmt_value(value: Any) -> str:
    """专属字段值 → 一行文本（集合用「、」连、缺值给「—」）。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) or "—"
    if isinstance(value, dict):
        return "；".join(f"{key}={_fmt_value(item)}" for key, item in value.items()) or "—"
    return str(value)


def _optional_text(value: Any) -> str | None:
    """请求体里的可选字符串（表单可能给空串 / null / 非字符串）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class SurfaceBase:
    """各面共用的取数 / 信封助手；本身不注册任何端点。"""

    def _described_part(self, described: Any) -> dict[str, Any] | None:
        """L3 描述件产出的信封 → 出站载荷（**与 `POST /api/chat` 同一口径**）。

        非 `ok` 的六态**原样下发**（`empty` 与 `unavailable` 是两回事，不吞原因）；`ok` 者先过
        本层两道闸（必填槽 + 中性化门）再出——不回可渲染的描述（[01 §12]）。
        """
        if not isinstance(described, ResultEnvelope):
            return None
        if described.status != "ok":
            return envelope_payload(described)
        try:
            return envelope_payload(ResultEnvelope.ok(self._gate_description(described.data)))
        except ValueError as exc:
            return envelope_payload(ResultEnvelope.validation_failed(str(exc)))

    def _part(self, component_type: str, *, slots: dict[str, Any],
              text_kinds: dict[str, str], title: str | None = None) -> dict[str, Any]:
        """一段 `ok` 出站载荷（过本层两道闸；不过闸即抛，由调用方转 `validation_failed`）。"""
        return envelope_payload(ResultEnvelope.ok(
            self._gated_description(
                component_type, slots=slots, text_kinds=text_kinds, title=title
            )
        ))

    def _gated_description(
        self,
        component_type: str,
        *,
        slots: dict[str, Any],
        text_kinds: dict[str, str],
        title: str | None = None,
        as_of: datetime | None = None,
    ) -> UiDescription:
        """造一份描述并过**本层两道闸**：必填槽（注册表镜像）+ 渲染前中性化门（01 §6 执行点 2）。

        不过闸即抛 :class:`ValueError`（调用方回 `validation_failed`，[01 §12]：不回可渲染的描述）。
        """
        description = checked_description(
            description_id=new_description_id(),
            component_type=component_type,
            slots=slots,
            text_kinds=text_kinds,
            title=title,
            as_of=as_of,
        )
        return self._gate_description(description)

    def _gate_description(self, description: UiDescription) -> UiDescription:
        """过**本层两道闸**：必填槽（注册表镜像）+ 渲染前中性化门（[01 §6] 执行点 2）。

        不过闸即抛 :class:`ValueError`（调用方回 `validation_failed`，[01 §12]：不回可渲染的描述）。
        既服务本层**自造**的描述，也服务 L3 描述件产出、经本层出站的那些（同 `POST /api/chat`）。
        """
        gaps = slot_gaps(description)
        if gaps:
            raise ValueError(
                f"{description.component_type} 缺少必填槽："
                f"{'、'.join(gaps)}（01 §12 的必填槽表）"
            )
        verdict = self.neutrality_gate.check(description)
        if not verdict.passed:
            raise ValueError(verdict.reason)
        return description

    def _call_face(
        self,
        where: str,
        face: Any,
        label: str,
        fn: Callable[[Any], Any],
    ) -> dict[str, Any]:
        """调一次面操作并包成信封载荷（**本方法不向上抛**，同 :meth:`api_chat` 的口径）。

        - 重启闸门已置位（清空 / 恢复之后）⇒ `unavailable` + 该原因——此时手里的存储
          句柄已失效，**任何**数据面读数都不可信（见 :class:`RestartGate`）；
        - 面未注入 ⇒ `unavailable` + 点名（不 500、不伪造）；
        - `ValueError` / `KeyError`（输入类，含契约构造失败）⇒ `validation_failed`；
        - 返回 `ResultEnvelope` ⇒ 原样出（六态由该面给，不吞）；其余值 ⇒ `ok` 包起来；
        - 其他异常 ⇒ `failed` + `log_ref`（[00 §6]，不静默）。
        """
        if self.restart_gate.reason:
            return envelope_payload(
                ResultEnvelope.unavailable(self.restart_gate.reason, last_updated_at=_now())
            )
        if face is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    f"未接入{label}，该端点不可用（装配归生产入口）", last_updated_at=_now()
                )
            )
        try:
            result = fn(face)
        except (ValueError, KeyError) as exc:
            _LOG.info("表现层端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
            return envelope_payload(
                ResultEnvelope.validation_failed(str(exc) or "请求不合契约")
            )
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/{where}-{uuid.uuid4().hex[:12]}"
            _LOG.exception("表现层端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
            return envelope_payload(
                ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref)
            )
        if isinstance(result, ResultEnvelope):
            return envelope_payload(result)
        return envelope_payload(ResultEnvelope.ok(result))

    def _require_handle(self, face: Any, name: str, label: str) -> Any:
        """取门面上的一个句柄；缺即 `unavailable` + 点名（返回信封，调用方判类型）。"""
        value = getattr(face, name, None)
        if value is None:
            return ResultEnvelope.unavailable(
                f"未接入{label}（{name}），该动作不可用（装配归组合根）",
                last_updated_at=_now(),
            )
        return value
