"""设置区读面：存储 / LLM / 数据 / 隐私 / 网络 / 维护。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.data_catalog import (
    catalog_payload,
    domain_label,
    sync_catalog_payload,
    table_label,
    task_label,
)
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _LOG,
    _now,
    _optional_text,
)
from st_agent.ui.surfaces.settings.shared import (
    _KIND_LABELS,
    _STATE_LABELS,
    _STATUS_LABELS,
    _SYNC_JOB_LABELS,
    _SYNC_STATUS_LABELS,
    _bytes_text,
    _capability_lines,
    _capability_text,
    _credential_row,
    _job_scope_text,
    _llm_boot_values,
    _maintenance_slots,
    _mode_line,
    _privacy_entries,
    _provider_entries,
    _retention_option_labels,
    _root_location,
    _short_stamp,
    _st_scope_line,
    _stamp,
    _switch_entries,
    _task_name,
    _watch_scope_line,
    _window_start,
    _window_text,
    _wipe_tokens,
)


class SettingsReadSurface(SurfaceBase):
    """设置区读面：存储 / LLM / 数据 / 隐私 / 网络 / 维护。"""

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

    def api_settings_data_job(self) -> dict[str, Any]:
        """轻量批次快照（运行中轮询用；不读表行数、不做整库统计；[D-130]）。"""
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-job", face, "L0 数据同步面",
            lambda sync: ResultEnvelope.ok({"job": sync.job() or {}}),
        )

    def api_settings_data_preflight(self, body: dict[str, Any]) -> dict[str, Any]:
        """发起前预检：标的 / 数据集 / 指标组 / 时间 / 自动补入 / 请求估算。"""
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        items = body.get("items")
        custom = _optional_text(body.get("custom_codes_text")) or ""
        if isinstance(items, list):
            return self._call_face(
                "settings-data-preflight", face, "L0 数据同步面",
                lambda sync: sync.preflight_items(items, custom_text=custom),
            )
        raw = body.get("task_keys")
        keys = [str(item) for item in raw] if isinstance(raw, list) else []
        start = _optional_text(body.get("start"))
        end = _optional_text(body.get("end"))
        scope = _optional_text(body.get("scope")) or "market"
        return self._call_face(
            "settings-data-preflight", face, "L0 数据同步面",
            lambda sync: sync.preflight(keys, start=start, end=end, scope=scope),
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
        catalog = {
            **catalog_payload(overview["tasks"]),
            **sync_catalog_payload(overview),
        }
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
