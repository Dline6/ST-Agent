"""设置区各子面在 `SettingsSurface` 上的合流。"""

from __future__ import annotations

from st_agent.ui.surfaces.settings.read import SettingsReadSurface
from st_agent.ui.surfaces.settings.web import SettingsWebSurface
from st_agent.ui.surfaces.settings.write import SettingsWriteSurface


class SettingsSurface(SettingsWebSurface, SettingsWriteSurface):
    """设置区：读面（含开放网络页）与写面的合流（端口名 = 面名 = `settings`）。"""


__all__ = ["SettingsReadSurface", "SettingsSurface", "SettingsWebSurface",
           "SettingsWriteSurface"]
