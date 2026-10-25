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
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

from st_agent import __version__
from st_agent.contracts.identifiers import DeliveryId
from st_agent.contracts.permissions import DECISION_LABELS, describe_permission
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.l0.backup.errors import (
    BackupAuthError,
    BackupCorruptedError,
    BackupVersionError,
    RetentionError,
    WipeConfirmationError,
)
from st_agent.l0.bootstrap.dotenv import read_dotenv, write_dotenv
from st_agent.l0.llm.boot_config import load_boot_config, save_boot_config
from st_agent.l0.llm.http_transport import OpenAiRoute
from st_agent.l0.secrets.masking import mask_secret
from st_agent.l0.storage import Store
from st_agent.l0.storage.errors import (
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
)
from st_agent.l1.runtime import LLM_CREDENTIAL_ID, LlmBootstrap
from st_agent.l1.mcp.errors import (
    McpCancelledError,
    McpConnectionError,
    McpError,
    McpServerNotFoundError,
)
from st_agent.l1.runner.models import RUN_PREFIX
from st_agent.l1.skills.errors import SkillNotFoundError
from st_agent.l1.studio.errors import StudioError
from st_agent.l1.workflow.errors import TrialError, WorkflowNotFoundError
from st_agent.l1.workflow.ids import base_of
from st_agent.l3.render.describe import STANCE_LABELS, describe_divergence_map, describe_trace
from st_agent.l4.errors import BuiltinLensError, LensNotFoundError
from st_agent.l5.budget import LEVELS as SIGNAL_LEVELS
from st_agent.l5.budget import BudgetKey
from st_agent.l5.channels import CHANNEL_KINDS
from st_agent.l5.daily_report import NO_CONTENT_NOTE
from st_agent.l5.daily_report_store import REPORT_PREFIX
from st_agent.l5.errors import ChannelNotWiredError, L5Error
from st_agent.l5.fatigue import ANSWERS as FATIGUE_ANSWERS

from st_agent.ui.envelope import envelope_payload
from st_agent.ui.data_catalog import catalog_payload, domain_label, table_label, task_label
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

DEV_TOKEN_MARKER = "<!--ST_DEV_TOKEN-->"
"""dev 面把启动令牌注入静态壳的占位（仅 dev；发布构建替换为空串）。

用途：应用内 Browser 预览只能打开 ``http://localhost:<port>``，**无法携带 URL fragment**，
而令牌正典通道就是 fragment。dev 面（[D-060] ④I，发布构建物理剔除）把令牌以 meta 标签
放进同一份静态壳，前端在 fragment 缺失时才回退读取；生产口径不变。
"""

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


# ───────────────────── 能力区取数装配（[T-UI-015.1]） ─────────────────────
# 都做**纯函数**：只把 L1 的模型摆成描述槽的形状，不落盘、不引第二套语义。系统文案
# （来源 / 离线档 / 版本策略 / 六态）经本地生成文案表解析，Skill 自述与被呈现的值一律
# 按 `data` 原样（[D-053] / [D-064] 的分流）。


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


def _contract_keys(schema: Any) -> str:
    """schema 的顶层键清单（键是字段名，不算展示文本，故直接列）。"""
    if not isinstance(schema, Mapping) or not schema:
        return "（未声明）"
    properties = schema.get("properties")
    keys = list(properties) if isinstance(properties, Mapping) else list(schema)
    return "、".join(str(key) for key in keys) or "（未声明）"


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


def _brief_json(value: Any, *, limit: int = 60) -> str:
    """一份 JSON 可序列化值的**一行摘要**（超长即截断并标注，不静默丢内容）。"""
    try:
        text = json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "（无法序列化）"
    return text if len(text) <= limit else text[:limit] + "…"


# ───────────────────── 能力区取数装配：MCP Hub（[T-UI-015.2]） ─────────────────────


def _mcp_error_envelope(exc: Exception) -> ResultEnvelope:
    """MCP 层异常 → 六态（**必须显式翻译**，[00 §6] 失败显式化）。

    - **未注册**（`McpServerNotFoundError`）⇒ `unavailable` ＋ 点名——与「请求不合契约」
      互不冒充；
    - **连不上 / 调用被中止**（`McpConnectionError` / `McpCancelledError`）⇒ `unavailable`
      ＋ 原因——目标不可达不是「工具坏了」，也不是用户的输入问题；
    - 其余（声明非法 / 重名 / 权限未声明 / 状态或映射非法）⇒ `validation_failed`。
    """
    if isinstance(exc, McpServerNotFoundError):
        return ResultEnvelope.unavailable(
            f"{exc}——该标识未注册（不是请求不合契约）", last_updated_at=_now()
        )
    if isinstance(exc, (McpConnectionError, McpCancelledError)):
        return ResultEnvelope.unavailable(f"{exc}", last_updated_at=_now())
    return ResultEnvelope.validation_failed(str(exc) or "MCP 请求不合契约")


def _optional_text(value: Any) -> str | None:
    """可选文本项：空白视同未给（`None`），不把 `""` 当命令 / URL 传下去。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def _mcp_server_row(face: Any, record: Any) -> dict[str, Any]:
    """一行 Server 摘要（状态由 `state_of` **纯读**推导，不产生副作用）。"""
    machine = face.machine
    state = machine.state_of(record.server_id) if machine is not None else ""
    return {
        "display_name": record.display_name,
        "server_id": record.server_id,
        "transport": _MCP_TRANSPORT_LABELS.get(record.transport, record.transport),
        "enabled": "启用" if record.enabled else "停用",
        "state": _MCP_STATE_LABELS.get(state, "—"),
        "permissions": len(record.permissions),
    }


def _mcp_tool_row(face: Any, mapping: Any) -> dict[str, Any]:
    """一行 tool 映射：状态 + 变化说明 + **派生描述体**的参数面（schema 已随派生落到 Skill）。"""
    descriptor = _skill_descriptor_or_none(face.skills, mapping.skill_id)
    return {
        "tool": mapping.tool,
        "skill_id": mapping.skill_id,
        "status": _MCP_MAPPING_LABELS.get(mapping.status, mapping.status),
        "version": mapping.mapping_version,
        "inputs": (
            _contract_keys(descriptor.input_schema) if descriptor is not None
            else "（描述体不在）"
        ),
        "change_note": mapping.change_note or "—",
    }


def _mcp_approval_slots(record: Any, approvals: tuple[Any, ...]) -> dict[str, Any]:
    """`permission_approval_card` 的槽（[01 §10]：逐条「这个 Server 想做什么」＋ 当前态）。

    措辞取**契约级**的唯一真相源 `describe_permission` / `DECISION_LABELS`，本层不另写词表。
    """
    return {
        "key": record.server_id,
        "source": f"MCP Server · {record.display_name}",
        "items": [
            {
                "permission": approval.permission,
                "state": approval.decision,
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "panel_field": {"widget": "逐项批准 / 拒绝"},
            }
            for approval in approvals
        ],
        "labels": {
            approval.permission: {
                "description": describe_permission(approval.permission),
                "chat_text": DECISION_LABELS.get(approval.decision, approval.decision),
            }
            for approval in approvals
        },
    }


def _mcp_approval_entries(record: Any, approvals: tuple[Any, ...]) -> list[dict[str, Any]]:
    """逐项批准 / 拒绝的处置条目（`setting_panel`：动作即取值，故只给两个动作按钮）。"""
    return [
        {
            "identifier": approval.permission,
            "description": describe_permission(approval.permission),
            "current": DECISION_LABELS.get(approval.decision, approval.decision),
            "options": [],
            "actions": ["approve", "reject"],
            "params": {"server_id": record.server_id, "permission": approval.permission},
        }
        for approval in approvals
    ]


# ───────────────────── 能力区取数装配：Studio 附加面（[T-UI-015.3]） ─────────────────────


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

_ENVELOPE_STATE_LABELS: dict[str, str] = {
    "ok": "完成",
    "empty": "空结果",
    "delayed": "延后",
    "unavailable": "不可用",
    "failed": "失败",
    "validation_failed": "不合契约",
}
"""[01 §5] 六态的中文名（系统文案）——执行日志按原值取此表，**不合并**（六态互不冒充）。"""

_SKILL_LOG_LIMIT = 50
"""Skill 执行日志页单次列出的上限（超出即如实标注「只显示最近 N 条」，不静默截断）。"""

MCP_APPROVAL_SURFACE = "mcp-permissions"
"""MCP 权限逐项批准面的面键（`setting_panel` 的 `surface` 槽；[`T-UI-015.2`]）。

与 `skill-params` / `LENS_SURFACE` 同为**枚举键**：路由在渲染件的固定表里解析，描述
不接受 URL / 方法（[01 §12]）。
"""

_MCP_APPROVAL_LABELS: dict[str, str] = {"approve": "批准", "reject": "拒绝"}
"""权限处置面的动作标签（生成文案槽 `labels`，过 [01 §6]）。"""

_MCP_ACTION_LABELS: dict[str, str] = {
    "enable": "启用",
    "disable": "停用",
    "remove": "移除",
    "test": "连接测试",
}
"""Hub 面板的 Server 动作标签（生成文案槽 `labels`）。`remove` 在界面上只对**本地 stdio**
之外一概可用——回收语义对两种传输一致（[03 §5.2]）。"""

_MCP_TRANSPORT_LABELS: dict[str, str] = {
    "stdio": "本地 stdio（子进程）",
    "remote": "远程 HTTP/SSE",
}
"""MCP 传输的中文名（系统文案；[03 §5.1] 两种传输）。"""

_MCP_STATE_LABELS: dict[str, str] = {
    "connected": "已连接",
    "disconnected": "未连接",
    "reconnecting": "重连中",
    "permission_pending": "待批准权限",
    "disabled": "已停用",
}
"""MCP Server 生命周期状态的中文名（系统文案；[03 §5.3] 五个状态）。"""

_MCP_MAPPING_LABELS: dict[str, str] = {
    "active": "生效",
    "pending-remap": "待重新映射",
    "vanished": "已失效",
}
"""tool → Skill 映射状态的中文名（系统文案；[03 §5.2] 三个状态）。`vanished` 追加一句
「tool 从 Server 消失」在行内说明——**不删记录**、可自愈，故措辞不带「已删除」。"""

_MCP_NOTICE_LIMIT = 20
"""Server 详情页单次列出的状态留痕条数上限（超出如实标注）。"""

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
    "ok": "完成", "empty": "空", "partial": "部分完成",
    "unavailable": "不可用", "failed": "失败", "cancelled": "已取消",
    "validation_failed": "请求不合法", "dependency_failed": "前置未就绪",
}
"""出网结果 / 六态 → 中性中文。"""

_SYNC_STATUS_LABELS = {
    "ok": "正常", "partial": "部分完成", "failed": "失败",
}
"""`sync_state.last_status` → 中性中文；缺行由调用方呈现为「未同步」。"""

_SYNC_JOB_LABELS = {
    "running": "进行中", "done": "已完成", "failed": "失败", "cancelled": "已取消",
}
"""手动同步批次状态 → 中性中文（[D-129] / [D-130]）。"""


def _task_name(key: Any) -> str:
    """任务键 → 中文名（目录词表；未知键回退原键，不伪造）。"""
    text = str(key or "")
    return task_label(text)[0] if text else ""


def _optional_text(value: Any) -> str | None:
    """请求体里的可选字符串（表单可能给空串 / null / 非字符串）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _st_scope_line(st: Any) -> str:
    """总览里的 ST 池一行（可用 / 空 / 缺视图三态显式）。"""
    if not isinstance(st, dict) or not st:
        return "—"
    if not st.get("available"):
        return f"不可用：{st.get('reason') or '未知原因'}"
    count = int(st.get("count") or 0)
    if not count:
        return str(st.get("reason") or "空（需先同步证券主档与日线行情）")
    return f"{count} 只（数据截止 {st.get('as_of') or '—'}）"


def _watch_scope_line(watch: Any) -> str:
    """总览里的关注池一行。"""
    if not isinstance(watch, dict) or not watch:
        return "—"
    count = int(watch.get("count") or 0)
    if not count:
        return str(watch.get("reason") or "空（持仓 + 关注均无证券）")
    return f"{count} 只"


def _job_scope_text(job: Mapping[str, Any]) -> str:
    """批次的证券范围一行（[D-130]）。"""
    scope = job.get("scope")
    if not isinstance(scope, dict):
        return "全市场" if not scope else str(scope)
    kind = scope.get("scope")
    if kind == "st":
        return f"仅 ST（{scope.get('count')} 只，数据截止 {scope.get('as_of') or '—'}）"
    if kind == "watchlist":
        return f"仅关注池（{scope.get('count')} 只）"
    count = scope.get("count")
    return f"全市场（{count if count is not None else '—'} 只在市）"


def _llm_boot_values(face: Any) -> dict[str, Any]:
    """LLM 引导配置的生效值（读面；Key 只出掩码；[D-129]）。"""
    env_path = getattr(face, "dotenv_path", None)
    env_values = read_dotenv(env_path) if env_path else {}
    try:
        stored = load_boot_config(face.store)
    except Exception:          # noqa: BLE001 — 记录损坏由读面在别处显式呈现
        stored = None
    env_base = str(env_values.get("LLM_BASE_URL") or "").strip()
    env_model = str(env_values.get("LLM_MODEL") or "").strip()
    base_url = env_base or (stored.base_url if stored else "")
    model = env_model or (stored.model if stored else "")
    if env_base or env_model:
        source = "引导 .env（优先）"
    elif stored is not None:
        source = "本地配置 llm-boot.json（.env 缺三键时的回退）"
    else:
        source = "未配置"
    masked = ""
    try:
        masked = face.vault.get_view(LLM_CREDENTIAL_ID).masked
    except Exception:          # noqa: BLE001 — 未解锁 / 未登记都可能，取 .env 的掩码
        env_key = str(env_values.get("LLM_API_KEY") or "").strip()
        masked = mask_secret(env_key) if env_key else ""
    return {
        "base_url": base_url,
        "model": model,
        "api_key_masked": masked,
        "source": source,
        "dotenv_path": str(env_path) if env_path else "",
        "vault_unlocked": bool(getattr(face.store, "secrets_unlocked", False)),
    }


def _root_location(face: Any) -> tuple[str, str]:
    """存储根的位置来源与启动指针（设置-存储页的两行；[D-129]）。"""
    root = Path(str(face.store.root))
    boot = getattr(face, "root_boot", None)
    if boot is None:
        return "启动指针未接入（装配归组合根）", "—"
    pointer = boot.read()
    default = Path(str(boot.default))
    if pointer is None:
        source = (
            "平台默认（未设置启动指针）"
            if root.resolve() == default.resolve()
            else "启动参数或环境变量（优先于指针）"
        )
        pointer_text = f"未设置（默认 {default}）"
    elif Path(pointer).resolve() == root.resolve():
        source = "设置页启动指针"
        pointer_text = str(pointer)
    else:
        source = "启动参数或环境变量（优先于指针）"
        pointer_text = f"{pointer}（当前进程未使用）"
    return source, pointer_text

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


def _short_stamp(value: Any) -> str:
    """同步状态里的短时刻（`YYYY-MM-DD HH:MM`；解析不了就给原串前 16 字）。"""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)[:16]


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


# ───────────────────── 触达区（[T-UI-017]） ─────────────────────

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

    studio: Any = None
    """L1 Studio 面（工作流 / 模板库 / 试跑；[`T-UI-015.3`]）；缺省 ``None`` → fail-closed。

    与 `reflection` 那个 Studio 面**不是同一件事**：后者是 L6 主动提案的**画布会话**交接面
    （`proposal_id` 寻址），本端口是 L1 的**自建工作流**面（版本 / 模板 / 试跑）。同一页面
    `/studio` 上两块各走各的口。
    """

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

    shell: bool = False
    """本进程是否由**桌面壳**拉起（[`T-UI-027.1`]）。

    为真时前端才接管 `Ctrl+W` / `Ctrl+Q`——浏览器直开（dev / CI 面）必须让这两个键维持
    浏览器自己的语义，否则会把用户的标签页按住。
    """

    shell_control: Any = None
    """壳意图端口（鸭子类型 ``(action) -> None``；`hide` / `quit`）；缺省 ``None`` → 显式不可用。"""

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
        """健康面：恒为 `ok`——本叶不接真实数据（任务假设 `A5`）。

        `shell` 标记（[`T-UI-027.1`]）告诉前端「本进程由桌面壳拉起」——只有此时才接管
        `Ctrl+W` / `Ctrl+Q`；浏览器面必须让这两个键维持浏览器语义。
        """
        return envelope_payload(
            ResultEnvelope.ok(
                {
                    "service": "st-agent-ui",
                    "version": _VERSION,
                    "dev": self.dev_enabled,
                    "shell": self.shell,
                }
            )
        )

    def api_shell_intent(self, body: dict[str, Any]) -> dict[str, Any]:
        """壳意图（[`T-UI-027.1`]）：`Ctrl+W` → `hide`（收托盘）、`Ctrl+Q` → `quit`（退出）。

        壳没有 JS 桥（[00 §1.1]「壳不提供任何专有通道」），故动作经回环面到后端、再由后端
        沿既有 stdout 控制管道回传壳。非壳会话（浏览器直开）显式 `unavailable`，不静默。
        """
        action = body.get("action")
        if action not in ("hide", "quit"):
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    f"壳意图动作非法：{action!r}（须为 hide / quit）"
                )
            )
        if not self.shell or self.shell_control is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "当前不是桌面壳会话：收起 / 退出由壳执行，浏览器面不接管该按键",
                    last_updated_at=_now(),
                )
            )
        try:
            self.shell_control(action)
        except Exception as exc:                     # noqa: BLE001 —— 端点须回信封
            return envelope_payload(
                ResultEnvelope.failed(f"壳意图投递失败：{exc}", log_ref="st-agent/ui/shell")
            )
        return envelope_payload(ResultEnvelope.ok({"action": action}))

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
        """设置-LLM 提供方页：引导配置 + 提供方主机映射 + 端点 + 凭据（掩码）。

        四段**并列**，不合并：引导配置（``.env`` / ``llm-boot.json`` 生效值）、端点
        （`EndpointRegistry`）、提供方→主机映射（`llm-provider-host/` 的 01 §7 条目，
        可改值）、凭据（`CredentialVault` 的掩码视图 + 使用记录计数）。
        **凭据明文永不出站**——页面只拿到掩码。
        """
        return self._settings_parts(
            "settings-llm", ("endpoints", "vault", "llm_runtime"),
            ("boot", "panel", "endpoints", "credentials"),
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

    def api_settings_data(self) -> dict[str, Any]:
        """设置-数据同步页：数据总览 + 逐表清单 + 当前批次（[02 §5]；[D-129]）。

        四段并列：`summary`（库是否建立 / 任务与行数合计）、`tasks`（逐任务状态与水位）、
        `tables`（逐表行数）、`job`（当前 / 最近一次批次的进度与结果）。读面**不触网**，
        只读本地 `sync_state` 与各表行数。
        """
        return self._settings_parts(
            "settings-data", ("data_sync",),
            ("summary", "tasks", "tables", "job"),
            lambda face: self._data_parts(face),
        )

    def api_settings_data_sync(self, body: dict[str, Any]) -> dict[str, Any]:
        """发起手动同步（显式动作；后台线程执行，立即返回 ``job_id``）。"""
        raw = body.get("task_keys")
        keys = [str(item) for item in raw] if isinstance(raw, list) else []
        start = _optional_text(body.get("start"))
        end = _optional_text(body.get("end"))
        scope = _optional_text(body.get("scope")) or "market"
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-sync", face, "L0 数据同步面",
            lambda sync: sync.start(keys, start=start, end=end, scope=scope),
        )

    def api_settings_data_job(self) -> dict[str, Any]:
        """轻量批次快照（运行中轮询用；不读表行数、不做整库统计；[D-130]）。"""
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-job", face, "L0 数据同步面",
            lambda sync: ResultEnvelope.ok({"job": sync.job() or {}}),
        )

    def api_settings_data_preflight(self, body: dict[str, Any]) -> dict[str, Any]:
        """发起前预检：执行顺序 / 自动补入 / 范围解析 / 忽略项 / 警告（[D-130]）。"""
        raw = body.get("task_keys")
        keys = [str(item) for item in raw] if isinstance(raw, list) else []
        start = _optional_text(body.get("start"))
        end = _optional_text(body.get("end"))
        scope = _optional_text(body.get("scope")) or "market"
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-preflight", face, "L0 数据同步面",
            lambda sync: sync.preflight(keys, start=start, end=end, scope=scope),
        )

    def api_settings_data_sync_cancel(self, body: dict[str, Any]) -> dict[str, Any]:
        """请求停止当前批次（任务间与网关调用前后生效；[D-130]）。"""
        _ = body
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-sync-cancel", face, "L0 数据同步面",
            lambda sync: sync.cancel(),
        )

    def api_settings_store_relocate(self, body: dict[str, Any]) -> dict[str, Any]:
        """更改存储位置：迁移 / 仅切换 / 恢复默认（[02 §2.1]；[D-129]）。

        成功后置重启闸门——本进程手里的 `Store` 仍指向旧根，**不假装已切换**。
        """
        return self._call_face(
            "settings-store-relocate", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_relocate(face, body),
        )

    def api_settings_llm_bootstrap(self, body: dict[str, Any]) -> dict[str, Any]:
        """保存 LLM 引导配置（``base_url`` / ``model`` / ``api_key``）并热更新（[D-129]）。"""
        return self._call_face(
            "settings-llm-bootstrap", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_llm_bootstrap(face, body),
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
        source, pointer_text = _root_location(face)
        summary = self._part(
            "report_card",
            slots={"sections": [
                {"title": "存储位置", "lines": [str(root)]},
                {"title": "位置来源", "lines": [source]},
                {"title": "启动指针", "lines": [pointer_text]},
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
        return {"panel": panel, "endpoints": endpoints_part,
                "credentials": credentials_part, "boot": self._llm_boot_part(face)}

    def _llm_boot_part(self, face: Any) -> dict[str, Any]:
        """LLM 引导配置的读面（``report_card`` + 供表单预填的 ``values`` 槽；[D-129]）。"""
        values = _llm_boot_values(face)
        lines = [
            ["服务地址", values["base_url"] or "（未配置）"],
            ["模型", values["model"] or "（未配置）"],
            ["API Key", values["api_key_masked"] or "（未配置）"],
            ["配置来源", values["source"]],
            ["引导 .env", str(face.dotenv_path) if face.dotenv_path else "（未接入）"],
            ["凭据分区", "已解锁" if values["vault_unlocked"] else "未解锁（保存时须给凭据口令）"],
        ]
        return self._part(
            "report_card",
            slots={
                "sections": [
                    {"title": "当前引导配置",
                     "lines": [f"{label}：{value}" for label, value in lines]},
                ],
                "values": values,
            },
            text_kinds={"sections": "data", "values": "data"},
            title="引导配置",
        )

    def _data_parts(self, face: Any) -> dict[str, Any]:
        """数据同步页的四段（[02 §5]；[D-129] / [D-130]）。"""
        overview = face.data_sync.overview()
        meta = overview["summary"]
        scope = overview.get("scope") or meta.get("scope") or {}
        catalog = catalog_payload(overview["tasks"])
        items = list(catalog["items"])
        ok_items = sum(1 for item in items if item["status"] == "ok")
        sections = [
            {"title": "本地市场库", "lines": [
                "已建立" if meta["db_exists"] else "尚未建立（首次同步未完成）"
            ]},
            {"title": "数据项", "lines": [
                f"{ok_items} / {len(items)} 个数据项最近一次成功"
            ]},
            {"title": "同步任务", "lines": [
                f"{meta['ok_task_count']} / {meta['task_count']} 个任务最近一次成功"
            ]},
            {"title": "数据表", "lines": [
                (f"{meta['table_count']} 张，合计 {meta['total_rows']} 行"
                 if meta["db_exists"]
                 else f"0 张（尚未建库；任务覆盖 {meta.get('covered_table_count', 0)} 张表）")
            ]},
            {"title": "ST 股票池", "lines": [_st_scope_line(scope.get("st"))]},
            {"title": "关注池", "lines": [_watch_scope_line(scope.get("watchlist"))]},
            {"title": "最后成功", "lines": [
                _short_stamp(meta["last_success_at"])
                if meta["last_success_at"] else "—"
            ]},
        ]
        if meta["note"]:
            sections.append({"title": "提示", "lines": [meta["note"]]})
        summary_values = {
            **meta,
            "catalog": {"item_count": len(items), "ok_item_count": ok_items},
        }
        summary = self._part(
            "report_card",
            slots={"sections": sections, "values": summary_values},
            text_kinds={"sections": "data", "values": "data"},
            title="数据总览",
        )
        item_rows = [
            {
                "item": f"{item['category_label']} · {item['display_name']}",
                "tables": "、".join(
                    f"{table['display_name']}"
                    f"（{table['rows'] if table['rows'] is not None else '—'}）"
                    for table in item["tables"]
                ),
                "status": (
                    _SYNC_STATUS_LABELS.get(str(item["status"]), str(item["status"]))
                    if item["status"] else "未同步"
                ),
                "last_success": (
                    "—"
                    if not item["last_success_at"] and not item["watermark"]
                    else f"{_short_stamp(item['last_success_at'])}"
                         f" · 水位 {str(item['watermark'])[:20] if item['watermark'] else '—'}"
                ),
            }
            for item in items
        ]
        tasks = self._part(
            "table",
            slots={**table_slots(
                (
                    Column("item", "数据项"),
                    Column("tables", "目标表（行数）"),
                    Column("status", "状态"),
                    Column("last_success", "最近成功 · 水位"),
                ),
                item_rows,
            ), "values": catalog},
            text_kinds={**table_text_kinds(), "values": "data"},
            title="数据项与状态",
        )
        table_rows = [
            {
                "table": item["table"],
                "display_name": table_label(item["table"])[0],
                "description": table_label(item["table"])[1],
                "rows": item["rows"],
                "domains": "、".join(domain_label(domain) for domain in item["domains"]),
            }
            for item in overview["tables"]
        ]
        tables = (
            self._part(
                "table",
                slots=table_slots(
                    (
                        Column("display_name", "数据表"),
                        Column("description", "说明"),
                        Column("rows", "行数", "number", digits=0),
                        Column("domains", "所属域"),
                    ),
                    table_rows,
                ),
                text_kinds=table_text_kinds(),
                title="本机数据明细",
            )
            if table_rows
            else envelope_payload(ResultEnvelope.empty(
                "本地市场数据库尚未建立，暂无数据表可列——先执行首次同步"
            ))
        )
        job = overview.get("job")
        if job is None:
            job_lines = ["当前无同步批次"]
            job_values: dict[str, Any] = {}
        else:
            done = sum(
                1 for item in job["results"].values()
                if item.get("status") in ("ok", "empty", "unavailable", "failed",
                                          "validation_failed", "dependency_failed",
                                          "cancelled")
            )
            job_lines = [
                f"批次 {job['job_id']}：{_SYNC_JOB_LABELS.get(job['state'], job['state'])}",
                f"进度：{done} / {len(job['order'])}",
                f"当前任务：{_task_name(job.get('current')) or '—'}",
                f"本次范围：{_job_scope_text(job)}",
            ]
            if job.get("start") or job.get("end"):
                job_lines.append(
                    f"本次区间：{job.get('start') or '（按水位）'} → "
                    f"{job.get('end') or '今日'}"
                )
            if job.get("auto_added"):
                job_lines.append(
                    "自动补入前置：" + "、".join(
                        _task_name(key) for key in job["auto_added"]
                    )
                )
            if job.get("range_ignored"):
                job_lines.append(
                    "忽略日期区间：" + "、".join(
                        _task_name(key) for key in job["range_ignored"]
                    )
                )
            if job.get("scope_ignored"):
                job_lines.append(
                    "始终全市场：" + "、".join(
                        _task_name(key) for key in job["scope_ignored"]
                    )
                )
            if job.get("error"):
                job_lines.append(f"批次错误：{job['error']}")
            for key, result in job["results"].items():
                reason = f"（{result['reason']}）" if result.get("reason") else ""
                rows = (
                    f" · {result['rows']} 行"
                    if result.get("rows") is not None else ""
                )
                took = (
                    f" · {result['duration_ms']} ms"
                    if result.get("duration_ms") is not None else ""
                )
                status = _STATUS_LABELS.get(
                    str(result.get("status")), str(result.get("status"))
                )
                job_lines.append(
                    f"{_task_name(key)}（{key}）：{status}{rows}{took}{reason}"
                )
            job_values = job
        job_part = self._part(
            "report_card",
            slots={
                "sections": [{"title": "同步批次", "lines": job_lines}],
                "values": job_values,
            },
            text_kinds={"sections": "data", "values": "data"},
            title="最近一次同步",
        )
        return {
            "summary": summary, "tasks": tasks, "tables": tables, "job": job_part,
        }

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

    def _run_relocate(self, face: Any, body: dict[str, Any]) -> Any:
        """迁移 / 切换 / 恢复默认存储位置（[02 §2.1]；[D-129]）。"""
        mode = str(body.get("mode") or "migrate").strip()
        if mode not in ("migrate", "switch", "reset"):
            raise ValueError(f"存储位置动作只认 migrate / switch / reset：{mode!r}")
        boot = getattr(face, "root_boot", None)
        if boot is None:
            return ResultEnvelope.unavailable(
                "存储根指针未接入设置面（装配归组合根）", last_updated_at=_now()
            )
        current = Path(str(face.store.root))
        env_override = str(os.environ.get("ST_AGENT_ROOT") or "").strip()
        env_note = (
            f"；注意环境变量 ST_AGENT_ROOT={env_override} 仍优先于启动指针，"
            "重启前请先移除它"
            if env_override else ""
        )
        if mode == "reset":
            changed = boot.clear()
            self.restart_gate.reason = (
                f"存储位置已恢复为平台默认 {boot.default}：请重启应用后再继续；"
                "当前进程仍指向旧根"
            )
            return {
                "target_root": str(boot.default),
                "mode": "reset",
                "restart_required": True,
                "note": ("已删除启动指针" if changed else "启动指针原本就不存在") + env_note,
            }
        target = Path(str(_required(body, "target")).strip()).expanduser()
        if target.resolve() == current.resolve():
            raise ValueError("目标根不得与当前存储根相同")
        if _is_inside(target, current):
            raise ValueError(f"目标根不得落在当前存储根之内（{target}）")
        if mode == "switch":
            if not Store.exists(target):
                raise ValueError(
                    f"目标根不是既有存储（{target}）：请选择含 store.json / keyfile.json 的目录，"
                    "或改用迁移"
                )
            boot.write(target)
            self.restart_gate.reason = (
                f"存储位置已切换至 {target}：请重启应用后再继续；当前进程仍指向旧根"
            )
            return {
                "target_root": str(target),
                "mode": "switch",
                "restart_required": True,
                "note": "只写指针，未复制任何数据" + env_note,
            }
        if target.exists() and any(target.iterdir()):
            raise ValueError(f"目标根 {target} 非空：迁移只写全新根")
        target.parent.mkdir(parents=True, exist_ok=True)
        if face.convert_store is None:
            return ResultEnvelope.unavailable(
                "明密转换口未注入设置面（装配归组合根）", last_updated_at=_now()
            )
        source_pass = str(body.get("passphrase") or "").strip() or None
        target_pass = source_pass if str(face.store.mode) == "encrypted" else None
        envelope = _store_errors(lambda: face.convert_store(
            current,
            target,
            to_format=str(face.store.mode),
            source_passphrase=source_pass,
            target_passphrase=target_pass,
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        boot.write(target)
        self.restart_gate.reason = (
            f"存储位置已迁移至 {target}：请重启应用后再继续；当前进程仍指向旧根 {current}"
        )
        return {
            "target_root": str(target),
            "mode": "migrate",
            "restart_required": True,
            "note": "源根未改动（可另行删除）；凭据分区原样搬运，凭据口令不变" + env_note,
        }

    def _run_llm_bootstrap(self, face: Any, body: dict[str, Any]) -> Any:
        """保存 LLM 引导配置并热更新（[02 §4]；[D-129]）。"""
        base_url = str(_required(body, "base_url")).strip()
        model = str(_required(body, "model")).strip()
        api_key = str(body.get("api_key") or "").strip()
        passphrase = str(body.get("passphrase") or "").strip() or None
        # 先校验 URL 形态（非法取值不得落进任何记录）
        OpenAiRoute(base_url=base_url, model=model)
        runtime = getattr(face, "llm_runtime", None)
        if runtime is None:
            return ResultEnvelope.unavailable(
                "LLM 运行时未接入设置面（装配归组合根）", last_updated_at=_now()
            )
        store = face.store
        if not bool(getattr(store, "secrets_unlocked", False)):
            if not passphrase:
                return ResultEnvelope.unavailable(
                    "凭据分区未解锁（凭据恒加密）：请填写凭据口令后再保存",
                    last_updated_at=_now(),
                )
            unlocked = _store_errors(lambda: store.unlock_secrets(passphrase))
            if isinstance(unlocked, ResultEnvelope):
                return unlocked
        config = LlmBootstrap(
            api_key=api_key or "unchanged", base_url=base_url, model=model
        )
        applied = runtime.apply_llm_bootstrap(config, rotate_key=bool(api_key))
        save_boot_config(store, base_url=base_url, model=model)
        updates = {"LLM_BASE_URL": base_url, "LLM_MODEL": model}
        if api_key:
            updates["LLM_API_KEY"] = api_key
        dotenv_written = False
        dotenv_note = "未接入引导 .env（跳过）"
        if face.dotenv_path:
            try:
                write_dotenv(face.dotenv_path, updates)
                dotenv_written = True
                dotenv_note = "已写回引导 .env（明文文件，仅开发 / 自用通道）"
            except Exception as exc:      # noqa: BLE001 —— 写不进 .env 不否定已生效的配置
                dotenv_note = f"未能写回 .env：{exc}；配置已存入本地配置并立即生效"
        return {
            "base_url": base_url,
            "model": model,
            "api_key_masked": mask_secret(api_key) if api_key else "",
            "host": applied.get("host"),
            "key_rotated": bool(api_key),
            "dotenv_written": dotenv_written,
            "dotenv": str(face.dotenv_path) if face.dotenv_path else None,
            "restart_required": False,
            "note": dotenv_note,
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

    # ───────────────────── 能力区：Skill 库（[T-UI-015.1]） ─────────────────────
    # story-02 的四条屏（[11-sitemap §2 能力]）。三处取数口各司其职：清单 / 详情走
    # `SkillRegistry`；参数落值走 `set_parameters`（[03 §1.1]：Skill **无状态**，用户状态
    # 只存 L2 记忆与配置注册表，故描述体即参数的唯一真相源）；执行日志读 `execution_log`
    # 分区 `skill-run/`——那是 [03 §1.2] 步骤 5 的**既有**落盘，本层只新增一条**只读列表**，
    # 不新开分区、不改 L1 口径。
    #
    # 三种缺口互不冒充（[01 §5]）：缺查询串 ⇒ `validation_failed`；标识**形态**非法 ⇒
    # `validation_failed`（`check_skill_id` 抛的 `SkillValidationError` 是 `ValueError`）；
    # 形态合法但**未注册** ⇒ `unavailable` + 点名。最后一档在此**就地**翻译——`SkillNotFoundError`
    # 是 `KeyError`，落到 `_call_face` 会被说成「请求不合契约」，那是把「没有这个能力」误报成
    # 「你请求错了」（同 `T-UI-016.2` 的三种缺口分流）。

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

    # ───────────────────── 能力区：MCP Hub（[T-UI-015.2]） ─────────────────────
    # story-08 的五条屏（[11-sitemap §2 能力]）＋ 添加 Server 表单。四支读面：清单 / 详情 /
    # tool 浏览器 / 权限面（声明 ＋ 处置两段）。两处刻意：
    #
    # - **移除必须走 `L1Runtime.remove_mcp_server`**（经门面的 `remove_server` 引用）——
    #   它先 `disable` 再 `remove_server`，后者触发**组合根接的回收回调**（删映射记录 +
    #   反注册派生 Skill + 清生命周期当前态）。表现层自行拼两步会把回收落掉，派生 Skill
    #   与映射记录成孤儿（[03 §5.2] 回收落地口径）。
    # - **连接测试失败不是「工具坏了」**——`add_server` / `test_connection` 以 `ok=False`
    #   如实回结论且**不落盘**（[03 §5.1]），故翻成 `unavailable` ＋ 原因，而不是 `failed`。

    def api_mcp_servers(self) -> dict[str, Any]:
        """Server 清单 → `table`（逐条并列，含各自的生命周期状态）。"""
        return self._mcp_call(
            "mcp-servers", self._mcp_servers_envelope
        )

    def api_mcp_server(self, *, server_id: str) -> dict[str, Any]:
        """Server 详情 → `report_card`（记录 / 状态 / 留痕 / 降级候选 / 数据外发风险五段）。"""
        if not server_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Server 标识（id）"))
        return self._mcp_call(
            "mcp-server",
            lambda face: self._mcp_server_envelope(face, server_id),
        )

    def api_mcp_tools(self, *, server_id: str) -> dict[str, Any]:
        """tool schema 浏览器 → `table`（逐 tool 的映射状态与**派生描述体**的参数面）。"""
        if not server_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Server 标识（id）"))
        return self._mcp_call(
            "mcp-tools",
            lambda face: self._mcp_tools_envelope(face, server_id),
        )

    def api_mcp_permissions(self, *, server_id: str) -> dict[str, Any]:
        """权限面 → 两段：`declaration`（`permission_approval_card`，只读声明）＋
        `decisions`（`setting_panel`，逐项批准 / 拒绝）。

        两段同出于一条端点：声明面讲「这个 Server 想做什么」（[01 §10]），处置面才是动作。
        `permission_approval_card` 渲染件**只渲染不落账**（其头注明写），故动作面另走
        `setting_panel`——同 `T-UI-004.4` 对导入审批的处理（声明 `report_card` ＋ 处置
        `setting_panel`）。
        """
        if not server_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Server 标识（id）"))
        return self._mcp_parts(
            "mcp-permissions", ("declaration", "decisions"),
            lambda face: self._mcp_permissions_parts(face, server_id),
        )

    def api_mcp_server_create(self, body: dict[str, Any]) -> dict[str, Any]:
        """添加一台 Server（GWT-1）：保存即**自动连接测试**，成功才落盘（[03 §5.1]）。"""
        return self._mcp_call(
            "mcp-server-create",
            lambda face: self._mcp_server_create_envelope(face, body),
        )

    def api_mcp_server_update(self, body: dict[str, Any]) -> dict[str, Any]:
        """一台 Server 的动作（启用 / 停用 / 移除 / 连接测试）——四者皆显式动作。"""
        return self._mcp_call(
            "mcp-server-update",
            lambda face: self._mcp_server_update_envelope(face, body),
        )

    def api_mcp_permission_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """逐项批准 / 拒绝一条权限（[01 §10]：逐项、不合并）。"""
        return self._mcp_call(
            "mcp-permission-decide",
            lambda face: self._mcp_permission_decide_envelope(face, body),
        )

    def _mcp_call(self, where: str, fn: Callable[[Any], Any]) -> dict[str, Any]:
        """调一次 MCP 面操作：**先按 MCP 异常族翻成六态**，再交 `_call_face` 出信封。

        MCP 错误全部继承 ``Exception`` 而非 ``ValueError`` / ``KeyError``（见
        [`l1/mcp/errors.py`] 的模块取向），故 `_call_face` 的输入类分流**覆盖不到**——不翻译
        则「该 Server 未注册」「权限未声明」会被报成 `failed`，读起来是「工具坏了」（同
        [`T-UI-018.2`] 对 L0 备份族异常的处理）。
        """

        def guarded(face: Any) -> Any:
            try:
                return fn(face)
            except McpError as exc:
                return _mcp_error_envelope(exc)

        return self._call_face(where, self.mcp, "L1 能力（MCP）面", guarded)

    def _mcp_parts(
        self, where: str, keys: tuple[str, ...], fn: Callable[[Any], dict[str, Any]]
    ) -> dict[str, Any]:
        """多段 MCP 端点：**任一失败，各段都是同一支错误信封**（同 `_settings_parts` 口径）。

        分段端点**不能**经 `_call_face`——后者会把非信封的返回值包成 `ok`，于是
        `{declaration, decisions}` 会变成 `data` 里的一层，页面逐段找键就找不到了。
        """
        def blank(envelope: ResultEnvelope) -> dict[str, Any]:
            payload = envelope_payload(envelope)
            return {key: payload for key in keys}

        if self.restart_gate.reason:
            return blank(ResultEnvelope.unavailable(
                self.restart_gate.reason, last_updated_at=_now()
            ))
        face = self.mcp
        if face is None:
            return blank(ResultEnvelope.unavailable(
                "未接入 L1 能力（MCP）面，该端点不可用（装配归生产入口）",
                last_updated_at=_now(),
            ))
        try:
            return fn(face)
        except McpError as exc:
            return blank(_mcp_error_envelope(exc))
        except (ValueError, KeyError) as exc:
            _LOG.info("表现层端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
            return blank(ResultEnvelope.validation_failed(str(exc) or "请求不合契约"))
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/{where}-{uuid.uuid4().hex[:12]}"
            _LOG.exception("表现层端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
            return blank(ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref))

    def _mcp_server_record(self, face: Any, server_id: str) -> Any:
        """读一台 Server 的注册记录（不存在即抛，由 `_call_face` 归 `validation_failed`）。"""
        return face.servers.get_server(server_id)

    def _mcp_servers_envelope(self, face: Any) -> ResultEnvelope:
        servers = tuple(face.servers.list_servers())
        if not servers:
            return ResultEnvelope.empty(
                "尚未挂载任何 MCP Server——在上方「添加 Server」填入命令或 URL 即可挂载"
                "（03 §5.1）"
            )
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("display_name", "名称"),
                    Column("server_id", "标识"),
                    Column("transport", "传输"),
                    Column("enabled", "开关"),
                    Column("state", "状态"),
                    Column("permissions", "权限数", "number", digits=0),
                ),
                [_mcp_server_row(face, record) for record in servers],
            ),
            text_kinds=table_text_kinds(),
            title="MCP Server 清单",
        ))

    def _mcp_server_envelope(self, face: Any, server_id: str) -> ResultEnvelope:
        record = self._mcp_server_record(face, server_id)
        machine = face.machine
        sections: list[dict[str, Any]] = [
            {"title": "基本信息", "lines": [
                f"名称：{record.display_name}",
                f"标识：{record.server_id}",
                f"传输：{_MCP_TRANSPORT_LABELS.get(record.transport, record.transport)}",
                f"开关：{'启用' if record.enabled else '停用'}",
                f"声明权限：{len(record.permissions)} 条",
                f"挂载时刻：{record.created_at.isoformat()}",
            ]},
        ]
        if machine is not None:
            sections.append({"title": "生命周期状态", "lines": [
                f"当前：{_MCP_STATE_LABELS.get(machine.state_of(record.server_id), '—')}",
            ]})
            transitions = tuple(machine.transitions(record.server_id))[-_MCP_NOTICE_LIMIT:]
            sections.append({"title": "状态转移留痕", "lines": [
                f"{item.occurred_at.isoformat()} "
                f"{_MCP_STATE_LABELS.get(item.from_state, item.from_state)} → "
                f"{_MCP_STATE_LABELS.get(item.to_state, item.to_state)}"
                f"（{item.reason}）"
                for item in transitions
            ] or ["（无转移留痕）"]})
            notices = tuple(machine.notices(record.server_id))[-_MCP_NOTICE_LIMIT:]
            sections.append({"title": "提示留痕", "lines": [
                f"{item.occurred_at.isoformat()} [{item.kind}] {item.message}"
                for item in notices
            ] or ["（无提示）"]})
            # 降级候选**并列陈列**：不带排序位、不带推荐语（[03 §5.3] GWT-3）。
            candidates = tuple(machine.degrade_candidates(record.server_id))
            sections.append({"title": "降级候选（并列，不排序）", "lines": [
                f"{candidate.name}（{candidate.skill_id}）· "
                f"{'可用' if candidate.available else '不可用'}：{candidate.detail}"
                for candidate in candidates
            ] or ["（无候选）"]})
        if record.data_egress_risk:
            sections.append({"title": "数据外发风险", "lines": [record.data_egress_risk]})
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": sections},
            text_kinds={"sections": "data"},
            title=f"{record.display_name} · 详情",
        ))

    def _mcp_tools_envelope(self, face: Any, server_id: str) -> ResultEnvelope:
        record = self._mcp_server_record(face, server_id)
        if face.mapper is None:
            return ResultEnvelope.unavailable(
                "未接入 tool 映射面，tool 浏览器不可读（装配归生产入口）", last_updated_at=_now()
            )
        mappings = tuple(face.mapper.mappings(record.server_id))
        if not mappings:
            return ResultEnvelope.empty(
                f"「{record.display_name}」还没有 tool 映射——权限全部批准后同步 tool 列表"
                "才会产生（03 §5.2）"
            )
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("tool", "tool"),
                    Column("skill_id", "派生 Skill"),
                    Column("status", "映射状态"),
                    Column("version", "映射版本"),
                    Column("inputs", "参数面（派生的输入契约）"),
                    Column("change_note", "变化说明"),
                ),
                [_mcp_tool_row(face, mapping) for mapping in mappings],
            ),
            text_kinds=table_text_kinds(),
            title=f"{record.display_name} · tool 与映射",
        ))

    def _mcp_permissions_parts(self, face: Any, server_id: str) -> dict[str, Any]:
        """权限面的两段（**不做异常处理**——那归 `_mcp_parts` 的统一分流）。"""
        record = self._mcp_server_record(face, server_id)
        approvals = tuple(face.permissions.approvals(record.server_id))
        declaration = envelope_payload(ResultEnvelope.ok(self._gated_description(
            "permission_approval_card",
            slots=_mcp_approval_slots(record, approvals),
            text_kinds={"key": "data", "source": "data", "items": "data",
                        "labels": "generated"},
            title=f"{record.display_name} · 权限申请",
        )))
        if not approvals:
            # 没声明权限 ⇒ **没有可批准项**，不是「面读不到」（[01 §5] 六态不混用）。
            return {
                "declaration": declaration,
                "decisions": envelope_payload(ResultEnvelope.empty(
                    f"「{record.display_name}」未声明任何权限，无可批准项（01 §10 逐项批准）"
                )),
            }
        return {
            "declaration": declaration,
            "decisions": envelope_payload(ResultEnvelope.ok(self._gated_description(
                "setting_panel",
                slots={
                    "entries": _mcp_approval_entries(record, approvals),
                    "labels": dict(_MCP_APPROVAL_LABELS),
                    "surface": MCP_APPROVAL_SURFACE,
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title=f"{record.display_name} · 逐项批准",
            ))),
        }

    def _mcp_server_create_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        result = face.servers.add_server(
            str(_required(body, "server_id")),
            display_name=str(_required(body, "display_name")),
            transport=str(body.get("transport") or "stdio"),
            command=_optional_text(body.get("command")),
            args=tuple(str(item) for item in (body.get("args") or ())),
            env_refs=tuple(str(item) for item in (body.get("env_refs") or ())),
            url=_optional_text(body.get("url")),
            remote_acknowledged=bool(body.get("remote_acknowledged")),
            permissions=tuple(str(item) for item in (body.get("permissions") or ())),
        )
        if not result.ok:
            # 连接测试失败**不落盘**——如实回不可用 ＋ 原因（不是「工具坏了」）。
            return ResultEnvelope.unavailable(
                f"连接测试未通过，Server 未挂载：{result.reason}", last_updated_at=_now()
            )
        return ResultEnvelope.ok({
            "server_id": result.server_id,
            "connected": True,
            "protocol_version": result.protocol_version,
            "permissions": list(result.permissions),
            "tool_count": len(result.tools),
        })

    def _mcp_server_update_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        server_id = str(_required(body, "server_id"))
        action = str(_required(body, "action"))
        record = self._mcp_server_record(face, server_id)
        if action == "enable":
            if face.machine is None:
                return ResultEnvelope.unavailable(
                    "未接入 Hub 状态机，启停不可执行（装配归生产入口）", last_updated_at=_now()
                )
            face.machine.enable(record.server_id)
            return ResultEnvelope.ok({"server_id": record.server_id, "enabled": True})
        if action == "disable":
            if face.machine is None:
                return ResultEnvelope.unavailable(
                    "未接入 Hub 状态机，启停不可执行（装配归生产入口）", last_updated_at=_now()
                )
            face.machine.disable(record.server_id)
            return ResultEnvelope.ok({"server_id": record.server_id, "enabled": False})
        if action == "remove":
            if face.remove_server is None:
                return ResultEnvelope.unavailable(
                    "未接入移除序列，无法回收该 Server（装配归生产入口）", last_updated_at=_now()
                )
            # 走整条序列（disable → remove_server → 回收回调），不自行拼两步。
            face.remove_server(record.server_id)
            return ResultEnvelope.ok({"server_id": record.server_id, "removed": True})
        if action == "test":
            result = face.servers.test_connection(record.server_id)
            if not result.ok:
                return ResultEnvelope.unavailable(
                    f"连接测试未通过：{result.reason}", last_updated_at=_now()
                )
            return ResultEnvelope.ok({
                "server_id": result.server_id,
                "connected": True,
                "protocol_version": result.protocol_version,
                "tool_count": len(result.tools),
            })
        return ResultEnvelope.validation_failed(
            f"未知动作 {action!r}（可选：enable / disable / remove / test）"
        )

    def _mcp_permission_decide_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        server_id = str(_required(body, "server_id"))
        permission = str(_required(body, "permission"))
        decision = str(_required(body, "decision"))
        if decision not in ("approve", "reject"):
            return ResultEnvelope.validation_failed(
                f"未知处置 {decision!r}（可选：approve / reject）"
            )
        record = self._mcp_server_record(face, server_id)
        approval = (
            face.permissions.approve(record.server_id, permission)
            if decision == "approve"
            else face.permissions.reject(record.server_id, permission)
        )
        return ResultEnvelope.ok({
            "server_id": record.server_id,
            "permission": approval.permission,
            "decision": approval.decision,
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        })

    # ───────────────────── 能力区：Studio 附加面（[T-UI-015.3]） ─────────────────────
    # story-06 除主画布外的五条屏（[11-sitemap §2 能力]；主画布与导入导出已交付，见
    # [`T-UI-005`] / [`T-UI-004.4`]）。**零新造型**：参数面板复用 `skill-params` 面键与
    # `/api/skills/params`（[`.1`] 已落地），属性面板走 `report_card`、版本历史走
    # `timeline_view`、模板库走 `table` / `report_card`、调试控制台走 `report_card` +
    # `table` + `trace_timeline`——全是已实现型。
    #
    # 一处结构事实：**试跑是进程内的活会话**。`start` 把 `WorkflowTrial` 存进门面的会话表，
    # 后续 `step` / `pause` / `resume` / `abort` 都按 `trial_id` 取回**同一个对象**——
    # 试跑的步进态作用在节点边界上，无法只靠落盘还原（见 [`TrialSessions`]）。会话不在
    # 本进程 ⇒ `unavailable` ＋ 说明（重启后须重新起跑），**不冒充**「没有这次试跑」。

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

    # ───────────────────── 触达区（[T-UI-017]） ─────────────────────
    #
    # 三叶同批落在这一个区块：`.1` 日报与推送历史 · `.2` 预算 / 渠道 / 情境配置 ·
    # `.3` 五动作反馈与疲劳调整询问。四条纪律与其余各面一致——
    # ① 带标识 / 参数的面走**查询串**（[11-sitemap §2.2]：路径集仍是有限字面量集合）；
    # ② 写面是**显式动作**，进 `_POST_ROUTES`；③ L5 的失败词汇经 `_l5_envelope` 翻成六态
    # （**不**冒充 `failed`）；④ 动作不进描述——`setting_panel` 的路由由 `surface` 面键在
    # 渲染件的固定表里解析，描述只承载数据（[01 §12]）。

    def _require_handle(self, face: Any, name: str, label: str) -> Any:
        """取门面上的一个句柄；缺即 `unavailable` + 点名（返回信封，调用方判类型）。"""
        value = getattr(face, name, None)
        if value is None:
            return ResultEnvelope.unavailable(
                f"未接入{label}（{name}），该动作不可用（装配归组合根）",
                last_updated_at=_now(),
            )
        return value

    # ── `.1` 日报与推送历史 ────────────────────────────────────────────────

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

    # ── `.2` / `.3` 配置面板与落值 ─────────────────────────────────────────

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

    # ── `.3` 五动作反馈与疲劳调整询问 ──────────────────────────────────────

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
        """`index.html` —— dev 关闭时把两个 dev 占位都替换为空串。"""
        html = (self.web_root / "index.html").read_text(encoding="utf-8")
        tag = self.dev_package.dev_script_tag() if self.dev_enabled else ""
        token_tag = (
            f'<meta name="st-dev-token" content="{self.guard.token}">'
            if self.dev_enabled else ""
        )
        return html.replace(DEV_TOKEN_MARKER, token_tag).replace(DEV_SCRIPT_MARKER, tag)

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
    studio: Any = None,
    commands: Any = None,
    restart_gate: RestartGate | None = None,
    shell: bool = False,
    shell_control: Any = None,
) -> UiApp:
    """按已绑定的 ``host`` / ``port`` 装配表现层。

    ``dev=True`` 而 dev 子包不可用时抛 :class:`DevSurfaceUnavailable`——发布构建里
    「带 dev 跑」是配置错误，必须响，不能装作正常。

    ``chat`` / ``reflection`` / ``eco`` / ``memory``：M1–M4 的四个**鸭子端口**；``workspace`` /
    ``skills`` / ``mcp`` / ``graph`` / ``deliberation`` / ``delivery`` / ``settings``：M6 的
    七个（[`T-UI-011.1`]）；``studio``：L1 Studio 面（[`T-UI-015.3`]）；``commands``：快捷指令注册表（[`T-UI-012.1`]）。全部缺省
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
        studio=studio,
        commands=commands,
        restart_gate=restart_gate if restart_gate is not None else RestartGate(),
        shell=shell,
        shell_control=shell_control,
    )
