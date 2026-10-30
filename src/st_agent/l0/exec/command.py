"""一次性本地命令执行面（T-AGT-016.1；[01 §10](../../../docs/技术架构-v2/01-平台共享契约.md)）。

口径：

- **一次性、无交互**——``shell=True`` 交一次命令（Windows ``cmd /c`` / POSIX
  ``/bin/sh -c``），不提供 stdin 与 ``write_stdin``（本期明确不做交互式 shell）。
- **一次性工作目录**——每次执行在系统临时目录下造一个空目录作 cwd，执行后删除。
  **不把 ``workspace`` 分区交给外部进程**：分区内容受 ``Store`` 的清单 / 加密
  管理，外部写入会绕过清单；需要留存的产物由 Agent 用 ``sk_file_write`` 落盘。
- **超时终止**——超过 ``timeout_s`` 终止进程树（Windows ``taskkill /T``、POSIX
  进程组），结果显式标 ``timed_out``，**不把超时当正常输出**。
- **输出上限**——stdout / stderr 各自按 ``max_output_chars`` 截断（读取阶段即
  限字节，避免大输出撑爆内存），附 ``truncated``。
- **每次留痕**——给了 ``Store`` 即落 ``execution_log/exec-command/<run_id>.json``：
  命令文本（截断 2000 字符）、cwd、退出码、耗时、超时 / 截断标志、发起方与 trace。
  留痕只记计数，不记 stdout / stderr 正文。

**边界（如实声明）**：cwd 只是起始目录，**不是**文件系统 jail——命令进程仍可
访问当前用户可访问的路径。进程级隔离未实现，与 [02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
的 stdio MCP 子进程同一已知边界；权限与开关是**声明式**控制，不是 OS 级沙箱。
"""

from __future__ import annotations

import json
import locale
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from secrets import token_hex
from typing import Any

from st_agent.l0.exec.errors import CommandError

__all__ = [
    "COMMAND_LOG_PREFIX",
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT_S",
    "MAX_OUTPUT_CHARS",
    "MAX_TIMEOUT_S",
    "CommandResult",
    "CommandRunner",
    "spawn_shell",
]

DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0
DEFAULT_MAX_OUTPUT_CHARS = 20_000
MAX_OUTPUT_CHARS = 50_000

COMMAND_LOG_PREFIX = "exec-command/"
"""``execution_log`` 分区内的执行留痕前缀。"""

_COMMAND_LOG_LIMIT = 2_000
"""留痕里命令文本的上限（字符）。"""

_BYTES_PER_CHAR = 4
"""读取阶段按「字符数 × 4 字节」限流（UTF-8 最坏情形），避免大输出整块进内存。"""


@dataclass(frozen=True)
class CommandResult:
    """一次命令执行的可观察结果。"""

    run_id: str
    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_chars: int
    stderr_chars: int
    truncated: bool
    timed_out: bool
    duration_ms: int


@dataclass(frozen=True)
class _Output:
    """执行器的原始产出（``spawn`` 的返回形态；便于测试注入）。"""

    exit_code: int | None
    stdout: str
    stderr: str
    stdout_chars: int
    stderr_chars: int
    truncated: bool
    timed_out: bool
    duration_ms: int


Spawn = Callable[[str, str, float, int], _Output]
"""``(command, cwd, timeout_s, max_output_chars) -> _Output``。"""


def _decode(raw: bytes) -> str:
    """按本机首选编码解码子进程输出；非法字节替换，不抛。"""
    try:
        encoding = locale.getpreferredencoding(False) or "utf-8"
    except Exception:  # noqa: BLE001 —— 取不到编码就退 UTF-8，不让解码问题打断执行
        encoding = "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _read_limited(stream: Any, max_chars: int) -> tuple[str, int, bool]:
    """读一个临时输出文件，最多取 ``max_chars`` 字符；返回 ``(文本, 字符数, 是否截断)``。"""
    limit = max_chars * _BYTES_PER_CHAR + 1
    stream.seek(0)
    raw = stream.read(limit)
    text = _decode(raw)
    truncated = len(raw) >= limit or len(text) > max_chars
    return text[:max_chars], min(len(text), max_chars), truncated


def _terminate(proc: subprocess.Popen) -> None:
    """终止进程树：Windows 走 ``taskkill /T``，POSIX 走进程组 ``SIGKILL``。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False, timeout=10,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def spawn_shell(
    command: str, cwd: str, timeout_s: float, max_output_chars: int
) -> _Output:
    """缺省执行器：一次性 shell 命令 + 超时终止 + 输出上限。"""
    started = time.monotonic()
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        try:
            proc = subprocess.Popen(
                command, cwd=cwd, shell=True, stdin=subprocess.DEVNULL,
                stdout=out_file, stderr=err_file, **kwargs,
            )
        except OSError as exc:
            raise CommandError(f"命令无法启动：{exc}") from exc
        timed_out = False
        try:
            exit_code: int | None = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate(proc)
            exit_code = proc.wait()
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, out_chars, out_cut = _read_limited(out_file, max_output_chars)
        stderr, err_chars, err_cut = _read_limited(err_file, max_output_chars)
    return _Output(
        exit_code=exit_code,
        stdout=stdout, stderr=stderr,
        stdout_chars=out_chars, stderr_chars=err_chars,
        truncated=out_cut or err_cut,
        timed_out=timed_out, duration_ms=duration_ms,
    )


class CommandRunner:
    """命令执行器 + 每次执行的留痕面。

    :param store: 可选 ``Store`` 句柄——给了则每次执行落一条
        ``execution_log/exec-command/<run_id>.json``；不给则只执行不留痕。
    :param spawn: 可选执行函数（缺省 :func:`spawn_shell`；测试注入替身）。
    :param clock: 取时函数（测试注入固定时钟；缺省本机当前时刻）。
    """

    def __init__(
        self, store: Any = None, *, spawn: Spawn | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._spawn = spawn_shell if spawn is None else spawn
        self._clock = clock or (lambda: datetime.now().astimezone())

    def run(
        self,
        command: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        initiator: str = "",
        trace_id: str | None = None,
    ) -> CommandResult:
        """执行一次命令并（有 ``Store`` 时）留痕。"""
        if not isinstance(command, str) or not command.strip():
            raise CommandError("命令不得为空")
        timeout = min(max(float(timeout_s), 0.1), MAX_TIMEOUT_S)
        max_chars = min(max(int(max_output_chars), 1), MAX_OUTPUT_CHARS)
        now = self._clock()
        run_id = f"{int(now.timestamp() * 1000)}-{token_hex(4)}"
        cwd = tempfile.mkdtemp(prefix="st-agent-exec-")
        try:
            output = self._spawn(command, cwd, timeout, max_chars)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        result = CommandResult(
            run_id=run_id, command=command, cwd=cwd,
            exit_code=output.exit_code, stdout=output.stdout, stderr=output.stderr,
            stdout_chars=output.stdout_chars, stderr_chars=output.stderr_chars,
            truncated=output.truncated, timed_out=output.timed_out,
            duration_ms=output.duration_ms,
        )
        self._record(result, initiator=initiator, trace_id=trace_id)
        return result

    def _record(
        self, result: CommandResult, *, initiator: str, trace_id: str | None
    ) -> None:
        if self._store is None:
            return
        payload = {
            "run_id": result.run_id,
            "command": result.command[:_COMMAND_LOG_LIMIT],
            "command_truncated": len(result.command) > _COMMAND_LOG_LIMIT,
            "cwd": result.cwd,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "truncated": result.truncated,
            "stdout_chars": result.stdout_chars,
            "stderr_chars": result.stderr_chars,
            "duration_ms": result.duration_ms,
            "initiator": initiator,
            "trace_id": trace_id,
            "occurred_at": self._clock().isoformat(),
        }
        self._store.put(
            "execution_log", f"{COMMAND_LOG_PREFIX}{result.run_id}.json",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
