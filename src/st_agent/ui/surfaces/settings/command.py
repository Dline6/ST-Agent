"""设置-本地命令页（T-AGT-016.2；[01 §7] / [02 §7]）。

单面拆出以守住 `ui/surfaces` 的体量上限（`tests/test_module_size.py`）：本页只承载
「本地命令执行开关」——默认关闭、显式开启、变更留痕可回滚。开关经统一落值面
（`/api/settings/apply`，面键 `settings-config`）生效；关闭时原语不可用且不执行
任何命令。
"""

from __future__ import annotations

from typing import Any

from st_agent.ui.surfaces.settings.read import SettingsReadSurface
from st_agent.ui.surfaces.settings.shared import _command_entries

__all__ = ["SettingsCommandSurface"]


class SettingsCommandSurface(SettingsReadSurface):
    """设置-本地命令页（读面；写面仍走 `/api/settings/apply`）。"""

    def api_settings_command(self) -> dict[str, Any]:
        """设置-本地命令页：命令执行开关（`setting_panel`，统一落值面）。"""
        return self._settings_parts(
            "settings-command", ("config_registry",), ("panel",),
            lambda face: {"panel": self._part(
                "setting_panel",
                slots={
                    "entries": _command_entries(face.config_registry),
                    "surface": "settings-config",
                    "labels": {"enable": "开启", "disable": "关闭"},
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title="本地命令执行",
            )},
        )
