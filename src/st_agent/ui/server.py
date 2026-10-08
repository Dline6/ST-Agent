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
from collections.abc import Callable
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from st_agent.contracts.result_envelope import ResultEnvelope

from st_agent.ui.app import PAGE_PATHS, UiApp, build_ui
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.security import new_token

__all__ = ["RunningUi", "UiServer", "serve"]

_LOG = logging.getLogger("st_agent.ui.server")

_MAX_BODY_BYTES = 256 * 1024
"""对话请求体的读取上限（回环本机、单轮文本；超限截断后按畸形处理，不整块吃进内存）。"""

_CONTENT_TYPES = {    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

_GET_ROUTES: dict[str, Callable[[UiApp, str], dict]] = {
    "/api/health": lambda app, _q: app.api_health(),
    # 面可用性探针（`.1` 交付；各叶在此表**只增**各自的取数端点）。
    "/api/reflection/status": lambda app, _q: app.api_face_status("reflection"),
    "/api/evolution/status": lambda app, _q: app.api_face_status("reflection"),
    "/api/eco/status": lambda app, _q: app.api_face_status("eco"),
    # 反思中心（`T-UI-004.2`）
    "/api/reflection/reports": lambda app, q: app.api_reflection_report(week=_query(q, "week")),
    "/api/reflection/reports/trace": lambda app, q: app.api_reflection_trace(
        week=_query(q, "week")
    ),
    "/api/reflection/feedback-capture": lambda app, _q: app.api_reflection_feedback_capture(),
    "/api/reflection/proposals": lambda app, _q: app.api_reflection_proposals(),
    "/api/reflection/experiments": lambda app, _q: app.api_reflection_experiments(),
    "/api/reflection/training": lambda app, _q: app.api_reflection_training(),
    # 演进面（`T-UI-004.3`）
    "/api/evolution/changes": lambda app, _q: app.api_evolution_changes(),
    "/api/evolution/authorization": lambda app, _q: app.api_evolution_authorization(),
    # 生态面（`T-UI-004.4`）
    "/api/eco/export/plan": lambda app, _q: app.api_eco_export_plan(),
    "/api/eco/inbox": lambda app, _q: app.api_eco_inbox(),
    "/api/eco/index": lambda app, _q: app.api_eco_index(),
    "/api/eco/imports": lambda app, _q: app.api_eco_imports(),
    "/api/eco/violations": lambda app, _q: app.api_eco_violations(),
    "/api/eco/violations/history": lambda app, _q: app.api_eco_violation_history(),
}
"""`GET` 数据面的路由表：路径 → `(UiApp, 查询串)` → 载荷。**只此一处**判定「某路径存在与否」
——散落的 `if path == …` 分支会随着端点增多而漂移（[`T-UI-003`] 的同型取向）。"""

_POST_ROUTES: dict[str, Callable[[UiApp, dict], dict]] = {
    "/api/chat": lambda app, body: app.api_chat(body),
    # 反思中心的写面（`T-UI-004.2`）：反馈采集与提案处置
    "/api/reflection/feedback": lambda app, body: app.api_reflection_feedback(body),
    "/api/reflection/proposals/decide": lambda app, body: app.api_reflection_decide(body),
    # 主动提案落 Studio（`T-L6-004.2`）：交 Studio 落画布 + 接受 / 否决
    "/api/reflection/proposals/studio": lambda app, body: app.api_reflection_studio_handoff(body),
    "/api/reflection/proposals/studio/decide": lambda app, body: (
        app.api_reflection_studio_decide(body)
    ),
    # 演进面的写面（`T-UI-004.3`）：一键回滚 / 应用设置 / 出厂重置
    "/api/evolution/changes/rollback": lambda app, body: app.api_evolution_rollback(body),
    "/api/evolution/authorization": lambda app, body: app.api_evolution_set(body),
    "/api/evolution/factory-reset": lambda app, body: app.api_evolution_factory_reset(body),
    # 生态面的写面（`T-UI-004.4`）：导出 / 导入校验与逐项批准与安装 / 禁用越界能力
    "/api/eco/export": lambda app, body: app.api_eco_export(body),
    "/api/eco/import/review": lambda app, body: app.api_eco_import_review(body),
    "/api/eco/import/permissions": lambda app, body: app.api_eco_import_permissions(body),
    "/api/eco/import/decide": lambda app, body: app.api_eco_import_decide(body),
    "/api/eco/import/install": lambda app, body: app.api_eco_import_install(body),
    "/api/eco/violations/disable": lambda app, body: app.api_eco_disable(body),
}
"""`POST` 数据面的路由表：路径 → `(UiApp, body)` → 载荷。**非本表内的 POST 一律 404**。

受限写端点（提案处置 / 回滚 / 导出导入 / 授权切换 / 出厂重置）由各叶在此表登记；
请求体一律经 :meth:`UiRequestHandler._read_json_body` 归一（畸形 / 超大 → `{}`，由门面 fail-closed）。
"""


def _query(raw: str, name: str) -> str:
    """取查询串里的一个参数（缺省空串；只取首个值，同既有 dev 端点的口径）。"""
    return parse_qs(raw).get(name, [""])[0]


def _dispatch_guarded(where: str, call: Callable[[], dict]) -> dict:
    """跑一次端点，**任何逸出的异常都转成信封**（[00 §6] 失败显式化）。

    各端点自身已按「输入类失败 / 其余」分流（见 `UiApp._call_face`）；本条是**兜底**，
    拦的是「参数在进入端点之前就抛」那一类——例如请求体的必填项在路由 lambda 里求值。
    没有它，异常会从 `http.server` 逸出、客户端只看到断连而拿不到任何信封（[`T-UI-003`]
    在 `/api/chat` 上遇到过的同一形态，2026-10-07 由 [`T-UI-004.3`] 在演进端点上再撞一次）。
    """
    try:
        return call()
    except (ValueError, KeyError) as exc:
        _LOG.info("端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
        return envelope_payload(
            ResultEnvelope.validation_failed(str(exc) or "请求不合契约")
        )
    except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
        log_ref = f"ui/route-{uuid4().hex[:12]}"
        _LOG.exception("端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
        return envelope_payload(
            ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref)
        )


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

        handler = _GET_ROUTES.get(path)
        if handler is not None:
            self._send_json(200, _dispatch_guarded(path, lambda: handler(self.app, parsed.query)))
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

        if path in PAGE_PATHS or path in ("/", "/index.html"):
            self._send_text(200, self.app.index_html())
            return

        asset = self.app.static_file(path)
        if asset is None:
            self._send_text(404, "未找到")
            return
        self._send_file(asset)

    def do_POST(self) -> None:  # noqa: N802 —— http.server 规定的接口名
        """数据面的写端点，路径全部取自 :data:`_POST_ROUTES`（表外一律 404）。

        校验与 GET 同一档——`/api/*` 必带令牌（`Origin` 校验防 DNS rebinding）；
        请求体经 `_read_json_body` 归一为 dict；组合根未注入门面时各端点自身回
        `unavailable`（不伪造，见 `UiApp.api_chat` 与各面端点）。
        """
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        verdict = self.app.guard.check(self.headers, require_token=path.startswith("/api/"))
        if not verdict.allowed:
            self._send_json(verdict.status, {"error": verdict.reason})
            return

        handler = _POST_ROUTES.get(path)
        if handler is None:
            self._send_text(404, "未找到")
            return
        body = self._read_json_body()
        self._send_json(200, _dispatch_guarded(path, lambda: handler(self.app, body)))

    # ── 发送 ────────────────────────────────────────────────────────────────
    def _read_json_body(self) -> dict:
        """读并解析 JSON 请求体（畸形 / 超大一律回空体，由门面 fail-closed）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, _MAX_BODY_BYTES))
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

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
    chat: object = None,
    reflection: object = None,
    eco: object = None,
) -> RunningUi:
    """绑定 → 按**实际端口**装配 → 起服务线程 → 返回句柄。

    端口取 ``0`` 时由 OS 分配；守卫必须拿到真实端口才能校验 ``Host`` / ``Origin``，
    故顺序是「先绑、后装配」。``chat`` / ``reflection`` / ``eco`` 透传给 :func:`build_ui`
    （组合根注入三个鸭子端口；缺省全 ``None`` ⇒ 各面 fail-closed）。
    """
    resolved_token = token or new_token()
    server = UiServer((host, port), UiRequestHandler)
    app = build_ui(
        host=str(server.server_address[0]),
        port=int(server.server_address[1]),
        dev=dev,
        token=resolved_token,
        chat=chat,
        reflection=reflection,
        eco=eco,
    )
    server.RequestHandlerClass = type("BoundUiRequestHandler", (UiRequestHandler,), {"app": app})
    threading.Thread(target=server.serve_forever, name="st-agent-ui", daemon=True).start()
    return RunningUi(app=app, server=server, token=resolved_token)
