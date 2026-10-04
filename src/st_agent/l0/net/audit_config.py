"""出网审计开关（02 §6；01 §7 配置注册表条目）。

审计由「强制逐次留痕」改为**可选、默认关**（[D-073](../../../项目管理/决策日志.md)）：
开关值落 ``config`` 分区的 ``net-audit/enabled.json``，是**唯一真相源**；
:class:`~st_agent.l0.net.gateway.EgressGateway` 的构造参数只是覆盖入口。

本模块交付 owner 侧的读写与条目形态；统一注册表的**消费面**（列举 / 落值
带 ``change_id`` 留痕）由 L1 门面经 :class:`~st_agent.l1.registry.families.NetAuditFamily`
接入——与 [02 §9](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的留存两旋钮同款，
「所有可配置项必须注册」不留孤儿（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from st_agent.contracts.registry_types import ChangePolicy, ConfigEntry, PanelField
from st_agent.l0.net.errors import NetAuditConfigError

__all__ = [
    "AUDIT_CONFIG_ID",
    "AUDIT_POLICY_PATH",
    "DEFAULT_AUDIT_ENABLED",
    "NetAuditPolicy",
    "get_audit_policy",
    "net_audit_config_entries",
    "set_audit_policy",
]

AUDIT_CONFIG_ID = "net-audit/enabled"
"""01 §7 条目标识（路径前缀式，与存量族同款；落盘路径见下）。"""

AUDIT_POLICY_PATH = "net-audit/enabled.json"
"""``config`` 分区内开关文件的相对路径（``config_id`` 主干即文件名）。"""

DEFAULT_AUDIT_ENABLED = False
"""缺省＝**关**（02 §6：逐次留痕为可选项、默认关）。"""


class NetAuditPolicy(BaseModel):
    """出网审计开关（当前只有一个旋钮，成模型以便后续扩展与损坏显式化）。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool = DEFAULT_AUDIT_ENABLED


def get_audit_policy(store) -> NetAuditPolicy:
    """读取开关（无配置即默认值＝关；损坏显式抛，不静默回退）。"""
    try:
        raw = store.get("config", AUDIT_POLICY_PATH)
    except KeyError:
        return NetAuditPolicy()
    try:
        return NetAuditPolicy.model_validate_json(raw.decode("utf-8"))
    except (ValueError, ValidationError, UnicodeDecodeError) as exc:
        raise NetAuditConfigError(f"出网审计开关文件损坏：{exc}") from exc


def set_audit_policy(store, *, enabled: bool) -> NetAuditPolicy:
    """写入开关（取值须为布尔；非法即拒）。"""
    if not isinstance(enabled, bool):
        raise NetAuditConfigError(f"审计开关须为布尔：{enabled!r}")
    policy = NetAuditPolicy(enabled=enabled)
    store.put("config", AUDIT_POLICY_PATH, policy.model_dump_json().encode("utf-8"))
    return policy


def net_audit_config_entries() -> tuple[ConfigEntry, ...]:
    """开关的 01 §7 ConfigEntry（供 L1 注册表 / L3 双通道显式告知默认值）。"""
    return (
        ConfigEntry(
            config_id=AUDIT_CONFIG_ID,
            display_name="出网审计",
            value_schema={"type": "boolean"},
            default=DEFAULT_AUDIT_ENABLED,
            description_for_chat=(
                "是否逐次记录所有对外网络请求（域名 / 目的 / 时间 / 数据量）。"
                "默认关闭——关闭时请求照常执行，但不留痕，网络活动面板也无记录可查；"
                "开启后每次请求落一条审计"
            ),
            panel_form_spec=PanelField(
                widget="toggle", label="出网审计",
                help_text="开启后逐次记录对外网络请求（不含请求内容）；默认关闭",
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=False),
        ),
    )
