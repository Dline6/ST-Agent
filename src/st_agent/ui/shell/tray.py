"""托盘边：系统托盘图标与菜单（pystray；[D-076] ③）。

菜单四项即壳的全部用户动作面：**显示窗口 / 开机自启（勾选）/ 发送测试通知 / 退出**。
图标是**程序内画的中性几何形**（不引外部资产、不写字，避免字体与文案问题）。

pystray 收在可选 extra ``[shell]``（CI / 发布不装），故**惰性导入**、缺依赖显式抛。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from st_agent.ui.shell.errors import ShellSurfaceUnavailable

__all__ = ["MenuItem", "PystrayTray", "load_pystray", "neutral_icon_image"]

_TITLE = "ST Agent"


def load_pystray() -> Any:
    """惰性取 pystray；未安装即显式抛（附带安装命令）。"""
    try:
        import pystray                     # noqa: PLC0415
    except ImportError as exc:
        raise ShellSurfaceUnavailable(
            "未安装桌面壳依赖 pystray：pip install -e \".[shell]\""
        ) from exc
    return pystray


def neutral_icon_image(size: int = 64) -> Any:
    """画一枚中性托盘图标（深底 + 浅色方框），**不写任何文字**。"""
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


@dataclass(frozen=True)
class MenuItem:
    """托盘菜单项：``label`` + ``action`` + 可选的勾选态读取器。"""

    label: str
    action: Callable[[], None]
    checked: Callable[[], bool] | None = None


class PystrayTray:
    """托盘端口实现：`install(items)` → `run_detached()` → `stop()`。"""

    def __init__(self, *, title: str = _TITLE, pystray: Any = None, image: Any = None) -> None:
        self._pystray = load_pystray() if pystray is None else pystray
        self._title = title
        self._image = neutral_icon_image() if image is None else image
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
