"""窗口边：用**系统 WebView** 打开本机回环界面（pywebview；[D-076] ①）。

三引擎即 [00 §1.1] 立的兼容底线——Windows WebView2 / macOS WKWebView / Linux WebKitGTK——
故「壳内渲染」与浏览器直开走的是同一份零构建静态资产、同一个回环面。

pywebview 收在可选 extra ``[shell]``（CI / 发布**不装**），故**惰性导入**：缺依赖时显式抛
:class:`ShellSurfaceUnavailable`，不静默降级成「没有窗口的窗口应用」。

**关窗只隐藏**（[D-076] ③）：`closing` 事件返回 ``False`` 取消关闭，后端因此活得比窗口久。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from st_agent.ui.shell.errors import ShellSurfaceUnavailable

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
    """窗口端口实现（`create` / `show` / `hide` / `destroy` / `run`）。

    ``webview`` 可注入（用例用替身，无需真 GUI）。
    """

    def __init__(self, *, title: str = _TITLE, webview: Any = None) -> None:
        self._webview = load_pywebview() if webview is None else webview
        self._title = title
        self.window: Any = None

    def create(self, url: str, *, on_close_requested: Callable[[], None]) -> None:
        """开窗并接管「关闭」：回调后**取消关闭**（只隐藏），故窗口关掉后端仍在跑。"""
        self.window = self._webview.create_window(self._title, url)

        def _closing() -> bool:
            on_close_requested()
            return False

        self.window.events.closing += _closing

    def show(self) -> None:
        if self.window is not None:
            self.window.show()

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()

    def destroy(self) -> None:
        if self.window is not None:
            self.window.destroy()

    def run(self) -> None:
        """阻塞在 GUI 主循环（必须主线程；后端是独立进程，不受此约束）。"""
        self._webview.start()
