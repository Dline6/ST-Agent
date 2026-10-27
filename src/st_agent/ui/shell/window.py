"""窗口边：用**系统 WebView** 打开本机回环界面（pywebview；[D-076] ①）。

三引擎即 [00 §1.1] 立的兼容底线——Windows WebView2 / macOS WKWebView / Linux WebKitGTK——
故「壳内渲染」与浏览器直开走的是同一份零构建静态资产、同一个回环面。

pywebview 收在可选 extra ``[shell]``（CI / 发布**不装**），故**惰性导入**：缺依赖时显式抛
:class:`ShellSurfaceUnavailable`，不静默降级成「没有窗口的窗口应用」。

**关窗只隐藏**（[D-076] ③）：`closing` 事件返回 ``False`` 取消关闭，后端因此活得比窗口久。
**窗口规格与记忆**（[`T-UI-027.1`]）：默认 1440×900、最小 1024×640；尺寸 / 位置 / 最大化态
经 [prefs](prefs.py) 跨会话恢复，多显示器下夹进可见面。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from st_agent.ui.shell.errors import ShellSurfaceUnavailable
from st_agent.ui.shell.prefs import (
    MIN_HEIGHT,
    MIN_WIDTH,
    WindowGeometry,
    clamp_to_screens,
    screen_rects,
)

__all__ = ["PywebviewWindow", "load_pywebview"]

_TITLE = "ST Agent"


def load_pywebview() -> Any:
    """惰性取 pywebview；未安装即显式抛（附带安装命令）。"""
    try:
        import webview                     # noqa: PLC0415
    except ImportError as exc:
        raise ShellSurfaceUnavailable(
            "未安装桌面壳依赖 pywebview：pip install -e \".[shell]\""
        ) from exc
    return webview


class PywebviewWindow:
    """窗口端口实现（`create` / `show` / `hide` / `destroy` / `run` / `load_url`）。

    ``webview`` 可注入（用例用替身，无需真 GUI）；``screens`` 可注入（离屏夹取用例）。
    """

    def __init__(
        self,
        *,
        title: str = _TITLE,
        webview: Any = None,
        screens: Callable[[], Sequence[tuple[int, int, int, int]]] = screen_rects,
    ) -> None:
        self._webview = load_pywebview() if webview is None else webview
        self._title = title
        self._screens = screens
        self.window: Any = None
        self._secondaries: list[Any] = []
        self._geometry = WindowGeometry()
        self._on_geometry_changed: Callable[[WindowGeometry], None] | None = None
        self._maximized = False
        self._quitting = False
        """显式退出中（`destroy()` 置位）。关窗回调恒返回 False 取消关闭，
        故显式退出必须先解除这一层——否则 `quit()` 会把自己取消掉。"""

    def create(
        self,
        url: str,
        *,
        on_close_requested: Callable[[], None],
        geometry: WindowGeometry | None = None,
        on_geometry_changed: Callable[[WindowGeometry], None] | None = None,
    ) -> None:
        """开窗并接管「关闭」：回调后**取消关闭**（只隐藏），故窗口关掉后端仍在跑。"""
        placed = clamp_to_screens(geometry or WindowGeometry(), self._screens())
        self._geometry = placed
        self._maximized = placed.maximized
        self._on_geometry_changed = on_geometry_changed
        options: dict[str, Any] = {
            "width": placed.width,
            "height": placed.height,
            "min_size": (MIN_WIDTH, MIN_HEIGHT),
            "maximized": placed.maximized,
        }
        if placed.x is not None and placed.y is not None:
            options["x"] = placed.x
            options["y"] = placed.y
        self.window = self._webview.create_window(self._title, url, **options)

        def _closing() -> bool:
            self._emit_geometry()
            if self._quitting:
                return True
            on_close_requested()
            return False

        self.window.events.closing += _closing
        self.window.events.moved += self._moved
        self.window.events.resized += self._resized
        self.window.events.maximized += self._maximized_changed
        self.window.events.restored += self._restored

    def show(self) -> None:
        if self.window is not None:
            self.window.show()

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()

    def load_url(self, url: str) -> None:
        """把主窗口带到另一条路由（托盘「主动触达设置…」用；令牌 fragment 由调用方带全）。"""
        if self.window is not None:
            self.window.load_url(url)

    def set_title(self, title: str) -> None:
        """把**原生标题栏**同步成页面标题（[14 册 §1]「标题 = <分区> · <对象> — ST Agent」）。

        WebView2 不把 `document.title` 反射到宿主窗口，故由页面经回环面 + 既有 stdout
        控制管道把标题送到壳；壳仍不提供 JS 桥。
        """
        if self.window is not None:
            self.window.title = title

    def open_secondary(self, url: str) -> Any:
        """开一个附属窗口（[`T-UI-027.2`]）：与主窗口同一回环地址，**不新增后端进程**。"""
        window = self._webview.create_window(
            f"{self._title} · 附属",
            url,
            width=max(MIN_WIDTH, min(self._geometry.width, 1600)),
            height=max(MIN_HEIGHT, min(self._geometry.height, 1000)),
            min_size=(MIN_WIDTH, MIN_HEIGHT),
        )
        self._secondaries.append(window)
        return window

    def destroy(self) -> None:
        self._quitting = True
        for window in self._secondaries:
            window.destroy()
        self._secondaries.clear()
        if self.window is not None:
            self.window.destroy()

    def run(self) -> None:
        """阻塞在 GUI 主循环（必须主线程；后端是独立进程，不受此约束）。"""
        self._webview.start()

    # ── 窗口态 ──────────────────────────────────────────────────────────────
    def _moved(self, x: int, y: int) -> None:
        self._geometry = WindowGeometry(
            width=self._geometry.width,
            height=self._geometry.height,
            x=int(x),
            y=int(y),
            maximized=self._maximized,
        )
        self._notify()

    def _resized(self, width: int, height: int) -> None:
        # 最大化时的 resized 报的是最大化尺寸；保留「还原尺寸」供下次启动。
        if self._maximized:
            return
        self._geometry = WindowGeometry(
            width=int(width),
            height=int(height),
            x=self._geometry.x,
            y=self._geometry.y,
            maximized=False,
        )
        self._notify()

    def _maximized_changed(self) -> None:
        self._maximized = True
        self._geometry = WindowGeometry(
            width=self._geometry.width,
            height=self._geometry.height,
            x=self._geometry.x,
            y=self._geometry.y,
            maximized=True,
        )
        self._notify()

    def _restored(self) -> None:
        self._maximized = False
        self._geometry = WindowGeometry(
            width=self._geometry.width,
            height=self._geometry.height,
            x=self._geometry.x,
            y=self._geometry.y,
            maximized=False,
        )
        self._notify()

    def _notify(self) -> None:
        if self._on_geometry_changed is not None:
            self._on_geometry_changed(self._geometry)

    def _emit_geometry(self) -> None:
        """关窗时把最后的态交出去（含最大化标志）。"""
        self._notify()
