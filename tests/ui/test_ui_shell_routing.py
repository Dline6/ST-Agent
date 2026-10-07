"""回环端的**路由骨架与面端口**（[`T-UI-004.1`]）。

本组锁三件事：

1. **面端口的 fail-closed**——端口未注入即 `unavailable` + 点名；注入即 `ok`。
   这条把「未接入」与「接入但无数据」分开（前者点名装配归属，后者由各面自己的读面给空态）。
2. **页面路径回静态壳、未知路径仍 404**——SPA 回退不得吞掉资产缺失（[D-063] 的令牌分档下，
   页面路径属静态壳档，故**不带令牌**也应可开）。
3. **写面走白名单**——`POST` 只认表内路径，表外一律 404；既有 `/api/chat` 行为不变。

另有一条**漂移断言**：前端页面登记表（`web/js/pages.js`）与 Python 侧 `PAGE_PATHS` 相等
（同 `tests/ui/test_ui_registry.py` 对组件类型两侧一致的取向）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import _dispatch_guarded, serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_PAGES_JS = _UI / "web" / "js" / "pages.js"

_FACE_STATUS_PATHS = ("/api/reflection/status", "/api/evolution/status", "/api/eco/status")


def _frontend_page_paths() -> set[str]:
    """从 `pages.js` 的登记项里取出页面路径（`{ path: '…', … }` 字面量）。"""
    text = _PAGES_JS.read_text(encoding="utf-8")
    return set(re.findall(r"\{\s*path:\s*'([^']+)'", text))


# ── 面端口：未注入 ⇒ unavailable + 点名 ────────────────────────────────────────
@pytest.mark.parametrize("path", _FACE_STATUS_PATHS)
def test_face_status_is_unavailable_and_names_the_face_when_not_wired(
    ui_server, http_get, auth, path: str
) -> None:
    """端口未注入 ⇒ `unavailable` + 点名（不 500、不伪造「面在」）。"""
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["reason"], "不可用必须说明原因"
    assert "未接入" in payload["reason"] and "面" in payload["reason"]
    assert payload["render"]["presentation"] == "delayed"


@pytest.mark.parametrize("path", _FACE_STATUS_PATHS)
def test_face_status_needs_the_token(ui_server, http_get, path: str) -> None:
    """数据面恒要令牌——新增端点**不得**放宽守卫。"""
    assert http_get(ui_server, path).status == 401


def test_face_status_is_ok_once_the_port_is_injected(http_get, auth) -> None:
    """注入端口 ⇒ `ok`；L6 面同时服务 `/api/reflection/*` 与 `/api/evolution/*`。"""
    with serve(dev=False, reflection=object(), eco=object()) as running:
        for path, face in (
            ("/api/reflection/status", "reflection"),
            ("/api/evolution/status", "reflection"),
            ("/api/eco/status", "eco"),
        ):
            payload = http_get(running, path, headers=auth(running)).json()
            assert payload["status"] == "ok"
            assert payload["data"] == {"face": face, "available": True}


# ── 页面路径：静态壳回退 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("path", sorted(PAGE_PATHS))
def test_page_paths_serve_the_static_shell_without_a_token(ui_server, http_get, path: str) -> None:
    """页面路径回**同一静态壳**，且**不带令牌**也可开（首次导航发不出自定义头）。"""
    response = http_get(ui_server, path)
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>ST Agent" in response.body.decode("utf-8")


@pytest.mark.parametrize("path", ["/no-such-page", "/reflection/no-such-page", "/api/no-such"])
def test_unknown_paths_are_not_pages(ui_server, http_get, auth, path: str) -> None:
    """未知路径仍 404——SPA 回退**不吞**「资产 / 端点不存在」这个事实。"""
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 404


def test_page_paths_still_check_host(ui_server, http_get) -> None:
    """页面路径属静态壳档（免令牌），但**不放宽** `Host` 校验（DNS rebinding）。"""
    assert http_get(ui_server, "/reflection", headers={"Host": "evil.example"}).status == 403


# ── 写面：白名单 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [*_FACE_STATUS_PATHS, "/api/health", "/api/unknown"])
def test_post_to_a_non_whitelisted_path_is_404(ui_server, http_post, auth, path: str) -> None:
    """POST 只认路由表内的路径——表外（含把 GET 路径当 POST 用）一律 404。"""
    assert http_post(ui_server, path, {}, headers=auth(ui_server)).status == 404


def test_chat_stays_the_only_write_endpoint_of_this_leaf(ui_server, http_post, auth) -> None:
    """`/api/chat` 仍按既有口径受理（未接门面 ⇒ `unavailable`），**未**被本次改造改道。"""
    payload = http_post(ui_server, "/api/chat", {"action": "post", "text": "你好"},
                        headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"


# ── 漂移：前端页面登记表 ↔ Python 侧 PAGE_PATHS ────────────────────────────────
def test_frontend_page_registry_matches_the_python_side() -> None:
    """两侧不漂移：前端登记的页面路径（除根路径外）须与 `PAGE_PATHS` 逐项相等。"""
    frontend = _frontend_page_paths() - {"/"}
    assert frontend == set(PAGE_PATHS), "前端页面登记表与 ui/app.py 的 PAGE_PATHS 不一致"


def test_every_registered_page_has_a_availability_probe() -> None:
    """每条页面登记都点名一个可用性探针路径（壳据此决定导航项是否可点）。"""
    text = _PAGES_JS.read_text(encoding="utf-8")
    probes = re.findall(r"statusPath:\s*'([^']+)'", text)
    assert len(probes) == len(_frontend_page_paths())
    assert all(probe.startswith("/api/") for probe in probes)


# ── 传输层兜底：逸出的异常也转成信封 ──────────────────────────────────────────
def _raise_value_error() -> dict:
    raise ValueError("请求不合契约")


def _raise_runtime_error() -> dict:
    raise RuntimeError("落盘损坏")


def test_transport_guard_turns_escaped_input_errors_into_envelopes() -> None:
    """**兜底**拦的是「参数在进入端点之前就抛」那一类——否则客户端只看到断连（[00 §6]）。"""
    payload = _dispatch_guarded("probe", _raise_value_error)
    assert payload["status"] == "validation_failed"
    assert "请求不合契约" in payload["reason"]


def test_transport_guard_turns_escaped_internal_errors_into_failed() -> None:
    payload = _dispatch_guarded("probe", _raise_runtime_error)
    assert payload["status"] == "failed"
    assert payload["log_ref"].startswith("ui/route-")


def test_transport_guard_passes_normal_payloads_through() -> None:
    sentinel = {"status": "ok"}
    assert _dispatch_guarded("probe", lambda: sentinel) is sentinel
