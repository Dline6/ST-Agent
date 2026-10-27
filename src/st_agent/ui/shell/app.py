"""壳的装配与生命周期（[`T-UI-002.2`]；[D-076] ②③④）。

壳的**全部语义**都在本模块——GUI 边（:mod:`~st_agent.ui.shell.window` /
:mod:`~st_agent.ui.shell.tray`）只是薄封装，故这里的每条动作都可用替身离线钉住：

- **关窗＝隐藏**（`hide_window`）：后端继续跑，托盘可再唤起（[00 §1.1]「后端比任何窗口活得久」）；
  **首次关窗**有一次显式说明（[`T-UI-027.1`] GWT-3），标记随窗口态落盘、此后不再出现；
- **窗口规格与记忆**（[`T-UI-027.1`]）：默认 1440×900 / 最小 1024×640；尺寸 / 位置 / 最大化态
  经 :mod:`~st_agent.ui.shell.prefs` 跨会话恢复；
- **退出＝回收**（`quit` → `run` 的 `finally`）：销毁窗口 → 停托盘 → **优雅回收后端**；
- **开机自启**：开关即读改平台登录项本身（不另存第二份状态，[D-076] 派生口径）；
- **测试通知**：本地系统通知（最小接线；业务通知链归 [`T-L5-002`](../../tasks/T-L5-002-渠道适配器投递编排与升级链.md)）；
- **凭据口令**（[`T-UI-002.4.2`]）：后端在就绪握手**之前**要口令时，由注入的**索取端口**去拿
  （先查系统凭据库、否则弹原生框、成功后写回）；「清除已记住的凭据口令」给这条记忆一个出口；
- **壳意图**（[`T-UI-027.1`]）：`Ctrl+W` / `Ctrl+Q` 由前端经回环端点发到后端，后端沿既有
  stdout 管道回 `event=shell`，壳据此收托盘 / 退出——**不加 JS 桥、不开壳专有通道**；
- **多窗口**（[`T-UI-027.2`]）：托盘「新建窗口」开一个附属窗口，复用同一后端句柄。

壳不 import 任何业务层（`tests/ui/test_shell_native_only.py` 钉住），窗口只经回环面取数。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from st_agent.ui.shell import autostart
from st_agent.ui.shell.backend import BackendHandle, BackendSpec, start_backend
from st_agent.ui.shell.errors import ShellCancelled, ShellError
from st_agent.ui.shell.notify import send_notification
from st_agent.ui.shell.passphrase import (
    NativePassphrasePrompt,
    PassphrasePrompt,
    PassphraseRequest,
    PassphraseResolver,
    PassphraseVault,
    build_vault,
)
from st_agent.ui.shell.prefs import (
    WindowGeometry,
    WindowState,
    load_state,
    save_state,
    state_path,
)
from st_agent.ui.shell.tray import MenuItem

__all__ = ["Shell", "run_shell"]

_TITLE = "ST Agent"

_FIRST_CLOSE_NOTICE = (
    "窗口已收进系统托盘，应用仍在后台运行（定时调度与主动触达不会中断）。"
    "点托盘图标可恢复主窗口；要真正退出，请在托盘菜单选「退出」。"
)
"""首次关窗的一次性说明（[`T-UI-027.1`] GWT-3）——只出现一次，标记随窗口态落盘。"""

_DELIVERY_SETTINGS = "/delivery?view=config"
"""托盘「主动触达设置…」打开的面（触达配置视图；[11 册 §2.2] 的子面走查询串）。"""

_TEST_NOTIFICATION = (
    "ST Agent",
    "测试通知：原生通知可达，且不发出任何网络请求。业务通知见触达渠道配置。",
)

_UNSET = object()
"""`run_shell(vault=…)` 的缺省哨兵——未指定 ⇒ 自动取系统凭据库（不可用即**显式降级**）。"""

_EXIT_STARTUP_FAILED = 3
"""壳自身启动失败的退出码（与后端的 2 区分开；用户取消则是 0）。"""


class WindowPort(Protocol):
    """窗口端口（替身：断言「关窗只隐藏 / 退出才销毁 / 窗口态往返」）。"""

    def create(
        self,
        url: str,
        *,
        on_close_requested: Callable[[], None],
        geometry: WindowGeometry | None = None,
        on_geometry_changed: Callable[[WindowGeometry], None] | None = None,
    ) -> None: ...

    def show(self) -> None: ...

    def hide(self) -> None: ...

    def load_url(self, url: str) -> None: ...

    def set_title(self, title: str) -> None: ...

    def open_secondary(self, url: str) -> Any: ...

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
    resolver: Callable[[PassphraseRequest], str | None] | None = None
    """口令索取端口（后端在就绪前要口令时调它）。``None`` ⇒ 壳不接口令交互。"""
    vault: PassphraseVault | None = None
    """口令记忆端口（供「清除已记住的凭据口令」用；``None`` ⇒ 未启用系统凭据库）。"""
    handle: BackendHandle | None = None
    prefs_path: Path | None = None
    """窗口态落盘位置（`None` ⇒ 本次会话不落盘；生产入口传 `prefs.state_path()`）。"""
    window_state: WindowState = field(default_factory=WindowState)

    # ── 启动 ────────────────────────────────────────────────────────────────
    def start(self) -> BackendHandle:
        """拉起后端 → 读握手 → 按握手地址开窗 → 装托盘。"""
        if self.prefs_path is not None:
            self.window_state = load_state(self.prefs_path)
        self.handle = self.starter(
            self.backend,
            parent_pid=self.parent_pid,
            resolve_passphrase=self.resolver,
            on_shell_event=self.handle_shell_event,
        )
        self.window.create(
            self.handle.client_url,
            on_close_requested=self.hide_window,
            geometry=self.window_state.geometry,
            on_geometry_changed=self._remember_geometry,
        )
        self.tray.install(self.menu_items())
        self.tray.run_detached()
        return self.handle

    def menu_items(self) -> list[MenuItem]:
        """托盘菜单：显示 / 新建窗口 / 主动触达设置 / 自启（勾选态＝登录项本身）/ 测试通知 / 清除口令 / 退出。"""
        return [
            MenuItem("显示主窗口", self.show_window),
            MenuItem("新建窗口", self.open_window),
            MenuItem("主动触达设置…", self.open_delivery_settings),
            MenuItem("开机自启", self.toggle_autostart, checked=self.login_item.is_enabled),
            MenuItem("发送测试通知", self.send_test_notification),
            MenuItem("清除已记住的凭据口令", self.forget_passphrase),
            MenuItem("退出", self.quit),
        ]

    # ── 动作 ────────────────────────────────────────────────────────────────
    def show_window(self) -> None:
        self.window.show()

    def hide_window(self) -> None:
        """关窗动作：**只隐藏**——后端与托盘继续（[D-076] ③）；首次附带一次性说明。"""
        if not self.window_state.first_close_notified:
            self.window_state = replace(self.window_state, first_close_notified=True)
            self.notifier(_TITLE, _FIRST_CLOSE_NOTICE)
        self._persist()
        self.window.hide()

    def open_delivery_settings(self) -> None:
        """把主窗口带到触达配置视图（壳不承载业务动作，只做导航）。"""
        if self.handle is None:
            return
        self.window.show()
        self.window.load_url(f"{self.handle.base_url}{_DELIVERY_SETTINGS}#t={self.handle.token}")

    def open_window(self) -> None:
        """开一个附属窗口（[`T-UI-027.2`]）：同一回环地址、同一后端进程。"""
        if self.handle is None:
            return
        self.window.open_secondary(self.handle.client_url)

    def handle_shell_event(self, payload: Mapping[str, Any]) -> None:
        """后端沿 stdout 回传的壳意图（`hide` / `quit` / `title`）。未知动作静默忽略。"""
        action = str(payload.get("action") or "")
        if action == "hide":
            self.hide_window()
        elif action == "quit":
            self.quit()
        elif action == "title":
            title = str(payload.get("title") or "").strip()
            if title:
                self.window.set_title(title)

    def _remember_geometry(self, geometry: WindowGeometry) -> None:
        """只更新内存（拖拽 / 缩放事件很密，逐次落盘没有意义）；落盘在关窗 / 退出时。"""
        self.window_state = replace(self.window_state, geometry=geometry)

    def _persist(self) -> None:
        if self.prefs_path is not None:
            save_state(self.prefs_path, self.window_state)

    def toggle_autostart(self) -> None:
        """开 / 关自启：真相源＝平台登录项，故只读改它本身。"""
        if self.login_item.is_enabled():
            self.login_item.disable()
        else:
            self.login_item.enable(self.command)

    def send_test_notification(self) -> None:
        self.notifier(*_TEST_NOTIFICATION)

    def forget_passphrase(self) -> None:
        """清掉本根在系统凭据库里的口令（[`T-UI-002.4.2`] GWT-5）。

        这是口令记忆的**出口**（可回滚）：清掉之后下次启动回到「弹框索取」。
        没有启用凭据库时如实说明——不假装清过了。
        """
        if self.vault is None or self.handle is None:
            self.notifier(_TITLE, "未启用系统凭据库，本机没有记住的口令。")
            return
        try:
            deleted = self.vault.delete(self.handle.root)
        except Exception as exc:                     # noqa: BLE001 —— 托盘动作须给出回执
            self.notifier(_TITLE, f"清除已记住的口令失败：{type(exc).__name__}: {exc}")
            return
        self.notifier(_TITLE, "已清除本机记住的凭据口令；下次启动将重新索取。"
                      if deleted else "本机没有记住过该存储根的凭据口令。")

    def quit(self) -> None:
        """显式退出：销毁窗口 → `run` 的收尾序列回收后端与托盘。"""
        self._persist()
        self.window.destroy()

    # ── 主循环 ──────────────────────────────────────────────────────────────
    def run(self) -> int:
        """跑窗口主循环；无论因何返回都**必**回收后端与托盘（不留孤儿）。"""
        try:
            self.window.run()
        finally:
            self._persist()
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
    prompt: PassphrasePrompt | None = None,
    vault: Any = _UNSET,
    prefs_path: Path | None = None,
) -> int:
    """生产入口：按缺省件装配壳并运行（各端口可注入，故用例不需要 GUI）。

    ``dev=True`` 时给后端加 ``--dev``（浏览器可达的 dev 面；发布构建里该子包已被剔除，
    届时后端会显式拒绝，不会静默降级）。

    ``prefs_path`` 是窗口态落盘位置（生产入口传 :func:`~st_agent.ui.shell.prefs.state_path`；
    ``None`` ⇒ 本次不落盘，用例因此不会碰到用户的真实目录）。

    口令两面：``prompt`` 缺省取原生口令框；``vault`` 缺省取系统凭据库，**取不到即降级为
    每次索取并显式告知一次**（[02 §3]）。两种收场都**以提示收尾**（不裸抛 traceback）：
    用户取消 ⇒ 退出码 0；壳自身启动失败 ⇒ 非零。
    """
    toast = send_notification if notifier is None else notifier
    if prompt is None:
        prompt = NativePassphrasePrompt()
    if vault is _UNSET:
        vault, notice = build_vault()
        if notice:
            toast(_TITLE, notice)
    resolver = PassphraseResolver(prompt=prompt, vault=vault)

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
        notifier=toast,
        parent_pid=os.getpid() if parent_pid is None else parent_pid,
        resolver=resolver,
        vault=vault,
        prefs_path=prefs_path,
    )
    try:
        shell.start()
    except ShellCancelled as exc:
        toast(_TITLE, str(exc))
        return 0
    except ShellError as exc:
        toast(_TITLE, f"桌面壳未能启动：{exc}")
        return _EXIT_STARTUP_FAILED
    return shell.run()
