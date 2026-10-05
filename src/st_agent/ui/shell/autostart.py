"""开机自启：各平台**登录项**的读写（[`T-UI-002.2`]）。

**自启不注册为 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 配置条目**（[D-076]）：
自启的真相源是**操作系统登录项注册本身**，把它再登记成产品配置会造出第二份副本
（与「单一事实源」取向相悖）；其交互面属[原生呈现面](../../../../docs/PRD-v2-Agent/11-sitemap.md)
（已划出页面集、归本任务）。**复核触发**：若日后要求「对话即配置自启」，按
[铁律 8](../../工程宪法.md) 先补 01 §7 条目再实现。

三个平台各一份薄实现，命令一律**由调用方给出**（壳自己怎么被拉起，只有入口知道）：

| 平台 | 落点 | `is_enabled` 判据 |
|---|---|---|
| Windows | `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` 的 `ST Agent` 值 | 值存在 |
| macOS | `~/Library/LaunchAgents/com.stagent.shell.plist`（`RunAtLoad`） | 文件存在 |
| Linux | `~/.config/autostart/st-agent.desktop` | 文件存在 |
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "LinuxDesktopEntry",
    "LoginItem",
    "MacLaunchAgent",
    "WindowsRunKey",
    "login_item",
    "on_disk_login_item",
    "self_command",
    "windows_command_line",
]

LABEL = "com.stagent.shell"
NAME = "ST Agent"
DEFAULT_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class LoginItem(Protocol):
    """登录项注册的鸭子面（测试可注入假件，故不绑任何平台 API）。"""

    def is_enabled(self) -> bool: ...

    def enable(self, command: Sequence[str]) -> None: ...

    def disable(self) -> None: ...


# ───────────────────────── 命令形态 ─────────────────────────


def windows_command_line(command: Sequence[str]) -> str:
    """Windows 登录项的值形态：可执行文件加引号，参数逐项加引号，空格用 ``"`` 包住。"""
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def posix_command(command: Sequence[str]) -> list[str]:
    """macOS ``ProgramArguments`` / Linux ``Exec=`` 的数组形态（逐项原样）。"""
    return [str(part) for part in command]


def desktop_exec(command: Sequence[str]) -> str:
    """``.desktop`` 的 ``Exec=``：含空格的项用双引号包住（Desktop Entry 规范）。"""
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def self_command(executable: str | os.PathLike[str] | None = None) -> list[str]:
    """壳自己的启动命令：冻结产物＝那个可执行文件本身；开发态＝``python -m st_agent.ui.shell``。

    冻结态必须走第一条：``sys.executable`` 是**冻结出来的壳**，它不吃 ``-m`` 这类解释器参数
    （照开发态写会把自启项写成 ``st-agent-shell.exe -m st_agent.ui.shell``，开机即失败）。
    """
    if executable is not None:
        return [str(executable)]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "st_agent.ui.shell"]


# ───────────────────────── Windows ─────────────────────────


class _WinRegStore:
    """``HKCU`` 下某个键的值读写（``winreg`` 的薄封装；测试注入假件以免动真注册表）。"""

    def __init__(self, key_path: str = DEFAULT_RUN_KEY) -> None:
        self.key_path = key_path

    def read(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return str(value)

    def write(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class WindowsRunKey:
    """Windows 登录项（``HKCU\\...\\Run``）。"""

    def __init__(self, name: str = NAME, *, store: Any = None) -> None:
        self.name = name
        self._store = _WinRegStore() if store is None else store

    def is_enabled(self) -> bool:
        return self._store.read(self.name) is not None

    def enable(self, command: Sequence[str]) -> None:
        self._store.write(self.name, windows_command_line(command))

    def disable(self) -> None:
        self._store.delete(self.name)


# ───────────────────────── macOS / Linux（文件型） ─────────────────────────


class _FileLoginItem:
    """文件型登录项（macOS LaunchAgent / Linux autostart 条目）的公共部分。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def is_enabled(self) -> bool:
        return self.path.exists()

    def enable(self, command: Sequence[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._content(command), encoding="utf-8")

    def disable(self) -> None:
        self.path.unlink(missing_ok=True)

    def _content(self, command: Sequence[str]) -> str:      # pragma: no cover - 子类实现
        raise NotImplementedError


class MacLaunchAgent(_FileLoginItem):
    """macOS ``~/Library/LaunchAgents/<label>.plist``（``RunAtLoad``）。"""

    def __init__(self, label: str = LABEL, *, path: Path | None = None) -> None:
        self.label = label
        super().__init__(path or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")

    def _content(self, command: Sequence[str]) -> str:
        arguments = "".join(
            f"        <string>{_xml(part)}</string>\n" for part in posix_command(command)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "    <key>Label</key>\n"
            f"    <string>{_xml(self.label)}</string>\n"
            "    <key>ProgramArguments</key>\n"
            "    <array>\n"
            f"{arguments}"
            "    </array>\n"
            "    <key>RunAtLoad</key>\n"
            "    <true/>\n"
            "</dict>\n"
            "</plist>\n"
        )


class LinuxDesktopEntry(_FileLoginItem):
    """Linux ``~/.config/autostart/st-agent.desktop``（XDG Autostart）。"""

    def __init__(self, name: str = NAME, *, path: Path | None = None) -> None:
        self.name = name
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        super().__init__(path or base / "autostart" / "st-agent.desktop")

    def _content(self, command: Sequence[str]) -> str:
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={self.name}\n"
            f"Exec={desktop_exec(command)}\n"
            "X-GNOME-Autostart-enabled=true\n"
        )


# ───────────────────────── 工厂 ─────────────────────────


def on_disk_login_item(system: str | None = None) -> LoginItem:
    """按平台给**文件型**登录项（Windows 走注册表，故不在本工厂内）。"""
    system = sys.platform if system is None else system
    return MacLaunchAgent() if system == "darwin" else LinuxDesktopEntry()


def login_item(system: str | None = None, **kwargs: Any) -> LoginItem:
    """按平台给登录项实现（Windows → 注册表；macOS / Linux → 文件）。"""
    system = sys.platform if system is None else system
    if system.startswith("win"):
        return WindowsRunKey(**kwargs)
    return on_disk_login_item(system)


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
