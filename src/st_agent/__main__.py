"""生产运行入口 ``python -m st_agent``——**后端常驻主体**（[00 §1.1]；[D-076]）。

职责链：解析平台默认存储根 → [`build_m2_runtime`](../st_agent/app.py) 装配 L0–L4 →
`ui.serve(chat=…)` 起本机回环面 → 向 **stdout 输出单行机器可读握手**
（``event=ready`` / ``host`` / ``port`` / ``token`` / ``pid`` / ``root`` / ``version``），
它是桌面壳 [`T-UI-002.2`] 消费的唯一接口。UI 仍只经回环面取数——本入口**不提供**
任何壳专有通道。

三条收尾路径走**同一段优雅序列**（关服务 → 冲刷出网审计缓冲）：

1. **stdin 控制通道**（``--control-stdin``）——壳以管道投递 ``stop`` 或直接关掉管道
   （EOF）：**不依赖控制台**，故 Windows 上的 GUI 壳也能可靠地优雅停；
2. ``--parent-pid`` 看门狗 + 信号——父（壳）进程消失即自行退出，``SIGINT`` /
   ``SIGTERM`` / ``SIGBREAK`` 同走此路。**看门狗必需**：Windows 没有「子进程随父进程
   退出」的进程组语义，壳侧 ``terminate()`` 也非优雅；
3. 启动失败——stdout 仍出一行 ``event=error``（机器可读），细节进 stderr，退出码 2。

存储加密默认关（[D-073]/[D-074]），故免口令即可启动；**加密根**的主密码交互形态不在
本入口——未提供口令时显式失败（不静默降级为明文），口令只经环境变量传入
（``--passphrase-env``，不落 argv、不进日志）。
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
from pathlib import Path
from typing import Any, TextIO

from st_agent import __version__
from st_agent.app import build_m2_runtime
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.ui.server import serve

__all__ = [
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

_ROOT_ENV = "ST_AGENT_ROOT"
"""存储根的环境变量覆盖点（优先于平台惯例）。"""

_APP_DIR = "STAgent"
_LINUX_APP_DIR = "st-agent"
_PARENT_POLL_SECONDS = 1.0
"""看门狗轮询间隔：壳退出与后端退出之间的可见延迟上限。"""

_EXIT_STARTUP_FAILED = 2
_WIN_WAIT_TIMEOUT = 0x102


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


def _emit_error(stream: TextIO, reason: str, *, json_handshake: bool) -> None:
    if json_handshake:
        _emit(stream, json.dumps({"event": "error", "reason": reason}, ensure_ascii=False))
    else:
        _emit(stream, f"启动失败：{reason}")


# ───────────────────────── 运行 ─────────────────────────


def run_backend(
    *,
    root: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    dev: bool = False,
    passphrase: str | None = None,
    parent_pid: int | None = None,
    control_stdin: bool = False,
    json_handshake: bool = True,
    out: TextIO | None = None,
    market_query: Any = None,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    build_runtime: Callable[..., Any] = build_m2_runtime,
    serve_ui: Callable[..., Any] = serve,
    poll_seconds: float = _PARENT_POLL_SECONDS,
) -> int:
    """装配 → 起服务 → 输出握手 → 阻塞至收尾信号（返回进程退出码）。

    ``build_runtime`` / ``serve_ui`` 可注入（用例换确定性件；生产取真组合根与真服务）。
    启动失败一律：stdout 一行机器可读 ``event=error`` + stderr 追溯 + 退出码 2——
    壳据此显式报错，**不**出现「进程在、界面空」的静默态。
    """
    stream = sys.stdout if out is None else out
    feed = DeferredMarketQuery() if market_query is None else market_query
    try:
        runtime = build_runtime(
            root,
            passphrase,
            create=not Store.exists(root),
            market_query=feed,
            llm_env=llm_env,
            dotenv_path=dotenv_path,
        )
        running = serve_ui(host=host, port=port, dev=dev, chat=runtime.chat)
    except Exception as exc:                     # noqa: BLE001 —— 入口须给出机器可读失败行
        traceback.print_exc()
        _emit_error(stream, f"{type(exc).__name__}: {exc}", json_handshake=json_handshake)
        return _EXIT_STARTUP_FAILED

    stop = threading.Event()
    _install_signal_handlers(stop)
    if parent_pid:
        watch_parent(parent_pid, stop.set, poll_seconds=poll_seconds, alive=parent_alive)
    if control_stdin:
        watch_stdin(stop)
    _emit_ready(stream, running, Path(root), json_handshake=json_handshake)
    try:
        stop.wait()
    except KeyboardInterrupt:                    # 信号处理器不可用的场合（如非主线程）
        pass
    finally:
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
        "--passphrase-env", default=None, metavar="NAME",
        help="加密根的主密码取自该环境变量（不落 argv、不进日志）",
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
                f"环境变量 {args.passphrase_env} 未设置（主密码只经环境变量传入）",
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
        parent_pid=args.parent_pid,
        control_stdin=args.control_stdin,
        json_handshake=json_handshake,
    )


if __name__ == "__main__":                       # pragma: no cover - 手动入口
    raise SystemExit(main())
