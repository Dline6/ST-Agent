"""生产运行入口 ``python -m st_agent``——**后端常驻主体**（[00 §1.1]；[D-076]）。

职责链：解析平台默认存储根 → [`build_m4_runtime`](../st_agent/app.py) 装配 L0–L6 + 生态面 →
`ui.serve(chat=…)` 起本机回环面 → **起常驻驱动循环**（按 [`M4Runtime.tick`] 的节奏推进
主动服务与每周反思：调度 → 信号 → 升级链 → 日报 → 疲劳巡查 → 反思周报）→ 向 **stdout
输出单行机器可读握手**
（``event=ready`` / ``host`` / ``port`` / ``token`` / ``pid`` / ``root`` / ``version``）。
它是桌面壳 [`T-UI-002.2`] 消费的唯一接口：先 ``event=ready``，失败为 ``event=error``
（携机器可读 ``code``）。UI 仍只经回环面取数——本入口**不提供**任何壳专有通道。

**原生能力端口由本入口注入**（[`T-INT-004`] A4；[D-082] ③）：桌面通知渠道收的
``send(title, body)`` 取表现层的 `ui.shell.notify.send_notification`——本模块是**唯一**
允许同时 import ``app`` 与 ``ui`` 的地方（[`T-UI-002.1`]），故 `app` 自己不必（也不得）
持一份平台分支实现。

**常驻循环只决定「何时 tick」**：到点判定、去重、升级、越限全在 ``tick`` 内（[07 §5] 的
纯函数口径），故同一 ``(时刻, 留痕状态)`` 恒得同一结论，可离线复算；``--ambient-interval``
只影响「多久轮到一次」，不影响结论。``--ambient-interval 0`` 关掉循环（终端手跑用）。

**口令协商在就绪握手之前**（[00 §1.1]；[D-078]）：本根若需口令（加密根的**主密码**、
明文根的**凭据口令**，[02 §2.2 / §3]），入口先向 stdout 发 ``event=passphrase_required``
（``kind`` / ``attempt`` / ``root`` / 中性 ``reason``），再**阻塞读 stdin 一行**取值，
口令错误时有界重试（3 次）。口令**只经进程管道**传递——不落 argv、不落环境变量、不进日志。
三个开关分工：``--ask-passphrase``（开启协商）· ``--control-stdin``（停止词 / EOF 收尾）·
``--passphrase-env``（无交互路径，终端与自用）。协商期间 stdin **只当口令通道**、不解释停止词。

三条收尾路径走**同一段优雅序列**（停常驻循环 → 关服务 → 冲刷出网审计缓冲）：

1. **stdin 控制通道**（``--control-stdin``）——壳以管道投递 ``stop`` 或直接关掉管道
   （EOF）：**不依赖控制台**，故 Windows 上的 GUI 壳也能可靠地优雅停；
2. ``--parent-pid`` 看门狗 + 信号——父（壳）进程消失即自行退出，``SIGINT`` /
   ``SIGTERM`` / ``SIGBREAK`` 同走此路。**看门狗必需**：Windows 没有「子进程随父进程
   退出」的进程组语义，壳侧 ``terminate()`` 也非优雅；
3. 启动失败——stdout 仍出一行 ``event=error``（机器可读），细节进 stderr，退出码 2。

存储加密默认关（[D-073]/[D-074]），故**免口令**的新根不触发任何协商。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from st_agent import __version__
from st_agent.app import build_m4_runtime
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import (
    StorageCorruptionError,
    StorageOpenError,
    StoragePassphraseRequired,
    StorageSecretsLockedError,
    Store,
)
from st_agent.ui.server import serve
from st_agent.ui.shell.notify import send_notification

__all__ = [
    "AMBIENT_INTERVAL_SECONDS",
    "DeferredMarketQuery",
    "build_parser",
    "default_root",
    "flush_audit",
    "main",
    "parent_alive",
    "run_backend",
    "watch_parent",
    "watch_stdin",
]

AMBIENT_INTERVAL_SECONDS = 60.0
"""常驻驱动循环的轮询间隔（秒）——``tick`` 的判定精度到分钟，60 s 足够不误点；``0`` ＝关闭。"""

_ROOT_ENV = "ST_AGENT_ROOT"
"""存储根的环境变量覆盖点（优先于平台惯例）。"""

_APP_DIR = "STAgent"
_LINUX_APP_DIR = "st-agent"
_PARENT_POLL_SECONDS = 1.0
"""看门狗轮询间隔：壳退出与后端退出之间的可见延迟上限。"""

_AMBIENT_JOIN_SECONDS = 10.0
"""收尾时等待常驻驱动循环退出的上限（秒）——一轮 ``tick`` 可能正在跑，给它跑完的余地。"""

_EXIT_STARTUP_FAILED = 2
_WIN_WAIT_TIMEOUT = 0x102

_PASSPHRASE_ATTEMPTS = 3
"""口令允许的错误次数；第 ``_PASSPHRASE_ATTEMPTS + 1`` 次不再请求，直接显式失败。"""

_KIND_LABEL = {"main_passphrase": "主密码", "credentials": "凭据口令"}
"""口令种类（`StoragePassphraseRequired.kind`）的人可读标签。"""


# ───────────────────────── 市场取数面（延迟绑定） ─────────────────────────


class DeferredMarketQuery:
    """官方 Pack 取数面的**延迟绑定**适配（[02 §5] 的市场库经唯一 `Store` 属主落位）。

    `open_runtime` 把「解锁存储 + 装配运行时」作一体、**不接受**外部 `Store`，而官方
    Pack 执行器在**装配期**就要拿到取数源（缺它即 `OfficialPackLoadError`）。故入口先给
    存根，由组合根在 `Store` 属主就位后调 `bind(store)`（[`app._bind_market`]）补上——
    **单一 Store 属主**，不会出现两个实例各持一份清单副本、互相看不见对方的写入。

    实现 `query(sql, params=())` 与 `snapshot_id()`，即 L1 侧 `MarketQuerySource` 鸭子类型
    （测试面有同型件 `tests/integration/rig.MarketData`；两者各自服务生产与播种，不共用一个
    实现——播种件要能先在 `open_runtime` 之前建库，见其 GWT 面）。
    """

    def __init__(self) -> None:
        self._db: MarketDb | None = None

    def bind(self, store: Any) -> None:
        """绑到运行时的 `Store`（组合根在 `open_runtime` 返回后调用）。"""
        self._db = MarketDb(store)

    def query(self, sql: str, params: tuple = ()) -> Any:
        return self._require().query(sql, tuple(params))

    def snapshot_id(self) -> str:
        return self._require().snapshot_id()

    def _require(self) -> MarketDb:
        if self._db is None:
            raise RuntimeError("取数面未绑定（运行时尚未装配）")
        return self._db


# ───────────────────────── 平台默认根 ─────────────────────────


def default_root(
    env: Mapping[str, str] | None = None, system: str | None = None
) -> Path:
    """平台默认存储根（[02 §2.1] 物理组织由实现决定；`ST_AGENT_ROOT` 优先）。

    Windows ``%LOCALAPPDATA%\\STAgent`` · macOS ``~/Library/Application Support/STAgent`` ·
    其余 ``$XDG_DATA_HOME/st-agent``（缺省 ``~/.local/share/st-agent``）。
    """
    env = os.environ if env is None else env
    system = sys.platform if system is None else system
    override = str(env.get(_ROOT_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    home = Path(str(env.get("HOME") or env.get("USERPROFILE") or Path.home()))
    if system.startswith("win"):
        local = str(env.get("LOCALAPPDATA") or "").strip()
        return (Path(local) if local else home / "AppData" / "Local") / _APP_DIR
    if system == "darwin":
        return home / "Library" / "Application Support" / _APP_DIR
    xdg = str(env.get("XDG_DATA_HOME") or "").strip()
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / _LINUX_APP_DIR


# ───────────────────────── 父进程看门狗 ─────────────────────────


def parent_alive(pid: int) -> bool:
    """``pid`` 是否仍在运行（只探测，**不发任何信号**）。

    Windows 上**不得**用 ``os.kill(pid, 0)``——CPython 在该平台把它实现为
    ``OpenProcess`` + ``TerminateProcess``，等于**把父进程杀掉**。故走 kernel32 的
    ``WaitForSingleObject(handle, 0)``：仍运行 ⇒ ``WAIT_TIMEOUT``。
    """
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # 存在但不属于本用户
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    query_limited = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(synchronize | query_limited, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == _WIN_WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def watch_parent(
    pid: int,
    on_gone: Callable[[], None],
    *,
    poll_seconds: float = _PARENT_POLL_SECONDS,
    alive: Callable[[int], bool] | None = None,
) -> threading.Thread:
    """起一个守护线程：``pid`` 消失即调 ``on_gone``（只调一次）。"""
    probe = parent_alive if alive is None else alive

    def loop() -> None:
        while True:
            time.sleep(poll_seconds)
            if not probe(pid):
                on_gone()
                return

    thread = threading.Thread(target=loop, name="st-agent-parent-watch", daemon=True)
    thread.start()
    return thread


_STOP_WORDS = frozenset({"stop", "quit", "exit"})


def watch_stdin(stop: threading.Event, *, stream: Any = None) -> threading.Thread:
    """监听父（壳）经 **stdin** 投递的停止指令：读到 ``stop`` / ``quit`` / ``exit``
    或读到 **EOF** 即优雅收尾。

    壳↔后端的收尾通道因此**不依赖控制台**：Windows 上桌面壳没有控制台，
    ``CTRL_BREAK_EVENT`` 一类控制台信号送不到子进程；父进程若被强杀，管道关闭带来的
    EOF 同样会走到这里。只在 ``--control-stdin`` 下启用——终端里手动跑不应抢走输入。
    """
    source = sys.stdin if stream is None else stream

    def loop() -> None:
        try:
            for line in source:
                if str(line).strip().lower() in _STOP_WORDS:
                    break
        except (OSError, ValueError):
            pass
        stop.set()

    thread = threading.Thread(target=loop, name="st-agent-stdin-watch", daemon=True)
    thread.start()
    return thread


# ───────────────────────── 收尾 ─────────────────────────


def flush_audit(runtime: Any) -> None:
    """冲刷出网审计缓冲（[02 §6] 分段日志靠它成段，缓冲里的记录不随退出丢失）。

    ``runtime`` 取 :class:`~st_agent.app.M2Runtime` 或 :class:`~st_agent.app.M1Runtime`
    （两者的 ``.runtime.gateway`` 同形）；缺网关即不动。
    """
    m1 = getattr(runtime, "m1", runtime)
    gateway = getattr(getattr(m1, "runtime", None), "gateway", None)
    flush = getattr(gateway, "flush_audit", None)
    if callable(flush):
        flush()


# ───────────────────────── 生产组合根与常驻驱动 ─────────────────────────


def build_ambient_runtime(root: Path | str, passphrase: str, **kwargs: Any) -> Any:
    """生产组合根（M4）+ **原生通知端口**（表现层实现）。

    入口是**唯一**可同时 import ``app`` 与 ``ui`` 的模块（[`T-UI-002.1`]），故原生能力的
    接线在此完成——``app`` 自己不 import 表现层（层序表里没有 ``ui``，[D-060] ⑤），
    而平台命令构造保持**单一份实现**（[D-082] ③ 否决「组合根自持第二份」）。

    M4 起装的是 [`build_m4_runtime`]（L0–L5 + L6 反思演进 + ECO 生态面；[`T-INT-005`]）——
    常驻循环由此也推进**每周反思**（到点投出周报），见 [`M4Runtime.tick`]。
    """
    return build_m4_runtime(root, passphrase, notify=send_notification, **kwargs)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def ambient_loop(
    runtime: Any,
    stop: threading.Event,
    *,
    interval: float = AMBIENT_INTERVAL_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> threading.Thread | None:
    """起一个守护线程推进主动服务（[00 §5] 步 4–7）。

    循环体只做一件事：调 ``runtime.tick(now)``——判定全在 ``tick`` 内，故「跑多久」不影响
    结论。运行时**没有** ``tick`` 面（M1 / M2 组合根）或 ``interval <= 0`` 即不起线程，
    **不假装有主动服务**。

    ``tick`` 抛错**不吞**：整条追溯进 stderr（可见），循环继续——一次缺陷不该让常驻服务
    静默停摆，也不该被略过（[00 §6] 失败显式化）。
    """
    tick = getattr(runtime, "tick", None)
    if not callable(tick) or interval <= 0:
        return None
    reader = _local_now if clock is None else clock

    def loop() -> None:
        while True:
            try:
                tick(reader())
            except Exception:                       # noqa: BLE001 —— 显式打出追溯，不静默
                traceback.print_exc()
            if stop.wait(interval):
                return

    thread = threading.Thread(target=loop, name="st-agent-ambient", daemon=True)
    thread.start()
    return thread


def _install_signal_handlers(stop: threading.Event) -> None:
    """把 SIGINT / SIGTERM / SIGBREAK 一律折到 ``stop``（平台不支持的信号跳过）。"""

    def handler(_signum: int, _frame: Any) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):      # 非主线程 / 平台不支持
            continue


# ───────────────────────── 就绪 / 失败行 ─────────────────────────


def _emit(stream: TextIO, text: str) -> None:
    print(text, file=stream, flush=True)


def _client_url(running: Any) -> str:
    """带令牌的客户端地址（令牌走 URL fragment——不进服务端、不进 Referer）。"""
    return f"{running.base_url}/#t={running.token}"


def _emit_ready(stream: TextIO, running: Any, root: Path, *, json_handshake: bool) -> None:
    if json_handshake:
        _emit(stream, json.dumps({
            "event": "ready",
            "host": running.host,
            "port": running.port,
            "token": running.token,
            "pid": os.getpid(),
            "root": str(root),
            "version": __version__,
        }, ensure_ascii=False))
    else:
        _emit(stream, f"本机界面：{_client_url(running)}")
        _emit(stream, f"存储根：{root}　进程：{os.getpid()}　按 Ctrl-C 退出。")


def _emit_error(
    stream: TextIO, reason: str, *, code: str, json_handshake: bool
) -> None:
    """失败行。``code`` 供壳**机器可读**地分辨「需口令 / 口令超限 / 存储故障」（键集合可增不可改义）。"""
    if json_handshake:
        _emit(stream, json.dumps(
            {"event": "error", "reason": reason, "code": code}, ensure_ascii=False))
    else:
        _emit(stream, f"启动失败：{reason}")


# ───────────────────────── 口令协商（就绪握手之前） ─────────────────────────


@dataclass(frozen=True)
class _StartupFailure:
    """就绪握手之前的启动失败。

    ``exc`` 非空即「非口令类的真错误」——由调用方打完整追溯；口令类失败（未开协商 /
    连续给错 / 取消）不带追溯，它们不是缺陷而是**用户状态**。
    """

    code: str
    reason: str
    exc: BaseException | None = None


def _read_stdin_passphrase() -> str | None:
    """从 stdin 读一行口令；**EOF / 空行** ⇒ ``None``（＝没给，按取消处理）。

    只剥行终止符、**不 strip 内容**——口令的首尾空白是内容的一部分。空行当作「没打算
    输入」而非「空口令」：存储侧本就不接受空口令（[02 §2.2]）。
    """
    try:
        line = sys.stdin.readline()
    except (OSError, ValueError):
        return None
    if line == "":                       # EOF：壳关掉管道或壳被强杀
        return None
    line = line.rstrip("\r\n")
    return line if line.strip() else None


def _ask_passphrase(
    stream: TextIO, kind: str, attempt: int, root: Path, *, json_handshake: bool
) -> None:
    """请对方给口令（``event=passphrase_required``）。

    ``reason`` 取**中性**措辞：只说「需要什么口令」，不回显「口令错」与「结构异常」的
    区别——那会把可用的枚举信息递给对方，也让提示话术分叉。
    """
    label = _KIND_LABEL.get(kind, kind)
    reason = f"需要{label}：解锁本地存储的凭据分区（凭据恒加密，02 §2.2）"
    if json_handshake:
        _emit(stream, json.dumps({
            "event": "passphrase_required",
            "kind": kind,
            "attempt": attempt,
            "root": str(root),
            "reason": reason,
        }, ensure_ascii=False))
    else:
        _emit(stream, f"{label}（第 {attempt} 次；直接回车取消）：")


def _build_with_passphrase(
    *,
    root: Path,
    passphrase: str | None,
    ask_passphrase: bool,
    stream: TextIO,
    json_handshake: bool,
    build_runtime: Callable[..., Any],
    feed: Any,
    llm_env: Mapping[str, str] | None,
    dotenv_path: Path | str | None,
    read_passphrase: Callable[[], str | None],
) -> tuple[Any | None, _StartupFailure | None]:
    """装配运行时；遇「需口令」即经 stdout 索取 + 读管道，**有界重试**。

    **两处触发**，缺一不可：

    ① **开局只读探测**（:meth:`Store.passphrase_requirement`）——「已知需要口令」的根
    （加密根；或**凭据分区已建立**的明文根）即使装配过得去，也会在**第一次读凭据**时
    撞上未解锁；故先索取、让口令在 `open` 时就位。
    ② **装配期异常**——凭据分区**尚未建立**时探测说不出「要口令」（装配才决定要不要写
    凭据），由 :class:`StorageSecretsLockedError` / :class:`StoragePassphraseRequired`
    触发，且给错口令也走这里重试。

    **只对**这两族重试：keyfile 损坏 / 格式标记非法 / 分区损坏等一律立即失败——把必然
    失败的装配重试三遍只会把报错拖后，并让用户以为是口令问题。

    ``create`` **逐次重算**（``not Store.exists(root)``）：首次为「明文新根」建的根，
    到重试时已存在——沿用首次的 ``True`` 会让重试撞上 `Store.create` 的「已有存储」拒绝，
    把一次可恢复的口令失败变成一条假的存储故障。

    口令错误时，口令**不留痕**：`reason` 只给中性措辞（不含所给口令、不含校验细节）。
    """
    given = passphrase
    attempt = 0

    def ask(kind: str) -> str | None:
        nonlocal attempt
        attempt += 1
        _ask_passphrase(stream, kind, attempt, root, json_handshake=json_handshake)
        return read_passphrase()

    def refusal(kind: str) -> _StartupFailure:
        label = _KIND_LABEL.get(kind, kind)
        if not ask_passphrase:
            return _StartupFailure(
                "passphrase_required",
                f"本根需要{label}，但未开启口令交互：加 --ask-passphrase（经管道回传）"
                "或 --passphrase-env（经环境变量提供）",
            )
        return _StartupFailure(
            "passphrase_unavailable",
            f"{label}连续错误 {_PASSPHRASE_ATTEMPTS} 次，已放弃启动"
            "（存储未被改动、未降级为明文）",
        )

    # ① 开局探测（只读）：已知需要口令的根先索取，别把失败推到第一次读凭据时
    try:
        need = Store.passphrase_requirement(root)
    except StorageOpenError as exc:
        return None, _StartupFailure("storage_error", str(exc), exc)
    if need is not None and given is None:
        if not ask_passphrase:
            return None, refusal(need)
        line = ask(need)
        if line is None:
            return None, _StartupFailure("passphrase_required", "未提供口令，已取消启动")
        given = line

    while True:
        try:
            runtime = build_runtime(
                root, given, create=not Store.exists(root), market_query=feed,
                llm_env=llm_env, dotenv_path=dotenv_path,
            )
            return runtime, None
        except StorageSecretsLockedError:
            kind = "credentials"          # 明文根的凭据分区尚未解锁（02 §2.2）
        except StoragePassphraseRequired as exc:
            kind = exc.kind
        except StorageCorruptionError as exc:
            return None, _StartupFailure("storage_corruption", str(exc), exc)
        except StorageOpenError as exc:
            return None, _StartupFailure("storage_error", str(exc), exc)
        except Exception as exc:          # noqa: BLE001 —— 入口须给出机器可读失败行
            return None, _StartupFailure(
                "startup_failed", f"{type(exc).__name__}: {exc}", exc
            )

        if not ask_passphrase or attempt >= _PASSPHRASE_ATTEMPTS:
            return None, refusal(kind)
        line = ask(kind)
        if line is None:
            return None, _StartupFailure("passphrase_required", "未提供口令，已取消启动")
        given = line


# ───────────────────────── 运行 ─────────────────────────


def run_backend(
    *,
    root: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    dev: bool = False,
    passphrase: str | None = None,
    ask_passphrase: bool = False,
    parent_pid: int | None = None,
    control_stdin: bool = False,
    json_handshake: bool = True,
    out: TextIO | None = None,
    market_query: Any = None,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    build_runtime: Callable[..., Any] = build_ambient_runtime,
    serve_ui: Callable[..., Any] = serve,
    poll_seconds: float = _PARENT_POLL_SECONDS,
    ambient_interval: float = AMBIENT_INTERVAL_SECONDS,
    read_passphrase: Callable[[], str | None] | None = None,
) -> int:
    """装配 → 起服务 → 输出握手 → 起常驻驱动 → 阻塞至收尾信号（返回进程退出码）。

    ``build_runtime`` / ``serve_ui`` / ``read_passphrase`` 可注入（用例换确定性件；生产取
    真组合根、真服务与真 stdin）。本根需口令时**先协商再装配**（[00 §1.1]；[D-078]）；
    启动失败一律：stdout 一行机器可读 ``event=error``（携 ``code``）+ 真错误进 stderr 追溯
    + 退出码 2——壳据此显式报错，**不**出现「进程在、界面空」的静默态。

    ``ambient_interval`` 是常驻驱动循环的轮询间隔（``0`` ＝不起循环；见 :func:`ambient_loop`）。
    """
    stream = sys.stdout if out is None else out
    feed = DeferredMarketQuery() if market_query is None else market_query
    runtime, failure = _build_with_passphrase(
        root=Path(root),
        passphrase=passphrase,
        ask_passphrase=ask_passphrase,
        stream=stream,
        json_handshake=json_handshake,
        build_runtime=build_runtime,
        feed=feed,
        llm_env=llm_env,
        dotenv_path=dotenv_path,
        read_passphrase=(_read_stdin_passphrase if read_passphrase is None
                         else read_passphrase),
    )
    if failure is not None:
        if failure.exc is not None:
            traceback.print_exception(failure.exc)
        _emit_error(stream, failure.reason, code=failure.code,
                    json_handshake=json_handshake)
        return _EXIT_STARTUP_FAILED
    try:
        running = serve_ui(
            host=host,
            port=port,
            dev=dev,
            chat=runtime.chat,
            # L6 / ECO 面经**鸭子端口**注入（[`T-UI-004.1`] / [`.2`]）：表现层不 import `app`，
            # 也不 import 各层类型，只经这两个口消费。注入的是组合根造好的**适配面**
            # （`M4Runtime.reflection`），而非裸的 L6Stack——输入的归一与失败分类都留在
            # 组合根，表现层只按 `ValueError` / 其他异常两类回 `validation_failed` / `failed`。
            # 组合根未带该面（如 M1/M2 根）时传 `None` ⇒ 相关端点回 `unavailable` + 点名，
            # 而不是启动失败。
            reflection=getattr(runtime, "reflection", None),
            eco=getattr(runtime, "ecosystem", None),
        )
    except Exception as exc:                     # noqa: BLE001 —— 入口须给出机器可读失败行
        traceback.print_exc()
        _emit_error(stream, f"{type(exc).__name__}: {exc}", code="serve_failed",
                    json_handshake=json_handshake)
        return _EXIT_STARTUP_FAILED

    stop = threading.Event()
    _install_signal_handlers(stop)
    if parent_pid:
        watch_parent(parent_pid, stop.set, poll_seconds=poll_seconds, alive=parent_alive)
    if control_stdin:
        watch_stdin(stop)
    _emit_ready(stream, running, Path(root), json_handshake=json_handshake)
    ambient = ambient_loop(runtime, stop, interval=ambient_interval)
    try:
        stop.wait()
    except KeyboardInterrupt:                    # 信号处理器不可用的场合（如非主线程）
        pass
    finally:
        if ambient is not None:
            stop.set()                           # 循环的 stop.wait 立即返回
            ambient.join(timeout=_AMBIENT_JOIN_SECONDS)
        running.shutdown()
        flush_audit(runtime)
        if json_handshake:
            _emit(stream, json.dumps({"event": "stopped"}, ensure_ascii=False))
        else:
            _emit(stream, "已退出。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI 面（壳只依赖 ``--root`` / ``--handshake json`` / ``--parent-pid`` 三个）。"""
    parser = argparse.ArgumentParser(
        prog="python -m st_agent",
        description="ST Agent 后端常驻主体（UI 是它的客户端，只经本机回环面取数）",
    )
    parser.add_argument(
        "--root", default=None,
        help=f"存储根（缺省按平台惯例；环境变量 {_ROOT_ENV} 优先）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址（只应绑回环）")
    parser.add_argument("--port", type=int, default=0, help="0 = 由 OS 分配")
    parser.add_argument("--dev", action="store_true", help="启用 dev 面（发布构建不含）")
    parser.add_argument(
        "--handshake", choices=("json", "human"), default="human",
        help="就绪行形态：桌面壳传 json（默认给终端看）",
    )
    parser.add_argument(
        "--parent-pid", type=int, default=None,
        help="父进程 pid：其消失后本进程优雅退出（壳传入）",
    )
    parser.add_argument(
        "--control-stdin", action="store_true",
        help="监听 stdin 的停止指令 / EOF（壳以管道投递；终端手动运行不要开）",
    )
    parser.add_argument(
        "--ask-passphrase", action="store_true",
        help="本根需口令时经 stdout 索取、经 stdin 回传（壳传入；口令不落 argv / 环境变量）",
    )
    parser.add_argument(
        "--passphrase-env", default=None, metavar="NAME",
        help="口令取自该环境变量（无交互路径：终端 / 自用；不落 argv、不进日志）",
    )
    parser.add_argument(
        "--ambient-interval", type=float, default=AMBIENT_INTERVAL_SECONDS,
        metavar="SECONDS",
        help="常驻驱动循环的轮询间隔（秒；0 = 不起循环，只为界面服务）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    json_handshake = args.handshake == "json"

    passphrase = None
    if args.passphrase_env:
        passphrase = os.environ.get(args.passphrase_env)
        if passphrase is None:
            _emit_error(
                sys.stdout,
                f"环境变量 {args.passphrase_env} 未设置（口令只经环境变量传入）",
                code="passphrase_required",
                json_handshake=json_handshake,
            )
            return _EXIT_STARTUP_FAILED

    root = Path(args.root).expanduser() if args.root else default_root()
    return run_backend(
        root=root,
        host=args.host,
        port=args.port,
        dev=args.dev,
        passphrase=passphrase,
        ask_passphrase=args.ask_passphrase,
        parent_pid=args.parent_pid,
        control_stdin=args.control_stdin,
        json_handshake=json_handshake,
        ambient_interval=args.ambient_interval,
    )


if __name__ == "__main__":                       # pragma: no cover - 手动入口
    raise SystemExit(main())
