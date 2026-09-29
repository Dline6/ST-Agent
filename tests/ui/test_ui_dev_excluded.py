"""dev 面的构建期开关与发布剔除（[D-060] ④I）。

两个方向都要钉：**开着**时 dev 面真的可用（浏览器直开的前提）；**发布构建**里它被物理
剔除，且此时请求 dev 面**显式失败**而不是静默降级。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import st_agent.ui.app as app_module
from st_agent.ui.dev import SAMPLE_STATUSES
from st_agent.ui.errors import DevSurfaceUnavailable

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_release_build_excludes_the_dev_package() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    exclude = data["tool"]["setuptools"]["packages"]["find"]["exclude"]
    assert "st_agent.ui.dev" in exclude


def test_web_assets_travel_with_the_package() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["st_agent.ui"]
    assert any(pattern.startswith("web/") for pattern in patterns)


def test_dev_endpoint_absent_when_dev_is_off(ui_server, http_get, auth) -> None:
    response = http_get(ui_server, "/api/dev/sample?status=ok", headers=auth(ui_server))
    assert response.status == 404


def test_dev_endpoint_serves_every_state_when_on(ui_server_dev, http_get, auth) -> None:
    for status in SAMPLE_STATUSES:
        headers = auth(ui_server_dev)
        response = http_get(ui_server_dev, f"/api/dev/sample?status={status}", headers=headers)
        assert response.status == 200
        payload = response.json()
        assert payload["status"] == status
        assert payload["render"]["presentation"]


def test_dev_endpoint_rejects_unknown_state(ui_server_dev, http_get, auth) -> None:
    response = http_get(ui_server_dev, "/api/dev/sample?status=nope", headers=auth(ui_server_dev))
    assert response.status == 404


def test_dev_script_is_injected_only_when_dev_is_on(ui_server, ui_server_dev, http_get, auth) -> None:
    off = http_get(ui_server, "/", headers=auth(ui_server))
    on = http_get(ui_server_dev, "/", headers=auth(ui_server_dev))
    assert b"/dev/devpanel.js" not in off.body
    assert b"/dev/devpanel.js" in on.body


def test_dev_assets_are_served_when_dev_is_on(ui_server_dev, http_get, auth) -> None:
    response = http_get(ui_server_dev, "/dev/devpanel.js", headers=auth(ui_server_dev))
    assert response.status == 200
    assert "javascript" in response.headers["content-type"]


def test_requesting_dev_without_the_package_fails_loudly(monkeypatch) -> None:
    """发布构建里 `st_agent.ui.dev` 不存在：`dev=True` 必须响，不许装作正常。"""
    monkeypatch.setattr(app_module, "_load_dev", lambda: None)
    with pytest.raises(DevSurfaceUnavailable):
        app_module.build_ui(host="127.0.0.1", port=1234, dev=True)
