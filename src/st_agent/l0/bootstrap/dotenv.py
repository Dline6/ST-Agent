"""``.env`` 的读取与**原地改写**（启动引导配置；[D-129]）。

写面只改**目标键**的行，其余行（注释 / 空行 / 其他键）原样保留；缺键追加到文件末尾。
原子写：同目录临时文件 → ``os.replace``；POSIX 上尽量收紧为 ``0600``（Windows 依赖
用户目录 ACL）。**值不进日志、不出现在异常文本里**。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from st_agent.l0.bootstrap import BootstrapConfigError

__all__ = ["BootstrapConfigError", "read_dotenv", "render_dotenv_value", "write_dotenv"]

_INLINE_COMMENT = re.compile(r"\s+#")
_SAFE_VALUE = re.compile(r"^[^\s#'\"]+$")


def read_dotenv(path: Path | str) -> dict[str, str]:
    """读 ``KEY=VALUE`` 行（整行注释 / 行尾注释 / 成对引号剥除）。

    文件不存在或不可读 → 空字典——缺省路径下没有 ``.env`` 是常态，不是错误。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value.startswith("#"):
            value = ""
        elif value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            value = _INLINE_COMMENT.split(value, maxsplit=1)[0].strip()
        if key.strip():
            values[key.strip()] = value
    return values


def render_dotenv_value(value: str) -> str:
    """把取值渲染成 ``.env`` 里的右值；含空白 / ``#`` 时用双引号包起来。"""
    if "\n" in value or "\r" in value:
        raise BootstrapConfigError("配置值不得含换行")
    if _SAFE_VALUE.match(value):
        return value
    if '"' in value:
        raise BootstrapConfigError("配置值含双引号且需要加引号，无法安全写入 .env")
    return f'"{value}"'


def write_dotenv(path: Path | str, updates: Mapping[str, str]) -> Path:
    """把 ``updates`` 写进 ``.env``（保留其他行）；返回文件路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        original = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = ""
    except OSError as exc:
        raise BootstrapConfigError(f".env 不可读（{target}）：{exc}") from exc

    wanted = {str(key): str(value) for key, value in updates.items()}
    rendered = {key: render_dotenv_value(value) for key, value in wanted.items()}
    seen: set[str] = set()
    lines: list[str] = []
    for raw in original.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in rendered:
                lines.append(f"{key}={rendered[key]}")
                seen.add(key)
                continue
        lines.append(raw)
    missing = [key for key in rendered if key not in seen]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(f"{key}={rendered[key]}" for key in missing)
    text = "\n".join(lines).rstrip("\n") + "\n"

    temporary = target.with_name(f"{target.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        if os.name != "nt":
            os.chmod(target, 0o600)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BootstrapConfigError(f".env 写入失败（{target}）：{exc}") from exc
    return target
