"""本地命令执行的配置面（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)；T-AGT-016.2）。

一条全局条目：

- ``local-exec/enabled``——本地命令执行开关，布尔，**缺省 ``false``**（D-137 D-3
  「命令执行默认禁、显式开启」）。关闭时 ``sk_run_command`` 在**触碰执行器之前**
  返回 ``unavailable`` 且**不执行任何命令**；开启走统一落值面并产生 ``ChangeRecord``
  （留痕、可回滚）。

本模块只做读写与值域校验；配置变更的 ``ChangeRecord`` 留痕由 L1 注册表族
（``LocalExecFamily``）补落，与 ``web-egress`` / ``net-audit`` 同款。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from st_agent.contracts.registry_types import ChangePolicy, ConfigEntry, PanelField
from st_agent.l0.exec.errors import CommandConfigError

__all__ = [
    "DEFAULT_LOCAL_EXEC_ENABLED",
    "LOCAL_EXEC_DISCLOSURE",
    "LOCAL_EXEC_ENABLED_CONFIG_ID",
    "LOCAL_EXEC_ENABLED_PATH",
    "LocalExecSettings",
    "exec_config_entries",
    "get_local_exec_enabled",
    "load_local_exec_settings",
    "set_local_exec_enabled",
]

LOCAL_EXEC_ENABLED_CONFIG_ID = "local-exec/enabled"
LOCAL_EXEC_ENABLED_PATH = "local-exec/enabled.json"

DEFAULT_LOCAL_EXEC_ENABLED = False

LOCAL_EXEC_DISCLOSURE = (
    "本地命令执行默认关闭。开启后，Agent 可在本机运行一次性命令（超时与输出上限受限），"
    "每次执行都会在本地留痕；命令以当前用户身份运行，能访问该用户可访问的文件与目录。"
    "请在「设置 → 本地命令」阅读说明并显式开启。"
)
"""关闭态的原语说明（含出境告知同款的风险说明；中性措辞）。"""


@dataclass(frozen=True)
class LocalExecSettings:
    """一次读取的本地命令运行态。"""

    enabled: bool


def _read_enabled(store) -> bool | None:
    try:
        raw = store.get("config", LOCAL_EXEC_ENABLED_PATH)
    except KeyError:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CommandConfigError(f"本地命令配置 {LOCAL_EXEC_ENABLED_PATH} 损坏：{exc}") from exc
    if not isinstance(payload, dict) or "enabled" not in payload:
        raise CommandConfigError(
            f"本地命令配置 {LOCAL_EXEC_ENABLED_PATH} 缺少字段 'enabled'"
        )
    value = payload["enabled"]
    if not isinstance(value, bool):
        raise CommandConfigError(f"local-exec/enabled 须为布尔：{value!r}")
    return value


def get_local_exec_enabled(store) -> bool:
    """读本地命令开关（缺省关；非法取值显式抛）。"""
    value = _read_enabled(store)
    return DEFAULT_LOCAL_EXEC_ENABLED if value is None else value


def set_local_exec_enabled(store, enabled: bool) -> bool:
    """写本地命令开关（取值须为布尔）。"""
    if not isinstance(enabled, bool):
        raise CommandConfigError(f"local-exec/enabled 须为布尔：{enabled!r}")
    body = json.dumps({"enabled": enabled}, ensure_ascii=False).encode("utf-8")
    store.put("config", LOCAL_EXEC_ENABLED_PATH, body)
    return enabled


def load_local_exec_settings(store) -> LocalExecSettings:
    """一次取齐本地命令运行态。"""
    return LocalExecSettings(enabled=get_local_exec_enabled(store))


def exec_config_entries() -> tuple[ConfigEntry, ...]:
    """``local-exec/enabled`` 条目的规范形态（当前值由 L1 注册表族叠加）。"""
    return (
        ConfigEntry(
            config_id=LOCAL_EXEC_ENABLED_CONFIG_ID,
            display_name="本地命令执行",
            value_schema={"type": "boolean"},
            default=DEFAULT_LOCAL_EXEC_ENABLED,
            description_for_chat=(
                "是否允许 Agent 在本机运行一次性命令。默认关闭——开启后命令以当前用户"
                "身份运行、可访问该用户可访问的文件与目录；每次执行都会在本地留痕"
            ),
            panel_form_spec=PanelField(
                widget="toggle", label="本地命令执行",
                help_text=(
                    "开启后 Agent 可在本机执行一次性命令（超时与输出上限受限）。"
                    "命令以当前用户身份运行、能访问该用户可访问的路径；默认关闭，"
                    "关闭时不执行任何命令"
                ),
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        ),
    )
