"""本机回环 HTTP 服务（[00 §1.1]；[D-060] ③ 取 F）。

只绑 ``127.0.0.1``；端口取 ``0`` 由 OS 分配（避开可猜的固定端口）；每次启动换令牌。
实现用**标准库**——与「L1 手写 JSON-RPC、不引官方 MCP SDK」同一取向（[D-063] ②）。

**UI 只是客户端**：本服务只读各层公共 API，不直开存储（单一写者＝后端进程，[02 §2.4]）。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from st_agent.ui.app import UiApp, build_ui
from st_agent.ui.security import new_token

__all__ = ["RunningUi", "UiServer", "serve"]

_LOG = logging.getLogger("st_agent.ui.server")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class UiRequestHandler(BaseHTTPRequestHandler):
    """回环请求处理。

    ``app`` 由 :func:`serve` 在**绑定后**以子类属性注入——每个服务实例一个子类，
    避免同进程内多个实例互相串台。
    """

    app: UiApp

    server_version = "STAgentUI/0.1"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        _LOG.debug("%s - %s", self.address_string(), fmt % args)

    # ── 路由 ────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 —— http.server 规定的接口名
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        verdict = self.app.guard.check(self.headers, require_token=path.startswith("/api/"))
        if not verdict.allowed:
            self._send_json(verdict.status, {"error": verdict.reason})
            return

        if path == "/api/health":
            self._send_json(200, self.app.api_health())
            return

        if path == "/api/dev/sample":
            wanted = parse_qs(parsed.query).get("status", [""])[0]
            payload = self.app.api_dev_sample(wanted)
            if payload is None:
                self._send_text(404, "未找到")
                return
            self._send_json(200, payload)
            return

        if path == "/api/dev/description":
            wanted = parse_qs(parsed.query).get("kind", [""])[0]
            described = self.app.api_dev_description(wanted)
            if described is None:
                self._send_text(404, "未找到")
                return
            self._send_json(200, described)
            return

        if path in ("/", "/index.html"):
            self._send_text(200, self.app.index_html())
            return

        asset = self.app.static_file(path)
        if asset is None:
            self._send_text(404, "未找到")
            return
        self._send_file(asset)

    # ── 发送 ────────────────────────────────────────────────────────────────
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_text(self, status: int, text: str) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/html; charset=utf-8")

    def _send_file(self, path: Path) -> None:
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self._send_bytes(200, path.read_bytes(), content_type)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 用户数据不进任何中间缓存；同时**不发任何 Access-Control-Allow-***（见 security 模块）
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


class UiServer(ThreadingHTTPServer):
    """回环服务本体（只绑回环地址由调用方保证）。"""

    daemon_threads = True
    allow_reuse_address = True


@dataclass
class RunningUi:
    """一个已启动的服务实例（上下文管理器；退出即关）。"""

    app: UiApp
    server: UiServer
    token: str

    @property
    def host(self) -> str:
        return str(self.server.server_address[0])

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "RunningUi":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    dev: bool = False,
    token: str | None = None,
) -> RunningUi:
    """绑定 → 按**实际端口**装配 → 起服务线程 → 返回句柄。

    端口取 ``0`` 时由 OS 分配；守卫必须拿到真实端口才能校验 ``Host`` / ``Origin``，
    故顺序是「先绑、后装配」。
    """
    resolved_token = token or new_token()
    server = UiServer((host, port), UiRequestHandler)
    app = build_ui(
        host=str(server.server_address[0]),
        port=int(server.server_address[1]),
        dev=dev,
        token=resolved_token,
    )
    server.RequestHandlerClass = type("BoundUiRequestHandler", (UiRequestHandler,), {"app": app})
    threading.Thread(target=server.serve_forever, name="st-agent-ui", daemon=True).start()
    return RunningUi(app=app, server=server, token=resolved_token)
