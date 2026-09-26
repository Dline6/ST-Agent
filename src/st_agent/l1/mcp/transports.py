"""MCP 传输层（T-L1-002.1；03 §5.1「支持 stdio 与 HTTP/SSE 两种传输」）。

两种传输共用一层 JSON-RPC 2.0 收发骨架（``McpTransport``）：子类只要实现
``_write``（把一段报文发出去）与 ``_read``（取回指定 ``id`` 的响应文本），
其余（请求编号、``initialize`` 握手、通知、错误解包）由基类统一。

- ``StdioTransport``：本地子进程，行分隔 JSON（``stdout`` 由后台线程泵入队列，
  以便在不支持 ``select`` 于管道的平台上仍能带超时读取）。
- ``HttpSseTransport``：远程 HTTP-SSE。**出网一律经 L0 网关**（02 §6：
  「所有对外网络请求必须经统一网关执行并登记」，远程 MCP 属五类之一）——
  真实 POST 在按次 ``sender`` 闭包内完成，网关独占在线判定 / 审计落盘 /
  超时与取消职责；响应体经闭包带回（见任务文件假设 A2，沿用 T-L0-005
  注入按操作抓取闭包的先例）。

**协议面取最小子集**（任务文件假设 A1）：``initialize`` /
``notifications/initialized`` / ``tools/list`` / ``tools/call``。
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from st_agent.l0.net import EgressCancelledError, EgressError, EgressUnavailableError
from st_agent.l1.mcp.errors import McpConnectionError
from st_agent.l1.mcp.ids import check_remote_url, check_server_id, remote_host

__all__ = [
    "CLIENT_INFO",
    "DEFAULT_PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT_MS",
    "HttpSseTransport",
    "McpTransport",
    "REMOTE_PURPOSE",
    "StdioTransport",
    "extract_response",
]

DEFAULT_TIMEOUT_MS = 30_000
"""单次收发的缺省超时（毫秒）。"""

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
"""客户端首选 MCP 协议版本；Server 返回的值以 Server 为准（宽松接受）。"""

CLIENT_INFO = {"name": "st-agent", "version": "0.1.0"}
"""``initialize`` 的 clientInfo（MCP 握手必填字段）。"""

REMOTE_PURPOSE = "MCP 远程 Server 调用"
"""出网审计的目的说明（02 §6：人可读；**不得**含内容/凭据字段名）。"""


def extract_response(raw: str, request_id: int) -> str:
    """从一段响应文本里取出 ``id == request_id`` 的 JSON-RPC 报文。

    接受两种形态：整体 JSON（``application/json``）与 SSE
    （``text/event-stream``，取 ``data:`` 行）。取不到即
    ``McpConnectionError``（失败显式化，不静默返回空）。
    """
    text = (raw or "").strip()
    if not text:
        raise McpConnectionError("MCP 端点返回空响应")
    candidates: list[str] = []
    if text.startswith("data:") or "\ndata:" in text or "\r\ndata:" in text:
        candidates = [ln[5:].strip() for ln in text.replace("\r\n", "\n").split("\n")
                      if ln.startswith("data:")]
    else:
        candidates = [text]
    for chunk in candidates:
        if not chunk or chunk == "[DONE]":
            continue
        try:
            msg = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == request_id:
            return chunk
    raise McpConnectionError(f"MCP 端点响应里找不到 id={request_id} 的报文")


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _unwrap(raw_message: str) -> dict[str, Any]:
    """解包一条 JSON-RPC 响应 → ``result``（``error`` → ``McpConnectionError``）。"""
    try:
        msg = json.loads(raw_message)
    except ValueError as exc:
        raise McpConnectionError(f"MCP 响应不是合法 JSON：{raw_message[:200]}") from exc
    if not isinstance(msg, dict):
        raise McpConnectionError("MCP 响应不是 JSON 对象")
    error = msg.get("error")
    if error is not None:
        if isinstance(error, dict):
            detail = f"{error.get('code', '')} {error.get('message', '')}".strip()
        else:
            detail = str(error)
        raise McpConnectionError(f"MCP 调用被拒：{detail or '未说明原因'}")
    result = msg.get("result")
    return result if isinstance(result, dict) else {}


class McpTransport(ABC):
    """一次 MCP 会话的传输骨架（同类传输之间的公共逻辑，非跨层契约）。"""

    def __init__(self, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise McpConnectionError(f"timeout_ms 须为正整数，收到 {timeout_ms!r}")
        self._timeout_ms = timeout_ms
        self._request_seq = 0
        self._opened = False
        self._protocol_version = ""
        self._server_name = ""
        self._server_version = ""

    # ───────────────────────── 子类实现面 ─────────────────────────

    @abstractmethod
    def _write(self, payload: str) -> None:
        """把一段报文发出去（通知无响应，故此处不含读取）。"""

    @abstractmethod
    def _read(self, request_id: int, timeout_ms: int) -> str:
        """取回 ``id == request_id`` 的 JSON-RPC 响应文本。"""

    def _open(self) -> None:
        """建立底层通道（默认无操作）。"""

    def _close(self) -> None:
        """释放底层通道（默认无操作）。"""

    # ───────────────────────── 生命周期 ─────────────────────────

    def open(self) -> None:
        """建立通道（幂等）。"""
        if self._opened:
            return
        self._open()
        self._opened = True

    def close(self) -> None:
        """释放通道（幂等）。"""
        if not self._opened:
            return
        try:
            self._close()
        finally:
            self._opened = False

    def __enter__(self) -> "McpTransport":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ───────────────────────── 收发 ─────────────────────────

    @property
    def protocol_version(self) -> str:
        """握手后的协议版本（Server 返回值为准；未握手为空串）。"""
        return self._protocol_version

    @property
    def server_name(self) -> str:
        """握手带回的 Server 名（未握手为空串）。"""
        return self._server_name

    @property
    def server_version(self) -> str:
        """握手带回的 Server 版本（未握手为空串）。"""
        return self._server_version

    def initialize(self) -> dict[str, Any]:
        """MCP 握手：``initialize`` + ``notifications/initialized``。"""
        result = self.request(
            "initialize",
            {
                "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(CLIENT_INFO),
            },
        )
        info = result.get("serverInfo")
        info = info if isinstance(info, dict) else {}
        self._protocol_version = str(result.get("protocolVersion", "") or "")
        self._server_name = str(info.get("name", "") or "")
        self._server_version = str(info.get("version", "") or "")
        self.notify("notifications/initialized")
        return result

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发一次请求并解包结果（失败一律 ``McpConnectionError``）。"""
        if not self._opened:
            self.open()
        self._request_seq += 1
        request_id = self._request_seq
        self._write(_dumps({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params if params is not None else {},
        }))
        return _unwrap(self._read(request_id, self._timeout_ms))

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发一条通知（无响应）。"""
        if not self._opened:
            self.open()
        self._write(_dumps({
            "jsonrpc": "2.0", "method": method,
            "params": params if params is not None else {},
        }))


class StdioTransport(McpTransport):
    """本地 stdio 传输：行分隔 JSON-RPC（03 §5.1「本地运行优先」）。"""

    def __init__(
        self,
        command: str,
        args: Sequence[str] = (),
        env: dict[str, str] | None = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        super().__init__(timeout_ms=timeout_ms)
        if not (command or "").strip():
            raise McpConnectionError("stdio 传输的 command 不得为空")
        self._command = command.strip()
        self._args = tuple(args)
        self._env = dict(env) if env else None
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._pump: threading.Thread | None = None

    # ───────────────────────── 通道 ─────────────────────────

    def _open(self) -> None:
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - 命令由用户显式声明并逐项批准
                [self._command, *self._args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1, env=self._env,
            )
        except OSError as exc:
            raise McpConnectionError(
                f"无法启动 MCP Server 进程 {self._command!r}：{exc}"
            ) from exc
        self._pump = threading.Thread(target=self._pump_lines, daemon=True)
        self._pump.start()

    def _pump_lines(self) -> None:
        """把 stdout 行泵入队列（管道上无法用 select，故走线程 + 队列超时）。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._lines.put(None)
            return
        try:
            for line in proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(None)          # EOF：进程已退出

    def _close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for closer in (proc.stdin, proc.stdout, proc.stderr):
            if closer is not None:
                try:
                    closer.close()
                except OSError:
                    pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    # ───────────────────────── 收发 ─────────────────────────

    def _write(self, payload: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpConnectionError("MCP Server 进程未在运行（stdio 通道已关闭）")
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except OSError as exc:
            raise McpConnectionError(f"向 MCP Server 进程写入失败：{exc}") from exc

    def _read(self, request_id: int, timeout_ms: int) -> str:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpConnectionError(f"等待 MCP 响应超时（{timeout_ms}ms）")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise McpConnectionError(f"等待 MCP 响应超时（{timeout_ms}ms）") from None
            if line is None:
                raise McpConnectionError("MCP Server 进程已退出（stdout 关闭）")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue                   # Server 写到 stdout 的非 JSON 行：忽略
            if isinstance(msg, dict) and msg.get("id") == request_id:
                return line


class HttpSseTransport(McpTransport):
    """远程 HTTP-SSE 传输——**出网一律经 L0 网关**（02 §6）。

    :param cancel: 可选取消信号（``threading.Event``；``T-L1-002.3`` 的禁用中止
        经此接入）。置位后在**发包前 / 发包后**各查一次：已置即按
        ``EgressCancelledError`` 上报，网关据此落 ``cancelled`` 审计、信封走失败
        分支——不留「已禁用但调用成功」的假象。会话中间无法抢占式中断（02 §6
        的 ``execute`` 只在发起前查 ``cancel``），故此处是**检查点**语义。
    """

    def __init__(
        self,
        url: str,
        gateway,
        server_id: str,
        *,
        post: Callable[[str, str, int], str] | None = None,
        cancel: "threading.Event | None" = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        super().__init__(timeout_ms=timeout_ms)
        self._url = check_remote_url(url)
        self._gateway = gateway
        self._server_id = check_server_id(server_id)
        self._post = post if post is not None else _stdlib_post
        self._cancel = cancel
        self._pending: str | None = None

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def cancel(self) -> "threading.Event | None":
        """本次会话绑定的取消信号（未绑定为 ``None``）。"""
        return self._cancel

    def _write(self, payload: str) -> None:
        if self._gateway is None:
            raise McpConnectionError("远程 MCP 传输必须经 L0 出网网关（02 §6 基座不可绕过）")
        holder: list[str] = []
        body_bytes = len(payload.encode("utf-8"))

        def sender(_kind: str, _target_host: str, timeout_ms: int) -> tuple[int, int, list[str]]:
            """按次发包实现：真实 POST 在此完成，响应体经闭包带回（假设 A2）。"""
            if self._cancel is not None and self._cancel.is_set():
                raise EgressCancelledError("MCP 调用已取消（Server 已被禁用）")
            body = self._post(self._url, payload, timeout_ms)
            if self._cancel is not None and self._cancel.is_set():
                raise EgressCancelledError("MCP 调用已取消（Server 已被禁用）")
            holder.append(body)
            return body_bytes, len(body.encode("utf-8")), [body]

        envelope = self._gateway.execute(
            "remote_mcp", remote_host(self._url),
            initiator=self._server_id, purpose=REMOTE_PURPOSE,
            bytes_out=body_bytes, timeout_ms=self._timeout_ms,
            cancel=self._cancel, sender=sender,
        )
        if envelope.status != "ok":
            raise McpConnectionError(
                f"远程 MCP 出网未成功（{envelope.status}）：{envelope.reason or '未说明原因'}"
            )
        self._pending = holder[0] if holder else ""

    def _read(self, request_id: int, timeout_ms: int) -> str:
        raw, self._pending = self._pending, None
        return extract_response(raw or "", request_id)

    def _close(self) -> None:
        self._pending = None


def _stdlib_post(url: str, body: str, timeout_ms: int) -> str:
    """标准库 POST（远程传输的缺省发包实现；测试注入替身以便离线验证）。

    失败按**网关的错误词汇**上报（``EgressUnavailableError`` / ``EgressError``），
    使网关照常落一条审计记录——本函数是网关的按次发包插件，不是绕过它。
    """
    request = urllib.request.Request(  # noqa: S310 - url 已经 check_remote_url 限定 scheme
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as response:  # noqa: S310
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise EgressError(f"MCP 远程端点返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise EgressUnavailableError(f"MCP 远程端点不可达：{exc.reason}") from exc
    except OSError as exc:
        raise EgressUnavailableError(f"MCP 远程端点不可达：{exc}") from exc
