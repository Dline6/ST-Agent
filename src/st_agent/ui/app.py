"""表现层组合根：静态资产 + API 路由 + 请求守卫（[00 §1.1]；[D-060]）。

本模块只做**装配与传输**——不引入任何域逻辑。真实数据的接线留给需要它的任务：M1 骨架叶
只证明「管道通、六态对、鉴权硬」（[D-063] 的 M1 最小面口径，见任务假设 `A5`）。

dev 面的开关是**构建期**语义：``st_agent.ui.dev`` 子包在发布构建里被
``[tool.setuptools.packages.find] exclude`` **物理剔除**，剔除后 ``dev=True`` 必须
显式失败（:class:`~st_agent.ui.errors.DevSurfaceUnavailable`），而不是静默降级。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from st_agent import __version__
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.l0.backup.errors import (
    BackupAuthError,
    BackupCorruptedError,
    BackupVersionError,
    RetentionError,
    WipeConfirmationError,
)
from st_agent.l0.storage.errors import (
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
)
from st_agent.l3.render.describe import STANCE_LABELS, describe_divergence_map, describe_trace
from st_agent.l4.errors import BuiltinLensError, LensNotFoundError

from st_agent.ui.envelope import envelope_payload
from st_agent.ui.errors import DevSurfaceUnavailable
from st_agent.ui.neutrality_gate import NeutralityGate
from st_agent.ui.registry import slot_gaps
from st_agent.ui.security import RequestGuard, new_token, resolve_static
from st_agent.ui.tables import Column, table_slots, table_text_kinds

__all__ = ["DEV_SCRIPT_MARKER", "PAGE_PATHS", "UiApp", "WEB_ROOT", "build_ui"]

WEB_ROOT = Path(__file__).resolve().parent / "web"
"""生产静态资产根（无构建 ES modules）。"""

PAGE_PATHS = frozenset({
    "/workspace",
    "/memory",
    "/skills",
    "/mcp",
    "/deliberation",
    "/delivery",
    "/reflection",
    "/reflection/changes",
    "/reflection/experiments",
    "/reflection/training",
    "/settings",
    "/settings/evolution",
    "/eco",
    "/eco/import",
    "/eco/index",
    "/eco/imports",
    "/eco/security",
    "/studio",
})
"""已知**页面路径**——它们回同一静态壳（SPA 回退），与 `ui/web/js/pages.js` 的登记表同源。

本集合＝[11-sitemap §2.2]（[`T-UI-010.2`] 冻结的权威口径）声明的**全部 19 条路径**去掉
主入口 `/`（根路径在 `server.py` 里与 `/index.html` 同处判定，不进本集合）——两端由
`tests/ui/test_ui_shell_routing.py` 的漂移断言逐项钉住。

页面路径属**静态壳档**（只校 `Host` / 存在的 `Origin`），故首次导航不必带令牌
（[D-063] 的令牌分档：令牌只压 `/api/*`）；用户数据的取数一律在 `/api/*` 之后。
**未知路径仍 404**——不把任意路径都当页面（否则静态壳会吞掉资产缺失的错误）。
"""

DEV_SCRIPT_MARKER = "<!--ST_DEV_SCRIPT-->"
"""`index.html` 里的 dev 脚本占位：dev 关闭时被替换为空串，故**发布产物不含 dev 引用**。"""

_VERSION = __version__
"""与 `pyproject.toml` 的 `project.version` 一致——单一真相源＝包根 `st_agent.__version__`。"""

_LOG = logging.getLogger("st_agent.ui.app")


def _now() -> datetime:
    """带时区的当前时刻（01 §8：内部传输时间锚点带时区）。"""
    return datetime.now().astimezone()


def _load_dev() -> Any | None:
    """尝试加载 dev 子包；发布构建里它不存在，返回 ``None``。"""
    try:
        from st_agent.ui import dev  # noqa: PLC0415
    except ImportError:
        return None
    return dev


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


def _studio_envelope(payload: dict[str, Any], label: str) -> Any:
    """Studio 面的返回 → 信封：`available: false` ⇒ `unavailable` + 点名；否则 `ok`。"""
    if not payload.get("available"):
        return ResultEnvelope.unavailable(
            payload.get("reason") or f"未接入{label}面（装配归组合根）",
            last_updated_at=_now(),
        )
    return ResultEnvelope.ok(payload)


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

_STUDIO_CANVAS_TITLE = "Studio 画布"
"""画布描述标题（生成文案，过 [01 §6] 执行点 2）。"""

# ── 各表的列（`table` 槽的**唯一漏斗**在 `ui/tables.py`，形状与呈现角色见其模块文档） ──
#
# 列名是**生成文案**（过 [01 §6] 执行点 2）；表要用 `table_slots` / `table_text_kinds` 出，
# 不在这里手拼 slots——手拼正是 2026-10-09 那处「产出方与渲染件形状不一致、六张表全空」的成因
# （[D-105] ①）。

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

_INBOX_COLUMNS = (
    Column("file_name", "文件名"),
    Column("size", "大小（字节）", kind="number", digits=0),
)
"""导入收件目录的表。"""

_INDEX_COLUMNS = (
    Column("kind", "类别"),
    Column("name", "名称"),
    Column("version", "版本"),
    Column("description", "说明"),
    Column("download_url", "下载地址"),
    Column("checksum", "校验和"),
)
"""官方 Skill 索引的表（[09 §6]）。"""

_IMPORT_COLUMNS = (
    Column("import_id", "留痕"),
    Column("kind", "种类"),
    Column("installed_id", "装入标识"),
    Column("sharer", "分享者"),
    Column("imported_at", "导入时间"),
)
"""导入留痕的表（[09 §7] 的来源追溯）。"""

_VIOLATION_COLUMNS = (
    Column("skill_id", "能力"),
    Column("violation", "越界类别"),
    Column("occurred_at", "时刻"),
    Column("trace_id", "关联推理链"),
    Column("disabled", "已禁用"),
)
"""越界行为留痕的表（[09 §3]）。"""

_POSITION_COLUMNS = (
    Column("code", "代码"),
    Column("code_name", "名称"),
    Column("close", "收盘", kind="number"),
    Column("pct_chg", "涨跌幅", kind="direction"),
    Column("turn", "换手率", kind="number"),
)
"""记忆区的**持仓 / 关注行情读面**（[`T-UI-009.2`]）。

`pct_chg` 是 `direction` 列——**三路冗余编码（颜色 + ▲▼ + 带符号数值）由渲染件一次产出**
（[13-visual-design §2.2]），本层只给**数值**；某标的在本地缓存里没有日线时给 `None`，
由渲染面出「无数据」，**不**冒充 0、**不**读作「平」。"""

_POSITION_SCOPES: dict[str, tuple[str, str]] = {
    "holdings": ("持仓", "记忆中没有持仓信息"),
    "watchlist": ("关注池", "记忆中没有关注池信息"),
}
"""两段的**面键** →（段名 · 空态原因）。原因措辞与 [05 §2] 上下文卡片的同段一致：
空是邀请（说清空的原因），不是留白。"""

_MEMORY_VIEW_LABELS: dict[str, str] = {
    "graph": "图谱",
    "list": "列表",
    "timeline": "时间线",
}
"""三视图的中文名（空态原因与标题用）。"""

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

_MEMORY_LIST_COLUMNS = (
    Column("label", "记忆"),
    Column("type", "类型"),
    Column("confidence", "置信度(存储)", kind="number"),
    Column("effective_confidence", "置信度(有效)", kind="number"),
    Column("updated_at", "更新时刻"),
    Column("source", "来源"),
)
"""记忆区**列表视图**的列（[11-sitemap §2 记忆]）。

置信度**有效值**与**存储值并置**（[04 §5]：衰减与重估算出、不回写盘）——两列同现使
「衰减了多少」可追溯，不合成一个「总分」。类型 / 来源两列的值由描述层按生成文案表解析
（前端不自带词表，[D-064]）；标签列是记忆本体（`data` 槽，原样呈现，[D-053]）。"""


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


def _confidence_bucket(value: Any) -> str:
    """置信度浮点 → 图谱视图的三档标签（`high` / `medium` / `low`）。

    分档只是 `graph_view` 的**呈现词汇**（渲染件样例即三档），不是第二套置信度语义——
    浮点原值与有效值在**列表视图**并置呈现（[_MEMORY_LIST_COLUMNS]），故「衰减了多少」
    仍可追溯。缺值给 `low`（渲染件回退 `低`），不冒充 `medium`。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "low"
    if number >= 0.7:
        return "high"
    if number >= 0.4:
        return "medium"
    return "low"


def _iso_day(value: Any) -> str:
    """ISO 串 → 日期串（时间线视图的 `at` 槽；取日期部分，坏值原样给，不抛）。"""
    if not isinstance(value, str) or not value:
        return ""
    return value[:10]


def _fmt_confidence(value: Any) -> str:
    """置信度 → 两小数浮点串（有效值与存储值同款呈现，不四舍五入成档）。"""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_value(value: Any) -> str:
    """专属字段值 → 一行文本（集合用「、」连、缺值给「—」）。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) or "—"
    if isinstance(value, dict):
        return "；".join(f"{key}={_fmt_value(item)}" for key, item in value.items()) or "—"
    return str(value)


# ───────────────────── 设置区的纯函数（[T-UI-018]） ─────────────────────

_STATE_LABELS = {
    "ok": "正常", "corrupted": "损坏", "missing": "缺失", "locked": "未解锁",
}
"""分区状态 → 中性中文（`StorageState.state` 的取值面）。"""

_KIND_LABELS = {
    "cloud": "云端", "local": "本地推理",
    "llm": "LLM 调用", "market_fetch": "行情抓取", "info_fetch": "信息面抓取",
    "channel_delivery": "渠道投递", "mcp_remote": "远程 MCP", "index_browse": "官方索引浏览",
    "cancelled": "已取消",
}
"""两份取值面共用一张表：端点 `kind`（cloud / local）与出网请求类目（`NetworkRequestKind`）。"""

_STATUS_LABELS = {
    "ok": "完成", "unavailable": "不可用", "failed": "失败", "cancelled": "已取消",
}
"""出网结果 / 六态 → 中性中文。"""

_RETENTION_OPTIONS = {
    "retention.chat_history_days": (30, 90, 180, 365, 730),
    "retention.execution_log_days": (7, 30, 90, 180, 365),
}
"""留存两旋钮的候选值（页面给的是**预设档位**，不是任意整数——[02 §9] 未定默认值以外
的自由度，故页面上以档位选取、避免手输造成的口径漂移）。"""

_MAX_WINDOW_HOURS = 24 * 30
"""网络活动窗口的防跑飞上限（30 天）。"""


def _retention_option_labels() -> dict[str, str]:
    """候选值 → 展示文案（`setting_panel` 的 `labels` 槽按值键取）。"""
    return {str(days): f"{days} 天" for days in _RETENTION_OPTIONS["retention.chat_history_days"]} | {
        str(days): f"{days} 天" for days in _RETENTION_OPTIONS["retention.execution_log_days"]
    }


def _switch_entries(registry: Any, prefix: str) -> list[dict[str, Any]]:
    """把 ``prefix`` 下的布尔条目摆成 `setting_panel` 的 entries（**动作即取值**）。

    布尔条目不走 `options`：`<select>` 的值**恒为字符串**（`"true"` / `"false"`），
    落值面要求的是布尔——用两个动作（`enable` / `disable`）表达取值，字符串这层绕开。
    当前值取条目的 `default`（各族的 `entries()` 已把**当前值**放在该字段）。
    """
    entries = [entry for entry in registry.list() if entry.config_id.startswith(prefix)]
    out: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: e.config_id):
        enabled = bool(entry.default)
        out.append({
            "config_id": entry.config_id,
            "params": {"config_id": entry.config_id},
            "identifier": entry.display_name,
            "current": "启用" if enabled else "停用",
            "options": [],
            "actions": ["enable", "disable"],
            "description": entry.panel_form_spec.help_text,
        })
    return out


def _privacy_entries(registry: Any) -> list[dict[str, Any]]:
    """隐私页的两条族（留存策略 + 出网审计）摆成同一张 entries 表。"""
    out: list[dict[str, Any]] = []
    for config_id, options in _RETENTION_OPTIONS.items():
        entry = registry.entry(config_id)
        if entry is None:
            continue
        out.append({
            "config_id": config_id,
            "params": {"config_id": config_id},
            "identifier": entry.display_name,
            "current": entry.default,
            "options": [{"value": value} for value in options],
            "actions": ["set"],
            "description": entry.panel_form_spec.help_text,
        })
    audit = registry.entry("net-audit/enabled")
    if audit is not None:
        enabled = bool(audit.default)
        out.append({
            "config_id": audit.config_id,
            "params": {"config_id": audit.config_id},
            "identifier": audit.display_name,
            "current": "已开启" if enabled else "已关闭",
            "options": [],
            "actions": ["enable", "disable"],
            "description": audit.panel_form_spec.help_text,
            "note": "关闭时仍有旧记录可查；「待补发」一类查询走显式不可用，不静默返回空集",
        })
    return out


def _provider_entries(registry: Any) -> list[dict[str, Any]]:
    """`llm-provider-host/` 的逐提供方主机映射。

    该族的 `panel_form_spec.widget` 是 **`text`**（主机名不预先枚举），故条目给
    `input: "text"`——`setting_panel` 据此出**自由文本输入**。值域仍由 owner
    （:class:`~st_agent.l1.sandbox.provider_hosts.ProviderHostRegistry`）校验，
    前端不替它枚举候选，也不替它判合法性。
    """
    entries = [
        entry for entry in registry.list()
        if entry.config_id.startswith("llm-provider-host/")
    ]
    out: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: e.config_id):
        out.append({
            "config_id": entry.config_id,
            "params": {"config_id": entry.config_id},
            "identifier": entry.display_name,
            "current": entry.default,
            "options": [],
            "input": "text",
            "actions": ["set"],
            "description": entry.panel_form_spec.help_text,
        })
    return out


def _credential_row(view: Any, vault: Any) -> dict[str, Any]:
    """一条凭据 → `table` 一行（**只取掩码视图**，明文永不进描述）。"""
    uses = ()
    try:
        uses = vault.query_usage(view.credential_id)
    except Exception:  # noqa: BLE001 —— 使用记录读不到不该拖垮整张表
        uses = ()
    last = max((record.timestamp for record in uses), default=None) if uses else None
    return {
        "credential_id": view.credential_id,
        "kind": view.kind,
        "label": view.label or "—",
        "masked": view.masked,
        "version": view.version,
        "updated_at": _stamp(view.updated_at),
        "usage_count": len(uses),
        "last_used_at": _stamp(last) if last is not None else "（未使用）",
    }


def _capability_lines(records: Any, empty: str) -> list[str]:
    """能力声明逐条成行（`display_name` + 降级标注）；空集给中性占位而非留白。"""
    lines = []
    for record in records:
        note = str(getattr(record, "degrade_note", "") or "")
        lines.append(f"{record.display_name}——{note}" if note else str(record.display_name))
    return lines or [empty]


def _capability_text(capability: Any) -> str:
    """端点能力 → 一行摘要（取不到即「—」，不臆造能力）。"""
    if capability is None:
        return "—"
    parts = []
    if getattr(capability, "supports_function_calling", None) is not None:
        parts.append("工具调用" if capability.supports_function_calling else "无工具调用")
    if getattr(capability, "supports_streaming", None) is not None:
        parts.append("流式" if capability.supports_streaming else "非流式")
    tokens = getattr(capability, "max_context_tokens", None)
    if tokens:
        parts.append(f"上下文 {tokens}")
    return " · ".join(parts) or "—"


def _mode_line(mode: str) -> str:
    """落盘格式 → 中性说明（02 §2.2：加密是**可选项**、默认关；凭据恒加密）。"""
    if mode == "plain":
        return "明文落盘（免口令）——凭据分区仍恒加密，访问凭据时才要求口令"
    if mode == "encrypted":
        return "加密落盘——密钥由主密码派生；主密码丢失即不可恢复（无产品方恢复通道）"
    return f"未知格式：{mode or '（缺）'}"


def _store_errors(call: Callable[[], Any]) -> Any:
    """跑一次 L0 存储写面调用，把它的异常翻成**六态**（[01 §5]；`_call_face` 只认 `ValueError`）。

    备份族（`BackupError`）与存储族（`StorageError`）抛的**都不是** `ValueError`；不翻译，
    「密码打错了」「确认不足」就会被当成未预期失败报成 `failed`——既不准确，也让用户以为
    工具坏了。故此处按语义分两路：

    - **输入类**（密码错 / 版本不兼容 / 确认不足 / 留存取值非法 / 目标格式非法）
      ⇒ `validation_failed`；
    - **完整性类**（归档损坏、分区损坏、加解密失败）⇒ `failed` + `log_ref`（真失败，要能查）。

    成功时原样返回被调方的结果。
    """
    try:
        return call()
    except (BackupAuthError, BackupVersionError, WipeConfirmationError, RetentionError,
            StorageOpenError) as exc:
        return ResultEnvelope.validation_failed(str(exc))
    except (BackupCorruptedError, CryptoError, StorageCorruptionError) as exc:
        log_ref = f"ui/settings-store-{uuid.uuid4().hex[:12]}"
        _LOG.warning("存储写面完整性失败（log_ref=%s）：%s", log_ref, exc)
        return ResultEnvelope.failed(str(exc), log_ref=log_ref)


def _bytes_text(size: int) -> str:
    """字节数 → 人可读（二进制档位；口径固定，便于逐次比对）。"""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _window_start(hours: Any) -> datetime:
    """网络活动窗口起点（缺省 24 小时；非正整数 / 超上限即拒——不静默夹取）。"""
    raw = str(hours or "").strip()
    if not raw:
        span = 24
    elif raw.isdigit() and int(raw) > 0:
        span = int(raw)
    else:
        raise ValueError(f"窗口须为正整数小时：{hours!r}")
    if span > _MAX_WINDOW_HOURS:
        raise ValueError(f"窗口不得超过 {_MAX_WINDOW_HOURS} 小时（{raw}）")
    return _now() - timedelta(hours=span)


def _window_text(hours: Any) -> str:
    span = str(hours or "").strip() or "24"
    return f"过去 {span} 小时"


def _stamp(value: Any, *, compact: bool = False) -> str:
    """时刻 → 文本（缺值给「—」；`compact` 用于文件名，去掉分隔符）。"""
    if value is None:
        return "—"
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    if not compact:
        return text
    return "".join(ch for ch in text if ch.isalnum())


def _coerce_value(entry: Any, value: Any) -> Any:
    """表单回传值 → 该条目的声明类型（[01 §7] 的 `value_schema` 是唯一判据）。

    `<select>` / `<input>` 的值**恒为字符串**，而落值面按声明类型校验（布尔 / 整数）。
    这里按 `value_schema.type` 就地还原；还原不了即抛——**不猜测、不静默透传**。
    """
    declared = (getattr(entry, "value_schema", None) or {}).get("type")
    if declared == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "false"):
            return text == "true"
        raise ValueError(f"{entry.config_id} 的取值须为布尔：{value!r}")
    if declared == "number":
        if isinstance(value, bool):
            raise ValueError(f"{entry.config_id} 的取值须为数值：{value!r}")
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if text.lstrip("+-").isdigit():
            return int(text)
        raise ValueError(f"{entry.config_id} 的取值须为整数：{value!r}")
    return value


def _maintenance_slots(kind: str, *, tokens: tuple[str, ...] = ()) -> dict[str, Any]:
    """备份 / 恢复 / 清空三条**声明**的 `permission_approval_card` 槽。

    逐条、不合并（[01 §10] 的取向）：把「这次要动什么」一条一条列清楚，**不给建议**。
    清空的第三步把三枚有序令牌如实摆出——那是 L0 的硬门，不是文案。
    """
    if kind == "backup":
        items = [
            ("backup-memory", "包含：记忆图谱全部分区内容（节点与边）"),
            ("backup-config", "包含：配置注册表数据、Skill 参数、版本锁"),
            ("backup-chat", "包含：对话历史（可搜索、可导出 Markdown）"),
            ("backup-reflection", "包含：反思报告、反馈事件、A/B 实验日志"),
            ("backup-data-cache", "可选：市场数据缓存（默认不纳入；不纳入时档案型文本在源端失效后不可恢复）"),
            ("backup-secrets", "不包含：凭据与执行留痕永不进归档（凭据只在本机加密分区内）"),
        ]
        key, source = "backup", "本机存储"
    elif kind == "restore":
        items = [
            ("restore-verify", "恢复前校验：归档完整性、主密码、版本兼容性——任一项不过即拒且不写脏数据"),
            ("restore-overwrite", "覆盖写：归档内的分区整体替换目标根的同名分区；归档外的分区不动"),
            ("restore-format", "目标格式：新根沿用归档口径（加密）；既有根按其格式标记分派，不擅自改格式"),
            ("restore-target", "默认目标是当前存储根——覆盖写后须重启进程再继续"),
        ]
        key, source = "restore", "备份归档"
    else:
        items = [
            ("wipe-scope", "删除范围：全部分区 + 派生数据 + 缓存 + 日志（存储根整目录）"),
            ("wipe-confirm", "三次确认：三枚有序令牌须按序集齐，缺一即拒"),
            ("wipe-outside", "不触碰：备份归档落在存储根之外，由用户自行处理"),
        ]
        key, source = "wipe", "本机存储"
    for index, token in enumerate(tokens, start=1):
        items.append((f"wipe-token-{index}", f"确认令牌 {index}：{token}"))
    return {
        "key": key,
        "source": source,
        "items": [
            {
                "permission": identifier,
                "state": "pending",
                "decided_at": None,
                "panel_field": {
                    "widget": "toggle", "label": identifier,
                    "help_text": "由页面上的确认流程承载；本卡只声明范围",
                },
            }
            for identifier, _text in items
        ],
        "labels": {identifier: {"description": text, "chat_text": text}
                   for identifier, text in items},
    }


def _wipe_tokens(face: Any) -> tuple[str, ...]:
    """L0 的三枚有序确认令牌（取不到即空——页面据此显式标注，不编造令牌）。"""
    return tuple(getattr(face.backup, "WIPE_CONFIRM_TOKENS", ()) or ())


def _backup_partitions(face: Any) -> tuple[str, ...]:
    """归档必含分区表（[02 §8.1] 的四分区；取不到即空，不臆造）。"""
    return tuple(getattr(face.backup, "BACKUP_PARTITIONS", ()) or ())


def _default_backup_path(face: Any, root: Path) -> Path:
    """缺省归档落点：**存储根之外**的同级目录（清空时不会被一并删掉）。"""
    configured = getattr(face, "backups_dir", None)
    base = Path(configured) if configured is not None else root.parent / f"{root.name}-backups"
    return base / f"st-agent-{_stamp(_now(), compact=True)}.stbackup"


def _is_inside(target: Path, root: Path) -> bool:
    """`target` 是否落在 `root` 之内（**先解析再做后代判定**，符号链接也算）。"""
    try:
        resolved, base = target.resolve(), root.resolve()
    except OSError:  # 不存在的路径在 Windows 上可能抛；退回字面比较
        resolved, base = target.absolute(), root.absolute()
    return resolved == base or base in resolved.parents


@dataclass
class RestartGate:
    """破坏性操作（完全清空 / 恢复）之后的**进程级闸门**。

    ``wipe_all`` 直接删掉存储根整目录、``restore_backup`` 覆盖写当前根——两者都让本进程
    手里的 `Store` **句柄失效**（清单缓存过期，甚至根目录已不存在）。此后的读写要么报错、
    要么**静默作用于陈旧清单**，后者正是必须防的那一类（[00 §6] 失败显式化）。

    故操作成功后置 :attr:`reason`：:meth:`UiApp._call_face` 据此把**全部**数据面端点回
    `unavailable` + 该原因，直到用户重启进程。**不假装进程仍健康**。
    """

    reason: str | None = None


@dataclass(frozen=True)
class UiApp:
    """一次服务进程的装配结果（不可变；由 :func:`build_ui` 构造）。"""

    guard: RequestGuard
    dev: bool = False
    web_root: Path = WEB_ROOT
    dev_package: Any = None
    neutrality_gate: NeutralityGate = field(default_factory=NeutralityGate)
    chat: Any = None
    """对话门面（鸭子类型 ``turn(body) -> dict``）；缺省 ``None`` → 对话端点 fail-closed。

    由 M1 关卡的组合根注入（`ui` **不 import** `st_agent.app`，只经此鸭子端口消费，
    否则破 `test_ui_is_client_only`；见 [T-INT-002] 假设 A2）。
    """

    reflection: Any = None
    """L6 反思演进栈（鸭子端口；缺省 ``None`` → 反思 / 演进而端点 fail-closed）。

    同一端口服务 `/api/reflection/*` 与 `/api/evolution/*`——两者同属 L6（[08-L6] 的
    反思面与演进面）。由生产入口注入（`ui` 不 import `l6` 的类型，也不 import `app`）。
    """

    eco: Any = None
    """生态面（鸭子端口：`exporter` / `importer` / `index`）；缺省 ``None`` → 生态面 fail-closed。"""

    memory: Any = None
    """记忆区读面（鸭子端口：``positions(scope) -> dict``）；缺省 ``None`` → 记忆区 fail-closed。

    **与其余三端口同一形**：由组合根注入适配面（`MemoryPositionsFacade`），`ui` 不 import
    `l2` / `l0` 的类型。注意它与「面在、但行情源没注入」是**两回事**——后者是面自己回的
    ``available=False``（表现为 `unavailable` + 原因并有最后更新时间），本条只管「面本身没接」。
    """

    # ── M6 各面（七个新鸭子端口；[`T-UI-011.1`]） ──────────────────────────
    # 每个端口由组合根注入同名薄门面（[`st_agent.app`]），`ui` 不 import 任何层类型；
    # 缺省 `None` ⇒ 该面探针 fail-closed（`unavailable` + 点名，不 500、不装空表）。
    # **端口名 = 面名**，故 `api_face_status("skills")` 的 `getattr` 直接可用。

    workspace: Any = None
    """工作区（钉住的持久组件）面（鸭子端口：``pins()`` / ``pin(body)`` / ``unpin(body)`` /
    ``refresh(body)``）；缺省 ``None`` → fail-closed。

    由组合根注入 [`WorkspaceFacade`](../../src/st_agent/l3/workspace.py)——**重解析来源表
    也在那一侧**（真取数口住组合根，表现层不认识来源面键背后的任何东西，[01 §12] 的
    「动作不进描述」）。`ui` 不 import `l3` / `l0` 的类型，只经此鸭子端口消费。
    """

    skills: Any = None
    """L1 能力（Skill）面；缺省 ``None`` → fail-closed。"""

    mcp: Any = None
    """L1 能力（MCP）面；缺省 ``None`` → fail-closed。"""

    graph: Any = None
    """L2 记忆图谱面（服务既有 `/memory` 的三视图 / 画像）；缺省 ``None`` → fail-closed。"""

    deliberation: Any = None
    """L4 多视角推理面；缺省 ``None`` → fail-closed。"""

    delivery: Any = None
    """L5 主动触达面；缺省 ``None`` → fail-closed。"""

    settings: Any = None
    """L0 存储 / 配置面；缺省 ``None`` → fail-closed。"""

    restart_gate: RestartGate = field(default_factory=RestartGate)
    """破坏性操作后的进程级闸门（见 :class:`RestartGate`）。

    ``frozen`` 的 :class:`UiApp` 装的是一枚**可变**持有者——闸门要在端点里被置位，
    而整个 app 的其余部分不该跟着变。`build_ui` 可注入同一实例供集成套件断言。
    """

    commands: Any = None
    """快捷指令注册表（鸭子端口：``list() -> tuple[QuickCommand, ...]``）；缺省 ``None`` → fail-closed。

    [05 §8](../../docs/技术架构-v2/05-L3-对话主入口.md) 的命令注册表由 L3 维持（含官方 Pack
    的 `command` kind 整组替换），故首页的 `/` 补全清单**经此端口取真注册表**、不在前端另造
    一份枚举。由组合根注入 `M1Runtime.commands`；`ui` 不 import `l3` 的类型。
    """

    @property
    def dev_enabled(self) -> bool:
        """dev 面是否**在本进程内真实可用**（开关打开 **且** 子包在盘上）。"""
        return self.dev and self.dev_package is not None

    def api_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """对话端点：经注入的门面走一段反向流，出站点仍过**同一条**校验。

        门面的返回含 `reply`（`ResultEnvelope`，附六态渲染语义）、可选
        `description`（`UiDescription`，走 :meth:`api_description` 的中性化门与必填槽）与可选
        `clarification`（澄清轮：问项 / 方向候选 / 超预算默认项，[05 §3.2]，原样透传）。
        未注入门面 → `unavailable` + 点名（不伪造，同 `api_health` 之外的各 fail-closed 面）。

        **本方法不向上抛**（[00 §6] 失败显式化）：非法请求体（门面在构造契约对象时
        拒绝，属 `ValueError` / `KeyError` 族）转 `validation_failed`；其余未预期异常
        记 traceback 后转 `failed`（附可查 `log_ref`）——两端都回信封，连接不被关闭。
        """
        if self.chat is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入对话门面，/api/chat 不可用（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        try:
            result = self.chat.turn(body)
        except (ValueError, KeyError) as exc:
            # 请求体非法 → 门面构造契约对象时拒绝：pydantic 包装的 ValidationError 与 L3 各
            # *ValidationError 均属 ValueError；未知 session_id 属 SessionNotFoundError(KeyError)。
            _LOG.info("对话请求体被门面拒绝（%s）：%s", type(exc).__name__, exc)
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    "对话请求体不合法，无法受理"
                    "（需非空 text；action=dispatch 需带待确认的 session_id）"
                )
            )
        except Exception as exc:  # noqa: BLE001 —— 真正的编程/内部错误：不吞，落日志 + 显式 failed
            log_ref = f"ui/chat-{uuid.uuid4().hex[:12]}"
            _LOG.exception("对话门面未预期异常（log_ref=%s）：%s", log_ref, exc)
            return envelope_payload(
                ResultEnvelope.failed("对话处理未预期失败，详见服务端日志", log_ref=log_ref)
            )
        reply = result.get("reply")
        payload = envelope_payload(reply) if isinstance(reply, ResultEnvelope) else {
            "status": "failed", "reason": "对话门面返回非法结构", "render": {},
        }
        payload["session_id"] = result.get("session_id")
        payload["needs_confirmation"] = bool(result.get("needs_confirmation"))
        payload["clarification"] = result.get("clarification")
        description = result.get("description")
        if description is not None:
            described = envelope_payload(ResultEnvelope.ok(description))
            gaps = slot_gaps(description)
            verdict = self.neutrality_gate.check(description)
            if gaps or not verdict.passed:
                payload["description"] = envelope_payload(
                    ResultEnvelope.validation_failed(
                        "、".join(gaps) if gaps else verdict.reason
                    )
                )
            else:
                payload["description"] = described
        else:
            payload["description"] = None
        # 各视角推理链（[`T-UI-016.3`]）：`analyze` 去向的结论卡据此展开「推理链」面板——
        # 逐条与 `description` 同一道门（缺链条目 `description` 给 `None`，渲染面显式告知）。
        payload["traces"] = self._chat_traces(result.get("traces"))
        return payload

    def _chat_traces(self, raw: Any) -> list[dict[str, Any]]:
        """对话返程里的各视角链 → 出站载荷（逐条过必填槽 + 中性化门）。"""
        out: list[dict[str, Any]] = []
        for entry in tuple(raw or ()):
            if not isinstance(entry, Mapping):
                continue
            description = entry.get("description")
            if description is None:
                described: Any = None
            else:
                try:
                    described = envelope_payload(
                        ResultEnvelope.ok(self._gate_description(description))
                    )
                except ValueError as exc:
                    described = envelope_payload(ResultEnvelope.validation_failed(str(exc)))
            out.append({
                "lens_id": entry.get("lens_id"),
                "trace_id": entry.get("trace_id"),
                "description": described,
            })
        return out

    def api_chat_context(self) -> dict[str, Any]:
        """首页上下文卡片（[05 §2]）：经注入的对话门面取卡片描述（`context_card`）。

        门面缺席、或其未持 `context_card` 取数口 ⇒ `unavailable` + 点名（不伪造一张卡片）。
        卡片六段与非 ok 段的「原因 + 生产方」由 L3 的 `describe_context_card` 给出，表现层
        只转发（[01 §5] 六态不混用）。
        """
        face = self.chat
        if face is None or not hasattr(face, "context_card"):
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入上下文卡片面：对话门面或 context_card 取数口缺席"
                    "（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        return self._call_face(
            "chat-context", face, "上下文卡片面", lambda f: f.context_card(),
        )

    def api_chat_commands(self) -> dict[str, Any]:
        """快捷指令清单（[05 §8]）：经注入的指令注册表取条目（名称 / 说明 / 意图 / 来源）。

        注册表未接 ⇒ `unavailable` + 点名（不摆假清单、不前端硬编码）。指令名与说明在
        注册时已过中性门（[01 §6] 执行点 1），故此处只转发、不复检。
        """
        return self._call_face(
            "chat-commands", self.commands, "快捷指令注册表",
            lambda face: self._commands_envelope(face),
        )

    def _commands_envelope(self, registry: Any) -> ResultEnvelope:
        commands = tuple(registry.list())
        if not commands:
            return ResultEnvelope.empty("快捷指令注册表为空（无可用指令）")
        return ResultEnvelope.ok(
            [
                {
                    "name": command.name,
                    "description": command.description,
                    "intent": command.intent,
                    "source": command.source,
                }
                for command in commands
            ]
        )

    # ───────────────────── 推理区（[T-UI-016.1]） ─────────────────────
    # 页内**自带触发**一次多视角编排（[D-112] ②：无会话态、无落盘），并把结果按 `/api/chat`
    # 的先例**分多段**回（`reply` 信封 + 各自独立渲染的描述段）。阵容面复用 `setting_panel`
    # （新增 `deliberation-lens` 面键），中性化校验由 `LensRoster.add_custom` 把关，不前置到前端。

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

    # ───────────────────── 决策记录（[T-UI-016.3]） ─────────────────────

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

    def api_health(self) -> dict[str, Any]:
        """健康面：恒为 `ok`——本叶不接真实数据（任务假设 `A5`）。"""
        return envelope_payload(
            ResultEnvelope.ok(
                {"service": "st-agent-ui", "version": _VERSION, "dev": self.dev_enabled}
            )
        )

    def api_face_status(self, face: str) -> dict[str, Any]:
        """面可用性探针：端口在 ⇒ `ok`；不在 ⇒ `unavailable` + 点名。

        表现层壳用它决定导航项是否可用。**「未接入」与「接入但无数据」是两回事**——
        前者是本进程没拿到该面的装配口（点名归属），后者由各面自己的读面给空态
        （[01 §5] 六态不可混用，同 [08 §4]「不由计数 0 反推」的口径）。
        """
        if getattr(self, face, None) is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    f"未接入 {face} 面：该面端点在本次装配中不可用（装配归生产入口）",
                    last_updated_at=_now(),
                )
            )
        return envelope_payload(ResultEnvelope.ok({"face": face, "available": True}))

    # ───────────────────── 设置区（[T-UI-018]） ─────────────────────

    def api_settings_storage(self) -> dict[str, Any]:
        """设置-存储页：**存储位置与大小** + 落盘格式 + 凭据分区锁定态（[02 §2.1–§2.3]）。

        两段：`summary`（`report_card`：位置 / 格式 / 凭据分区）与 `partitions`（`table`：
        逐分区状态 / 文件数 / 明文字节数）。用量取自 :meth:`Store.usage`——**未解锁或损坏的
        分区不谎报为 0**，其 `detail` 与该行的「状态」列一并透出。
        """
        return self._settings_parts(
            "settings-storage", ("store",), ("summary", "partitions"),
            lambda face: self._storage_parts(face),
        )

    def api_settings_llm(self) -> dict[str, Any]:
        """设置-LLM 提供方页：提供方主机映射（`setting_panel`）+ 端点 + 凭据（掩码）。

        三段**并列**，不合并：端点（`EndpointRegistry`）、提供方→主机映射（`llm-provider-host/`
        的 01 §7 条目，可改值）、凭据（`CredentialVault` 的掩码视图 + 使用记录计数）。
        **凭据明文永不出站**——页面只拿到 `CredentialView.masked`。
        """
        return self._settings_parts(
            "settings-llm", ("endpoints", "vault"),
            ("panel", "endpoints", "credentials"),
            lambda face: self._llm_parts(face),
        )

    def api_settings_sources(self) -> dict[str, Any]:
        """设置-数据源页：逐源开关（`setting_panel`，面键 `settings-config`；story-05）。"""
        return self._settings_parts(
            "settings-sources", ("config_registry",), ("panel",),
            lambda face: {"panel": self._part(
                "setting_panel",
                slots={
                    "entries": _switch_entries(face.config_registry, "data-source/"),
                    "surface": "settings-config",
                    "labels": {"enable": "启用", "disable": "停用"},
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title="数据源开关",
            )},
        )

    def api_settings_privacy(self) -> dict[str, Any]:
        """设置-隐私页：数据留存策略 + 出网审计开关（两条都经 [01 §7] 注册表落值）。

        **两条族混在一个面板里**（`retention.*` 与 `net-audit/enabled`）——它们都是
        「本机的数据边界」旋钮，且落值走**同一条** `/api/settings/apply`（[01 §7] 的统一
        落值面），故不必拆两面。
        """
        return self._settings_parts(
            "settings-privacy", ("config_registry",), ("panel",),
            lambda face: {"panel": self._part(
                "setting_panel",
                slots={
                    "entries": _privacy_entries(face.config_registry),
                    "surface": "settings-config",
                    "labels": {
                        "set": "应用", "enable": "开启", "disable": "关闭",
                        **_retention_option_labels(),
                    },
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title="隐私与留存",
            )},
        )

    def api_settings_network(self, *, hours: str = "") -> dict[str, Any]:
        """网络活动面板：过去 N 小时（缺省 24）的出网留痕（`table`）。

        **三态互不混同**（[02 §6]）：审计开启且有记录 ⇒ `ok`；审计开启但窗口内无记录 ⇒
        `empty` + 原因；**审计未开启 ⇒ `unavailable` + 原因**——不得静默呈现空表被读成
        「没有请求」。
        """
        return self._call_face(
            "settings-network", self._gateway_face(), "L0 出网网关面",
            lambda face: self._network_envelope(face, hours),
        )

    def api_settings_offline(self) -> dict[str, Any]:
        """离线横幅与三类清单（[02 §7] / [11-sitemap §3.9]）。

        `report_card` 四段并列：离线态 + 三类能力（可用 / 受限 / 不可用）——**受限项带
        降级标注**（本地替代说明或快照过期），依赖 LLM 的能力由 `cloud-llm` 那条如实写明
        「断网时不可用；已配置本地推理端点则改用本地端点」。
        """
        return self._call_face(
            "settings-offline", self._gateway_face(), "L0 出网网关面",
            lambda face: ResultEnvelope.ok(self._offline_description(face)),
        )

    def api_settings_maintenance(self) -> dict[str, Any]:
        """维护面：备份 / 恢复 / 清空**三条声明**（各一张 `permission_approval_card`）。

        声明面只讲「这次要动什么」，**不代用户表态**——密码输入与三步确认由页面 chrome
        承载（先例＝ [`renderEvolutionPage`] 的出厂重置块）。清空的「三次确认」在 L0 是
        三枚**有序**令牌（`:data:`WIPE_CONFIRM_TOKENS``），此处如实列出。
        """
        return self._settings_parts(
            "settings-backup", ("backup", "store"), ("backup", "restore", "wipe"),
            lambda face: {
                "backup": self._part(
                    "permission_approval_card",
                    slots=_maintenance_slots("backup"),
                    text_kinds={"key": "data", "source": "data", "items": "data",
                                "labels": "generated"},
                    title="一键备份",
                ),
                "restore": self._part(
                    "permission_approval_card",
                    slots=_maintenance_slots("restore"),
                    text_kinds={"key": "data", "source": "data", "items": "data",
                                "labels": "generated"},
                    title="恢复",
                ),
                "wipe": self._part(
                    "permission_approval_card",
                    slots=_maintenance_slots("wipe", tokens=_wipe_tokens(face)),
                    text_kinds={"key": "data", "source": "data", "items": "data",
                                "labels": "generated"},
                    title="完全清空",
                ),
            },
        )

    def api_settings_apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """应用一条设置——经 [01 §7] 统一落值面生效并留痕（**不旁路写盘**，GWT-2）。

        动作三选一：`set`（取 `value`）/ `enable` / `disable`（布尔条目的两个动作）。
        表单控件一律以**字符串**回传（`<select>` / `<input>` 的值都是字符串），故此处按
        条目的 ``value_schema.type`` **就地还原成声明类型**再落值——类型不符即
        `validation_failed`，不猜测。
        """
        return self._call_face(
            "settings-apply", self.settings, "L0 存储 / 配置面",
            lambda face: ResultEnvelope.ok(self._apply_config(face, body)),
        )

    def api_settings_convert(self, body: dict[str, Any]) -> dict[str, Any]:
        """明密双向转换：**源不动、写新根**（[02 §2.2](../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

        转换**不碰当前存储根**（[`Store.convert`] 收尾整目录 `os.replace` 到目标路径），
        故本进程的句柄仍然有效、不置闸门——但**切换存储根须重启进程并指向新根**，
        回报里明说这一点。目标落在当前根之内即拒：那等于把新根写进旧根里。
        """
        return self._call_face(
            "settings-convert", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_convert(face, body),
        )

    def api_settings_backup_run(self, body: dict[str, Any]) -> dict[str, Any]:
        """一键备份：**输入密码 → 单个加密文件**（[02 §8.1]；本体归 L0，本层只做门）。

        归档**不得落在存储根之内**——`wipe_all` 删的是整个存储根，把备份放进去等于
        清空时连备份一起删（[02 §8.3] 明说「备份文件位置由用户自行处理」）。
        """
        return self._call_face(
            "settings-backup-run", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_backup(face, body),
        )

    def api_settings_restore(self, body: dict[str, Any]) -> dict[str, Any]:
        """恢复：**输入密码 → 完整还原**；成功后置重启闸门（覆盖写的是运行中的存储根）。"""
        return self._call_face(
            "settings-restore", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_restore(face, body),
        )

    def api_settings_wipe(self, body: dict[str, Any]) -> dict[str, Any]:
        """完全清空：**三次确认**（三枚有序令牌）→ 彻底删除本地数据；成功后置重启闸门。"""
        return self._call_face(
            "settings-wipe", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_wipe(face, body),
        )

    def api_settings_credential(self, body: dict[str, Any]) -> dict[str, Any]:
        """凭据新增 / 重设 / 吊销（[02 §3]；`secrets` 分区恒加密、惰性解锁）。

        未解锁即 `unavailable` + 点名——**不静默当作「没有凭据」**。
        """
        return self._call_face(
            "settings-credential", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_credential(face, body),
        )

    def api_settings_online(self, body: dict[str, Any]) -> dict[str, Any]:
        """同步在线态（[02 §7] 的检测面缺口由**出口侧**补上）。

        平台侧没有网络探测（`EgressGateway.online` 是手动态），而**唯一的探测器就在出口**：
        浏览器。壳把 `navigator.onLine` 的变化报到这里，经 `L1Runtime.set_online` 翻转—
        从离线翻回在线时**按策略补跑**待补到期点（03 §6）。重复报同一态无副作用。
        """
        return self._call_face(
            "settings-online", self.settings, "L0 存储 / 配置面",
            lambda face: self._set_online(face, body),
        )

    def api_settings_network_export(self, body: dict[str, Any]) -> dict[str, Any]:
        """导出网络活动报告到导出目录（[02 §6]「可导出」）；回路径与条数。"""
        return self._call_face(
            "settings-network-export", self._gateway_face(),
            "L0 出网网关面",
            lambda face: self._export_network(face, body),
        )

    # ── 设置区的取数装配 ──────────────────────────────────────────────

    def _gateway_face(self) -> Any:
        """出网网关的取数口（挂在 `settings` 门面上；门面缺席即 `None`，由 `_call_face` 点名）。"""
        return None if self.settings is None else getattr(self.settings, "gateway", None)

    def _part(self, component_type: str, *, slots: dict[str, Any],
              text_kinds: dict[str, str], title: str | None = None) -> dict[str, Any]:
        """一段 `ok` 出站载荷（过本层两道闸；不过闸即抛，由调用方转 `validation_failed`）。"""
        return envelope_payload(ResultEnvelope.ok(
            self._gated_description(
                component_type, slots=slots, text_kinds=text_kinds, title=title
            )
        ))

    def _settings_parts(
        self,
        where: str,
        requires: tuple[str, ...],
        keys: tuple[str, ...],
        fn: Callable[[Any], dict[str, Any]],
    ) -> dict[str, Any]:
        """多段设置端点：**任一失败，各段都是同一支错误信封**（同 `analyze` 的分段口径）。

        `requires` 是该端点用到的门面字段——缺任一项即 `unavailable` + 点名（装配归组合根），
        不半途抛 `AttributeError` 变成一条假的 `failed`。
        """
        def blank(envelope: ResultEnvelope) -> dict[str, Any]:
            payload = envelope_payload(envelope)
            return {key: payload for key in keys}

        if self.restart_gate.reason:
            return blank(ResultEnvelope.unavailable(
                self.restart_gate.reason, last_updated_at=_now()
            ))
        face = self.settings
        if face is None:
            return blank(ResultEnvelope.unavailable(
                "未接入 L0 存储 / 配置面，该端点不可用（装配归组合根）",
                last_updated_at=_now(),
            ))
        missing = [name for name in requires if getattr(face, name, None) is None]
        if missing:
            return blank(ResultEnvelope.unavailable(
                f"设置面缺句柄：{'、'.join(missing)}（装配归组合根）", last_updated_at=_now()
            ))
        try:
            return fn(face)
        except (ValueError, KeyError) as exc:
            _LOG.info("表现层端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
            return blank(ResultEnvelope.validation_failed(str(exc) or "请求不合契约"))
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/{where}-{uuid.uuid4().hex[:12]}"
            _LOG.exception("表现层端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
            return blank(ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref))

    def _storage_parts(self, face: Any) -> dict[str, Any]:
        store = face.store
        root = getattr(store, "root", None)
        if root is None:
            raise ValueError("存储句柄未透出根目录（装配归组合根）")
        mode = str(getattr(store, "mode", ""))
        unlocked = bool(getattr(store, "secrets_unlocked", False))
        usage = store.usage()
        total = sum(item.bytes for item in usage)
        summary = self._part(
            "report_card",
            slots={"sections": [
                {"title": "存储位置", "lines": [str(root)]},
                {"title": "落盘格式", "lines": [_mode_line(mode)]},
                {"title": "凭据分区", "lines": [
                    "已解锁（本次会话可读写 API Key 等凭据）" if unlocked
                    else "未解锁——访问凭据时才要求口令；未解锁时凭据清单与增改一并不可用"
                ]},
                {"title": "占用合计", "lines": [f"{_bytes_text(total)}（全部分区的明文字节数之和）"]},
            ]},
            text_kinds={"sections": "data"},
            title="存储",
        )
        rows = [
            {
                "partition": item.partition,
                "state": _STATE_LABELS.get(item.state, item.state),
                "file_count": item.file_count,
                "bytes": item.bytes,
                "detail": item.detail or "",
            }
            for item in usage
        ]
        partitions = self._part(
            "table",
            slots=table_slots(
                (
                    Column("partition", "分区"),
                    Column("state", "状态"),
                    Column("file_count", "文件数", "number", digits=0),
                    Column("bytes", "占用（明文字节）", "number", digits=0),
                    Column("detail", "说明"),
                ),
                rows,
            ),
            text_kinds=table_text_kinds(),
            title="分区与用量",
        )
        return {"summary": summary, "partitions": partitions}

    def _llm_parts(self, face: Any) -> dict[str, Any]:
        registry = face.config_registry
        panel = self._part(
            "setting_panel",
            slots={
                "entries": _provider_entries(registry),
                "surface": "settings-config",
                "labels": {"set": "应用"},
            },
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title="提供方主机映射",
        )
        endpoints = [
            {
                "provider": getattr(item, "provider", ""),
                "kind": _KIND_LABELS.get(str(getattr(item, "kind", "")), "—"),
                "model": str(getattr(item, "model", "") or "—"),
                "priority": int(getattr(item, "priority", 0)),
                "credential": str(getattr(item, "credential_id", "") or "（未绑定）"),
                "capability": _capability_text(getattr(item, "capability", None)),
            }
            for item in face.endpoints.list_endpoints()
        ]
        endpoints_part = (
            self._part(
                "table",
                slots=table_slots(
                    (
                        Column("provider", "提供方"),
                        Column("kind", "类型"),
                        Column("model", "模型"),
                        Column("priority", "优先级", "number", digits=0),
                        Column("credential", "凭据"),
                        Column("capability", "能力"),
                    ),
                    endpoints,
                ),
                text_kinds=table_text_kinds(),
                title="已登记端点",
            )
            if endpoints
            else envelope_payload(ResultEnvelope.empty(
                "尚未登记任何 LLM 端点——请先新增端点与凭据（或经启动参数装载引导配置）"
            ))
        )
        vault, store = face.vault, face.store
        if not bool(getattr(store, "secrets_unlocked", False)):
            credentials_part = envelope_payload(ResultEnvelope.unavailable(
                "凭据分区未解锁（凭据恒加密、惰性解锁）：本次会话读不到凭据清单，"
                "亦不可增改——经启动参数提供凭据口令后可用",
                last_updated_at=_now(),
            ))
        else:
            views = list(vault.list_credentials())
            credentials_part = (
                self._part(
                    "table",
                    slots=table_slots(
                        (
                            Column("credential_id", "标识"),
                            Column("kind", "类别"),
                            Column("label", "名称"),
                            Column("masked", "掩码"),
                            Column("version", "版本", "number", digits=0),
                            Column("updated_at", "最近更新"),
                            Column("usage_count", "使用次数", "number", digits=0),
                            Column("last_used_at", "最近使用"),
                        ),
                        [_credential_row(view, vault) for view in views],
                    ),
                    text_kinds=table_text_kinds(),
                    title="凭据（掩码）",
                )
                if views
                else envelope_payload(ResultEnvelope.empty(
                    "凭据分区已解锁，但本机尚无凭据——新增后在此列出（只显示掩码）"
                ))
            )
        return {"panel": panel, "endpoints": endpoints_part, "credentials": credentials_part}

    def _network_envelope(self, gateway: Any, hours: str) -> ResultEnvelope:
        since = _window_start(hours)
        events = gateway.query(since=since)
        if not gateway.audit_enabled:
            return ResultEnvelope.unavailable(
                f"出网审计未开启，无记录可查（窗口 {_window_text(hours)}）；"
                "开启后逐次留痕——这是「未开启」，不是「没有请求」",
                last_updated_at=_now(),
            )
        if not events:
            return ResultEnvelope.empty(
                f"审计已开启，{_window_text(hours)}内没有对外网络请求", as_of=_now()
            )
        rows = [
            {
                "timestamp": _stamp(event.timestamp),
                "initiator": str(event.initiator),
                "kind": _KIND_LABELS.get(str(event.kind), str(event.kind)),
                "target_host": str(event.target_host),
                "purpose": str(event.purpose),
                "bytes": int(event.bytes_out) + int(event.bytes_in),
                "status": _STATUS_LABELS.get(str(event.status), str(event.status)),
            }
            for event in events
        ]
        return ResultEnvelope.ok(self._part(
            "table",
            slots=table_slots(
                (
                    Column("timestamp", "时刻"),
                    Column("initiator", "发起方"),
                    Column("kind", "类目"),
                    Column("target_host", "目标主机"),
                    Column("purpose", "目的"),
                    Column("bytes", "数据量（字节）", "number", digits=0),
                    Column("status", "结果"),
                ),
                rows,
            ),
            text_kinds=table_text_kinds(),
            title=f"网络活动（{_window_text(hours)}）",
        ))

    def _offline_description(self, gateway: Any) -> UiDescription:
        report = gateway.offline_report()
        online = bool(getattr(gateway, "online", True))
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "连接状态", "lines": [
                    "在线" if online
                    else "离线模式——历史数据可查、记忆可编辑、已缓存 Skill 可运行"
                ]},
                {"title": "离线可用", "lines": _capability_lines(report.available, "（无）")},
                {"title": "可用但受限", "lines": _capability_lines(report.limited, "（无）")},
                {"title": "必须联网", "lines": _capability_lines(report.unavailable, "（无）")},
            ]},
            text_kinds={"sections": "data"},
            title="离线降级",
            as_of=report.generated_at,
        )

    def _apply_config(self, face: Any, body: dict[str, Any]) -> dict[str, Any]:
        registry = face.config_registry
        config_id = str(_required(body, "config_id"))
        action = str(body.get("action") or "set")
        entry = registry.entry(config_id)
        if entry is None:
            raise ValueError(f"该设置面不认这个条目：{config_id!r}（不在统一配置注册表内）")
        if action in ("enable", "disable"):
            value: Any = action == "enable"
        elif action == "set":
            if "value" not in body:
                raise ValueError(f"{config_id} 的 set 动作须给出 value")
            value = _coerce_value(entry, body.get("value"))
        else:
            raise ValueError(f"未知的设置动作：{action!r}（只认 set / enable / disable）")
        change = registry.set(config_id, value, trace_id=f"ui-settings/{config_id}")
        return {
            "config_id": config_id,
            "changed": change is not None,
            "change_id": getattr(change, "change_id", None),
        }

    def _run_convert(self, face: Any, body: dict[str, Any]) -> Any:
        to_format = str(_required(body, "to_format")).strip()
        target = str(_required(body, "target")).strip()
        root = Path(face.store.root)
        current = str(getattr(face.store, "mode", ""))
        if to_format not in ("plain", "encrypted"):
            raise ValueError(f"目标格式只认 plain / encrypted：{to_format!r}")
        if to_format == current:
            raise ValueError(f"当前存储已是该格式（{current}），无需转换")
        dest = Path(target)
        if _is_inside(dest, root):
            raise ValueError(
                f"目标根不得落在当前存储根之内（{dest}）——新根须与源并列或在他处"
            )
        if face.convert_store is None:
            return ResultEnvelope.unavailable(
                "明密转换口未注入设置面（装配归组合根）", last_updated_at=_now()
            )
        envelope = _store_errors(lambda: face.convert_store(
            root,
            dest,
            to_format=to_format,
            source_passphrase=str(body.get("source_passphrase") or "").strip() or None,
            target_passphrase=str(body.get("target_passphrase") or "").strip() or None,
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        return {
            "target": str(dest),
            "to_format": to_format,
            "note": "源根未改动；凭据分区原样搬运，凭据口令不变。"
                    "切换存储根须重启进程并指向新根",
        }

    def _run_backup(self, face: Any, body: dict[str, Any]) -> Any:
        passphrase = str(_required(body, "passphrase"))
        root = Path(face.store.root)
        dest = str(body.get("dest") or "").strip()
        target = Path(dest) if dest else _default_backup_path(face, root)
        if _is_inside(target, root):
            raise ValueError(
                f"备份归档不得落在存储根之内（{target}）——存储根会被「完全清空」整体删除，"
                "备份放进去等于一起删"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        envelope = _store_errors(lambda: face.backup.create_backup(
            face.store, passphrase, target,
            include_data_cache=bool(body.get("include_data_cache")),
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        written = envelope
        return {
            "archive": str(written),
            "include_data_cache": bool(body.get("include_data_cache")),
            "partitions": list(_backup_partitions(face)),
            "note": "归档为独立加密容器（密码即本次输入者）；可复制到任何存储介质",
        }

    def _run_restore(self, face: Any, body: dict[str, Any]) -> Any:
        archive = str(_required(body, "archive"))
        passphrase = str(_required(body, "passphrase"))
        root = Path(face.store.root)
        target_root = str(body.get("target_root") or "").strip() or str(root)
        target_pass = str(body.get("target_passphrase") or "").strip() or None
        envelope = _store_errors(lambda: face.backup.restore_backup(
            archive, passphrase, target_root, target_passphrase=target_pass,
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        report = envelope
        over_live = Path(target_root) == root
        note = (
            "已覆盖写当前存储根：本进程手里的存储句柄随之失效，须重启后继续"
            if over_live else f"已还原到 {target_root}（当前存储根未动）"
        )
        if over_live:
            self.restart_gate.reason = (
                f"已从归档恢复并覆盖写存储根 {root}：本进程的存储句柄已失效，"
                "继续读写会作用于陈旧清单——请重启后再操作"
            )
        return {
            "restored_partitions": list(report.restored_partitions),
            "file_count": int(report.file_count),
            "archived_at": _stamp(report.archived_at),
            "target_root": str(target_root),
            "restart_required": over_live,
            "note": note,
        }

    def _run_wipe(self, face: Any, body: dict[str, Any]) -> Any:
        raw = body.get("confirmations")
        tokens = [str(item) for item in raw] if isinstance(raw, list) else []
        root = Path(face.store.root)
        envelope = _store_errors(lambda: face.backup.wipe_all(root, tokens))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        report = envelope
        self.restart_gate.reason = (
            f"已完全清空存储根 {root}（含全部分区与派生数据）：本进程的数据面已停用，"
            "请重启后再操作；备份文件在存储根之外，由用户自行处理"
        )
        return {
            "deleted_partitions": list(report.deleted_partitions),
            "detail": str(report.detail),
            "restart_required": True,
            "note": "备份文件位置由用户自行处理——清空不触碰存储根之外的内容",
        }

    def _run_credential(self, face: Any, body: dict[str, Any]) -> Any:
        if not bool(getattr(face.store, "secrets_unlocked", False)):
            return ResultEnvelope.unavailable(
                "凭据分区未解锁（凭据恒加密、惰性解锁）：本次会话不可增改凭据——"
                "经启动参数提供凭据口令后可用",
                last_updated_at=_now(),
            )
        action = str(_required(body, "action"))
        credential_id = str(_required(body, "credential_id"))
        vault = face.vault
        if action == "add":
            view = vault.add(
                credential_id,
                str(_required(body, "kind")),
                str(_required(body, "value")),
                str(body.get("label") or ""),
            )
        elif action == "rotate":
            view = vault.rotate(credential_id, str(_required(body, "value")))
        elif action == "revoke":
            view = vault.revoke(credential_id)
        else:
            raise ValueError(f"未知的凭据动作：{action!r}（只认 add / rotate / revoke）")
        return {
            "credential_id": view.credential_id,
            "action": action,
            "masked": view.masked,
            "version": view.version,
            "note": "凭据明文不落日志、不出站；界面只显示掩码",
        }

    def _set_online(self, face: Any, body: dict[str, Any]) -> dict[str, Any]:
        flag = body.get("online")
        if not isinstance(flag, bool):
            raise ValueError(f"在线态须为布尔：{flag!r}")
        if face.set_online is None:
            return {"online": flag, "caught_up": 0,
                    "note": "在线态未接线（装配归组合根）：浏览器侧状态照常，后端未改"}
        runs = face.set_online(flag)
        return {"online": flag, "caught_up": len(runs or ()),
                "note": "由离线翻回在线时按策略补跑待补到期点；重复报同一态无副作用"}

    def _export_network(self, gateway: Any, body: dict[str, Any]) -> Any:
        exports_dir = getattr(self.settings, "exports_dir", None)
        if exports_dir is None:
            return ResultEnvelope.unavailable(
                "导出目录未注入设置面（装配归组合根）", last_updated_at=_now()
            )
        if not gateway.audit_enabled:
            return ResultEnvelope.unavailable(
                "出网审计未开启，无记录可导出（这是「未开启」，不是「没有请求」）",
                last_updated_at=_now(),
            )
        since = _window_start(body.get("hours"))
        report = gateway.export_report(since=since)
        target = Path(exports_dir) / f"network-activity-{_stamp(_now(), compact=True)}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(target), "count": int(report.get("count") or 0),
                "note": "报告只含计数与出网元数据（域名 / 目的 / 时间 / 数据量），不含请求内容"}

    # ───────────────────── 反思中心（[T-UI-004.2]） ─────────────────────

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

    # ───────────────────── Studio 画布（[`T-UI-005.1`]） ─────────────────────

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

    # ───────────────────── 反思中心的取数装配 ─────────────────────

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

    # ───────────────────── 演进面（[T-UI-004.3]） ─────────────────────

    def api_evolution_changes(self) -> dict[str, Any]:
        """变更历史时间线（`change_timeline`）——含回滚状态与「一键回滚」动作。"""
        return self._call_face(
            "evolution-changes", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(self._changes_description(face)),
        )

    def api_evolution_rollback(self, body: dict[str, Any]) -> dict[str, Any]:
        """一键回滚——按该变更的**生效前取值**回放；回滚**本身也是一次变更**（[08 §6]）。"""
        return self._call_face(
            "evolution-rollback", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(face.rollback(str(_required(body, "change_id")))),
        )

    def api_evolution_authorization(self) -> dict[str, Any]:
        """演进授权设置面（`setting_panel`）——档位可改，风险分级清单只呈现。"""
        return self._call_face(
            "evolution-authorization", self.reflection, "L6 反思演进面",
            lambda face: self._authorization_envelope(face),
        )

    def api_evolution_set(self, body: dict[str, Any]) -> dict[str, Any]:
        """应用一条设置——经 [01 §7] 的落值面生效（**切换留痕**，[08 §5]）。"""
        return self._call_face(
            "evolution-set", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                self._apply_setting(face, str(_required(body, "config_id")), body.get("value"))
            ),
        )

    def api_evolution_factory_reset(self, body: dict[str, Any]) -> dict[str, Any]:
        """「回滚到出厂设置」——**三次确认**是硬门（不足即拒、不留痕、不执行任何动作，[08 §6]）。"""
        return self._call_face(
            "evolution-factory-reset", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(face.factory_reset(body.get("confirmations"))),
        )

    # ───────────────────── 演进面的取数装配 ─────────────────────

    def _changes_description(self, face: Any) -> UiDescription:
        items = []
        for change in face.changes():
            rolled_back = bool(change.get("rolled_back_at"))
            items.append({
                **change,
                "rolled_back": rolled_back,
                # 已回滚过的条目不再给回滚动作——重复回滚会被 L6 显式拒（不静默）
                "actions": [] if rolled_back else ["rollback"],
            })
        return self._gated_description(
            "change_timeline",
            slots={"changes": items, "labels": {"rollback": "一键回滚"}},
            text_kinds={"changes": "data", "labels": "generated"},
            title="变更历史",
        )

    def _authorization_envelope(self, face: Any) -> ResultEnvelope:
        info = face.authorization()
        if not info.get("available"):
            return ResultEnvelope.unavailable(
                str(info.get("reason") or "演进授权面未接线"), last_updated_at=_now()
            )
        entries = [
            {
                "config_id": info.get("tier_config_id"),
                "params": {"config_id": info.get("tier_config_id")},
                "current": info.get("tier"),
                "options": [{"value": value} for value in info.get("tiers") or ()],
                "actions": ["set"],
            },
            {
                "config_id": info.get("grading_config_id"),
                "current": f"{len(info.get('grading') or ())} 条规则",
                "detail": info.get("grading") or [],
                "options": [],
                "actions": [],
                "note": "本页只呈现当前清单；清单整体替换不在本页范围",
            },
        ]
        return ResultEnvelope.ok(
            self._gated_description(
                "setting_panel",
                slots={
                    "entries": entries,
                    "surface": "evolution",
                    "labels": {
                        "set": "应用",
                        "manual": "手动",
                        "collaborative": "协作",
                        "autonomous": "自主",
                    },
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title="演进授权",
            )
        )

    def _apply_setting(self, face: Any, config_id: str, value: Any) -> dict[str, Any]:
        info = face.authorization()
        if not info.get("available"):
            raise ValueError(str(info.get("reason") or "演进授权面未接线"))
        if config_id == info.get("tier_config_id"):
            if value is None:
                raise ValueError("切换档位须给出 value")
            return face.set_authorization(tier=value)
        if config_id == info.get("grading_config_id"):
            if value is None:
                raise ValueError("替换风险分级清单须给出 value")
            return face.set_authorization(grading=value)
        raise ValueError(f"该设置面不认这个条目：{config_id!r}")

    # ───────────────────── 生态面（[T-UI-004.4]） ─────────────────────

    def api_eco_export_plan(self) -> dict[str, Any]:
        """导出计划的「本次导出包含以下公开信息」清单（`report_card`）。"""
        return self._call_face(
            "eco-export-plan", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(self._export_plan_description(face)),
        )

    def api_eco_export(self, body: dict[str, Any]) -> dict[str, Any]:
        """按种类导出到**导出目录**并回路径 + 分享卡片（[09 §2]）。"""
        return self._call_face(
            "eco-export", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(face.export(
                kind=str(_required(body, "kind")),
                ref=str(body.get("ref") or ""),
                author=str(body.get("author") or ""),
                confirmed_by=str(body.get("confirmed_by") or ""),
            )),
        )

    def api_eco_inbox(self) -> dict[str, Any]:
        """收件目录里的待导入文件（`table`；空即空态，不硬凑）。"""
        return self._call_face(
            "eco-inbox", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(self._inbox_description(face)),
        )

    def api_eco_import_review(self, body: dict[str, Any]) -> dict[str, Any]:
        """导入校验：身份 / 权限申请 / 依赖 / 来源追溯**四段并列**（`report_card`）。"""
        file_name = str(_required(body, "file_name"))
        return self._call_face(
            "eco-import-review", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(
                self._review_description(face, file_name, str(body.get("received_from") or ""))
            ),
        )

    def api_eco_import_permissions(self, body: dict[str, Any]) -> dict[str, Any]:
        """逐项批准 / 拒绝的处置面（`setting_panel`，面键 `import`）——[01 §10] 逐项、不合并。"""
        file_name = str(_required(body, "file_name"))
        received_from = str(body.get("received_from") or "")
        return self._call_face(
            "eco-import-permissions", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(
                self._permissions_description(face, file_name, received_from)
            ),
        )

    def api_eco_import_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """批准 / 拒绝一条已声明的权限（经 **L3 审批面**落账，[01 §10]）。"""
        return self._call_face(
            "eco-import-decide", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(face.decide_import(
                file_name=str(_required(body, "file_name")),
                permission=str(_required(body, "permission")),
                decision=str(_required(body, "action")),
            )),
        )

    def api_eco_import_install(self, body: dict[str, Any]) -> dict[str, Any]:
        """用户确认后安装（[09 §3] 第 4–5 段）——权限声明未全部批准即**显式拒**。"""
        file_name = str(_required(body, "file_name"))
        return self._call_face(
            "eco-import-install", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(face.install(
                file_name=file_name,
                confirmed_by=str(body.get("confirmed_by") or ""),
                received_from=str(body.get("received_from") or ""),
            )),
        )

    def api_eco_index(self) -> dict[str, Any]:
        """官方 Skill 索引浏览（`table`）；不可用即 `unavailable`（不留半截数据）。"""
        return self._call_face(
            "eco-index", self.eco, "ECO 生态面",
            lambda face: self._index_envelope(face),
        )

    def api_eco_imports(self) -> dict[str, Any]:
        """来源追溯：导入留痕（`table`）；**没有留痕即 `empty`**（用既有中性文本，不硬凑空表）。"""
        return self._call_face(
            "eco-imports", self.eco, "ECO 生态面",
            lambda face: self._imports_envelope(face),
        )

    def api_eco_violations(self) -> dict[str, Any]:
        """越界行为警示（`violation_alert`）——取**最新一条**，附「禁用该能力」动作。"""
        return self._call_face(
            "eco-violations", self.eco, "ECO 生态面",
            lambda face: self._violation_envelope(face),
        )

    def api_eco_violation_history(self) -> dict[str, Any]:
        """越界留痕全表（`table`）——警示之外仍可复核全部记录。"""
        return self._call_face(
            "eco-violation-history", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(self._violation_history_description(face)),
        )

    def api_eco_disable(self, body: dict[str, Any]) -> dict[str, Any]:
        """禁用越界能力（[03 §1.5] 的禁用面）。"""
        return self._call_face(
            "eco-disable", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(
                face.disable(skill_id=str(_required(body, "skill_id")))
            ),
        )

    # ───────────────────── 生态面的取数装配 ─────────────────────

    def _export_plan_description(self, face: Any) -> UiDescription:
        plan = face.export_plan()
        lines = [f"包含：{item}" for item in plan.get("included") or []]
        if not lines:
            lines = ["本次导出没有可公开的信息"]
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "本次导出包含以下公开信息", "lines": lines},
                {
                    "title": "隐私过滤",
                    "lines": [f"已过滤（未包含）的节点数：{plan.get('excluded_nodes', 0)}"],
                },
                {
                    "title": "导出位置",
                    "lines": [f"生成的文件落在：{plan.get('exports_dir', '')}（09 §6 的表现层口径）"],
                },
            ]},
            text_kinds={"sections": "data"},
            title="导出记忆片段",
        )

    def _inbox_description(self, face: Any) -> UiDescription:
        files = face.inbox()
        return self._gated_description(
            "table",
            slots=table_slots(
                _INBOX_COLUMNS,
                [
                    {"file_name": item.get("file_name"), "size": item.get("size")}
                    for item in files
                ],
            ),
            text_kinds=table_text_kinds(),
            title="导入收件目录",
        )

    def _review_description(self, face: Any, file_name: str, received_from: str) -> UiDescription:
        review = face.review(file_name=file_name, received_from=received_from)
        sections = [{"title": "能力", "lines": [str(review.get("identity") or file_name)]}]
        permissions = review.get("permissions") or []
        if permissions:
            sections.append({
                "title": "权限申请（逐项批准，01 §10）",
                "lines": [
                    f"{item.get('permission')}｜{item.get('state')}｜{item.get('description')}"
                    for item in permissions
                ],
            })
        else:
            sections.append({"title": "权限申请", "lines": ["无声明（不经本机的文件 / 网络 / 命令出口）"]})
        missing = review.get("missing") or []
        sections.append({
            "title": "依赖",
            "lines": (
                [f"缺失 {gap.get('skill_id')} —— {gap.get('how_to_get')}" for gap in missing]
                or ["本地齐备，无缺失"]
            ),
        })
        provenance = review.get("provenance") or {}
        chain = " → ".join(provenance.get("origin_chain") or []) or "（无，未经转手）"
        sections.append({
            "title": "来源追溯",
            "lines": [
                f"分享者 {provenance.get('sharer')}｜导入时间 {provenance.get('imported_at')}"
                f"｜校验和 {provenance.get('checksum')}｜出处链 {chain}"
            ],
        })
        return self._gated_description(
            "report_card",
            slots={"sections": sections},
            text_kinds={"sections": "data"},
            title=f"导入校验 · {file_name}",
        )

    def _permissions_description(
        self, face: Any, file_name: str, received_from: str = ""
    ) -> UiDescription:
        review = face.review(file_name=file_name, received_from=received_from)
        items = review.get("permissions") or []
        entries = [
            {
                "identifier": item.get("permission"),
                "params": {"file_name": file_name, "permission": item.get("permission")},
                "description": item.get("description"),
                "current": item.get("state"),
                "options": [],
                "actions": ["approve", "reject"],
            }
            for item in items
        ]
        return self._gated_description(
            "setting_panel",
            slots={
                "entries": entries,
                "surface": "import",
                "labels": {"approve": "批准", "reject": "拒绝"},
            },
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title=f"权限申请（逐项批准）· {file_name}",
        )

    def _index_envelope(self, face: Any) -> ResultEnvelope:
        index = face.index()
        if not index.get("available"):
            return ResultEnvelope.unavailable(
                str(index.get("reason") or "官方索引不可用"), last_updated_at=_now()
            )
        entries = index.get("entries") or []
        return ResultEnvelope.ok(
            self._gated_description(
                "table",
                slots=table_slots(
                    _INDEX_COLUMNS,
                    [
                        {
                            "kind": item.get("kind"),
                            "name": item.get("name"),
                            "version": item.get("version"),
                            "description": item.get("description"),
                            "download_url": item.get("download_url"),
                            "checksum": item.get("checksum"),
                        }
                        for item in entries
                    ],
                ),
                text_kinds=table_text_kinds(),
                title="官方 Skill 索引",
            )
        )

    def _imports_envelope(self, face: Any) -> ResultEnvelope:
        """导入留痕：**空即空态**（`table` 渲染件不显示描述标题，故空态不能用标题承载）。

        空态文案沿用 L1 的中性文本（「你的 Skill 库目前只有官方 Pack…」，story-10 的空状态）。
        """
        ledger = face.imports_ledger()
        records = ledger.get("records") or []
        if not records:
            return ResultEnvelope.empty(str(ledger.get("empty_text") or "尚无导入留痕"))
        return ResultEnvelope.ok(self._imports_description(records))

    def _imports_description(self, records: list[dict[str, Any]]) -> UiDescription:
        return self._gated_description(
            "table",
            slots=table_slots(
                _IMPORT_COLUMNS,
                [
                    {
                        "import_id": item.get("import_id"),
                        "kind": item.get("kind"),
                        "installed_id": item.get("installed_id"),
                        "sharer": (item.get("origin") or {}).get("sharer"),
                        "imported_at": (item.get("origin") or {}).get("imported_at"),
                    }
                    for item in records
                ],
            ),
            text_kinds=table_text_kinds(),
            title="导入留痕",
        )

    def _violation_envelope(self, face: Any) -> ResultEnvelope:
        info = face.violations()
        if not info.get("available"):
            return ResultEnvelope.unavailable(
                str(info.get("reason") or "越界行为面未接线"), last_updated_at=_now()
            )
        records = info.get("records") or []
        if not records:
            return ResultEnvelope.empty("尚无越界行为记录")
        newest = dict(records[0])
        newest["disabled"] = newest.get("skill_id") in set(info.get("disabled") or ())
        return ResultEnvelope.ok(
            self._gated_description(
                "violation_alert",
                slots={
                    "record": newest,
                    "labels": {
                        "warning": "该能力的行为超出其声明的范围，已被拦截。",
                        "disable": "禁用该能力",
                    },
                },
                text_kinds={"record": "data", "labels": "generated"},
                title="越界行为警示",
            )
        )

    def _violation_history_description(self, face: Any) -> UiDescription:
        info = face.violations()
        records = info.get("records") or []
        disabled = set(info.get("disabled") or ())
        return self._gated_description(
            "table",
            slots=table_slots(
                _VIOLATION_COLUMNS,
                [
                    {
                        "skill_id": item.get("skill_id"),
                        "violation": item.get("violation"),
                        "occurred_at": item.get("occurred_at"),
                        "trace_id": item.get("trace_id"),
                        "disabled": "是" if item.get("skill_id") in disabled else "否",
                    }
                    for item in records
                ],
            ),
            text_kinds=table_text_kinds(),
            title="越界行为留痕",
        )

    # ───────────────────── 记忆区（[T-UI-009.2]） ─────────────────────

    def api_memory_positions(self, scope: str) -> dict[str, Any]:
        """记忆区的**持仓 / 关注行情读面**（`table`）——两条端点，一条一段。

        这是 [13-visual-design §2.2] 涨跌三路冗余编码与 `--font-num` 的**首个真实消费方**：
        描述只承载**数值**（涨跌幅给带符号百分数），编码由渲染件一次产出——故「颜色与符号
        恒同时出现」是结构上成立的，不靠本层自觉（[D-105] ②）。

        三种缺口**互不冒充**（[01 §5] 六态）：面未接线 ⇒ `unavailable` + 点名；记忆里这段为空
        ⇒ `empty` + 原因；行情源没注入 / 本地行情库非 `ok` ⇒ `unavailable` + 原因。某标的
        在缓存里没有日线**不是**缺口——那一行的行情列给 `None`，由渲染面出「无数据」。
        """
        if scope not in _POSITION_SCOPES:
            return envelope_payload(
                ResultEnvelope.validation_failed(f"不认识的面：{scope!r}")
            )
        return self._call_face(
            f"memory-{scope}", self.memory, "记忆面",
            lambda face: self._positions_envelope(face, scope),
        )

    # ───────────────────── 记忆区图谱面（[T-UI-014.1] / `.2` / `.3`） ─────────────────────

    def api_memory_browse(
        self, *, view: str, topic: str = "", task_type: str = "chat"
    ) -> dict[str, Any]:
        """三视图读面（`graph` / `list` / `timeline`）——三条走**同一条**查询层（[04 §3.1]）。

        视图名由**查询串**给（[11-sitemap §2.2]：带标识的面走查询串，路径集仍是有限
        字面量集合）。面未接线 ⇒ `unavailable` + 点名；候选集为空 ⇒ `empty` + 引导
        （**不静默留空**）；视图名不认识 ⇒ `validation_failed`。
        """
        return self._call_face(
            "memory-browse", self.graph, "记忆图谱面",
            lambda face: self._browse_envelope(face, view=view, topic=topic, task_type=task_type),
        )

    def _browse_envelope(
        self, face: Any, *, view: str, topic: str, task_type: str
    ) -> ResultEnvelope:
        if view not in ("graph", "list", "timeline"):
            return ResultEnvelope.validation_failed(
                f"不认识的三视图：{view!r}（须为 graph / list / timeline）"
            )
        payload = face.browse(view=view, topic=topic, task_type=task_type)
        if payload.get("empty"):
            return ResultEnvelope.empty(f"记忆里还没有{_MEMORY_VIEW_LABELS[view]}内容")
        return ResultEnvelope.ok(self._browse_description(payload))

    def api_memory_profile(self) -> dict[str, Any]:
        """偏好画像卡读面：`identity` / `evolution` 摘要，**不超过 5 条**标签（[11-sitemap §2 记忆]）。"""
        return self._call_face(
            "memory-profile", self.graph, "记忆图谱面",
            lambda face: self._profile_envelope(face),
        )

    def _profile_envelope(self, face: Any) -> ResultEnvelope:
        payload = face.profile()
        if not payload.get("tags"):
            return ResultEnvelope.empty("还没有可用的偏好画像（无 identity / evolution 节点）")
        return ResultEnvelope.ok(self._profile_description(payload))

    def _profile_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """画像卡 → `report_card`（复用既有型；**不超过 5 条**关键标签，超出显式收窄）。"""
        labels = payload.get("labels") or {}
        types = labels.get("types") or {}
        tags = list(payload.get("tags") or [])
        lines = [
            f"{types.get(tag.get('kind'), tag.get('kind'))}：{tag.get('label')}"
            for tag in tags
        ]
        total = int(payload.get("total") or len(tags))
        limit = int(payload.get("limit") or len(tags))
        if payload.get("truncated"):
            hint = f"另有 {total - limit} 条未显示（画像卡上限 {limit} 条）"
        else:
            hint = f"共 {total} 条"
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "当前画像", "lines": lines},
                {"title": "说明", "lines": [hint]},
            ]},
            text_kinds={"sections": "data"},
            title="偏好画像",
        )

    # ───────────────────── 记忆中详情 / 修正历史（[T-UI-014.2]） ─────────────────────

    def api_memory_node(self, *, node_id: str) -> dict[str, Any]:
        """单节点详情读面（`?node=<id>`）——缺 id ⇒ `validation_failed`；无此节点 ⇒ `unavailable`。"""
        if not node_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 node 参数（节点 id）"))
        return self._call_face(
            "memory-node", self.graph, "记忆图谱面",
            lambda face: self._node_envelope(face, node_id),
        )

    def _node_envelope(self, face: Any, node_id: str) -> ResultEnvelope:
        payload = face.node(node_id=node_id)
        if payload is None:
            return ResultEnvelope.unavailable(
                f"记忆里没有这个节点：{node_id}", last_updated_at=_now()
            )
        return ResultEnvelope.ok(self._node_description(payload))

    def _node_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """节点详情 → `report_card`（复用既有型）。置信度按 [D-111] A1 标注为**存储值**。"""
        labels = payload.get("labels") or {}
        types = labels.get("types") or {}
        sources = labels.get("sources") or {}
        privacy = labels.get("privacy") or {}
        relations = labels.get("relations") or {}
        fields = payload.get("fields") or {}
        lines = [
            f"类型：{types.get(payload.get('type'), payload.get('type') or '—')}",
            f"标识：{payload.get('node_id') or '—'}",
            f"置信度（存储值·非有效值）：{_fmt_confidence(payload.get('confidence'))}",
            f"来源：{sources.get(payload.get('source'), payload.get('source') or '—')}",
            f"隐私分级：{privacy.get(payload.get('privacy'), payload.get('privacy') or '—')}",
            f"创建：{payload.get('created_at') or '—'}",
            f"更新：{payload.get('updated_at') or '—'}",
        ]
        field_lines = [f"{key}：{_fmt_value(value)}" for key, value in sorted(fields.items())]
        edges = payload.get("edges") or []
        edge_lines = [
            f"{relations.get(item['edge']['relation'], item['edge']['relation'])} → {item['other']}"
            for item in edges
        ]
        sections = [
            {"title": "基本信息", "lines": lines},
            {"title": "专属字段", "lines": field_lines or ["（无）"]},
            {"title": "相接关系", "lines": edge_lines or ["（无）"]},
        ]
        return self._gated_description(
            "report_card",
            slots={"sections": sections},
            text_kinds={"sections": "data"},
            title=str(payload.get("label") or "节点详情"),
        )

    def api_memory_node_revisions(self, *, node_id: str) -> dict[str, Any]:
        """修正历史读面（`?node=<id>&view=revisions`）——旧值保留供审计（[04 §1]）。"""
        if not node_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 node 参数（节点 id）"))
        return self._call_face(
            "memory-node-revisions", self.graph, "记忆图谱面",
            lambda face: self._revisions_envelope(face, node_id),
        )

    def _revisions_envelope(self, face: Any, node_id: str) -> ResultEnvelope:
        payload = face.revisions(node_id=node_id)
        if payload is None:
            return ResultEnvelope.unavailable(
                f"记忆里没有这个节点：{node_id}", last_updated_at=_now()
            )
        revisions = payload.get("revisions") or []
        if not revisions:
            return ResultEnvelope.empty("该节点没有修正历史")
        return ResultEnvelope.ok(self._revisions_description(payload))

    def _revisions_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """修正历史 → `timeline_view`（最新修正在上；旧值原样呈现、**不覆盖**）。"""
        entries = [
            {
                "id": f"{payload.get('node_id')}-r{index}",
                "at": _iso_day(item.get("replaced_at")),
                "kind": "correction",
                "content": item.get("reason") or "修正（未填缘由）",
                "state": "ok",
                "previous": _fmt_value(item.get("previous")),
            }
            for index, item in enumerate(payload.get("revisions") or [])
        ]
        return self._gated_description(
            "timeline_view",
            slots={
                "entries": entries,
                "labels": {
                    "kinds": {"correction": "修正"},
                    "states": dict(_TIMELINE_STATE_LABELS),
                },
                "empty_hint": "该节点没有修正历史",
            },
            text_kinds={"entries": "data", "labels": "generated", "empty_hint": "generated"},
            title=f"修正历史 · {payload.get('label') or payload.get('node_id')}",
        )

    # ───────────────────── 导入导出面板与 Onboarding（[T-UI-014.3]） ─────────────

    def api_memory_export_plan(self) -> dict[str, Any]:
        """导出计划读面：出「本次导出包含的公开信息」清单（`report_card`；[04 §8] 第二步）。"""
        return self._call_face(
            "memory-export-plan", self.graph, "记忆图谱面",
            lambda face: self._memory_export_plan_envelope(face),
        )

    def _memory_export_plan_envelope(self, face: Any) -> ResultEnvelope:
        plan = face.export_plan()
        return ResultEnvelope.ok(self._memory_export_plan_description(plan))

    def _memory_export_plan_description(self, plan: Mapping[str, Any]) -> UiDescription:
        lines = [f"包含：{item}" for item in plan.get("included") or []]
        if not lines:
            lines = ["本次导出没有可公开的信息"]
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "本次导出包含以下公开信息", "lines": lines},
                {
                    "title": "隐私过滤",
                    "lines": [f"已过滤（未包含）的节点数：{plan.get('excluded_nodes', 0)}"],
                },
                {"title": "确认门", "lines": ["确认后才会生成导出产物；未经确认不导出。"]},
            ]},
            text_kinds={"sections": "data"},
            title="导出记忆片段",
        )

    def api_memory_export(self, body: dict[str, Any]) -> dict[str, Any]:
        """导出**产出**（写面）：体 `{confirmed_by}`——非 `user` 即 `validation_failed`。"""
        return self._call_face(
            "memory-export", self.graph, "记忆图谱面",
            lambda face: face.export_memory(confirmed_by=str(body.get("confirmed_by") or "")),
        )

    def api_memory_onboarding(self) -> dict[str, Any]:
        """Onboarding 状态读面：问题清单 + 空维度（`setting_panel` 复用面以只读视图承载）。"""
        return self._call_face(
            "memory-onboarding", self.graph, "记忆图谱面",
            lambda face: self._onboarding_envelope(face),
        )

    def _onboarding_envelope(self, face: Any) -> ResultEnvelope:
        state = face.onboarding_state()
        return ResultEnvelope.ok(self._onboarding_description(state))

    def _onboarding_description(self, state: Mapping[str, Any]) -> UiDescription:
        lines = [
            f"{question['prompt']}（留空即跳过）"
            for question in state.get("questions") or []
        ]
        if state.get("has_profile"):
            lines = ["已有初始画像。"] + lines
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "建立初始画像", "lines": lines or ["（无问题）"]},
                {
                    "title": "状态",
                    "lines": ["已有画像" if state.get("has_profile") else "尚未建立画像"],
                },
            ]},
            text_kinds={"sections": "data"},
            title="Onboarding 引导",
        )

    def api_memory_onboarding_submit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Onboarding 提交（写面）：体 `{answers: {question_id: value}}`——落初始画像节点。"""
        answers = body.get("answers")
        if not isinstance(answers, Mapping):
            return envelope_payload(ResultEnvelope.validation_failed("answers 须为对象"))
        return self._call_face(
            "memory-onboarding-submit", self.graph, "记忆图谱面",
            lambda face: face.submit_onboarding(answers=answers),
        )

    def _browse_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """三视图 → 描述：`graph_view` / 列表 `table` / `timeline_view`（复用既有三型）。

        **不新登记组件型**——三视图与画像卡都由 [01 §12] 已登记并实现的型承载，
        故本叶不触 [铁律 8]（[D-111] ③）。
        """
        view = str(payload.get("view") or "list")
        slices = list(payload.get("slices") or [])
        labels = payload.get("labels") or {}
        if view == "graph":
            return self._gated_description(
                "graph_view",
                slots={
                    "nodes": [
                        {
                            "id": item["id"],
                            "type": item["type"],
                            "label": item["label"],
                            "confidence": _confidence_bucket(item.get("confidence")),
                            "privacy": item.get("privacy"),
                        }
                        for item in slices
                    ],
                    "edges": list(payload.get("edges") or []),
                    "labels": {
                        "types": labels.get("types") or {},
                        "privacy": labels.get("privacy") or {},
                        "relations": labels.get("relations") or {},
                    },
                    "empty_hint": "记忆里还没有图谱内容",
                },
                text_kinds={
                    "nodes": "data",
                    "edges": "data",
                    "labels": "generated",
                    "empty_hint": "generated",
                },
                title="记忆图谱",
            )
        if view == "timeline":
            return self._gated_description(
                "timeline_view",
                slots={
                    "entries": [
                        {
                            "id": item["id"],
                            "at": _iso_day(item.get("updated_at") or item.get("as_of")),
                            "kind": item["type"],
                            "content": item["label"],
                            "state": "ok",
                            "confidence": _confidence_bucket(item.get("confidence")),
                            "source": item.get("source"),
                        }
                        for item in slices
                    ],
                    "labels": {
                        "kinds": labels.get("types") or {},
                        "states": dict(_TIMELINE_STATE_LABELS),
                        "sources": labels.get("sources") or {},
                    },
                    "empty_hint": "记忆里还没有时间线内容",
                },
                text_kinds={
                    "entries": "data",
                    "labels": "generated",
                    "empty_hint": "generated",
                },
                title="记忆时间线",
            )
        # 列表视图：复用 `table`（与持仓 / 关注两表同型），类型 / 来源在描述层按文案表解析
        types = labels.get("types") or {}
        sources = labels.get("sources") or {}
        rows = [
            {
                "id": item["id"],
                "label": item["label"],
                "type": types.get(item["type"], item["type"]),
                "confidence": item.get("confidence"),
                "effective_confidence": item.get("effective_confidence"),
                "updated_at": item.get("updated_at"),
                "source": sources.get(item.get("source"), item.get("source")),
            }
            for item in slices
        ]
        return self._gated_description(
            "table",
            slots=table_slots(_MEMORY_LIST_COLUMNS, rows),
            text_kinds=table_text_kinds(),
            title="记忆列表",
        )

    def _positions_envelope(self, face: Any, scope: str) -> ResultEnvelope:
        label, empty_reason = _POSITION_SCOPES[scope]
        view = face.positions(scope)
        if not view.get("available"):
            return ResultEnvelope.unavailable(
                str(view.get("reason") or "记忆面未接线"), last_updated_at=_now()
            )
        rows = view.get("rows") or []
        if not rows:
            return ResultEnvelope.empty(empty_reason)
        return ResultEnvelope.ok(
            self._gated_description(
                "table",
                slots=table_slots(_POSITION_COLUMNS, rows),
                text_kinds=table_text_kinds(),
                title=f"{label}行情",
            )
        )

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

    # ───────────────────── 工作区（[T-UI-013]） ─────────────────────
    # 钉住物的持久承载面（[05 §6]）。读面回的是**描述序列**——[01 §5] 允许工作区的 `data`
    # 为描述序列，[01 §12] 同批写明「工作区是**承载页**、钉住物按其**自身型**渲染」，
    # 故本层不另造容器型、也不直灌 JSON：逐条走**同一条**渲染路径（注册表 ＋ 两道闸）。

    def api_workspace_pins(self) -> dict[str, Any]:
        """工作区读面：钉住物按**新钉在前**排的描述序列。

        空工作区是 ``empty`` ＋ 原因（[01 §5]：不得返回裸空），不是 ``ok`` 空数组——两者
        在渲染面上是「还没钉过东西」与「钉过但都取不出来」的区别。
        """
        return self._call_face(
            "workspace-pins", self.workspace, "工作区面", self._workspace_pins_envelope
        )

    def api_workspace_pin(self, body: dict[str, Any]) -> dict[str, Any]:
        """钉住一份描述（同 `description_id` 重复钉住 ＝ 覆盖，即刷新落盘的那一半）。"""
        return self._call_face(
            "workspace-pin", self.workspace, "工作区面",
            lambda face: self._gate_single_description(face.pin(body)),
        )

    def api_workspace_unpin(self, body: dict[str, Any]) -> dict[str, Any]:
        """取消钉住（按 `description_id`，[01 §1] 的该 id 即寻址单位）。"""
        return self._call_face(
            "workspace-unpin", self.workspace, "工作区面", lambda face: face.unpin(body)
        )

    def api_workspace_refresh(self, body: dict[str, Any]) -> dict[str, Any]:
        """重解析一条钉住物（来源面键 → 组合根注入的取数口）；见 [`WorkspaceFacade`]。

        三种如实降级（无来源面键 / 该键未接取数口 / 来源自己非 `ok`）都由面给出
        ``unavailable`` ＋ 原因，本层只把它**原样**送到渲染面（[01 §5] 六态不混用）。
        """
        return self._call_face(
            "workspace-refresh", self.workspace, "工作区面",
            lambda face: self._gate_single_description(face.refresh(body)),
        )

    def _workspace_pins_envelope(self, face: Any) -> ResultEnvelope:
        """逐条过闸（必填槽 ＋ 渲染前中性门）——序列里任何一条不过即整面 `validation_failed`。

        不逐条降级为「坏的那条不出、好的照出」：一条不过闸说明**产出方**有问题，
        静默漏掉它会让工作区看起来「本来就少一块」。
        """
        envelope = face.pins()
        if envelope.status != "ok":
            return envelope
        gated = [self._gate_description(item) for item in envelope.data]
        return envelope.model_copy(update={"data": gated})

    def _gate_single_description(self, envelope: ResultEnvelope) -> ResultEnvelope:
        """`ok` 且载荷是**单份**描述时过闸；非 `ok` 原样出（六态由面给，不吞）。"""
        if envelope.status != "ok" or not isinstance(envelope.data, UiDescription):
            return envelope
        return envelope.model_copy(update={"data": self._gate_description(envelope.data)})

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

    def api_description(self, description: UiDescription) -> dict[str, Any]:
        """出站一份 UI 描述：先查该型必填槽，再过**中性化门**（[01 §6] 执行点 2）。

        两道任一不过都**阻断渲染**并回 `validation_failed`——不回可渲染的描述（[01 §12]）。
        门与注册表都在 Python 侧，规则库与类型表因此只有一份真相源。
        """
        gaps = slot_gaps(description)
        if gaps:
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    f"UI 描述缺少 {description.component_type} 的必填槽：{'、'.join(gaps)}"
                )
            )
        verdict = self.neutrality_gate.check(description)
        if not verdict.passed:
            return envelope_payload(ResultEnvelope.validation_failed(verdict.reason))
        return envelope_payload(ResultEnvelope.ok(description))

    def api_dev_description(self, kind: str) -> dict[str, Any] | None:
        """dev 示例描述：走与真实描述**同一条**出站校验路径。"""
        if not self.dev_enabled:
            return None
        description = self.dev_package.sample_description(kind)
        if description is None:
            return None
        return self.api_description(description)

    def api_dev_sample(self, status: str) -> dict[str, Any] | None:
        """dev 示例端点：按状态名造一条合法信封；未知状态名返回 ``None``（调用方回 400）。"""
        if not self.dev_enabled:
            return None
        envelope = self.dev_package.sample_envelope(status)
        if envelope is None:
            return None
        return envelope_payload(envelope)

    def index_html(self) -> str:
        """`index.html` —— dev 关闭时把占位注释替换为空串。"""
        html = (self.web_root / "index.html").read_text(encoding="utf-8")
        tag = self.dev_package.dev_script_tag() if self.dev_enabled else ""
        return html.replace(DEV_SCRIPT_MARKER, tag)

    def static_file(self, url_path: str) -> Path | None:
        """解析一个静态资产路径（生产根，或 dev 开启时的 dev 根）。"""
        if self.dev_enabled and url_path.startswith("/dev/"):
            return resolve_static(self.dev_package.web_root(), url_path[len("/dev/") :])
        return resolve_static(self.web_root, url_path)


def build_ui(
    *,
    host: str,
    port: int,
    dev: bool = False,
    token: str | None = None,
    web_root: Path | None = None,
    chat: Any = None,
    reflection: Any = None,
    eco: Any = None,
    memory: Any = None,
    workspace: Any = None,
    skills: Any = None,
    mcp: Any = None,
    graph: Any = None,
    deliberation: Any = None,
    delivery: Any = None,
    settings: Any = None,
    commands: Any = None,
    restart_gate: RestartGate | None = None,
) -> UiApp:
    """按已绑定的 ``host`` / ``port`` 装配表现层。

    ``dev=True`` 而 dev 子包不可用时抛 :class:`DevSurfaceUnavailable`——发布构建里
    「带 dev 跑」是配置错误，必须响，不能装作正常。

    ``chat`` / ``reflection`` / ``eco`` / ``memory``：M1–M4 的四个**鸭子端口**；``workspace`` /
    ``skills`` / ``mcp`` / ``graph`` / ``deliberation`` / ``delivery`` / ``settings``：M6 的
    七个（[`T-UI-011.1`]）；``commands``：快捷指令注册表（[`T-UI-012.1`]）。全部缺省
    ``None`` ⇒ 对应面 fail-closed（`ui` 不 import `st_agent.app`，只经端口消费）。

    ``restart_gate``：见 :class:`RestartGate`；缺省新建一枚。集成套件可注入同一实例，
    以便在端点之外断言「清空 / 恢复之后数据面确实停了」。
    """
    dev_package = _load_dev() if dev else None
    if dev and dev_package is None:
        raise DevSurfaceUnavailable(
            "dev 面不可用：st_agent.ui.dev 未安装（发布构建已按 [D-060] ④I 剔除）"
        )
    return UiApp(
        guard=RequestGuard(token=token or new_token(), host=host, port=port),
        dev=dev,
        web_root=web_root or WEB_ROOT,
        dev_package=dev_package,
        chat=chat,
        reflection=reflection,
        eco=eco,
        memory=memory,
        workspace=workspace,
        skills=skills,
        mcp=mcp,
        graph=graph,
        deliberation=deliberation,
        delivery=delivery,
        settings=settings,
        commands=commands,
        restart_gate=restart_gate if restart_gate is not None else RestartGate(),
    )
