"""用户文件面：工作区分区与导入收件目录（T-AGT-014.2 / T-AGT-014.3）。

两个区域，路径一律写成 ``<area>/<相对路径>``：

- ``workspace/<rel>`` —— [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的
  ``workspace`` 分区的文件（Agent 产出与用户交给 Agent 处理的文件）。读写都经
  ``Store``，故随存储加密、进一键备份、可独立清空。
- ``inbox/<rel>`` —— [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的**导入收件
  目录**（存储根下的 ``inbox/``，用户把待处理文件放这里）。只读；写入一律落
  ``workspace``（[D-146](../../项目管理/决策日志.md)）。

边界：

- **只接受相对 POSIX 路径**——绝对路径、盘符、``..`` 逃逸、NUL 一律拒；
  反斜杠按分隔符归一化。
- **收件目录按 realpath 前缀判界**（与表现层同一套判法）：解析后的真实路径必须
  仍在收件目录内，否则拒绝（防符号链接越界）。
- **不臆造内容**：文件缺失 → :class:`FileAccessError`，由原语映射成显式信封；
  二进制内容不作文本冒充（:meth:`FileWorkspace.read_text` 如实标注）。

本模块只管文件面，不做权限判断——``local_read`` / ``local_write`` 的核对在
L1 沙箱（01 §10），原语经 ``ctx.sandbox`` 调用本模块。
"""

from __future__ import annotations

import fnmatch
import hashlib
import posixpath
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = [
    "AREAS",
    "INBOX_DIR_NAME",
    "BinaryFileError",
    "FileAccessError",
    "FileWorkspace",
]

INBOX_DIR_NAME = "inbox"
"""导入收件目录名（存储根下；与 [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 同一常量）。"""

AREAS: tuple[str, ...] = ("workspace", "inbox")
"""文件面的两个区域。"""

_DRIVE_RE = re.compile(r"^[A-Za-z]:")



class FileAccessError(Exception):
    """文件面拒绝：路径非法 / 越界 / 不存在 / 不可读。"""


class BinaryFileError(FileAccessError):
    """文件存在但不是 UTF-8 文本（没有可交付的文本视图）。"""


class FileWorkspace:
    """工作区分区与导入收件目录的读写面。

    :param store: ``Store`` 句柄（工作区分区的读写入口）
    :param inbox_dir: 收件目录的落点；缺省取 ``<存储根>/inbox``
    """

    def __init__(self, store: Any, *, inbox_dir: Path | str | None = None) -> None:
        self._store = store
        self._inbox = (
            Path(inbox_dir) if inbox_dir is not None
            else Path(store.root) / INBOX_DIR_NAME
        )

    @property
    def inbox_dir(self) -> Path:
        """收件目录的落点（只读；表现层与组合根同取此值，不各写一份常量）。"""
        return self._inbox

    # ───────────────────────── 路径 ─────────────────────────

    def split(self, path: str) -> tuple[str, str]:
        """把 ``<area>/<rel>`` 拆成 ``(area, rel)``；非法即 :class:`FileAccessError`。"""
        if not isinstance(path, str) or not path.strip():
            raise FileAccessError("文件路径不得为空")
        raw = path.strip().replace("\\", "/")
        if "\x00" in raw:
            raise FileAccessError("文件路径含非法字符")
        if raw.startswith("/") or _DRIVE_RE.match(raw):
            raise FileAccessError(f"只接受工作区内的相对路径，拒绝绝对路径 {path!r}")
        norm = posixpath.normpath(raw)
        if norm in (".", "") or norm == ".." or norm.startswith("../"):
            raise FileAccessError(f"路径逃逸出工作区，拒绝 {path!r}")
        area, _, rel = norm.partition("/")
        if area not in AREAS or not rel:
            raise FileAccessError(
                f"路径须为 {' 或 '.join(f'{a}/<相对路径>' for a in AREAS)}，"
                f"得到 {path!r}"
            )
        return area, rel

    # ───────────────────────── 读 ─────────────────────────

    def read(self, path: str) -> bytes:
        """读文件原始字节；不存在即 :class:`FileAccessError`。"""
        area, rel = self.split(path)
        if area == "workspace":
            try:
                return self._store.get("workspace", rel)
            except KeyError as exc:
                raise FileAccessError(f"工作区内无此文件：{path!r}") from exc
        target = self._inbox_target(rel)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise FileAccessError(f"收件目录内无此文件：{path!r}") from exc
        except OSError as exc:
            raise FileAccessError(f"收件目录文件不可读：{path!r}（{exc}）") from exc

    def read_text(self, path: str) -> tuple[str, bytes]:
        """读文本；返回 ``(text, raw)``，非 UTF-8 文本抛 :class:`FileAccessError`。"""
        raw = self.read(path)
        try:
            return raw.decode("utf-8"), raw
        except UnicodeDecodeError as exc:
            raise BinaryFileError(
                f"{path!r} 不是 UTF-8 文本（{len(raw)} 字节，sha256="
                f"{hashlib.sha256(raw).hexdigest()[:16]}…）；本原语只读文本"
            ) from exc

    # ───────────────────────── 写 ─────────────────────────

    def write(self, path: str, data: bytes) -> int:
        """写工作区分区（覆盖写）；``inbox`` 只读，拒写。返回写入字节数。"""
        area, rel = self.split(path)
        if area != "workspace":
            raise FileAccessError(
                f"只写 workspace 分区；{area!r} 是只读的导入收件目录（D-146）"
            )
        self._store.put("workspace", rel, data)
        return len(data)

    # ───────────────────────── 列目录 / glob ─────────────────────────

    def list_files(self, pattern: str = "**", *, areas: Iterable[str] = AREAS) -> list[str]:
        """按 glob 列出文件（返回 ``<area>/<rel>``；只列普通文件）。

        ``pattern`` 同时匹配完整路径（``workspace/a/b.txt``）与区域内相对路径
        （``a/b.txt``），故 ``*.md`` 可命中任意层级——``fnmatch`` 的 ``*`` 也匹配
        ``/``，这里不为另造一套通配语法。
        """
        areas = tuple(areas)
        unknown = [a for a in areas if a not in AREAS]
        if unknown:
            raise FileAccessError(f"未知区域 {unknown}；合法区域 = {list(AREAS)}")
        out: list[str] = []
        for area in areas:
            for rel in self._files_of(area):
                full = f"{area}/{rel}"
                if fnmatch.fnmatchcase(full, pattern) or fnmatch.fnmatchcase(rel, pattern):
                    out.append(full)
        return sorted(out)

    def _files_of(self, area: str) -> tuple[str, ...]:
        if area == "workspace":
            return self._store.list_files("workspace")
        root = self._safe_inbox_root()
        if not root.is_dir():
            return ()
        found: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            found.append(rel)
        return tuple(sorted(found))

    # ───────────────────────── 内部工具 ─────────────────────────

    def _safe_inbox_root(self) -> Path:
        return self._inbox.resolve()

    def _inbox_target(self, rel: str) -> Path:
        """解析收件目录内路径，并核真实路径仍在收件目录内（防符号链接越界）。"""
        root = self._safe_inbox_root()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise FileAccessError(
                f"路径经真实路径解析后越出收件目录，拒绝 {rel!r}"
            ) from exc
        return target
