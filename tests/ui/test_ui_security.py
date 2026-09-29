"""请求守卫的单元行为（[D-060] ③ 取 F）。

三重限制逐条钉住：令牌、`Host`（挡 DNS rebinding）、`Origin`。走 HTTP 的拒绝场景在
`test_ui_server.py`（真服务），本模块只管判定逻辑本身。
"""

from __future__ import annotations

from st_agent.ui.security import TOKEN_HEADER, RequestGuard, new_token

_TOKEN = "t0ken-0123456789"
_HOST = "127.0.0.1"
_PORT = 54321


def _guard() -> RequestGuard:
    return RequestGuard(token=_TOKEN, host=_HOST, port=_PORT)


def test_accepts_matching_request() -> None:
    verdict = _guard().check({"Host": f"{_HOST}:{_PORT}", TOKEN_HEADER: _TOKEN})
    assert verdict.allowed


def test_rejects_missing_or_wrong_token() -> None:
    for headers in (
        {"Host": f"{_HOST}:{_PORT}"},
        {"Host": f"{_HOST}:{_PORT}", TOKEN_HEADER: ""},
        {"Host": f"{_HOST}:{_PORT}", TOKEN_HEADER: "not-the-token"},
    ):
        verdict = _guard().check(headers)
        assert not verdict.allowed
        assert verdict.status == 401
        assert verdict.reason


def test_rejects_foreign_host() -> None:
    """DNS rebinding：攻击页把域名解析到本机时，请求的 `Host` 是攻击域名。"""
    verdict = _guard().check({"Host": "evil.example", TOKEN_HEADER: _TOKEN})
    assert not verdict.allowed
    assert verdict.status == 403


def test_rejects_foreign_origin() -> None:
    headers = {
        "Host": f"{_HOST}:{_PORT}",
        TOKEN_HEADER: _TOKEN,
        "Origin": "http://evil.example",
    }
    verdict = _guard().check(headers)
    assert not verdict.allowed
    assert verdict.status == 403


def test_accepts_same_origin() -> None:
    headers = {
        "Host": f"{_HOST}:{_PORT}",
        TOKEN_HEADER: _TOKEN,
        "Origin": f"http://{_HOST}:{_PORT}",
    }
    assert _guard().check(headers).allowed


def test_header_lookup_is_case_insensitive() -> None:
    """`http.server` 传进来的头名大小写不定，判定不得因此漏检。"""
    headers = {"host": f"{_HOST}:{_PORT}", TOKEN_HEADER.lower(): _TOKEN}
    assert _guard().check(headers).allowed


def test_static_tier_skips_only_the_token() -> None:
    """`require_token=False` 只放掉令牌那一档；`Host` / `Origin` 照校（[D-060] ③F）。"""
    assert _guard().check({"Host": f"{_HOST}:{_PORT}"}, require_token=False).allowed
    assert _guard().check({"Host": "evil.example"}, require_token=False).status == 403
    foreign_origin = {"Host": f"{_HOST}:{_PORT}", "Origin": "http://evil.example"}
    assert _guard().check(foreign_origin, require_token=False).status == 403


def test_new_token_is_long_and_unpredictable() -> None:
    tokens = {new_token() for _ in range(8)}
    assert len(tokens) == 8
    assert all(len(token) >= 32 for token in tokens)
