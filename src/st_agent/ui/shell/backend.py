"""壳侧的后端句柄：拉起后端 → 读[握手](T-UI-002.1-生产运行入口与壳后端握手协议.md)
→ 优雅回收。

壳与后端是**两个进程**（[00 §1.1]；[D-076]）：本模块只做进程生命周期，**不含任何业务
逻辑**；取数一律由窗口经回环面自行完成，壳不代理、不缓存、不注入桥。

两处与平台有关的事实写在这里而不散落别处：

- **后端以 ``--parent-pid`` 记下壳的 pid**：Windows 没有「子进程随父退出」的进程组语义，
  壳被强杀时后端须能自己走。
- **收尾走 stdin 控制通道**：GUI 壳没有控制台，控制台信号（Windows ``CTRL_BREAK_EVENT``、
  POSIX ``SIGTERM``）送不到无控制台的子进程；写 ``stop`` 或关掉管道则一定能。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from st_agent.ui.shell.errors import ShellError

__all__ = ["BackendHandle", "BackendSpec", "find_sibling_backend", "start_backend"]

_SERVER_NAME = "st-agent-server"
"""后端二进制的产物名（与 `packaging/st-agent-server.spec` 同名）。"""

_READY_TIMEOUT = 90.0
"""等就绪握手行的上限（冷启动含存储解锁与组合根装配）。"""

_STOP_TIMEOUT = 20.0
_STDERR_LINES = 200
"""子进程 stderr 的保留行数（**必须持续排空**：管道写满会卡住子进程）。"""


@dataclass(frozen=True)
class BackendSpec:
    """后端的启动口径。"""

    argv: tuple[str, ...]
    """后端命令。冻结产物传后端二进制路径；开发态缺省 ``python -m st_agent``。"""

    env: Mapping[str, str] | None = None
    """追加/覆盖的环境变量（缺省继承壳的环境）。"""

    ready_timeout: float = _READY_TIMEOUT

    @classmethod
    def default(cls, executable: str | os.PathLike[str] | None = None) -> "BackendSpec":
        """缺省后端：显式给了可执行文件就用它 → 冻结壳找**同级**后端二进制 → 开发态用解释器。

        冻结产物里 ``sys.executable`` 是**壳自己**，故 ``python -m st_agent`` 那条路在冻结态
        不成立（会变成拿壳当解释器）——所以要先按产物摆放惯例找后端。
        """
        if executable is not None:
            return cls(argv=(str(executable),))
        sibling = find_sibling_backend()
        if sibling is not None:
            return cls(argv=(str(sibling),))
        return cls(argv=(sys.executable, "-m", "st_agent"))


@dataclass
class BackendHandle:
    """一个已就绪的后端子进程（握手字段即后端给出的事实）。"""

    process: subprocess.Popen[str]
    host: str
    port: int
    token: str
    root: str
    version: str
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=_STDERR_LINES))

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def client_url(self) -> str:
        """窗口要打开的地址：令牌走 **URL fragment**（[`api.js`](../web/js/api.js) 的口径）。"""
        return f"{self.base_url}/#t={self.token}"

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self, timeout: float = _STOP_TIMEOUT) -> int:
        """优雅回收：stdin 投递 ``stop`` → 关管道 → 等；超时才强杀（并如实回报退出码）。"""
        if self.process.poll() is not None:
            return int(self.process.returncode or 0)
        stdin = self.process.stdin
        if stdin is not None:
            try:
                stdin.write("stop\n")
                stdin.flush()
                stdin.close()
            except (OSError, ValueError):
                pass
        try:
            return int(self.process.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            self.process.kill()
            return int(self.process.wait(timeout=timeout))


def find_sibling_backend(
    name: str = _SERVER_NAME,
    *,
    executable: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
) -> Path | None:
    """按**产物摆放惯例**找后端二进制：同目录，或「同级目录下的同名目录」。

    打包产物是两份 onedir（``dist/st-agent-shell/st-agent-shell.exe`` 与
    ``dist/st-agent-server/st-agent-server.exe``），故两条路都要认。开发态（未冻结且未显式
    给可执行文件）返回 ``None`` ⇒ 回落到 ``python -m st_agent``。
    """
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return None
    current = Path(executable or sys.executable)
    candidates = (
        current.with_name(f"{name}{current.suffix}"),
        current.parent.parent / name / f"{name}{current.suffix}",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def start_backend(
    spec: BackendSpec,
    *,
    parent_pid: int | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> BackendHandle:
    """拉起后端并等它的就绪握手；失败即抛 :class:`ShellError`（点名后端给的原因）。

    后端固定的三个开关：``--handshake json``（机器可读）· ``--control-stdin``（收尾通道）·
    ``--parent-pid``（壳消失时后端自走）。
    """
    command: list[str] = list(spec.argv)
    command += ["--handshake", "json", "--control-stdin"]
    if parent_pid is not None:
        command += ["--parent-pid", str(parent_pid)]
    env = dict(os.environ)
    if spec.env:
        env.update(spec.env)
    process = subprocess.Popen(
        command,
        cwd=None if cwd is None else str(cwd),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    tail: deque[str] = deque(maxlen=_STDERR_LINES)
    _drain_stderr(process, tail)
    payload = _read_handshake(process, spec.ready_timeout, tail)
    return BackendHandle(
        process=process,
        host=str(payload["host"]),
        port=int(payload["port"]),
        token=str(payload["token"]),
        root=str(payload["root"]),
        version=str(payload.get("version") or ""),
        stderr_tail=tail,
    )


def _drain_stderr(process: subprocess.Popen[str], sink: deque[str]) -> threading.Thread:
    """把子进程 stderr 持续排空到有界队列——不排空会把子进程卡死在写管道上。"""

    def loop() -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            for line in stream:
                sink.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=loop, name="st-agent-shell-stderr", daemon=True)
    thread.start()
    return thread


def _read_handshake(
    process: subprocess.Popen[str], timeout: float, tail: deque[str]
) -> dict[str, Any]:
    """读后端 stdout 的**首行**（就绪 / 失败都是它）。"""
    line = _readline_with_timeout(process, timeout)
    if line is None:
        process.kill()
        raise ShellError(
            f"后端在 {timeout:.0f}s 内未给出握手行；stderr：{' | '.join(list(tail)[-3:])}"
        )
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ShellError(f"后端握手行不是合法 JSON：{line!r}（{exc}）") from exc
    event = payload.get("event")
    if event != "ready":
        raise ShellError(f"后端启动失败：{payload.get('reason') or line!r}")
    missing = {"host", "port", "token", "pid", "root"} - set(payload)
    if missing:
        raise ShellError(f"后端握手缺字段：{sorted(missing)}")
    return payload


def _readline_with_timeout(
    process: subprocess.Popen[str], timeout: float
) -> str | None:
    """读一行，超时返 ``None``（``readline`` 自身没有超时，故借线程）。"""
    if process.stdout is None:      # pragma: no cover - Popen 恒给管道
        return None
    box: list[str] = []

    def read() -> None:
        line = process.stdout.readline()        # type: ignore[union-attr]
        if line:
            box.append(line)

    reader = threading.Thread(target=read, name="st-agent-shell-handshake", daemon=True)
    reader.start()
    reader.join(timeout)
    return box[0] if box else None
