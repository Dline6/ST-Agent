"""设置-运行期授权页（含 Agent 工具开关；[D-143] / [D-161] / T-UI-038）。

从 `read.py` 拆出以守住 `ui/surfaces` 的体量上限（`tests/test_module_size.py`）：本面承载
「档位 + 动作级分级清单 + 缺省来源 + 留痕 + 循环预算 + Agent 工具开关」。档位与清单共用
L6 的运行期授权面；预算段是 L3 `investigate.*` 三条目；工具段把 `SkillRegistry.tool_catalog()`
按功能域分组列出，逐条给出 `agent-tools/<base>` 的当前开关（[01 §2] / [01 §7]）。
"""

from __future__ import annotations

import uuid
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.skills.tool_selection import (
    ToolSelectionError,
    get_disabled_tools,
    tool_selection_config_id,
)
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.surfaces.base import _LOG, _now
from st_agent.ui.surfaces.settings.read import SettingsReadSurface
from st_agent.ui.surfaces.settings.shared import budget_part
from st_agent.ui.tables import Column, table_slots, table_text_kinds

__all__ = ["SettingsAgentSurface"]

_TOOL_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("control", "循环控制", ("sk_plan_update", "sk_ask_user", "sk_spawn_agent")),
    ("data", "数据与文档", (
        "sk_local_sql", "sk_data_status", "sk_data_sync", "sk_data_fetch", "sk_docs",
    )),
    ("files", "文件", ("sk_file_read", "sk_file_glob", "sk_file_grep", "sk_file_write")),
    ("web", "联网", ("sk_web_search", "sk_web_fetch")),
    ("exec", "本地命令", ("sk_run_command",)),
    ("subprotocol", "交互卡与子协议", (
        "sk_configure", "sk_memory_op", "sk_analyze", "sk_train", "sk_explain",
    )),
    ("results", "结果与 MCP 资源", (
        "sk_result_read", "sk_mcp_list_resources", "sk_mcp_read_resource",
    )),
)
"""工具的功能域分组（**展示口径**，不进入能力登记；未收录的落「其他能力」）。"""

_OTHER_GROUP = ("other", "其他能力")


class SettingsAgentSurface(SettingsReadSurface):
    """设置-运行期授权页（读面；写面仍走 `/api/settings/apply` 与档位路由）。"""

    def api_settings_agent_authorization(self) -> dict[str, Any]:
        """运行期授权页：档位 + 分级清单 + 缺省来源 + 留痕 + 循环预算 + Agent 工具开关。"""
        budget = budget_part(self)
        if self.restart_gate.reason:
            payload = envelope_payload(ResultEnvelope.unavailable(
                self.restart_gate.reason, last_updated_at=_now()
            ))
            return {"panel": payload, "trail": payload, "budget": budget, "tools": payload}
        tools = self._agent_tools_part()
        face = self.reflection
        if face is None:
            payload = envelope_payload(ResultEnvelope.unavailable(
                "未接入L6 反思演进面，该端点不可用（装配归生产入口）",
                last_updated_at=_now(),
            ))
            return {"panel": payload, "trail": payload, "budget": budget, "tools": tools}
        try:
            parts = self._agent_authorization_parts(face)
            parts["budget"] = budget
            parts["tools"] = tools
            return parts
        except (ValueError, KeyError) as exc:
            _LOG.info(
                "表现层端点 settings-agent-authorization 拒绝请求（%s）：%s",
                type(exc).__name__, exc,
            )
            return self._agent_authorization_error(
                ResultEnvelope.validation_failed(str(exc) or "请求不合契约"),
                budget=budget, tools=tools,
            )
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/settings-agent-authorization-{uuid.uuid4().hex[:12]}"
            _LOG.exception(
                "表现层端点 settings-agent-authorization 未预期失败（log_ref=%s）：%s",
                log_ref, exc,
            )
            return self._agent_authorization_error(
                ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref),
                budget=budget, tools=tools,
            )

    @staticmethod
    def _agent_authorization_error(
        envelope: ResultEnvelope, *, budget: dict[str, Any] | None = None,
        tools: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """多段端点的错误态：**每一段都是同一支错误信封**（同 `_settings_parts`）。

        ``budget`` / ``tools`` 是本页**独立于反思面**的两段：给出时原样保留，不被同一条
        错误吞掉（与既有口径一致）。
        """
        payload = envelope_payload(envelope)
        return {
            "panel": payload, "trail": payload,
            "budget": budget if budget is not None else payload,
            "tools": tools if tools is not None else payload,
        }

    def _agent_authorization_parts(self, face: Any) -> dict[str, Any]:
        info = face.agent_authorization()
        if not info.get("available"):
            return self._agent_authorization_error(ResultEnvelope.unavailable(
                str(info.get("reason") or "运行期授权面未接线"), last_updated_at=_now()
            ))
        source = (
            "用户设置" if info.get("tier_source") == "user"
            else "出厂缺省（尚未由用户修改）"
        )
        entries = [
            {
                "config_id": info.get("tier_config_id"),
                "params": {"config_id": info.get("tier_config_id")},
                "identifier": "运行期授权档位",
                "current": info.get("tier"),
                "options": [{"value": value} for value in info.get("tiers") or ()],
                "actions": ["set"],
                "description": (
                    f"自主查证循环调用工具前的授权档位；当前来源：{source}"
                ),
                "note": f"当前来源：{source}；出厂缺省 {info.get('default_tier')}",
            },
            {
                "config_id": info.get("grading_config_id"),
                "identifier": "动作级风险分级清单",
                "current": f"{len(info.get('grading') or ())} 条规则",
                "detail": info.get("grading") or [],
                "options": [],
                "actions": [],
                "note": info.get("grading_note") or "",
            },
        ]
        panel = self._part(
            "setting_panel",
            slots={
                "entries": entries,
                "surface": "settings-config",
                "labels": {
                    "set": "应用",
                    "manual": "手动",
                    "collaborative": "协作",
                    "autonomous": "自主",
                },
            },
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title="运行期授权",
        )
        changes = list(info.get("changes") or [])
        rows = [
            {
                "change_id": item.get("change_id", ""),
                "applied_at": item.get("applied_at", ""),
                "old_value": item.get("old_value", "—"),
                "new_value": item.get("new_value", "—"),
                "trace_ref": item.get("trace_ref") or "—",
            }
            for item in changes
        ]
        trail = self._part(
            "table",
            slots={
                **table_slots(
                    (
                        Column("applied_at", "时刻"),
                        Column("old_value", "原值"),
                        Column("new_value", "新值"),
                        Column("trace_ref", "trace 依据"),
                        Column("change_id", "变更 id"),
                    ),
                    rows,
                ),
                "values": {"changes": changes},
            },
            text_kinds={**table_text_kinds(), "values": "data"},
            title="运行期档位变更（agent-change/）",
        )
        return {"panel": panel, "trail": trail}

    def _agent_tools_part(self) -> dict[str, Any]:
        """Agent 工具开关：按功能域分组，逐条给出当前启用态（T-UI-038）。

        读面只呈现、不落值；开关经 `/api/settings/apply` 写 `agent-tools/<base>`。
        工具目录取 `SkillRegistry.tool_catalog()`（[01 §2] 的唯一来源）；关闭集合读取失败
        → 该段显式 `unavailable`，不静默当作全部启用。
        """
        face = self.settings
        skills = None if face is None else getattr(face, "skills", None)
        store = None if face is None else getattr(face, "store", None)
        if skills is None or store is None:
            return envelope_payload(ResultEnvelope.unavailable(
                "未接入 Skill 注册表或存储面，工具列表不可用（装配归组合根）",
                last_updated_at=_now(),
            ))
        try:
            catalog = skills.tool_catalog()
            disabled = get_disabled_tools(store)
        except ToolSelectionError as exc:
            return envelope_payload(ResultEnvelope.unavailable(
                f"工具开关配置损坏：{exc}", last_updated_at=_now()
            ))
        groups = _tool_groups(catalog, disabled)
        rows = [
            {
                "tool": tool["name"],
                "group": group["label"],
                "enabled": "启用" if tool["enabled"] else "关闭",
                "description": tool["description"],
                "config_id": tool["config_id"],
            }
            for group in groups
            for tool in group["tools"]
        ]
        total = sum(len(group["tools"]) for group in groups)
        enabled = sum(1 for tool in rows if tool["enabled"] == "启用")
        return self._part(
            "table",
            slots={
                **table_slots(
                    (
                        Column("tool", "工具"),
                        Column("group", "分组"),
                        Column("enabled", "状态"),
                        Column("description", "说明"),
                        Column("config_id", "配置条目"),
                    ),
                    rows,
                ),
                "values": {
                    "groups": groups,
                    "total": total,
                    "enabled": enabled,
                    "disabled": total - enabled,
                },
            },
            text_kinds={**table_text_kinds(), "values": "data"},
            title="Agent 工具",
        )


def _tool_groups(catalog: Any, disabled: frozenset[str]) -> list[dict[str, Any]]:
    """工具目录 → 按功能域分组的展示形态（未收录的落「其他能力」）。"""
    by_base = {entry.name: entry for entry in catalog}
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, label, bases in _TOOL_GROUPS:
        names = [base for base in bases if base in by_base]
        seen.update(names)
        if not names:
            continue
        groups.append({
            "key": key,
            "label": label,
            "tools": [_tool_view(by_base[name], disabled) for name in names],
        })
    others = sorted(set(by_base) - seen)
    if others:
        groups.append({
            "key": _OTHER_GROUP[0],
            "label": _OTHER_GROUP[1],
            "tools": [_tool_view(by_base[name], disabled) for name in others],
        })
    return groups


def _tool_view(entry: Any, disabled: frozenset[str]) -> dict[str, Any]:
    return {
        "name": entry.name,
        "skill_id": entry.skill_id,
        "description": entry.description,
        "enabled": entry.name not in disabled,
        "config_id": tool_selection_config_id(entry.name),
    }
