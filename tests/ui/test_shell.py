"""壳侧的窗口 / 托盘 / 自启 / 通知 / 生命周期（[`T-UI-002.2`]）。

GUI 边（pywebview / pystray）在 CI 里**不装也不跑**，故本文件的策略是：**语义全部落在
可注入的端口上**（窗口、托盘、后端拉起件、登录项、通知），用真后端子进程 + 四个替身把每条
GWT 钉死；GUI 薄边只断言「缺依赖时显式抛」「不 import 业务层」「不注册 JS 桥」。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from st_agent.l0.storage import Store
from st_agent.ui.shell import autostart, notify
from st_agent.ui.shell.app import Shell
from st_agent.ui.shell.backend import BackendHandle, BackendSpec, start_backend
from st_agent.ui.shell.errors import ShellError, ShellSurfaceUnavailable
from st_agent.ui.shell.tray import MenuItem, neutral_icon_image

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
_STOP_TIMEOUT = 30.0


def _child_env() -> dict[str, str]:
    return {"PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}


# ───────────────────────── 替身 ─────────────────────────


class FakeWindow:
    """窗口替身：记下开窗地址与每一次显隐 / 销毁。"""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.close_handler: Any = None
        self.calls: list[str] = []

    def create(self, url: str, *, on_close_requested: Any) -> None:
        self.urls.append(url)
        self.close_handler = on_close_requested

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def destroy(self) -> None:
        self.calls.append("destroy")

    def run(self) -> None:
        """真 GUI 阻塞到窗口销毁；替身直接返回，使 `run` 的收尾序列被执行。"""
        self.calls.append("run")


class FakeTray:
    def __init__(self) -> None:
        self.items: list[MenuItem] = []
        self.calls: list[str] = []

    def install(self, items: Sequence[MenuItem]) -> None:
        self.items = list(items)
        self.calls.append("install")

    def run_detached(self) -> None:
        self.calls.append("run_detached")

    def stop(self) -> None:
        self.calls.append("stop")


class FakeLoginItem:
    """登录项替身：真相源＝自身状态（真实现里是 OS 注册本身）。"""

    def __init__(self) -> None:
        self.state = False
        self.commands: list[Sequence[str]] = []

    def is_enabled(self) -> bool:
        return self.state

    def enable(self, command: Sequence[str]) -> None:
        self.state = True
        self.commands.append(list(command))

    def disable(self) -> None:
        self.state = False


class FakeHandle:
    """后端句柄替身（真句柄由下面用真子进程的用例覆盖）。"""

    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 51234
        self.token = "tok-test"
        self.version = "0.1.0"
        self.root = "/tmp/store"
        self.stopped = False

    @property
    def client_url(self) -> str:
        return f"http://{self.host}:{self.port}/#t={self.token}"

    def stop(self, timeout: float = _STOP_TIMEOUT) -> int:      # noqa: ARG002
        self.stopped = True
        return 0


class Rig:
    """一次壳装配 + 全部替身（供断言）。"""

    def __init__(self, window: FakeWindow | None = None) -> None:
        self.window = window or FakeWindow()
        self.tray = FakeTray()
        self.login = FakeLoginItem()
        self.handle = FakeHandle()
        self.notes: list[tuple[str, str]] = []
        self.shell = Shell(
            backend=BackendSpec.default(),
            window=self.window,
            tray=self.tray,
            login_item=self.login,
            command=("/opt/st-agent",),
            notifier=lambda title, body: self.notes.append((title, body)),
            parent_pid=4242,
            starter=lambda spec, *, parent_pid=None: self.handle,   # noqa: ARG005
        )


# ───────────────────────── GWT-1/3：真后端的拉起 → 握手 → 回收 ─────────────────────────


@pytest.fixture
def real_handle(tmp_path: Path) -> Iterator[BackendHandle]:
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    handle = start_backend(
        BackendSpec(argv=BackendSpec.default().argv, env=_child_env()),
        parent_pid=os.getpid(),
        cwd=workdir,
    )
    try:
        yield handle
    finally:
        if handle.alive:
            handle.stop()


def test_start_backend_reads_handshake_and_exposes_client_url(real_handle: BackendHandle) -> None:
    """GWT-1 · 握手字段落到壳侧句柄；窗口地址**带令牌 fragment**。"""
    assert real_handle.host == "127.0.0.1"
    assert real_handle.port > 0
    assert real_handle.token
    assert real_handle.version
    assert Path(real_handle.root).is_dir()
    assert real_handle.client_url == (
        f"http://127.0.0.1:{real_handle.port}/#t={real_handle.token}"
    )


def test_backend_stops_gracefully_through_shell(real_handle: BackendHandle) -> None:
    """GWT-3 · 壳的回收路径让后端**优雅**退出（stdin 控制通道，退出码 0）。"""
    assert real_handle.alive
    assert real_handle.stop() == 0
    assert not real_handle.alive


def test_start_backend_raises_on_backend_error(tmp_path: Path) -> None:
    """后端显式失败 ⇒ 壳抛 `ShellError` 并点名原因（不静默起一个空窗口）。"""
    root = tmp_path / "store"
    Store.create(root, "correct horse battery staple")
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    spec = BackendSpec(
        argv=(*BackendSpec.default().argv, "--root", str(root)), env=_child_env(),
    )
    with pytest.raises(ShellError) as failure:
        start_backend(spec, parent_pid=os.getpid(), cwd=workdir)
    assert "主密码" in str(failure.value)


# ───────────────────────── 冻结壳：按产物摆放惯例找后端二进制 ─────────────────────────


def _frozen_layout(tmp_path: Path, layout: str) -> tuple[Path, Path]:
    suffix = ".exe" if os.name == "nt" else ""
    shell_dir = tmp_path / "st-agent-shell"
    shell_dir.mkdir()
    shell_exe = shell_dir / f"st-agent-shell{suffix}"
    shell_exe.write_bytes(b"")
    if layout == "same-dir":
        target = shell_dir / f"st-agent-server{suffix}"
    else:
        target = tmp_path / "st-agent-server" / f"st-agent-server{suffix}"
        target.parent.mkdir()
    target.write_bytes(b"")
    return shell_exe, target


@pytest.mark.parametrize("layout", ["same-dir", "sibling-dir"])
def test_find_sibling_backend_layouts(tmp_path: Path, layout: str) -> None:
    """GWT-1 · 冻结壳认得两种产物摆放（同目录 / 同级同名目录）。"""
    from st_agent.ui.shell.backend import find_sibling_backend    # noqa: PLC0415

    shell_exe, target = _frozen_layout(tmp_path, layout)
    assert find_sibling_backend(executable=shell_exe, frozen=True) == target


def test_find_sibling_backend_returns_none_outside_frozen(tmp_path: Path) -> None:
    """开发态（未冻结、未显式指定）不猜路径——回落 `python -m st_agent`。"""
    from st_agent.ui.shell.backend import find_sibling_backend    # noqa: PLC0415

    shell_exe, _target = _frozen_layout(tmp_path, "same-dir")
    assert find_sibling_backend(executable=shell_exe, frozen=False) is None
    assert find_sibling_backend(executable=tmp_path / "nope" / "st-agent-shell", frozen=True) is None


def test_backend_spec_default_prefers_frozen_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """冻结态下缺省后端＝同级后端二进制（不能拿壳自己当解释器）。"""
    shell_exe, target = _frozen_layout(tmp_path, "sibling-dir")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(shell_exe))
    assert BackendSpec.default().argv == (str(target),)


# ───────────────────────── GWT-1/2/3：起壳 → 关窗 → 退出 ─────────────────────────


def test_start_opens_window_at_handshake_url_and_installs_tray() -> None:
    """GWT-1 · 开窗地址＝握手地址；托盘四项齐备且已跑起来。"""
    rig = Rig()
    rig.shell.start()
    assert rig.window.urls == [rig.handle.client_url]
    assert [item.label for item in rig.tray.items] == [
        "显示窗口", "开机自启", "发送测试通知", "退出",
    ]
    assert rig.tray.calls == ["install", "run_detached"]


def test_closing_window_keeps_backend_alive() -> None:
    """GWT-2 · 关窗＝隐藏：后端**不**被回收，托盘可再唤起。"""
    rig = Rig()
    rig.shell.start()
    rig.window.close_handler()                      # 用户关窗
    assert rig.window.calls[-1] == "hide"
    assert rig.handle.stopped is False
    rig.shell.show_window()
    assert rig.window.calls[-1] == "show"


def test_quit_destroys_window_and_reclaims_backend() -> None:
    """GWT-3 · 退出＝销毁窗口 → 停托盘 → 优雅回收后端（单条收尾路径）。"""
    rig = Rig()
    rig.shell.start()
    rig.shell.quit()
    assert rig.window.calls[-1] == "destroy"
    assert rig.shell.run() == 0
    assert rig.tray.calls[-1] == "stop"
    assert rig.handle.stopped is True


def test_run_stops_everything_even_if_window_loop_raises() -> None:
    """收尾序列在异常路径上同样执行（不留托盘、不留孤儿后端）。"""

    class Boom(FakeWindow):
        def run(self) -> None:
            raise RuntimeError("窗口循环炸了")

    rig = Rig(window=Boom())
    rig.shell.start()
    with pytest.raises(RuntimeError):
        rig.shell.run()
    assert rig.tray.calls[-1] == "stop"
    assert rig.handle.stopped is True


# ───────────────────────── GWT-4/5：自启开关与测试通知 ─────────────────────────


def test_toggle_autostart_mirrors_login_item_state() -> None:
    """GWT-4 · 开关即读写登录项本身（勾选态直接取自它），命令＝壳自己的启动命令。"""
    rig = Rig()
    assert rig.login.is_enabled() is False
    rig.shell.toggle_autostart()
    assert rig.login.is_enabled() is True
    assert rig.login.commands == [["/opt/st-agent"]]
    rig.shell.toggle_autostart()
    assert rig.login.is_enabled() is False


def test_test_notification_uses_notifier_port() -> None:
    """GWT-5 · 托盘「发送测试通知」走通知端口（生产默认＝本地子进程，不进网络）。"""
    rig = Rig()
    rig.shell.send_test_notification()
    assert len(rig.notes) == 1
    assert rig.notes[0][0] == "ST Agent"


# ───────────────────────── GUI 边：惰性依赖 / 无桥 / 无业务层 ─────────────────────────


def test_shell_package_imports_without_gui_deps() -> None:
    """未装 `[shell]` extra 时包仍可导入（GUI 依赖只在开窗 / 装托盘那一刻才要）。"""
    if "webview" in sys.modules or "pystray" in sys.modules:
        pytest.skip("本环境已装 GUI 依赖，惰性断言不适用")
    import st_agent.ui.shell as package      # noqa: PLC0415

    assert package.Shell is Shell


def _block_import(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """让 `import <name>` 失败（模拟未装该依赖），无论本机是否装了。"""
    import builtins                                # noqa: PLC0415

    real_import = builtins.__import__

    def fake_import(module: str, *args: Any, **kwargs: Any) -> Any:
        if module == name or module.startswith(f"{name}."):
            raise ImportError(f"simulated missing dependency: {name}")
        return real_import(module, *args, **kwargs)

    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_window_edge_fails_explicitly_without_webview(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 pywebview ⇒ `ShellSurfaceUnavailable`（点名装法），不静默降级。"""
    from st_agent.ui.shell.window import PywebviewWindow      # noqa: PLC0415

    _block_import(monkeypatch, "webview")
    with pytest.raises(ShellSurfaceUnavailable) as failure:
        PywebviewWindow()
    assert "pip install" in str(failure.value)


def test_tray_edge_fails_explicitly_without_pystray(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 pystray ⇒ 同样显式抛。"""
    from st_agent.ui.shell.tray import PystrayTray            # noqa: PLC0415

    _block_import(monkeypatch, "pystray")
    with pytest.raises(ShellSurfaceUnavailable) as failure:
        PystrayTray()
    assert "pip install" in str(failure.value)


def test_shell_has_no_js_bridge_or_business_imports() -> None:
    """壳**不提供**壳专有通道、**不引**任何业务层（[D-060] ①；[T-UI-001] 的约束）。"""
    shell_dir = SRC / "st_agent" / "ui" / "shell"
    for path in sorted(shell_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(shell_dir)
        for forbidden in ("js_api", "evaluate_js", "create_proxy"):
            assert forbidden not in source, f"{rel}: 出现壳专有桥 {forbidden}"
        for target in ("l0", "l1", "l2", "l3", "l4", "l5", "l6", "contracts", "app"):
            assert f"st_agent.{target}" not in source, f"{rel}: 壳不得引业务层 st_agent.{target}"


def test_neutral_icon_is_drawn_in_process() -> None:
    """托盘图标是程序内画的几何形（不引外部资产、不写字）。"""
    pytest.importorskip("PIL")
    assert neutral_icon_image(32).size == (32, 32)


# ───────────────────────── 自启：三平台实现（注入落点，不碰真系统） ─────────────────────────


class FakeRunKeyStore:
    """Windows Run 键的替身（免在测试里动真注册表）。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.values.get(name)

    def write(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def test_windows_run_key_round_trip() -> None:
    """GWT-4 · Windows：写入 → 读回 → 移除，全部落在 Run 键的值上。"""
    store = FakeRunKeyStore()
    item = autostart.WindowsRunKey(store=store)
    assert item.is_enabled() is False
    item.enable([r"C:\Program Files\ST Agent\st-agent.exe"])
    assert item.is_enabled() is True
    assert store.values["ST Agent"] == '"C:\\Program Files\\ST Agent\\st-agent.exe"'
    item.disable()
    assert item.is_enabled() is False


def test_macos_launch_agent_writes_plist(tmp_path: Path) -> None:
    """GWT-4 · macOS：LaunchAgent plist 含 ProgramArguments 与 RunAtLoad。"""
    plist = tmp_path / "com.stagent.shell.plist"
    item = autostart.MacLaunchAgent(path=plist)
    assert item.is_enabled() is False
    item.enable(["/Applications/ST Agent.app", "--dev"])
    assert item.is_enabled() is True
    content = plist.read_text(encoding="utf-8")
    assert "RunAtLoad" in content
    assert "<string>/Applications/ST Agent.app</string>" in content
    assert "<string>--dev</string>" in content
    item.disable()
    assert item.is_enabled() is False


def test_linux_desktop_entry_writes_exec(tmp_path: Path) -> None:
    """GWT-4 · Linux：XDG autostart 条目的 Exec 行（含空格的项加引号）。"""
    entry = tmp_path / "st-agent.desktop"
    item = autostart.LinuxDesktopEntry(path=entry)
    item.enable(["/opt/ST Agent/launch", "--flag"])
    assert item.is_enabled() is True
    content = entry.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert 'Exec="/opt/ST Agent/launch" --flag' in content
    item.disable()
    assert item.is_enabled() is False


@pytest.mark.parametrize(
    "system,kind",
    [
        ("win32", autostart.WindowsRunKey),
        ("darwin", autostart.MacLaunchAgent),
        ("linux", autostart.LinuxDesktopEntry),
    ],
)
def test_login_item_factory_picks_platform(system: str, kind: type) -> None:
    assert isinstance(autostart.login_item(system), kind)


def test_self_command_forms() -> None:
    """自启注册的命令＝壳自己的启动方式（冻结＝可执行文件；开发＝模块入口）。"""
    assert autostart.self_command("/opt/st-agent") == ["/opt/st-agent"]
    assert autostart.self_command().count("-m") == 1
    assert "st_agent.ui.shell" in autostart.self_command()


def test_self_command_is_bare_executable_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """冻结态不能写 ``-m st_agent.ui.shell``：冻结的壳不吃解释器参数（开机自启会直接失败）。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/st-agent-shell")
    assert autostart.self_command() == ["/opt/st-agent-shell"]


# ───────────────────────── 通知：本地命令（无网络） ─────────────────────────


@pytest.mark.parametrize("system", ["win32", "darwin", "linux"])
def test_notify_command_is_local(system: str) -> None:
    """GWT-5 · 通知命令是本地可执行文件，命令里不含任何 URL。"""
    command = notify.notify_command("标题", "正文", system=system)
    assert command
    assert not any(part.startswith(("http://", "https://")) for part in command)
    assert command[0] in {"powershell", "osascript", "notify-send"}


def test_notify_escaping_per_platform() -> None:
    """平台字面量转义：引号不得逃逸成命令（注入面）。"""
    win = notify.notify_command("a' b", "c' d", system="win32")[-1]
    assert "''" in win and "'a' b'" not in win
    mac = notify.notify_command('say "hi"', "line\\", system="darwin")[-1]
    assert '\\"hi\\"' in mac
    assert notify.notify_command("t", "b", system="linux") == ["notify-send", "t", "b"]


def test_send_notification_uses_injected_runner() -> None:
    seen: list[Sequence[str]] = []
    notify.send_notification("t", "b", system="linux", runner=seen.append)
    assert seen == [["notify-send", "t", "b"]]
