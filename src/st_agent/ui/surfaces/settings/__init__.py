"""设置区各子面在 `SettingsSurface` 上的合流。"""

from __future__ import annotations

from st_agent.ui.surfaces.settings.read import SettingsReadSurface
from st_agent.ui.surfaces.settings.write import SettingsWriteSurface

class SettingsSurface(SettingsReadSurface, SettingsWriteSurface):
    """设置区：读面与写面的合流（端口名 = 面名 = `settings`）。"""
