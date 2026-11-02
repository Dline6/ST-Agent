"""Agent 工具开关的配置读写（T-UI-038；[01 §7] / [01 §2]）。

逐工具登记布尔条目 ``agent-tools/<base>``：缺省 ``true``＝启用；关闭即从自主查证循环的
模型可见工具目录中移除，并由 L1 **统一执行入口**（``SkillRunner``）拒绝执行——故 Skill 库
手动执行、工作流与试跑同口径。本模块只做**读写与键校验**；变更留痕由 L1 注册表族
（``AgentToolFamily``）补落，与 ``data-sync/auto.*`` 同款。

落盘：``config`` 分区 ``agent-tools/disabled.json``，内容为 ``{base: true}``（只保留被关闭
的工具；重新启用即移除该键）。文件读不出 / 形态坏 → :class:`ToolSelectionError`
（消费方按 **fail-closed** 处理，不静默当作全部启用）。
"""

from __future__ import annotations

import json
from typing import Any

from st_agent.contracts.registry_types import ChangePolicy, ConfigEntry, PanelField
from st_agent.l1.skills.errors import SkillValidationError
from st_agent.l1.skills.ids import SKILL_BASE_PATTERN, check_skill_base

__all__ = [
    "TOOL_SELECTION_PATH",
    "TOOL_SELECTION_PREFIX",
    "ToolSelectionError",
    "get_disabled_tools",
    "is_tool_enabled",
    "parse_tool_selection_config_id",
    "set_tool_enabled",
    "tool_selection_config_id",
    "tool_selection_entry",
]

TOOL_SELECTION_PREFIX = "agent-tools/"
"""本族 ``config_id`` 前缀（[01 §7]）。"""

TOOL_SELECTION_PATH = "agent-tools/disabled.json"
"""关闭集合的落盘路径（``config`` 分区）。"""


class ToolSelectionError(ValueError):
    """工具开关配置的键 / 值不合法，或落盘形态损坏。"""


def tool_selection_config_id(base: str) -> str:
    """工具 base → ``config_id``。"""
    return f"{TOOL_SELECTION_PREFIX}{base}"


def parse_tool_selection_config_id(config_id: str) -> str | None:
    """``config_id`` → 工具 base；不属本族或 base 形态非法 → ``None``。"""
    text = str(config_id or "")
    if not text.startswith(TOOL_SELECTION_PREFIX):
        return None
    base = text[len(TOOL_SELECTION_PREFIX):]
    if not SKILL_BASE_PATTERN.match(base):
        return None
    try:
        return check_skill_base(base)
    except SkillValidationError:
        return None


def _read(store: Any) -> set[str]:
    try:
        raw = store.get("config", TOOL_SELECTION_PATH)
    except KeyError:
        return set()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ToolSelectionError(
            f"工具开关配置损坏（{TOOL_SELECTION_PATH}）：{exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ToolSelectionError(f"工具开关配置须为对象（{TOOL_SELECTION_PATH}）")
    disabled: set[str] = set()
    for base, value in payload.items():
        if parse_tool_selection_config_id(f"{TOOL_SELECTION_PREFIX}{base}") is None:
            raise ToolSelectionError(f"工具开关配置含非法工具名：{base!r}")
        if not isinstance(value, bool):
            raise ToolSelectionError(f"工具开关配置 {base} 须为布尔：{value!r}")
        if value:
            disabled.add(str(base))
    return disabled


def _write(store: Any, disabled: set[str]) -> None:
    body = json.dumps(
        {base: True for base in sorted(disabled)}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    store.put("config", TOOL_SELECTION_PATH, body)


def get_disabled_tools(store: Any) -> frozenset[str]:
    """全部被关闭的工具 base（缺省空集；配置损坏即抛 :class:`ToolSelectionError`）。"""
    return frozenset(_read(store))


def is_tool_enabled(store: Any, base: str) -> bool:
    """某工具是否允许使用（未落值即启用）。"""
    return base not in _read(store)


def set_tool_enabled(store: Any, base: str, enabled: bool) -> bool:
    """落一次开关（只保留被关闭项；非法工具名 / 非布尔即抛）。"""
    if parse_tool_selection_config_id(tool_selection_config_id(base)) is None:
        raise ToolSelectionError(f"非法工具名：{base!r}")
    if not isinstance(enabled, bool):
        raise ToolSelectionError(f"{base} 的取值须为布尔：{enabled!r}")
    disabled = _read(store)
    if enabled:
        disabled.discard(base)
    else:
        disabled.add(base)
    _write(store, disabled)
    return enabled


def tool_selection_entry(base: str, *, current: bool) -> ConfigEntry:
    """一条工具开关的规范形态。

    ``default`` 携带**当前值**（同 ``DataSourceSwitchFamily`` 的族口径：注册表条目没有
    单独的 current 字段，读面取 ``default``）；未落值时的出厂语义是启用，由
    :func:`get_disabled_tools` 的「缺席即启用」保证。
    """
    return ConfigEntry(
        config_id=tool_selection_config_id(base),
        display_name=f"Agent 工具 · {base}",
        value_schema={"type": "boolean"},
        default=current,
        description_for_chat=(
            "允许自主查证循环与手动执行使用该工具；关闭即从模型可见工具目录移除，"
            "并在 L1 执行入口拒绝执行"
        ),
        panel_form_spec=PanelField(
            widget="toggle",
            label="允许 Agent 使用",
            help_text="关闭后该工具从 Agent 可见面移除，手动执行与工作流同样被拒绝",
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=False),
    )
