"""回环服务的端到端行为：绑定、鉴权、静态服务、路径穿越、无 CORS 头、请求体读净。

全部走真 HTTP——这些性质在桩上不可见（同 [D-059] 的教训：替身能满足签名，却表达不了
「真的发生过」）。末组（[`T-UI-022`]）尤其如此：它观测的是**连接怎么关的**，桩与
内存传输都表达不了。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.security import TOKEN_HEADER, resolve_static
from st_agent.ui.server import serve


def test_binds_loopback_with_os_assigned_port(ui_server) -> None:
    assert ui_server.host == "127.0.0.1"
    assert ui_server.port > 0


def test_health_requires_token(ui_server, http_get) -> None:
    response = http_get(ui_server, "/api/health")
    assert response.status == 401
    assert b"render" not in response.body
    assert b"ok" not in response.body


def test_health_rejects_foreign_host(ui_server, http_get, auth) -> None:
    response = http_get(ui_server, "/api/health", headers=auth(ui_server, Host="evil.example"))
    assert response.status == 403
    assert b"render" not in response.body


def test_health_rejects_foreign_origin(ui_server, http_get, auth) -> None:
    headers = auth(ui_server, Origin="http://evil.example")
    response = http_get(ui_server, "/api/health", headers=headers)
    assert response.status == 403


def test_health_returns_ok_envelope_with_render(ui_server, http_get, auth) -> None:
    response = http_get(ui_server, "/api/health", headers=auth(ui_server))
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["render"]["presentation"] == "normal"
    assert response.headers["cache-control"] == "no-store"


def test_no_cors_headers_are_ever_sent(ui_server, http_get, auth) -> None:
    """不回任何 `Access-Control-Allow-*`：跨源预检因此必失败（第二道跨源闸门）。"""
    response = http_get(ui_server, "/api/health", headers=auth(ui_server))
    assert not [name for name in response.headers if name.startswith("access-control-")]


def test_index_is_served_without_dev_reference(ui_server, http_get, auth) -> None:
    response = http_get(ui_server, "/", headers=auth(ui_server))
    assert response.status == 200
    assert b"<!--ST_DEV_SCRIPT-->" not in response.body
    assert b"/dev/devpanel.js" not in response.body
    assert b"st-dev-token" not in response.body


def test_dev_index_injects_the_token_for_in_app_preview(ui_server_dev, http_get) -> None:
    """应用内 Browser 预览无法带 fragment；dev 面用 meta 注入令牌（发布构建没有）。"""
    response = http_get(ui_server_dev, "/")

    assert response.status == 200
    assert b'name="st-dev-token"' in response.body
    assert ui_server_dev.token.encode() in response.body


def test_static_asset_is_served(ui_server, http_get, auth) -> None:
    response = http_get(ui_server, "/js/render.js", headers=auth(ui_server))
    assert response.status == 200
    assert "javascript" in response.headers["content-type"]


def test_static_shell_loads_without_a_token(ui_server, http_get) -> None:
    """初始文档导航无法携带自定义头，故静态壳不要求令牌——壳里没有任何用户数据。

    这条是**在真浏览器里跑出来的**：若把令牌也压在 `/` 上，页面会直接显示 401 JSON，
    应用根本打不开（`T-UI-001.2` 的执行日志记了这次发现）。
    """
    assert http_get(ui_server, "/").status == 200
    assert http_get(ui_server, "/js/render.js").status == 200


def test_static_shell_still_rejects_foreign_host(ui_server, http_get) -> None:
    """壳不要求令牌，但 `Host` 照校——挡 DNS rebinding 的那道闸门不放松。"""
    response = http_get(ui_server, "/", headers={"Host": "evil.example"})
    assert response.status == 403


def test_static_shell_accepts_the_localhost_spelling(ui_server, http_get) -> None:
    """回环的另一种写法能直达（[D-113]）——此前它撞 403，而用户最自然会敲的就是 `localhost`。"""
    response = http_get(ui_server, "/", headers={"Host": f"localhost:{ui_server.port}"})
    assert response.status == 200


def test_api_accepts_the_localhost_spelling(ui_server, http_get, auth) -> None:
    """数据面同理：由 `localhost` 加载的页面发请求时 `Host` / `Origin` 都是该写法。"""
    headers = auth(
        ui_server,
        Host=f"localhost:{ui_server.port}",
        Origin=f"http://localhost:{ui_server.port}",
    )
    assert http_get(ui_server, "/api/health", headers=headers).status == 200


def test_reachable_by_the_localhost_hostname(ui_server) -> None:
    """**按主机名**（而非字面 IP）连得住——这是浏览器实际走的那条路（[D-113] 假设 A1）。

    本机 Windows 上 `localhost` 先解析到 `::1`，而服务只绑 IPv4：连接靠客户端
    逐地址回落才成立。这条把「回落可达」与「自动填出的 `Host: localhost:port` 过守卫」
    一并钉住——`http.client` 不手填 `Host`，由它按主机名生成。
    """
    conn = http.client.HTTPConnection("localhost", ui_server.port, timeout=5)
    try:
        conn.request("GET", "/")
        assert conn.getresponse().status == 200
    finally:
        conn.close()

    conn = http.client.HTTPConnection("localhost", ui_server.port, timeout=5)
    try:
        conn.request("POST", "/api/health", body=b"{}", headers={"Content-Length": "2"})
        assert conn.getresponse().status == 401  # 换写法不放松令牌那一档
    finally:
        conn.close()


@pytest.mark.parametrize(
    "path",
    ["/../pyproject.toml", "/..%2fpyproject.toml", "/%2e%2e/pyproject.toml", "/css/../../pyproject.toml"],
)
def test_path_traversal_is_rejected(ui_server, http_get, auth, path) -> None:
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 404
    assert b"st-agent" not in response.body


def test_unknown_path_is_404(ui_server, http_get, auth) -> None:
    assert http_get(ui_server, "/nope", headers=auth(ui_server)).status == 404


def test_resolve_static_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    assert resolve_static(root, "/index.html") == root / "index.html"
    assert resolve_static(root, "/../secret.txt") is None
    assert resolve_static(root, "/missing.html") is None


# ── 请求体读净（[`T-UI-022`]：[00 §6]「失败显式化」的传输层前提） ────────────────
#
# 服务写完响应就关闭连接（HTTP/1.0 ⇒ 每请求一条），而**接收队列里还留着未读的请求体**
# 时，本机 Windows 发 RST：客户端拿到的是 `ConnectionAbortedError`，服务端写出的那支
# 响应被丢掉。故判据是「关闭时接收队列为空」——任何出口都得先读净声明的体。
#
# 体必须**大于套接字缓冲**才复现（小体会被内核吸收、不留残量）：实测 4 MiB 体在修前
# 打 401 路径 0/8、合法路由 7/8、带体 GET 1/8、DELETE 0/8 中招；256 KiB 则 8/8 不复现。

_OVERSIZED = b"x" * (4 * 1024 * 1024)
"""大于套接字缓冲的请求体——小于它的体被内核缓冲吸收，复位就不出现。"""


@pytest.mark.parametrize(
    ("label", "path", "status", "with_token", "extra_headers", "marker"),
    [
        ("缺令牌 ⇒ 401", "/api/health", 401, False, {}, "缺少或无效的启动令牌"),
        (
            "Host 不符 ⇒ 403",
            "/api/health",
            403,
            True,
            {"Host": "evil.example"},
            "请求 Host 与回环地址不符",
        ),
        ("路径不在写表 ⇒ 404", "/api/nope", 404, True, {}, "未找到"),
    ],
)
def test_oversized_body_does_not_reset_a_rejected_connection(
    ui_server, http_post, auth, label, path, status, with_token, extra_headers, marker
) -> None:  # noqa: ANN001（参数化）
    """GWT-1：三条拒绝路径都先读净体——客户端拿到服务端写出的那支响应，而非连接复位。

    `http_post` **不吞传输异常**（夹具契约），故复位会以 `ConnectionAbortedError` 直接判
    失败——这正是 `test_ui_face_ports.py::test_new_paths_are_get_only` 此前偶发红的成因。
    """
    headers = auth(ui_server, **extra_headers) if with_token else dict(extra_headers)
    response = http_post(ui_server, path, _OVERSIZED, headers)
    assert response.status == status, label
    assert marker.encode("utf-8") in response.body, f"{label}：响应被复位吃掉了"


class _EchoChat:
    """只记下收到的请求体（用于观测归一结果），行为同最小对话门面。"""

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def turn(self, body: dict) -> dict:
        self.seen.append(dict(body))
        return {
            "reply": ResultEnvelope.ok({"echo": True}),
            "session_id": "sess_echo",
            "needs_confirmation": False,
            "description": None,
        }


def test_oversized_body_does_not_reset_an_accepted_route(http_post, auth) -> None:
    """GWT-2：**合法路由**同样先读净——`/api/chat` 的面未注入 ⇒ 仍回 200 信封（fail-closed）。"""
    with serve(dev=False) as running:
        response = http_post(running, "/api/chat", _OVERSIZED, auth(running))
    assert response.status == 200
    assert response.json()["status"] == "unavailable", "响应被复位吃掉了"


def test_body_beyond_the_parse_cap_is_truncated_but_fully_drained(http_post) -> None:
    """GWT-2：解析口径**不变**（超 `_MAX_BODY_BYTES` 的部分不参与解析）而**仍被读走**。

    整体是合法 JSON、前 256 KiB 却只是它的开头 ⇒ 截断后必判畸形 ⇒ 归一为 `{}`（既有的
    `T-UI-003` 口径）。两半同一条用例里钉住：门面收到 `{}`（截断生效）**且**信封回来了
    （余量已读净）。
    """
    chat = _EchoChat()
    oversized_json = b'{"action": "post", "text": "' + b"x" * (300 * 1024) + b'"}'
    assert len(oversized_json) > 256 * 1024
    with serve(dev=False, chat=chat) as running:
        response = http_post(
            running, "/api/chat", oversized_json, {TOKEN_HEADER: running.token}
        )
    assert response.status == 200
    assert response.json()["status"] == "ok", "响应被复位吃掉了"
    assert chat.seen == [{}], "超上限的体应只按前 256 KiB 解析 ⇒ 畸形 ⇒ {}"


def test_normal_body_still_reaches_the_face_intact(http_post) -> None:
    """GWT-4：排空**不得**吞掉正常体——合法请求的门面入参逐字不变。"""
    chat = _EchoChat()
    with serve(dev=False, chat=chat) as running:
        response = http_post(
            running,
            "/api/chat",
            {"action": "post", "text": "查 sh.600000 退市风险"},
            {TOKEN_HEADER: running.token},
        )
    assert response.status == 200
    assert response.json()["status"] == "ok"
    assert chat.seen == [{"action": "post", "text": "查 sh.600000 退市风险"}]


def test_get_carrying_a_body_is_drained(ui_server, auth) -> None:
    """GWT-3：带体的 `GET`（本服务不产该形态，但客户端可发）也在响应前读净。"""
    conn = http.client.HTTPConnection(ui_server.host, ui_server.port, timeout=5)
    try:
        conn.request("GET", "/api/health", body=_OVERSIZED, headers=auth(ui_server))
        raw = conn.getresponse()
        assert raw.status == 200
        assert json.loads(raw.read().decode("utf-8"))["status"] == "ok"
    finally:
        conn.close()


def test_unsupported_method_with_a_body_is_drained(ui_server) -> None:
    """`send_error` 出口（未支持的方法 ⇒ 501）同样是本服务写出的响应，一并在响应前读净。"""
    conn = http.client.HTTPConnection(ui_server.host, ui_server.port, timeout=5)
    try:
        conn.request("DELETE", "/api/health", body=_OVERSIZED)
        raw = conn.getresponse()
        assert raw.status == 501
        assert raw.read(), "响应被复位吃掉了"
    finally:
        conn.close()
