"""桌面壳闭合：窗口态、关窗说明、图标资源、壳意图通道与多窗口（[`T-UI-027.1`] / [`.2`]）。

GUI 边（pywebview / pystray）在 CI 里**不装也不跑**，故本文件的策略与 `test_shell.py` 一致：
语义落在可注入的端口与纯函数上，GUI 薄边只断言「几何参数传对了」「事件接上了」。
"""

from __future__ import annotations

import json
import os
import struct
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from st_agent.ui.shell import prefs
from st_agent.ui.shell.app import Shell
from st_agent.ui.shell.backend import BackendSpec, start_backend
from st_agent.ui.shell.prefs import (
    MIN_HEIGHT,
    MIN_WIDTH,
    WindowGeometry,
    WindowState,
    clamp_to_screens,
    load_state,
    save_state,
    state_path,
)
from st_agent.ui.shell.tray import ASSETS, TRAY_SIZES, MenuItem, PystrayTray, tray_asset
from st_agent.ui.shell.window import PywebviewWindow

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
SHELL = SRC / "st_agent" / "ui" / "shell"


# ───────────────────────── 窗口态：路径 / 读写 / 夹取 ─────────────────────────


def test_state_path_is_user_level_and_outside_the_store() -> None:
    """窗口态落用户目录、**不进存储根**（A4）——三平台各一条。"""
    windows = state_path({"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}, "win32")
    assert windows == Path(r"C:\Users\x\AppData\Local") / "STAgent-shell" / "window.json"
    mac = state_path({}, "darwin")
    assert mac.name == "window.json" and "Application Support" in str(mac)
    linux = state_path({"XDG_STATE_HOME": "/tmp/state"}, "linux")
    assert linux == Path("/tmp/state") / "STAgent-shell" / "window.json"


def test_state_round_trips_and_survives_broken_files(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "window.json"
    assert load_state(path) == WindowState(), "缺失文件应回落缺省"

    saved = WindowState(
        geometry=WindowGeometry(width=1280, height=800, x=12, y=34, maximized=True),
        first_close_notified=True,
    )
    save_state(path, saved)
    assert load_state(path) == saved

    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) == WindowState(), "坏文件不得让壳起不来"


def test_state_rejects_illegal_fields_instead_of_half_trusting(tmp_path: Path) -> None:
    path = tmp_path / "window.json"
    path.write_text(
        json.dumps({"width": -5, "height": "900", "x": True, "y": 10, "maximized": "yes"}),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.geometry.width == prefs.DEFAULT_WIDTH
    assert state.geometry.height == prefs.DEFAULT_HEIGHT
    assert state.geometry.x is None, "布尔不是坐标"
    assert state.geometry.y == 10
    assert state.geometry.maximized is False


def test_clamp_keeps_minimum_size_and_centres_lost_windows() -> None:
    screens = ((0, 0, 1920, 1080),)
    tiny = clamp_to_screens(WindowGeometry(width=400, height=300), screens)
    assert (tiny.width, tiny.height) == (MIN_WIDTH, MIN_HEIGHT)

    on_screen = clamp_to_screens(
        WindowGeometry(width=1200, height=800, x=100, y=100), screens
    )
    assert (on_screen.x, on_screen.y) == (100, 100)

    lost = clamp_to_screens(
        WindowGeometry(width=1200, height=800, x=5000, y=5000), screens
    )
    assert (lost.x, lost.y) == ((1920 - 1200) // 2, (1080 - 800) // 2)


def test_clamp_without_screen_geometry_does_not_guess() -> None:
    geometry = WindowGeometry(width=1200, height=800, x=5000, y=5000)
    assert clamp_to_screens(geometry, ()) == geometry


# ───────────────────────── 窗口边：入参 / 事件 / 关闭 ─────────────────────────


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.handlers.append(handler)
        return self

    def fire(self, *args: Any) -> list[Any]:
        return [handler(*args) for handler in self.handlers]


class FakeNativeWindow:
    def __init__(self, **options: Any) -> None:
        self.options = options
        self.calls: list[str] = []
        self.events = SimpleNamespace(
            closing=FakeEvent(),
            moved=FakeEvent(),
            resized=FakeEvent(),
            maximized=FakeEvent(),
            restored=FakeEvent(),
        )

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def destroy(self) -> None:
        self.calls.append("destroy")

    def load_url(self, url: str) -> None:
        self.calls.append(f"load:{url}")


class FakeWebview:
    def __init__(self) -> None:
        self.windows: list[FakeNativeWindow] = []
        self.titles: list[str] = []

    def create_window(self, title: str, url: str, **options: Any) -> FakeNativeWindow:
        window = FakeNativeWindow(**options)
        self.windows.append(window)
        self.titles.append(f"{title}|{url}")
        return window

    def start(self) -> None:
        pass


def test_window_create_passes_spec_and_clamps() -> None:
    """GWT-1/2：默认尺寸与最小档原样交给 pywebview，越界记忆值先夹取。"""
    webview = FakeWebview()
    window = PywebviewWindow(webview=webview, screens=lambda: [(0, 0, 1920, 1080)])
    window.create(
        "http://127.0.0.1:1/#t=x",
        on_close_requested=lambda: None,
        geometry=WindowGeometry(width=800, height=400, x=5000, y=5000),
    )
    options = webview.windows[0].options
    assert options["min_size"] == (MIN_WIDTH, MIN_HEIGHT)
    assert (options["width"], options["height"]) == (MIN_WIDTH, MIN_HEIGHT)
    assert (options["x"], options["y"]) == ((1920 - MIN_WIDTH) // 2, (1080 - MIN_HEIGHT) // 2)
    assert options["maximized"] is False


def test_window_events_write_back_geometry() -> None:
    """GWT-1：移动 / 缩放 / 最大化 / 还原都要回写，最大化时保留还原尺寸。"""
    webview = FakeWebview()
    seen: list[WindowGeometry] = []
    window = PywebviewWindow(webview=webview, screens=lambda: [])
    window.create(
        "http://127.0.0.1:1/#t=x",
        on_close_requested=lambda: None,
        geometry=WindowGeometry(width=1280, height=800, x=10, y=20),
        on_geometry_changed=seen.append,
    )
    events = webview.windows[0].events
    events.moved.fire(30, 40)
    assert seen[-1].x == 30 and seen[-1].y == 40
    events.resized.fire(1300, 820)
    assert (seen[-1].width, seen[-1].height) == (1300, 820)
    events.maximized.fire()
    assert seen[-1].maximized is True
    events.resized.fire(1920, 1080)          # 最大化尺寸不该覆盖还原尺寸
    assert (seen[-1].width, seen[-1].height) == (1300, 820)
    events.restored.fire()
    assert seen[-1].maximized is False


def test_closing_saves_and_cancels_the_close() -> None:
    """GWT-3 前提：关窗仍**取消关闭**（只隐藏），但先把最后的窗口态交出去。"""
    webview = FakeWebview()
    seen: list[WindowGeometry] = []
    closed: list[bool] = []
    window = PywebviewWindow(webview=webview, screens=lambda: [])
    window.create(
        "u",
        on_close_requested=lambda: closed.append(True),
        geometry=WindowGeometry(width=1280, height=800, x=1, y=2),
        on_geometry_changed=seen.append,
    )
    result = webview.windows[0].events.closing.fire()
    assert result and result[0] is False, "关窗不得真关（后端要比窗口活得久）"
    assert closed == [True] and seen


def test_window_can_navigate_and_open_a_secondary() -> None:
    """GWT-5（导航）/ `.2` GWT-1（附属窗口）：都走同一个 webview。"""
    webview = FakeWebview()
    window = PywebviewWindow(webview=webview, screens=lambda: [])
    window.create("u", on_close_requested=lambda: None)
    window.load_url("http://127.0.0.1:1/delivery?view=config#t=x")
    window.open_secondary("http://127.0.0.1:1/#t=x")
    assert webview.windows[0].calls == [
        "load:http://127.0.0.1:1/delivery?view=config#t=x"
    ]
    assert webview.titles[1].endswith("|http://127.0.0.1:1/#t=x")
    window.destroy()
    assert webview.windows[0].calls[-1] == "destroy"
    assert webview.windows[1].calls == ["destroy"]


def test_destroy_is_not_cancelled_by_the_hide_on_close_rule() -> None:
    """显式退出必须能真关窗——`closing` 恒返回 False 会把它自己取消掉（T-INT-008 走查发现）。"""
    webview = FakeWebview()
    window = PywebviewWindow(webview=webview, screens=lambda: [])
    closed: list[bool] = []
    window.create("u", on_close_requested=lambda: closed.append(True))
    window.destroy()
    result = webview.windows[0].events.closing.fire()
    assert result == [True], "显式退出不得被『关窗只隐藏』取消"
    assert closed == [], "显式退出不触发关窗说明（那是用户关窗的语义）"


class FakePystrayMenuItem:
    def __init__(self, label: Any, action: Any, checked: Any = None) -> None:
        self.label = label
        self.action = action
        self.checked = checked


class FakePystray:
    """pystray 的最小替身：只关心 `MenuItem(checked=…)` 的调用约定。"""

    def __init__(self) -> None:
        self.items: list[FakePystrayMenuItem] = []

    def Menu(self, *items: Any) -> tuple[Any, ...]:
        return items

    def MenuItem(self, label: Any, action: Any, checked: Any = None) -> FakePystrayMenuItem:
        item = FakePystrayMenuItem(label, action, checked)
        self.items.append(item)
        return item

    def Icon(self, *args: Any) -> Any:
        return SimpleNamespace(args=args)


def test_tray_checked_readers_accept_the_item_pystray_passes() -> None:
    """pystray 以**菜单项为参**调用 `checked`（`self._checked(self)`），零参可调用会炸掉整个菜单。"""
    fake = FakePystray()
    tray = PystrayTray(pystray=fake, image=object())
    state = {"enabled": True}
    tray.install([MenuItem("开机自启", lambda: None, checked=lambda: state["enabled"])])

    item = fake.items[0]
    assert item.checked(object()) is True
    state["enabled"] = False
    assert item.checked(object()) is False


def test_tray_items_without_a_reader_stay_unchecked() -> None:
    fake = FakePystray()
    tray = PystrayTray(pystray=fake, image=object())
    tray.install([MenuItem("退出", lambda: None)])
    assert fake.items[0].checked is None


# ───────────────────────── 壳装配：关窗说明 / 托盘 / 意图 / 多窗口 ─────────────────────────


class RigWindow:
    def __init__(self) -> None:
        self.seen: list[WindowGeometry] = []
        self.loaded: list[str] = []
        self.titles: list[str] = []
        self.secondaries: list[str] = []
        self.calls: list[str] = []
        self.close: Any = None

    def create(self, url, *, on_close_requested, geometry=None, on_geometry_changed=None):
        self.close = on_close_requested
        self.geometry = geometry
        self.notify = on_geometry_changed

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")

    def load_url(self, url: str) -> None:
        self.loaded.append(url)

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def open_secondary(self, url: str) -> None:
        self.secondaries.append(url)

    def destroy(self) -> None:
        self.calls.append("destroy")

    def run(self) -> None:
        self.calls.append("run")


class RigTray:
    def __init__(self) -> None:
        self.items: list[Any] = []
        self.calls: list[str] = []

    def install(self, items: Sequence[Any]) -> None:
        self.items = list(items)
        self.calls.append("install")

    def run_detached(self) -> None:
        self.calls.append("run_detached")

    def stop(self) -> None:
        self.calls.append("stop")


class RigLogin:
    def is_enabled(self) -> bool:
        return False

    def enable(self, command: Any) -> None: ...

    def disable(self) -> None: ...


class RigHandle:
    host = "127.0.0.1"
    port = 51234
    token = "tok-test"
    root = "/tmp/store"
    version = "0.1.0"

    @property
    def client_url(self) -> str:
        return f"http://{self.host}:{self.port}/#t={self.token}"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self, timeout: float = 30.0) -> int:      # noqa: ARG002
        return 0


def _rig(tmp_path: Path) -> tuple[Shell, RigWindow, RigTray, list[tuple[str, str]]]:
    window = RigWindow()
    tray = RigTray()
    notes: list[tuple[str, str]] = []
    started: list[Any] = []

    def starter(spec, *, parent_pid=None, resolve_passphrase=None, on_shell_event=None):
        started.append(on_shell_event)
        return RigHandle()

    shell = Shell(
        backend=BackendSpec.default(),
        window=window,
        tray=tray,
        login_item=RigLogin(),
        command=("/opt/st-agent",),
        notifier=lambda title, body: notes.append((title, body)),
        starter=starter,
        prefs_path=tmp_path / "shell" / "window.json",
    )
    shell.start()
    assert started and callable(started[0]), "壳启动时未把意图端口交给后端拉起件"
    return shell, window, tray, notes


def test_first_close_explains_once_and_restores_from_tray(tmp_path: Path) -> None:
    """GWT-3：首次关窗一次性说明；再次关闭不再提示；托盘可恢复。"""
    shell, window, _tray, notes = _rig(tmp_path)
    window.close()
    assert window.calls[-1] == "hide"
    assert len(notes) == 1 and "托盘" in notes[0][1]
    window.close()
    assert len(notes) == 1, "一次性说明重复出现"
    shell.show_window()
    assert window.calls[-1] == "show"


def test_first_close_flag_persists_across_sessions(tmp_path: Path) -> None:
    shell, window, _tray, notes = _rig(tmp_path)
    window.close()
    shell.run()                                    # 收尾落盘
    assert load_state(shell.prefs_path).first_close_notified is True

    second, second_window, _tray2, second_notes = _rig(tmp_path)
    second_window.close()
    assert second_notes == [], "重启后又提示了一次"


def test_tray_menu_covers_the_spec_and_opens_delivery_settings(tmp_path: Path) -> None:
    """GWT-5 相邻：托盘菜单可导航到触达配置（壳不承载业务动作）。"""
    shell, window, tray, _notes = _rig(tmp_path)
    labels = [item.label for item in tray.items]
    assert labels[:3] == ["显示主窗口", "新建窗口", "主动触达设置…"]
    assert labels[-1] == "退出"

    shell.open_delivery_settings()
    assert window.loaded == [
        "http://127.0.0.1:51234/delivery?view=config#t=tok-test"
    ]


def test_secondary_window_reuses_the_backend(tmp_path: Path) -> None:
    """`.2` GWT-1：附属窗口用同一回环地址，**不**再拉起后端。"""
    shell, window, _tray, _notes = _rig(tmp_path)
    shell.open_window()
    assert window.secondaries == ["http://127.0.0.1:51234/#t=tok-test"]
    assert shell.handle is not None


def test_shell_intents_hide_quit_and_title(tmp_path: Path) -> None:
    """GWT-5：后端回传的 `hide` / `quit` / `title` 各落其路径。"""
    shell, window, _tray, _notes = _rig(tmp_path)
    shell.handle_shell_event({"action": "hide"})
    assert window.calls[-1] == "hide"
    shell.handle_shell_event({"action": "title", "title": " 总览 — ST Agent "})
    assert window.titles == ["总览 — ST Agent"]
    shell.handle_shell_event({"action": "title", "title": "   "})   # 空标题不覆盖窗口标题
    assert window.titles == ["总览 — ST Agent"]
    shell.handle_shell_event({"action": "quit"})
    assert window.calls[-1] == "destroy"
    shell.handle_shell_event({"action": "unknown"})            # 未知动作不炸、不误动作
    assert window.calls[-1] == "destroy"


# ───────────────────────── 图标资源 ─────────────────────────


def _ico_sizes(path: Path) -> list[tuple[int, int]]:
    payload = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", payload[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for index in range(count):
        entry = payload[6 + index * 16: 6 + (index + 1) * 16]
        width, height = entry[0] or 256, entry[1] or 256
        sizes.append((width, height))
    return sizes


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


def test_icon_assets_have_the_three_sizes_and_both_themes() -> None:
    """GWT-4：`.ico` 含 16 / 32 / 256；托盘浅深两套、三档俱全。"""
    assert sorted(_ico_sizes(ASSETS / "st-agent.ico")) == [(16, 16), (32, 32), (256, 256)]
    for theme in ("light", "dark"):
        for size in TRAY_SIZES:
            assert _png_size(tray_asset(theme, size)) == (size, size)
    assert (ASSETS / "tray-light-256.png").read_bytes() != (
        ASSETS / "tray-dark-256.png"
    ).read_bytes(), "浅深两版不得是同一张图"


def test_icon_generator_is_checked_in_and_wired() -> None:
    """资源由脚本产出、随包、进 EXE：三处缺一即安装器取不到图标。"""
    assert (REPO / "packaging" / "make_icons.py").is_file()
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.gui-scripts]" in pyproject
    assert 'st-agent = "st_agent.ui.shell.__main__:main"' in pyproject
    assert '"st_agent.ui.shell" = ["assets/*"]' in pyproject
    spec = (REPO / "packaging" / "st-agent-shell.spec").read_text(encoding="utf-8")
    assert "st-agent.ico" in spec and "icon=" in spec


# ───────────────────────── 壳意图端点 ─────────────────────────


def test_shell_intent_endpoint_is_explicit_outside_the_shell() -> None:
    """浏览器直开：显式 `unavailable`，不假装执行、也不拦浏览器默认。"""
    from st_agent.ui.app import build_ui

    app = build_ui(host="127.0.0.1", port=1)
    payload = app.api_shell_intent({"action": "hide"})
    assert payload["status"] == "unavailable"
    assert "桌面壳" in payload["reason"]


def test_shell_intent_endpoint_dispatches_and_validates() -> None:
    from st_agent.ui.app import build_ui

    seen: list[tuple[str, Any]] = []

    def control(action: str, extra: Any = None) -> None:
        seen.append((action, extra))

    app = build_ui(host="127.0.0.1", port=1, shell=True, shell_control=control)
    assert app.api_shell_intent({"action": "hide"})["status"] == "ok"
    assert app.api_shell_intent({"action": "quit"})["status"] == "ok"
    assert app.api_shell_intent({"action": "title", "title": " 总览 — ST Agent "})["status"] == "ok"
    assert seen == [
        ("hide", None),
        ("quit", None),
        ("title", {"title": "总览 — ST Agent"}),
    ]
    assert app.api_shell_intent({"action": "close"})["status"] == "validation_failed"
    assert app.api_shell_intent({"action": "title", "title": "  "})["status"] == "validation_failed"
    assert app.api_shell_intent(
        {"action": "title", "title": "长" * 121}
    )["status"] == "validation_failed"


def test_health_announces_the_shell_session() -> None:
    from st_agent.ui.app import build_ui

    assert build_ui(host="127.0.0.1", port=1).api_health()["data"]["shell"] is False
    assert build_ui(host="127.0.0.1", port=1, shell=True).api_health()["data"]["shell"] is True


# ───────────────────────── 端到端：真后端沿 stdout 回传意图 ─────────────────────────


def _child_env() -> dict[str, str]:
    return {"PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}


@pytest.mark.skipif(
    not (sys.executable and os.name == "nt"), reason="真实回环冒烟只在 Windows 本机跑"
)
def test_shell_intent_reaches_the_shell_over_the_real_backend(tmp_path: Path) -> None:
    """A3 的端到端证据：前端端点 → 后端 → 既有 stdout 管道 → 壳回调（无 JS 桥）。

    不挂父进程看门狗（本用例不验那条路径）：大套件并发时进程调度会让「就绪」与
    「首个连接」之间出现短暂窗口，故连接失败按短超时重试，失败时把后端 stderr 带出来。
    """
    import threading
    import time
    import urllib.error
    import urllib.request

    workdir = tmp_path / "cwd"
    workdir.mkdir()
    store = tmp_path / "store"
    received: list[dict[str, Any]] = []
    ready = threading.Event()

    handle = start_backend(
        BackendSpec(
            argv=(*BackendSpec.default().argv, "--root", str(store)), env=_child_env()
        ),
        cwd=workdir,
        on_shell_event=lambda payload: (received.append(payload), ready.set()),
    )
    try:
        request = urllib.request.Request(
            f"{handle.base_url}/api/shell/intent",
            data=json.dumps({"action": "title", "title": "总览 — ST Agent"}).encode("utf-8"),
            method="POST",
            headers={"X-ST-Token": handle.token, "Content-Type": "application/json"},
        )
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except OSError as exc:               # URLError 亦属 OSError
                if not handle.alive or time.monotonic() > deadline:
                    raise AssertionError(
                        f"后端不可达：alive={handle.alive} "
                        f"stderr={list(handle.stderr_tail)[-5:]}"
                    ) from exc
                time.sleep(0.2)
        assert payload["status"] == "ok"
        assert ready.wait(15), "壳未收到 event=shell"
        assert received == [{"event": "shell", "action": "title", "title": "总览 — ST Agent"}]
    finally:
        assert handle.stop() == 0
