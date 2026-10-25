"""存储根指针（启动引导配置；[02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)；[D-129]）。

**为什么在存储根之外**：指针决定「打开哪个存储」，若放进存储本身会产生鸡生蛋问题；
而「完全清空」删的是整个存储根，指针在里面会被一并删掉。故它落用户级目录
``STAgent-boot/bootstrap.json``（各平台用户目录，见 :func:`bootstrap_path`）。

**解析顺序**：``--root`` > ``ST_AGENT_ROOT`` > 指针 > 平台默认。显式参数与显式环境变量
始终优先——运维 / 开发用它们临时换根，不应被设置页写下的指针覆盖。

**失败口径**：文件不存在 → ``None``（正常）；文件存在但损坏 → 抛
:class:`BootstrapConfigError`（不静默回退到默认根——那会让用户以为数据丢了）。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from st_agent.l0.bootstrap import BootstrapConfigError

__all__ = [
    "BootstrapConfigError",
    "RootBoot",
    "bootstrap_path",
    "clear_store_root",
    "platform_default_root",
    "read_store_root",
    "resolve_root",
    "write_store_root",
]

_APP_DIR = "STAgent-boot"
_FILENAME = "bootstrap.json"
_ROOT_KEY = "store_root"
_APP_DIR_WIN = "STAgent"
_APP_DIR_LINUX = "st-agent"


@dataclass(frozen=True)
class RootBoot:
    """设置面与入口共用的根指针端口（测试可注入替身）。"""

    path: Path
    default: Path

    def read(self) -> Path | None:
        return read_store_root(self.path)

    def write(self, root: Path | str) -> None:
        write_store_root(root, self.path)

    def clear(self) -> None:
        clear_store_root(self.path)


def bootstrap_path(
    env: Mapping[str, str] | None = None, platform: str | None = None
) -> Path:
    """指针文件的位置（用户级；**不在**默认存储根之内）。

    Windows ``%LOCALAPPDATA%\\STAgent-boot\\bootstrap.json`` · macOS
    ``~/Library/Application Support/STAgent-boot/bootstrap.json`` · 其余走
    ``$XDG_CONFIG_HOME``（缺省 ``~/.config``）。
    """
    values = os.environ if env is None else env
    system = sys.platform if platform is None else platform
    if system.startswith("win"):
        base = Path(values.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif system == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(values.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / _APP_DIR / _FILENAME


def platform_default_root(
    env: Mapping[str, str] | None = None, system: str | None = None
) -> Path:
    """平台默认存储根（不含 ``ST_AGENT_ROOT`` 与指针覆盖）。"""
    values = os.environ if env is None else env
    name = sys.platform if system is None else system
    home = Path(str(values.get("HOME") or values.get("USERPROFILE") or Path.home()))
    if name.startswith("win"):
        local = str(values.get("LOCALAPPDATA") or "").strip()
        return (Path(local) if local else home / "AppData" / "Local") / _APP_DIR_WIN
    if name == "darwin":
        return home / "Library" / "Application Support" / _APP_DIR_WIN
    xdg = str(values.get("XDG_DATA_HOME") or "").strip()
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / _APP_DIR_LINUX


def read_store_root(path: Path | str | None = None) -> Path | None:
    """读指针指向的存储根；文件缺失 → ``None``，损坏 → ``BootstrapConfigError``。"""
    target = Path(path) if path is not None else bootstrap_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BootstrapConfigError(f"启动配置不可读（{target}）：{exc}") from exc
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise BootstrapConfigError(f"启动配置不是合法 JSON（{target}）：{exc}") from exc
    if not isinstance(payload, dict):
        raise BootstrapConfigError(f"启动配置顶层须为对象（{target}）")
    value = payload.get(_ROOT_KEY)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise BootstrapConfigError(f"启动配置的 {_ROOT_KEY} 须为字符串（{target}）")
    return Path(value).expanduser()


def write_store_root(root: Path | str, path: Path | str | None = None) -> Path:
    """原子写指针；返回实际写入的文件路径。"""
    target = Path(path) if path is not None else bootstrap_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({_ROOT_KEY: str(Path(root).expanduser())}, ensure_ascii=False)
    temporary = target.with_name(f"{target.name}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BootstrapConfigError(f"启动配置写入失败（{target}）：{exc}") from exc
    return target


def clear_store_root(path: Path | str | None = None) -> bool:
    """清除指针；返回是否真的删除了文件（不存在不算失败）。"""
    target = Path(path) if path is not None else bootstrap_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise BootstrapConfigError(f"启动配置清除失败（{target}）：{exc}") from exc
    return True


def resolve_root(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    bootstrap_file: Path | str | None = None,
) -> tuple[Path, str]:
    """按顺序解析存储根，返回 ``(路径, 来源)``。

    来源取值：``cli`` / ``env`` / ``bootstrap`` / ``default``——设置页据此告诉用户
    「当前位置是显式参数指定，改指针不会立刻生效」这类事实。
    """
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser(), "cli"
    values = os.environ if env is None else env
    override = str(values.get("ST_AGENT_ROOT") or "").strip()
    if override:
        return Path(override).expanduser(), "env"
    pointer = read_store_root(bootstrap_file)
    if pointer is not None:
        if not pointer.exists():
            raise BootstrapConfigError(
                f"启动指针指向的存储根不存在：{pointer}；"
                "请恢复该目录，或在设置页把存储位置改回默认"
            )
        return pointer, "bootstrap"
    return platform_default_root(env=values, system=platform), "default"
