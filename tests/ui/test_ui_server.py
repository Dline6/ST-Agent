"""回环服务的端到端行为：绑定、鉴权、静态服务、路径穿越、无 CORS 头。

全部走真 HTTP——这些性质在桩上不可见（同 [D-059] 的教训：替身能满足签名，却表达不了
「真的发生过」）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.ui.security import resolve_static


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
