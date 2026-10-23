"""壳的窗口态：尺寸 / 位置 / 最大化与「首次关窗已说明」标记（[`T-UI-027.1`]）。

**纯函数 + 显式路径**，故窗口记忆的每条规则都能用替身钉住，不需要开真窗口：

- :func:`state_path` 定落点——用户级目录下的独立文件，**不写进存储根**（存储根归后端，
  壳只经回环面取数；界面偏好进数据面会污染备份与「完全清空」的语义）；
- :func:`load_state` / :func:`save_state` 读写；坏文件、坏字段一律回落缺省，不抛；
- :func:`clamp_to_screens` 把记忆值夹进可用显示器（多显示器拔掉后不跑出屏幕）。

[D-126] 拍定：窗口规格 1440×900 / 最小 1024×640，多显示器夹取只做 Windows。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "WindowGeometry",
    "WindowState",
    "clamp_to_screens",
    "load_state",
    "save_state",
    "screen_rects",
    "state_path",
]

DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900
MIN_WIDTH = 1024
MIN_HEIGHT = 640

_APP_DIR = "STAgent-shell"
_FILENAME = "window.json"
_VISIBLE_WIDTH = 96
"""窗口至少要有这么宽落在某块屏幕内，才判定「还在可见面」——拖出屏幕的窗口不是记忆。"""
_VISIBLE_HEIGHT = 48


@dataclass(frozen=True)
class WindowGeometry:
    """窗口几何（位置为 ``None`` ⇒ 交给系统摆放）。"""

    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    x: int | None = None
    y: int | None = None
    maximized: bool = False


@dataclass(frozen=True)
class WindowState:
    """窗口几何 + 一次性标记（两者同一份小文件，减少落盘面）。"""

    geometry: WindowGeometry = WindowGeometry()
    first_close_notified: bool = False


def state_path(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """窗口态落盘位置（逐平台用户目录；与后端的存储根**刻意分开**）。

    Windows ``%LOCALAPPDATA%\\STAgent-shell\\window.json`` · macOS
    ``~/Library/Application Support/STAgent-shell/window.json`` · 其余走
    ``$XDG_STATE_HOME``（缺省 ``~/.local/state``）。
    """
    values = os.environ if env is None else env
    name = sys.platform if platform is None else platform
    if name.startswith("win"):
        base = Path(values.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif name == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(values.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / _APP_DIR / _FILENAME


def load_state(path: str | os.PathLike[str]) -> WindowState:
    """读窗口态；文件缺失 / 损坏 / 字段非法一律回落缺省（不抛、不半信半疑）。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WindowState()
    if not isinstance(payload, dict):
        return WindowState()
    return _state_of(payload)


def save_state(path: str | os.PathLike[str], state: WindowState) -> None:
    """原子写窗口态（同目录临时文件 → ``os.replace``）；写不进去就静默放弃。

    壳的记忆是**尽力而为**：目录只读、磁盘满都不该让关窗 / 退出失败。
    """
    target = Path(path)
    payload = _payload_of(state)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        return


def clamp_to_screens(
    geometry: WindowGeometry, screens: Sequence[tuple[int, int, int, int]]
) -> WindowGeometry:
    """把记忆值夹进现有显示器：尺寸不小于最小档、位置必须还有可见面。

    ``screens`` 是 ``(x, y, width, height)`` 的序列（**首块视为主屏**）。空序列 ⇒ 原样返回
    （拿不到显示器几何时不猜）。
    """
    if not screens:
        return geometry
    widest = max(width for _x, _y, width, _height in screens)
    tallest = max(height for _x, _y, _width, height in screens)
    width = max(MIN_WIDTH, min(max(geometry.width, 1), widest))
    height = max(MIN_HEIGHT, min(max(geometry.height, 1), tallest))
    sized = replace(geometry, width=width, height=height)
    if geometry.x is None or geometry.y is None:
        return sized
    if _visible_on_any(geometry.x, geometry.y, width, height, screens):
        return sized
    return replace(sized, **_centred_on(screens[0], width, height))


def screen_rects() -> tuple[tuple[int, int, int, int], ...]:
    """枚举显示器矩形（``(x, y, width, height)``）；非 Windows 或失败即空。

    坐标是**物理像素**，而 pywebview 在 Windows 上按逻辑像素收发——高 DPI 下两者有比例差，
    故本函数只用于「窗口是不是整块跑出屏幕」这类粗判定，不用于精确摆位。
    """
    if not sys.platform.startswith("win"):
        return ()
    try:
        import ctypes                       # noqa: PLC0415 —— 仅 Windows 路径需要
        from ctypes import wintypes         # noqa: PLC0415

        monitors: list[tuple[int, int, int, int]] = []
        monitor_proc = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def collect(
            _monitor: Any, _hdc: Any, rect: Any, _data: Any
        ) -> int:
            box = rect.contents
            monitors.append(
                (int(box.left), int(box.top), int(box.right - box.left), int(box.bottom - box.top))
            )
            return 1

        callback = monitor_proc(collect)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
        return tuple(monitors)
    except Exception:                        # noqa: BLE001 —— 拿不到就当没有，不影响开窗
        return ()


def _payload_of(state: WindowState) -> dict[str, Any]:
    geometry = state.geometry
    return {
        "width": geometry.width,
        "height": geometry.height,
        "x": geometry.x,
        "y": geometry.y,
        "maximized": geometry.maximized,
        "first_close_notified": state.first_close_notified,
    }


def _state_of(payload: Mapping[str, Any]) -> WindowState:
    width = _positive(payload.get("width"), DEFAULT_WIDTH)
    height = _positive(payload.get("height"), DEFAULT_HEIGHT)
    return WindowState(
        geometry=WindowGeometry(
            width=max(MIN_WIDTH, width),
            height=max(MIN_HEIGHT, height),
            x=_coordinate(payload.get("x")),
            y=_coordinate(payload.get("y")),
            maximized=payload.get("maximized") is True,
        ),
        first_close_notified=payload.get("first_close_notified") is True,
    )


def _positive(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return fallback
    return int(value)


def _coordinate(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _visible_on_any(
    x: int,
    y: int,
    width: int,
    height: int,
    screens: Iterable[tuple[int, int, int, int]],
) -> bool:
    for screen_x, screen_y, screen_width, screen_height in screens:
        overlap_width = min(x + width, screen_x + screen_width) - max(x, screen_x)
        overlap_height = min(y + height, screen_y + screen_height) - max(y, screen_y)
        if overlap_width >= _VISIBLE_WIDTH and overlap_height >= _VISIBLE_HEIGHT:
            return True
    return False


def _centred_on(
    screen: tuple[int, int, int, int], width: int, height: int
) -> dict[str, int]:
    x, y, screen_width, screen_height = screen
    return {
        "x": x + max(0, (screen_width - width) // 2),
        "y": y + max(0, (screen_height - height) // 2),
    }
