"""壳的装配与生命周期（[`T-UI-002.2`]；[D-076] ②③④）。

壳的**全部语义**都在本模块——GUI 边（:mod:`~st_agent.ui.shell.window` /
:mod:`~st_agent.ui.shell.tray`）只是薄封装，故这里的每条动作都可用替身离线钉住：

- **关窗＝隐藏**（`hide_window`）：后端继续跑，托盘可再唤起（[00 §1.1]「后端比任何窗口活得久」）；
- **退出＝回收**（`quit` → `run` 的 `finally`）：销毁窗口 → 停托盘 → **优雅回收后端**；
- **开机自启**：开关即读改平台登录项本身（不另存第二份状态，[D-076] 派生口径）；
- **测试通知**：本地系统通知（最小接线；业务通知链归 [`T-L5-002`](../../tasks/T-L5-002-渠道适配器投递编排与升级链.md)）。

壳不 import 任何业务层（`tests/ui/test_shell_native_only.py` 钉住），窗口只经回环面取数。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from st_agent.ui.shell import autostart
from st_agent.ui.shell.backend import BackendHandle, BackendSpec, start_backend
from st_agent.ui.shell.notify import send_notification
from st_agent.ui.shell.tray import MenuItem

__all__ = ["Shell", "run_shell"]

_TEST_NOTIFICATION = (
    "ST Agent",
    "测试通知：原生通知可达，且不发出任何网络请求。业务通知见触达渠道配置。",
)


class WindowPort(Protocol):
    """窗口端口（替身：断言「关窗只隐藏 / 退出才销毁」）。"""

    def create(self, url: str, *, on_close_requested: Callable[[], None]) -> None: ...

    def show(self) -> None: ...

    def hide(self) -> None: ...

    def destroy(self) -> None: ...

    def run(self) -> None: ...


class TrayPort(Protocol):
    """托盘端口。"""

    def install(self, items: Sequence[MenuItem]) -> None: ...

    def run_detached(self) -> None: ...

    def stop(self) -> None: ...


@dataclass
class Shell:
    """一次壳会话（后端 + 窗口 + 托盘 + 登录项 + 通知）。"""

    backend: BackendSpec
    window: WindowPort
    tray: TrayPort
    login_item: Any
    command: Sequence[str]
    notifier: Callable[[str, str], None] = send_notification
    parent_pid: int | None = None
    starter: Callable[..., BackendHandle] = start_backend
    """后端拉起件（缺省 `start_backend`；用例注入替身以断言生命周期语义）。"""
    handle: BackendHandle | None = None

    # ── 启动 ────────────────────────────────────────────────────────────────
    def start(self) -> BackendHandle:
        """拉起后端 → 读握手 → 按握手地址开窗 → 装托盘。"""
        self.handle = self.starter(self.backend, parent_pid=self.parent_pid)
        self.window.create(self.handle.client_url, on_close_requested=self.hide_window)
        self.tray.install(self.menu_items())
        self.tray.run_detached()
        return self.handle

    def menu_items(self) -> list[MenuItem]:
        """托盘菜单：显示 / 自启（勾选态＝登录项本身）/ 测试通知 / 退出。"""
        return [
            MenuItem("显示窗口", self.show_window),
            MenuItem("开机自启", self.toggle_autostart, checked=self.login_item.is_enabled),
            MenuItem("发送测试通知", self.send_test_notification),
            MenuItem("退出", self.quit),
        ]

    # ── 动作 ────────────────────────────────────────────────────────────────
    def show_window(self) -> None:
        self.window.show()

    def hide_window(self) -> None:
        """关窗动作：**只隐藏**——后端与托盘继续（[D-076] ③）。"""
        self.window.hide()

    def toggle_autostart(self) -> None:
        """开 / 关自启：真相源＝平台登录项，故只读改它本身。"""
        if self.login_item.is_enabled():
            self.login_item.disable()
        else:
            self.login_item.enable(self.command)

    def send_test_notification(self) -> None:
        self.notifier(*_TEST_NOTIFICATION)

    def quit(self) -> None:
        """显式退出：销毁窗口 → `run` 的收尾序列回收后端与托盘。"""
        self.window.destroy()

    # ── 主循环 ──────────────────────────────────────────────────────────────
    def run(self) -> int:
        """跑窗口主循环；无论因何返回都**必**回收后端与托盘（不留孤儿）。"""
        try:
            self.window.run()
        finally:
            self.tray.stop()
            if self.handle is not None:
                self.handle.stop()
        return 0


def run_shell(
    *,
    backend_executable: str | os.PathLike[str] | None = None,
    backend_spec: BackendSpec | None = None,
    dev: bool = False,
    window: WindowPort | None = None,
    tray: TrayPort | None = None,
    login_item: Any = None,
    notifier: Callable[[str, str], None] | None = None,
    shell_executable: str | os.PathLike[str] | None = None,
    parent_pid: int | None = None,
) -> int:
    """生产入口：按缺省件装配壳并运行（各端口可注入，故用例不需要 GUI）。

    ``dev=True`` 时给后端加 ``--dev``（浏览器可达的 dev 面；发布构建里该子包已被剔除，
    届时后端会显式拒绝，不会静默降级）。
    """
    spec = BackendSpec.default(backend_executable) if backend_spec is None else backend_spec
    if dev:
        spec = replace(spec, argv=(*spec.argv, "--dev"))
    if window is None or tray is None:
        from st_agent.ui.shell.tray import PystrayTray    # noqa: PLC0415 —— 惰性：缺依赖时显式抛
        from st_agent.ui.shell.window import PywebviewWindow  # noqa: PLC0415

        window = PywebviewWindow() if window is None else window
        tray = PystrayTray() if tray is None else tray
    shell = Shell(
        backend=spec,
        window=window,
        tray=tray,
        login_item=autostart.login_item() if login_item is None else login_item,
        command=tuple(autostart.self_command(shell_executable)),
        notifier=send_notification if notifier is None else notifier,
        parent_pid=os.getpid() if parent_pid is None else parent_pid,
    )
    shell.start()
    return shell.run()
