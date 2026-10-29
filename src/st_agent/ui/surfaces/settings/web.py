"""设置-开放网络页（T-AGT-015.3；[01 §7] / [02 §6]）。

单面拆出以守住 `ui/surfaces` 的体量上限（`tests/test_module_size.py`）：本页只承载
「开关 / 域名名单 / 搜索提供方 / 搜索凭据掩码」，其余设置页仍留在 `read.py`。

开关与名单经统一落值面（`/api/settings/apply`，面键 `settings-config`）生效；
搜索密钥只显示掩码，明文永不出站。开关关闭时页面仍可读，只是两条原语不可用——
两种态在条目描述里显式区分。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.net.web_config import SEARCH_CREDENTIAL_ID
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.surfaces.base import _now
from st_agent.ui.surfaces.settings.read import SettingsReadSurface
from st_agent.ui.surfaces.settings.shared import _credential_row, _web_entries
from st_agent.ui.tables import Column, table_slots, table_text_kinds

__all__ = ["SettingsWebSurface"]


class SettingsWebSurface(SettingsReadSurface):
    """设置-开放网络页（读面；写面仍走 `/api/settings/apply` 与凭据路由）。"""

    def api_settings_web(self) -> dict[str, Any]:
        """设置-开放网络页：出网开关 / 域名名单 / 搜索提供方 / 搜索凭据掩码。

        两段并列：`panel`（`setting_panel`，开关与名单经统一落值面）、
        `credential`（搜索密钥的掩码视图；明文永不出站）。
        """
        return self._settings_parts(
            "settings-web", ("config_registry", "vault", "store"),
            ("panel", "credential"),
            lambda face: {
                "panel": self._part(
                    "setting_panel",
                    slots={
                        "entries": _web_entries(face.config_registry),
                        "surface": "settings-config",
                        "labels": {
                            "set": "应用", "enable": "开启", "disable": "关闭",
                            "": "未配置", "brave": "Brave Search", "tavily": "Tavily",
                        },
                    },
                    text_kinds={"entries": "data", "surface": "data",
                                "labels": "generated"},
                    title="开放网络",
                ),
                "credential": self._web_credential_part(face),
            },
        )

    def _web_credential_part(self, face: Any) -> dict[str, Any]:
        """搜索密钥的掩码视图（不存在 → 空态；分区未解锁 → 显式不可用）。"""
        if not bool(getattr(face.store, "secrets_unlocked", False)):
            return envelope_payload(ResultEnvelope.unavailable(
                "凭据分区未解锁（凭据恒加密、惰性解锁）：本次会话读不到搜索密钥，"
                "亦不可增改——经启动参数提供凭据口令后可用",
                last_updated_at=_now(),
            ))
        views = [
            view for view in face.vault.list_credentials()
            if view.credential_id == SEARCH_CREDENTIAL_ID
        ]
        if not views:
            return envelope_payload(ResultEnvelope.empty(
                "尚未配置搜索密钥——用下方凭据动作新增（标识 search-api-key、"
                "类别 search_api_key）；页面只显示掩码"
            ))
        return self._part(
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
                [_credential_row(view, face.vault) for view in views],
            ),
            text_kinds=table_text_kinds(),
            title="搜索凭据（掩码）",
        )
