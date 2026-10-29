"""文件读写原语（T-AGT-014.2 / T-AGT-014.3；[01 §10](../../../docs/技术架构-v2/01-平台共享契约.md)、
[02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)、
[09 §6](../../../docs/技术架构-v2/09-生态与分享.md)、[D-146](../../../项目管理/决策日志.md)）。

四条原语，共用 L0 的 :class:`~st_agent.l0.file_workspace.FileWorkspace` 文件面：

- ``sk_file_read`` / ``sk_file_glob`` / ``sk_file_grep`` —— 只读；声明
  ``local_read:<workspace/**>`` 与 ``local_read:<inbox/**>``。
- ``sk_file_write`` —— 落 ``workspace`` 分区；声明 ``local_write:<workspace/**>``。

口径：

- **权限即范围**——原语不另造路径判据：每次访问都经 ``ctx.sandbox`` 核对声明，
  越界 / 逃逸 / 未批准一律拦截且**不读取 / 不写入**（01 §10）。
- **收件目录只读**——``inbox`` 是用户交付文件的入口（09 §6），写只落
  ``workspace`` 分区（D-146）。
- **大文件截断并给指针**——``max_chars`` 之外的部分不静默丢，回 ``truncated`` 与
  ``next_offset``，可续读。
- **不冒充**——二进制文件不返回伪文本；文件缺失回 ``empty``、二进制回
  ``unavailable``，各带原因。
- **串行化**——写入经 ``Store`` 的分区锁（02 §2.4），并发写同一分区不会丢清单条目。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.file_workspace import (
    AREAS,
    BinaryFileError,
    FileAccessError,
    FileWorkspace,
)
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_CHARS",
    "FILE_GLOB",
    "FILE_GREP",
    "FILE_READ",
    "FILE_WRITE",
    "MAX_LIMIT",
    "MAX_LINE_CHARS",
    "READ_PERMISSIONS",
    "WRITE_PERMISSIONS",
]

READ_PERMISSIONS: tuple[str, ...] = (
    "local_read:<workspace/**>",
    "local_read:<inbox/**>",
)
"""读取面声明（工作区分区 + 导入收件目录）。"""

WRITE_PERMISSIONS: tuple[str, ...] = ("local_write:<workspace/**>",)
"""写入面声明（只落工作区分区）。"""

DEFAULT_MAX_CHARS = 20000
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_LINE_CHARS = 300
MAX_GREP_FILE_BYTES = 4 * 1024 * 1024

_READ_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer"},
        "max_chars": {"type": "integer"},
    },
    "required": ["path"],
}

_READ_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "area": {"type": "string"},
        "text": {"type": "string"},
        "chars": {"type": "integer"},
        "bytes": {"type": "integer"},
        "sha256": {"type": "string"},
        "truncated": {"type": "boolean"},
        "next_offset": {"type": "integer"},
    },
}

_LIST_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "area": {"type": "string"},
        "limit": {"type": "integer"},
    },
}

_LIST_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "paths": {"type": "array", "items": {"type": "string"}},
        "count": {"type": "integer"},
        "truncated": {"type": "boolean"},
    },
}

_GREP_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "area": {"type": "string"},
        "limit": {"type": "integer"},
        "ignore_case": {"type": "boolean"},
    },
    "required": ["pattern"],
}

_GREP_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "matches": {"type": "array"},
        "count": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "scanned": {"type": "integer"},
        "skipped_binary": {"type": "integer"},
        "skipped_large": {"type": "integer"},
    },
}

_WRITE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["path", "content"],
}

_WRITE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "area": {"type": "string"},
        "bytes": {"type": "integer"},
    },
}


def _now() -> datetime:
    return datetime.now().astimezone()


def _files_of(context: Any) -> FileWorkspace:
    return context["files"]


def _inputs_of(ctx: Any) -> dict[str, Any]:
    return dict(getattr(ctx, "inputs", None) or {})


def _int_param(value: Any, *, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _areas_of(value: Any) -> tuple[str, ...]:
    area = str(value or "").strip()
    if not area or area == "all":
        return AREAS
    return (area,)


def _no_sandbox() -> ResultEnvelope:
    return ResultEnvelope.unavailable(
        "文件原语需要执行沙箱核对 local_read / local_write，当前未接线"
        "（T-AGT-014.2：组合根未把沙箱注入执行上下文）——本次未访问任何文件",
        last_updated_at=_now(),
    )


def _read_error(exc: FileAccessError) -> ResultEnvelope:
    if isinstance(exc, BinaryFileError):
        return ResultEnvelope.unavailable(str(exc), last_updated_at=_now())
    return ResultEnvelope.empty(str(exc))


# ───────────────────────── sk_file_read ─────────────────────────


def _read_factory(context: Any) -> Any:
    files = _files_of(context)

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        if ctx.sandbox is None:
            return _no_sandbox()
        inputs = _inputs_of(ctx)
        raw_path = str(inputs.get("path") or "").strip()
        try:
            area, rel = files.split(raw_path)
        except FileAccessError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        path = f"{area}/{rel}"
        offset = _int_param(inputs.get("offset"), default=0)
        max_chars = _int_param(inputs.get("max_chars"), default=DEFAULT_MAX_CHARS, minimum=1)

        def reader(_checked: str) -> dict[str, Any]:
            text, raw = files.read_text(path)
            total = len(text)
            start = min(offset, total)
            end = min(start + max_chars, total)
            return {
                "path": path,
                "area": area,
                "text": text[start:end],
                "chars": total,
                "bytes": len(raw),
                "truncated": end < total,
                "next_offset": end if end < total else total,
            }

        try:
            return ctx.sandbox.read_file(path, reader)
        except FileAccessError as exc:
            return _read_error(exc)

    return execute


# ───────────────────────── sk_file_glob ─────────────────────────


def _glob_factory(context: Any) -> Any:
    files = _files_of(context)

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        if ctx.sandbox is None:
            return _no_sandbox()
        inputs = _inputs_of(ctx)
        pattern = str(inputs.get("pattern") or "**").strip() or "**"
        limit = min(_int_param(inputs.get("limit"), default=DEFAULT_LIMIT, minimum=1), MAX_LIMIT)
        areas = _areas_of(inputs.get("area"))
        collected: list[str] = []
        for area in areas:
            try:
                collected.extend(files.list_files(pattern, areas=(area,)))
            except FileAccessError as exc:
                return ResultEnvelope.validation_failed(str(exc))
        collected = sorted(set(collected))
        rows = collected[:limit]

        def reader(_checked: str) -> dict[str, Any]:
            return {
                "pattern": pattern,
                "paths": rows,
                "count": len(rows),
                "truncated": len(collected) > len(rows),
            }

        # 区域根即声明范围的最外层；逐个区域经沙箱核对（未批准即整条拒绝）。
        for area in areas:
            env = ctx.sandbox.read_file(area, lambda _p: True)
            if env.status != "ok":
                return env
        return ResultEnvelope.ok(reader(""))

    return execute


# ───────────────────────── sk_file_grep ─────────────────────────


def _grep_factory(context: Any) -> Any:
    files = _files_of(context)

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        if ctx.sandbox is None:
            return _no_sandbox()
        inputs = _inputs_of(ctx)
        needle = str(inputs.get("pattern") or "")
        if not needle:
            return ResultEnvelope.validation_failed("检索关键词不得为空")
        limit = min(_int_param(inputs.get("limit"), default=DEFAULT_LIMIT, minimum=1), MAX_LIMIT)
        ignore_case = bool(inputs.get("ignore_case", False))
        areas = _areas_of(inputs.get("area"))
        for area in areas:
            env = ctx.sandbox.read_file(area, lambda _p: True)
            if env.status != "ok":
                return env

        matches: list[dict[str, Any]] = []
        scanned = skipped_binary = skipped_large = 0
        truncated = False
        hay = needle.lower() if ignore_case else needle
        for path in files.list_files("**", areas=areas):
            if len(matches) >= limit:
                truncated = True
                break
            try:
                raw = files.read(path)
            except FileAccessError:
                continue
            if len(raw) > MAX_GREP_FILE_BYTES:
                skipped_large += 1
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), start=1):
                target = line.lower() if ignore_case else line
                if hay in target:
                    matches.append({
                        "path": path,
                        "line": number,
                        "text": line[:MAX_LINE_CHARS],
                    })
                    if len(matches) >= limit:
                        truncated = True
                        break
        return ResultEnvelope.ok({
            "pattern": needle,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
            "scanned": scanned,
            "skipped_binary": skipped_binary,
            "skipped_large": skipped_large,
        })

    return execute


# ───────────────────────── sk_file_write ─────────────────────────


def _write_factory(context: Any) -> Any:
    files = _files_of(context)

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        if ctx.sandbox is None:
            return _no_sandbox()
        inputs = _inputs_of(ctx)
        raw_path = str(inputs.get("path") or "").strip()
        content = inputs.get("content")
        if not isinstance(content, str):
            return ResultEnvelope.validation_failed("content 须为字符串")
        try:
            area, rel = files.split(raw_path)
        except FileAccessError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        if area != "workspace":
            return ResultEnvelope.validation_failed(
                "只写 workspace 分区；inbox 是只读的导入收件目录（D-146）"
            )
        path = f"{area}/{rel}"
        data = content.encode("utf-8")

        def writer(_checked: str) -> dict[str, Any]:
            written = files.write(path, data)
            return {"path": path, "area": area, "bytes": written}

        try:
            return ctx.sandbox.write_file(path, writer)
        except FileAccessError as exc:
            return ResultEnvelope.validation_failed(str(exc))

    return execute


FILE_READ = BuiltinPrimitive(
    base="sk_file_read",
    name="读取工作区文件",
    description=(
        "读取工作区或导入收件目录中的文本文件，可指定起始位置与最大字符数；"
        "超出部分返回续读指针，不静默丢弃"
    ),
    input_schema=_READ_INPUT,
    output_schema=_READ_OUTPUT,
    permissions=READ_PERMISSIONS,
    offline_level="full",
    requires=("files",),
    executor=_read_factory,
)

FILE_GLOB = BuiltinPrimitive(
    base="sk_file_glob",
    name="列出工作区文件",
    description="按通配符列出工作区与导入收件目录中的文件路径（只列名，不读内容）",
    input_schema=_LIST_INPUT,
    output_schema=_LIST_OUTPUT,
    permissions=READ_PERMISSIONS,
    offline_level="full",
    requires=("files",),
    executor=_glob_factory,
)

FILE_GREP = BuiltinPrimitive(
    base="sk_file_grep",
    name="检索工作区文件",
    description=(
        "在工作区与导入收件目录的文本文件中按关键词逐行检索，返回文件、行号与行内容；"
        "二进制与超大文件跳过并计数"
    ),
    input_schema=_GREP_INPUT,
    output_schema=_GREP_OUTPUT,
    permissions=READ_PERMISSIONS,
    offline_level="full",
    requires=("files",),
    executor=_grep_factory,
)

FILE_WRITE = BuiltinPrimitive(
    base="sk_file_write",
    name="写入工作区文件",
    description=(
        "把文本内容写入工作区分区（覆盖同名文件）——产出落成文件交付；"
        "只写 workspace，不写导入收件目录与任意路径"
    ),
    input_schema=_WRITE_INPUT,
    output_schema=_WRITE_OUTPUT,
    permissions=WRITE_PERMISSIONS,
    offline_level="full",
    requires=("files",),
    executor=_write_factory,
)
