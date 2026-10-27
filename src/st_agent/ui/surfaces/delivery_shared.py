"""触达区的纯函数与标签表（无状态，可独立测试）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l5.budget import LEVELS as SIGNAL_LEVELS
from st_agent.l5.channels import CHANNEL_KINDS
from st_agent.l5.daily_report import NO_CONTENT_NOTE
from st_agent.l5.daily_report_store import REPORT_PREFIX
from st_agent.l5.errors import ChannelNotWiredError, L5Error

from st_agent.ui.surfaces.base import (
    _now,
)


DELIVERY_CONFIG_SURFACE = "delivery-config"
"""触达配置面板的面键（`setting_panel` 的 `surface` 槽；[`T-UI-017`] 三叶共用）。

日报生成时刻、注意力预算（情境 / 格位）、渠道偏好、补发开关与疲劳阈值**共用一面键**——
它们同属触达设置面、同走一条回环路由（先例＝`settings-config` 的四设置页共用一个按键）。
路由由**枚举键**在渲染件的固定表里解析，描述**不接受** URL / 方法（[01 §12]「动作不进描述」）。
"""


_MODE_LABELS = {
    "workday": "工作日", "weekend": "周末", "business_trip": "出差",
    "vacation": "休假", "urgent": "紧急",
}
"""五情境模式的中文名（[07 §2] 的 `SituationMode`；系统文案）。"""


_CONTENT_LABELS = {
    "long_form": "长内容", "brief": "短提示",
    "interactive": "交互式", "retrospective": "周期回顾",
}
"""四内容类型的中文名（[07 §2 的两轴口径]；系统文案）。"""


_LEVEL_LABELS = {"emergency": "紧急", "important": "重要", "routine": "日常"}
"""三信号级别的中文名（[07 §1]；系统文案）。"""


_CHANNEL_LABELS = {
    "desktop": "桌面通知", "tts": "语音朗读", "email": "邮件", "im_webhook": "IM Webhook",
    "daily_report": "汇总进日报",
}
"""渠道的中文名（[07 §3] 的四类适配器 + 「汇总进日报」这一**路由位**，见 `DAILY_REPORT_CHANNEL`）。"""


_DELIVERY_STATE_LABELS = {
    "delivered": "已投出", "read": "已读", "unacknowledged": "链尽未读",
    "queued": "汇总进日报", "unavailable": "不可用", "failed": "失败",
}
"""推送留痕六态的中文名（[`DeliveryStatus`]；系统文案，**不合并**）。"""


_DELIVERY_TIMELINE_STATES = {
    "delivered": "ok", "read": "ok", "queued": "empty",
    "unacknowledged": "delayed", "unavailable": "delayed", "failed": "failed",
}
"""留痕态 → `timeline_view` 的四个呈现档（渲染件的 `STATE_CLASS` 只认 ok / empty / delayed / failed）。

`delayed` 同时承载「链尽未读」与「离线待补发」两态——两者的区分写在条目正文里
（「离线，暂无法发送」），**不靠**呈现档硬分。
"""


_FATIGUE_ANSWER_LABELS = {"reduce": "减少", "disable": "关闭", "keep": "保持"}
"""疲劳答复三选的中文名（[07 §7]）。"""


_FATIGUE_ANSWER_ACTION = "ignored"
"""疲劳答复所携 `FeedbackEvent.action`——[08 §1] 的 `action` 只有五值，**无**「减少 / 关闭 / 保持」
的对应值，故取一个合法固定值，答复语义载在 `context.fatigue_choice`（L5 `consume` 只读后者，
[`T-UI-017.3`] 假设 A2）。**不取** `rejected`——它强求 `reason`（按钮答复没有原因输入）。
"""


_REPORT_LIST_LIMIT = 30
"""已生成日报清单单次列出的上限（超出即如实标注「共 N 天、显示最近 M 天」，不静默截断）。"""


_HISTORY_LIMIT = 100
"""推送历史时间线单次列出的上限（同上）。"""


def _today() -> date:
    """本机墙钟日历日（[01 §8]；与日报的落盘键 `<YYYY-MM-DD>` 同口径）。"""
    return _now().date()


def _report_day(raw: str) -> date:
    """`?day=` → 日历日；缺省＝今日；非法即拒（**不静默回落**，否则会读出一份别的日期的报告）。"""
    text = (raw or "").strip()
    if not text:
        return _today()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"日期须为 YYYY-MM-DD（得到 {raw!r}）") from exc


def _bounded_limit(raw: str, *, default: int, cap: int, what: str) -> int:
    """列表条数上限（缺省 `default`；非法即拒**不夹取**；上限之上再夹一层是读取成本护栏）。"""
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"{what} 须为整数（得到 {raw!r}）") from exc
    if value < 1:
        raise ValueError(f"{what} 须为正整数（得到 {raw!r}）")
    return min(value, cap)


def _level_tokens() -> tuple[tuple[str, str], ...]:
    """允许级别集合的候选（token, 中文标签）——**恒含 `emergency`** 的非空子集（[07 §2]）。

    候选集由 `LEVELS` 推导而非硬编码：级别集合若随官方 Pack 扩了，选项面跟着长，
    不会悄悄少一档。token 是级别的逗号串（如 `emergency,important`）——稳定、可逆、
    与 `BudgetCell.allowed_levels` 一一对应。
    """
    tokens: list[tuple[str, str]] = []
    for size in range(1, len(SIGNAL_LEVELS) + 1):
        for combo in combinations(SIGNAL_LEVELS, size):
            if "emergency" not in combo:
                continue
            if size == len(SIGNAL_LEVELS):
                label = "全部级别"
            elif size == 1:
                label = f"仅{_LEVEL_LABELS[combo[0]]}"
            else:
                label = " + ".join(_LEVEL_LABELS[level] for level in combo)
            tokens.append((",".join(combo), label))
    return tuple(tokens)


def _l5_envelope(fn: Callable[[], Any]) -> Any:
    """包一次 L5 调用：**接线缺口 → `unavailable`；越界 → `validation_failed`**。

    L5 的失败词汇（[`L5Error`] 各支）**不是** `ValueError` ⇒ 不译就会被 `_call_face` 当成
    「未预期失败」报成 `failed`，那是把「你填错了」误报成「服务端坏了」（同 [`T-UI-015.1`]
    对 `SkillNotFoundError` 的就地翻译）。``ChannelNotWiredError`` 另作一支：它是**装配缺口**
    （渠道没接线），不是输入错误。

    成功时返回调用的原始结果（**不包信封**）——调用方按需决定怎么摆进描述。
    """
    try:
        return fn()
    except ChannelNotWiredError as exc:
        return ResultEnvelope.unavailable(str(exc), last_updated_at=_now())
    except L5Error as exc:
        return ResultEnvelope.validation_failed(str(exc))
    except (ValueError, KeyError) as exc:
        return ResultEnvelope.validation_failed(str(exc) or "请求不合契约")


def _as_envelope(value: Any) -> ResultEnvelope:
    """`_l5_envelope` 的产物 → 信封（已是信封的原样出，其余包 `ok`）。"""
    return value if isinstance(value, ResultEnvelope) else ResultEnvelope.ok(value)


def _change_payload(record: Any) -> dict[str, Any]:
    """一次落值的回执（渲染件据 `changed` 决定说「已应用（留痕已记）」还是「取值未变」）。"""
    return {"changed": record is not None, "change_id": getattr(record, "change_id", None)}


def _stored_report_days(store: Any) -> tuple[date, ...]:
    """`execution_log/daily_report/` 前缀下**已生成**的日（新在前；不可解析的文件名跳过）。

    文件名即 `<YYYY-MM-DD>.json`（[`DailyReportStore.path_for`] 的确定性派生），故列举目录
    就能得到日清单，无需逐日试探（同 [`T-UI-015.1`] 的 `skill-run/` 列法）。
    """
    days: set[date] = set()
    for name in store.list_files("execution_log"):
        if not (name.startswith(REPORT_PREFIX) and name.endswith(".json")):
            continue
        try:
            days.add(date.fromisoformat(name[len(REPORT_PREFIX):-len(".json")]))
        except ValueError:
            continue
    return tuple(sorted(days, reverse=True))


def _clock(at: datetime) -> str:
    """留痕时刻的中性呈现（本机墙钟，分钟精度）。"""
    return at.strftime("%Y-%m-%d %H:%M")


def _report_sections(report: Any) -> list[dict[str, Any]]:
    """`DailyReport` → `report_card` 的段（四段逐段并列 + 投出态；段级空态**显式**）。"""
    sections: list[dict[str, Any]] = []
    if report.empty:
        sections.append({"title": "今日无内容", "lines": [NO_CONTENT_NOTE]})
    for section in report.sections:
        lines = list(section.lines)
        if not lines:
            lines = [section.empty_note or "（本段无内容）"]
        sections.append({"title": section.label, "lines": lines})
    delivered = str(getattr(report, "delivered_channel", "") or "")
    sections.append({
        "title": "投出",
        "lines": [f"已投出到「{_CHANNEL_LABELS.get(delivered, delivered)}」" if delivered
                  else "尚未投出（订阅渠道与到点生成由常驻循环驱动）"],
    })
    return sections


def _history_entry(record: Any) -> dict[str, Any]:
    """一条推送留痕 → `timeline_view` 的条目（状态如实、离线态显式标注）。"""
    status = str(getattr(record, "status", ""))
    channel = str(getattr(record, "channel", ""))
    body = str(getattr(record, "body", ""))
    title = str(getattr(record, "title", ""))
    note = ""
    if getattr(record, "pending_reconnect", False):
        note = "（离线，暂无法发送；恢复后按开关补发）"
    elif status == "unacknowledged":
        note = "（链尽仍未读，已转日报汇总，不丢弃）"
    text = f"{title}：{body}" if body and body != title else title
    return {
        "at": _clock(record.created_at),
        "kind": channel,
        "state": _DELIVERY_TIMELINE_STATES.get(status, "empty"),
        "content": f"{_DELIVERY_STATE_LABELS.get(status, status)} · {text}{note}",
        "trace_ref": str(getattr(record, "delivery_id", "")),
    }


def _delivery_config_labels() -> dict[str, str]:
    """`delivery-config` 面板的标签表（动作键 + 候选取值 → 中性中文，过 [01 §6]）。"""
    return {
        "switch-mode": "切换",
        "set-cell": "应用",
        "set-policy": "应用",
        "resend-on": "开启",
        "resend-off": "关闭",
        "set-report-time": "应用",
        "set-fatigue-threshold": "应用",
        **_MODE_LABELS,
        **_CONTENT_LABELS,
        **{token: label for token, label in _level_tokens()},
    }


def _mode_entry(budget_map: Any) -> dict[str, Any]:
    """情境模式切换器（[07 §2]）；「按日历自动」**不给入口**——用户日历面未交付，不假装有。"""
    return {
        "identifier": "情境模式",
        "current": budget_map.current_mode,
        "options": [{"value": mode} for mode in budget_map.modes],
        "actions": ["switch-mode"],
        "description": "当前情境决定按哪一套注意力预算放行；按日历自动切换待用户日历面就位",
        "params": {},
    }


def _cell_entries(budget_map: Any) -> list[dict[str, Any]]:
    """**当前情境**下的预算格位（时段 × 内容类型）——逐格两条：允许级别 + 每格上限。

    只列当前情境是**可用性**取舍：全叉积是 5 × 5 × 4 = 100 格，平铺成面板条目不可读
    （[`T-UI-017.2`] 假设 A3）。切情境即重取该模式的格位。
    """
    tokens = _level_tokens()
    labels = {slot.slot_id: slot.label for slot in budget_map.slots}
    entries: list[dict[str, Any]] = []
    for view in budget_map.cells:
        if view.key.mode != budget_map.current_mode:
            continue
        head = f"{labels.get(view.key.slot_id, view.key.slot_id)} · " \
               f"{_CONTENT_LABELS.get(view.key.content_type, view.key.content_type)}"
        source = "用户已配" if view.source == "configured" else "缺省"
        entries.append({
            "identifier": f"{head} · 允许级别",
            "current": ",".join(lv for lv in SIGNAL_LEVELS if lv in view.allowed_levels),
            "options": [{"value": token} for token, _ in tokens],
            "actions": ["set-cell"],
            "description": f"{source}；允许单独触达的级别（恒含紧急——预算不挡紧急信号）",
            "params": {
                "mode": view.key.mode, "slot_id": view.key.slot_id,
                "content_type": view.key.content_type, "field": "levels",
            },
        })
        entries.append({
            "identifier": f"{head} · 每格上限",
            "current": str(view.max_per_slot),
            "options": [],
            "input": "text",
            "actions": ["set-cell"],
            "description": f"{source}；本格位在该时段内最多单独触达几条（整数，≥1）",
            "params": {
                "mode": view.key.mode, "slot_id": view.key.slot_id,
                "content_type": view.key.content_type, "field": "max",
            },
        })
    return entries


def _policy_entries(policies: Any) -> list[dict[str, Any]]:
    """逐级别的渠道链（有序链 + 未读等待）；取值形如 `desktop,email | 5,15`。

    链与等待是**一对**（不变式 `len(waits) == len(channels) - 1`），故同一条目承载两者：
    分开成两条会让「改链长」变成死锁（改链要新等待、改等待要新链）。等待段可省，
    省则只在链长不变时合法（L5 会显式拒，页面据此提示须补等待段）。
    """
    entries: list[dict[str, Any]] = []
    for policy in policies:
        level = str(policy.level)
        label = _LEVEL_LABELS.get(level, level)
        waits = ",".join(str(wait) for wait in policy.waits_minutes)
        current = f"{','.join(policy.channels)} | {waits}" if waits else ",".join(policy.channels)
        entries.append({
            "identifier": f"渠道链（{label}）",
            "current": current,
            "options": [],
            "input": "text",
            "actions": ["set-policy"],
            "description": (
                f"{'用户已配' if policy.source == 'configured' else '缺省'}；合法渠道键 = "
                f"{' / '.join(CHANNEL_KINDS)}；格式「渠道1,渠道2 | 等待1,等待2」，"
                "等待段可省（链长不变时沿用当前）"
            ),
            "params": {"level": level, "field": "chain"},
        })
    return entries


def _resend_entry(enabled: bool) -> dict[str, Any]:
    """断网补发开关（[07 §3]）。"""
    return {
        "identifier": "网络恢复后补发云端渠道",
        "current": "开启" if enabled else "关闭",
        "options": [],
        "actions": ["resend-on", "resend-off"],
        "description": "断网时云端渠道（邮件 / IM）记为待补发；网络恢复后按此开关补发",
        "params": {},
    }


def _report_time_entry(report_time: Any) -> dict[str, Any]:
    """日报生成时刻（[07 §5]；标量条目，取值 `HH:MM`）。"""
    return {
        "identifier": "日报生成时刻",
        "current": report_time.strftime("%H:%M"),
        "options": [],
        "input": "text",
        "actions": ["set-report-time"],
        "description": "每天什么时候本地生成「给你的信」（24 小时制 HH:MM）；到点生成归常驻循环",
        "params": {},
    }


def _fatigue_threshold_entry(threshold: int) -> dict[str, Any]:
    """推送疲劳阈值（[07 §7]；标量条目）。"""
    return {
        "identifier": "推送疲劳阈值（连续忽略次数）",
        "current": str(threshold),
        "options": [],
        "input": "text",
        "actions": ["set-fatigue-threshold"],
        "description": "某类推送被连续忽略达此次数即发问「要不要减少」（默认 5）",
        "params": {},
    }


def _parse_policy_value(raw: str) -> tuple[list[str], list[int] | None]:
    """渠道链条目的取值 → `(channels, waits)`；等待段省略即 `None`（由 L5 判是否可沿用）。"""
    head, _, tail = raw.partition("|")
    channels = [item.strip() for item in head.split(",") if item.strip()]
    if not tail.strip():
        return channels, None
    waits: list[int] = []
    for item in tail.split(","):
        text = item.strip()
        if not text:
            continue
        if not text.isdigit():
            raise ValueError(f"未读等待须为正整数分钟，收到 {text!r}")
        waits.append(int(text))
    return channels, waits


def _positive_int(raw: str, what: str) -> int:
    """正整数取值（表单回传恒为字符串）；非正整数即拒**不夹取**。"""
    text = raw.strip()
    if not text.isdigit() or int(text) < 1:
        raise ValueError(f"{what} 须为正整数，收到 {raw!r}")
    return int(text)
