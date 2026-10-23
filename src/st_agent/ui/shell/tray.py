"""托盘边：系统托盘图标与菜单（pystray；[D-076] ③）。

菜单即壳的用户动作面：**显示窗口 / 主动触达设置 / 开机自启 / 发送测试通知 /
清除已记住的凭据口令 / 退出**（[`T-UI-027.1`]；「新建窗口」由 [`T-UI-027.2`] 追加）。

图标是**随包资源**而非程序绘制（[`T-UI-027.1`] GWT-4）：``assets/tray-<theme>-<size>.png``
三档（16 / 32 / 256），浅色 / 深色主题各一套；主题取 Windows 的
``AppsUseLightTheme``，取不到或非 Windows 时用浅色版。资源缺失时**回落**到程序绘制的
中性几何形（仍不写任何文字），不静默无图标。

pystray 收在可选 extra ``[shell]``（CI / 发布不装），故**惰性导入**、缺依赖显式抛。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from st_agent.ui.shell.errors import ShellSurfaceUnavailable

__all__ = [
    "MenuItem",
    "PystrayTray",
    "TRAY_SIZES",
    "load_pystray",
    "load_tray_image",
    "neutral_icon_image",
    "prefers_light_theme",
    "tray_asset",
]

_TITLE = "ST Agent"
TRAY_SIZES = (16, 32, 256)
"""托盘图标的三档尺寸（[14 册 §11]）。"""

ASSETS = Path(__file__).resolve().parent / "assets"


def load_pystray() -> Any:
    """惰性取 pystray；未安装即显式抛（附带安装命令）。"""
    try:
        import pystray                     # noqa: PLC0415
    except ImportError as exc:
        raise ShellSurfaceUnavailable(
            "未安装桌面壳依赖 pystray：pip install -e \".[shell]\""
        ) from exc
    return pystray


def prefers_light_theme() -> bool:
    """系统是否使用浅色主题（Windows 注册表）；取不到 ⇒ 按浅色处理。"""
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg                       # noqa: PLC0415

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except OSError:
        return True


def tray_asset(theme: str, size: int) -> Path:
    """托盘资源路径（``theme`` 取 ``light`` / ``dark``）。"""
    return ASSETS / f"tray-{theme}-{size}.png"


def neutral_icon_image(size: int = 64) -> Any:
    """画一枚中性托盘图标（深底 + 浅色方框），**不写任何文字**（资源缺失时的回落）。"""
    from PIL import Image, ImageDraw    # noqa: PLC0415 —— Pillow 随 pystray 进来

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size // 8
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin), radius=size // 8, fill=(28, 32, 38, 255)
    )
    inner = size // 4
    draw.rectangle((inner, inner, size - inner, size - inner), fill=(226, 232, 240, 255))
    return image


def load_tray_image(*, light: bool | None = None) -> Any:
    """按主题取托盘图（256 档）；资源缺失或 Pillow 不可用即回落程序绘制。"""
    use_light = prefers_light_theme() if light is None else light
    path = tray_asset("light" if use_light else "dark", TRAY_SIZES[-1])
    try:
        from PIL import Image               # noqa: PLC0415

        with Image.open(path) as image:
            return image.convert("RGBA")
    except (OSError, ImportError):
        return neutral_icon_image()


@dataclass(frozen=True)
class MenuItem:
    """托盘菜单项：``label`` + ``action`` + 可选的勾选态读取器。"""

    label: str
    action: Callable[[], None]
    checked: Callable[[], bool] | None = None


class PystrayTray:
    """托盘端口实现：`install(items)` → `run_detached()` → `stop()`。"""

    def __init__(
        self, *, title: str = _TITLE, pystray: Any = None, image: Any = None,
        light: bool | None = None,
    ) -> None:
        self._pystray = load_pystray() if pystray is None else pystray
        self._title = title
        self._image = load_tray_image(light=light) if image is None else image
        self.icon: Any = None

    def install(self, items: Sequence[MenuItem]) -> None:
        """把菜单项交给 pystray。

        `action` / `checked` **原样传入**：pystray 按可调用对象的**位置参数个数**包装
        （0 参 ⇒ 包成 `(icon)`；1 参 ⇒ `(icon)`；2 参 ⇒ 原样），带默认值的闭包会把
        `co_argcount` 抬高到 3 而被拒——故菜单项里的可调用一律用零参形式。
        """
        menu = self._pystray.Menu(
            *[
                self._pystray.MenuItem(item.label, item.action, checked=item.checked)
                for item in items
            ]
        )
        self.icon = self._pystray.Icon(self._title, self._image, self._title, menu)

    def run_detached(self) -> None:
        """在后台跑托盘循环（Windows / macOS 支持 ``run_detached``；否则退化为线程）。"""
        if self.icon is None:
            return
        run_detached = getattr(self.icon, "run_detached", None)
        if callable(run_detached):
            run_detached()
            return
        import threading                    # noqa: PLC0415

        threading.Thread(target=self.icon.run, name="st-agent-tray", daemon=True).start()

    def stop(self) -> None:
        if self.icon is not None:
            self.icon.stop()
