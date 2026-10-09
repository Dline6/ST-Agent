"""dev 面的构建期开关与发布剔除（[D-060] ④I）。

两个方向都要钉：**开着**时 dev 面真的可用（浏览器直开的前提）；**发布构建**里它被物理
剔除，且此时请求 dev 面**显式失败**而不是静默降级。
"""

from __future__ import annotations

import glob
import tomllib
from pathlib import Path

import pytest

import st_agent.ui.app as app_module
from st_agent.ui.dev import SAMPLE_STATUSES
from st_agent.ui.errors import DevSurfaceUnavailable

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_WEB = _UI / "web"


def _package_data_patterns() -> list[str]:
    """`package-data["st_agent.ui"]` 的模式表（相对包目录 `st_agent/ui`）。"""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]["st_agent.ui"]


def _expand(pattern: str) -> set[Path]:
    """按 **setuptools 的同一套语义**求值：``glob(..., recursive=True)``。

    见 `setuptools.command.build_py.find_data_files`——它把模式交给 `glob(recursive=True)`，
    故 `**` 真递归。**不得**改用 `fnmatch`：它的 `*` 会跨 `/`，会把「未覆盖」算成
    「已覆盖」——本组断言正是为 2026-10-09 那次真 wheel 漏 10 个文件而加强的（[T-UI-008]）。
    """
    return {Path(p) for p in glob.glob(str(_UI / pattern), recursive=True) if Path(p).is_file()}


def test_release_build_excludes_the_dev_package() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    exclude = data["tool"]["setuptools"]["packages"]["find"]["exclude"]
    assert "st_agent.ui.dev" in exclude


def test_web_assets_travel_with_the_package() -> None:
    """`web/**` 下的**每个**文件都被某个模式覆盖。

    原断言只要求「存在任一以 `web/` 开头的模式」——任何模式集都能过，等于没守：
    T-UI-008 实测真 wheel 少 10 个前端文件，而本条用例**照样绿**。
    """
    covered: set[Path] = set()
    for pattern in _package_data_patterns():
        covered |= _expand(pattern)
    sources = {p for p in _WEB.rglob("*") if p.is_file()}
    assert sources, "未扫到任何前端资产（根路径写错会让本用例空跑）"
    assert not sources - covered, (
        "以下前端资产不会被任何 package-data 模式打进发布包（非 editable 安装下会 404 / 降级）："
        f"{sorted(p.relative_to(_UI).as_posix() for p in sources - covered)}"
    )


def test_package_data_patterns_never_reach_the_dev_surface() -> None:
    """模式不得匹配 `dev/**`——dev 面在发布构建里是**物理剔除**的（[D-060] ④I）。"""
    dev_files = {p for p in (_UI / "dev").rglob("*") if p.is_file()}
    assert dev_files, "未扫到 dev 面文件（本用例会空跑）"
    hit: set[Path] = set()
    for pattern in _package_data_patterns():
        hit |= _expand(pattern) & dev_files
    assert not hit, (
        f"以下 dev 面文件会被打进发布包：{sorted(p.relative_to(_UI).as_posix() for p in hit)}"
    )


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
