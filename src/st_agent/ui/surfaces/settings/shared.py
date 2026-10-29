"""设置区的纯函数与标签表（无状态，可独立测试）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.backup.errors import (
    BackupAuthError,
    BackupCorruptedError,
    BackupVersionError,
    RetentionError,
    WipeConfirmationError,
)
from st_agent.l0.bootstrap.dotenv import read_dotenv, write_dotenv
from st_agent.l0.llm.boot_config import load_boot_config, save_boot_config
from st_agent.l0.secrets.masking import mask_secret
from st_agent.l0.storage.errors import (
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
)
from st_agent.l1.runtime import LLM_CREDENTIAL_ID, LlmBootstrap
from st_agent.ui.data_catalog import (
    catalog_payload,
    domain_label,
    sync_catalog_payload,
    table_label,
    task_label,
)

from st_agent.ui.surfaces.base import (
    _LOG,
    _now,
)


_STATE_LABELS = {
    "ok": "正常", "corrupted": "损坏", "missing": "缺失", "locked": "未解锁",
}
"""分区状态 → 中性中文（`StorageState.state` 的取值面）。"""


_KIND_LABELS = {
    "cloud": "云端", "local": "本地推理",
    "llm": "LLM 调用", "market_fetch": "行情抓取", "info_fetch": "信息面抓取",
    "channel_delivery": "渠道投递", "mcp_remote": "远程 MCP", "index_browse": "官方索引浏览",
    "web_search": "联网搜索", "web_fetch": "网页抓取",
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


def _web_entries(registry: Any) -> list[dict[str, Any]]:
    """开放网络页的三条可改条目（开关 / 域名名单 / 搜索提供方）。

    搜索密钥**不进本表**：它由凭据 chrome 增改吊销，页面只显示掩码
    （``search.key`` 条目的写面同样落凭据库；此处不摆明文输入框）。
    """
    out: list[dict[str, Any]] = []
    enabled = registry.entry("web-egress/enabled")
    if enabled is not None:
        out.append({
            "config_id": enabled.config_id,
            "params": {"config_id": enabled.config_id},
            "identifier": enabled.display_name,
            "current": "已开启" if bool(enabled.default) else "已关闭",
            "options": [],
            "actions": ["enable", "disable"],
            "description": enabled.panel_form_spec.help_text,
        })
    for config_id in ("web-egress/allowed-hosts", "search.provider"):
        entry = registry.entry(config_id)
        if entry is None:
            continue
        if config_id == "search.provider":
            choices = entry.panel_form_spec.choices or ()
            current = str(entry.default or "")
            out.append({
                "config_id": entry.config_id,
                "params": {"config_id": entry.config_id},
                "identifier": entry.display_name,
                "current": current,
                "options": [{"value": value} for value in choices],
                "actions": ["set"],
                "description": entry.panel_form_spec.help_text,
                "note": "密钥在下方凭据动作区按标识 search-api-key 配置",
            })
        else:
            out.append({
                "config_id": entry.config_id,
                "params": {"config_id": entry.config_id},
                "identifier": entry.display_name,
                "current": str(entry.default or ""),
                "options": [],
                "input": "text",
                "actions": ["set"],
                "description": entry.panel_form_spec.help_text,
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
