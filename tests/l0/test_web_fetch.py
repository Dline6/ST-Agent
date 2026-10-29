"""网页抓取与正文抽取（T-AGT-015.2）。"""

from __future__ import annotations

import pytest

from st_agent.l0.net.errors import EgressError, EgressUnavailableError
from st_agent.l0.net.fetch import (
    build_fetch_sender,
    extract_fetched_text,
    parse_fetch_url,
)
from st_agent.l0.net.web_http import FetchedPayload


class FakeHttp:
    def __init__(self, payload: FetchedPayload):
        self.payload = payload
        self.calls: list[tuple] = []

    def __call__(self, method, url, headers, body, timeout_s, max_bytes):
        self.calls.append((method, url, headers, body, timeout_s, max_bytes))
        return self.payload


def _html(body: str, content_type: str = "text/html; charset=utf-8") -> FetchedPayload:
    return FetchedPayload(200, content_type, body.encode("utf-8"))


def test_parse_url_accepts_http_and_https() -> None:
    assert parse_fetch_url("https://docs.example.com/a?b=1") == (
        "https://docs.example.com/a?b=1", "docs.example.com",
    )
    assert parse_fetch_url("http://127.0.0.1:8080/x")[1] == "127.0.0.1"


@pytest.mark.parametrize("url", ["", "ftp://example.com", "https:///x", "https://u:p@x.com/a"])
def test_parse_url_rejects_unsafe_shapes(url: str) -> None:
    with pytest.raises(EgressUnavailableError):
        parse_fetch_url(url)


def test_html_is_reduced_to_text_and_title() -> None:
    out = extract_fetched_text(_html(
        "<html><head><title>页面标题</title><style>.x{}</style></head>"
        "<body><script>alert(1)</script><h1>小节</h1><p>正文 A&amp;B</p></body></html>"
    ))
    assert out["title"] == "页面标题"
    assert "小节" in out["text"]
    assert "正文 A&B" in out["text"]
    assert "alert" not in out["text"]
    assert ".x{}" not in out["text"]


def test_plain_text_is_returned_as_is() -> None:
    out = extract_fetched_text(FetchedPayload(200, "text/plain; charset=utf-8",
                                               "第一行\n第二行".encode("utf-8")))
    assert out["text"] == "第一行\n第二行"
    assert out["title"] == ""


def test_gbk_charset_is_decoded() -> None:
    out = extract_fetched_text(FetchedPayload(
        200, "text/html; charset=gbk", "<p>中文</p>".encode("gbk")
    ))
    assert out["text"] == "中文"


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "application/octet-stream", ""])
def test_non_text_content_type_is_explicitly_unsupported(content_type: str) -> None:
    with pytest.raises(EgressError):
        extract_fetched_text(FetchedPayload(200, content_type, b"\x89PNG"))


def test_binary_nul_is_rejected() -> None:
    with pytest.raises(EgressError):
        extract_fetched_text(FetchedPayload(200, "text/plain", b"abc\x00def"))


def test_undecodable_body_is_explicit() -> None:
    with pytest.raises(EgressError):
        extract_fetched_text(FetchedPayload(200, "text/plain; charset=utf-8", b"\xff\xfe"))


def test_sender_gets_host_and_reports_body_bytes() -> None:
    http = FakeHttp(_html("<p>ok</p>"))
    sender = build_fetch_sender("https://docs.example.com/a", http=http)
    out, in_bytes, chunks = sender("web_fetch", "docs.example.com", 20_000)
    assert out == 0
    assert in_bytes == len(http.payload.body)
    assert isinstance(chunks[0], FetchedPayload)
    method, url, headers, body, _, _ = http.calls[0]
    assert method == "GET" and url == "https://docs.example.com/a"
    assert body is None
    assert "ST-Agent" in headers["User-Agent"]


def test_sender_rejects_host_mismatch() -> None:
    sender = build_fetch_sender("https://docs.example.com/a", http=FakeHttp(_html("x")))
    with pytest.raises(EgressUnavailableError):
        sender("web_fetch", "evil.example", 20_000)


@pytest.mark.parametrize("status", [301, 302, 404, 500])
def test_sender_rejects_redirects_and_http_errors(status: int) -> None:
    sender = build_fetch_sender(
        "https://docs.example.com/a",
        http=FakeHttp(FetchedPayload(status, "text/html", b"")),
    )
    with pytest.raises(EgressError):
        sender("web_fetch", "docs.example.com", 20_000)
