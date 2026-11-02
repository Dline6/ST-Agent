"""设置区各子面在 `SettingsSurface` 上的合流。"""

from __future__ import annotations

from st_agent.ui.surfaces.settings.agent import SettingsAgentSurface
from st_agent.ui.surfaces.settings.command import SettingsCommandSurface
from st_agent.ui.surfaces.settings.read import SettingsReadSurface
from st_agent.ui.surfaces.settings.web import SettingsWebSurface
from st_agent.ui.surfaces.settings.write import SettingsWriteSurface


class SettingsSurface(
    SettingsAgentSurface, SettingsCommandSurface, SettingsWebSurface, SettingsWriteSurface
):
    """设置区：读面（含运行期授权 / 工具开关 / 开放网络 / 本地命令页）与写面的合流（端口名 = 面名 = `settings`）。"""


__all__ = ["SettingsAgentSurface", "SettingsCommandSurface", "SettingsReadSurface",
           "SettingsSurface", "SettingsWebSurface", "SettingsWriteSurface"]
