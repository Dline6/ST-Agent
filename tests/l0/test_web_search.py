"""联网搜索适配（T-AGT-015.1；02 §6）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from st_agent.l0.net.errors import EgressError, EgressUnavailableError
from st_agent.l0.net.search import (
    build_search_sender,
    parse_search_payload,
    provider_of,
    search_bytes_out,
)
from st_agent.l0.net.web_http import FetchedPayload

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class FakeHttp:
    def __init__(self, payload: FetchedPayload):
        self.payload = payload
        self.calls: list[tuple] = []

    def __call__(self, method, url, headers, body, timeout_s, max_bytes):
        self.calls.append((method, url, headers, body, timeout_s, max_bytes))
        return self.payload


def _json_payload(data: dict) -> FetchedPayload:
    return FetchedPayload(
        status=200,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
    )


def test_brave_request_and_normalisation() -> None:
    http = FakeHttp(_json_payload({
        "web": {"results": [
            {"title": "标题一", "url": "https://a.example/1",
             "description": "摘要一", "page_age": "2026-10-20"},
            {"title": "标题二", "url": "https://b.example/2",
             "description": "摘要二", "age": "2 days ago"},
        ]},
    }))
    sender = build_search_sender("brave", "利率 走势", "secret-key", 5, http=http)
    out, in_bytes, chunks = sender("web_search", "api.search.brave.com", 20_000)
    assert out == len("利率 走势".encode("utf-8"))
    assert in_bytes == len(http.payload.body)
    assert isinstance(chunks[0], FetchedPayload)
    assert http.calls[0][0] == "GET"
    assert "q=%E5%88%A9%E7%8E%87" in http.calls[0][1]
    assert http.calls[0][2]["X-Subscription-Token"] == "secret-key"

    payload = parse_search_payload("brave", chunks[0], retrieved_at=NOW)
    assert payload["provider"] == "brave"
    assert payload["count"] == 2
    assert payload["results"][0] == {
        "title": "标题一", "url": "https://a.example/1", "snippet": "摘要一",
        "source": "brave", "published_hint": "2026-10-20",
    }
    assert payload["results"][1]["published_hint"] == "2 days ago"
    assert payload["retrieved_at"] == NOW.isoformat()


def test_tavily_request_is_a_json_post() -> None:
    http = FakeHttp(_json_payload({
        "results": [
            {"title": "T", "url": "https://t.example", "content": "C",
             "published_date": "2026-10-21"},
        ],
    }))
    sender = build_search_sender("tavily", "query", "k", 3, http=http)
    sender("web_search", "api.tavily.com", 20_000)
    method, url, headers, body, _, _ = http.calls[0]
    assert method == "POST"
    assert url == "https://api.tavily.com/search"
    assert headers["Authorization"] == "Bearer k"
    assert json.loads(body.decode("utf-8")) == {"query": "query", "max_results": 3}
    payload = parse_search_payload("tavily", http.payload, retrieved_at=NOW)
    assert payload["results"][0]["snippet"] == "C"
    assert payload["results"][0]["published_hint"] == "2026-10-21"


def test_empty_result_list_is_a_successful_empty_answer() -> None:
    payload = parse_search_payload(
        "brave", _json_payload({"web": {"results": []}}), retrieved_at=NOW
    )
    assert payload["count"] == 0 and payload["results"] == []


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"web": {}}', b'{"web": {"results": 1}}'])
def test_malformed_response_is_explicit(body: bytes) -> None:
    payload = FetchedPayload(200, "application/json", body)
    with pytest.raises(EgressError):
        parse_search_payload("brave", payload, retrieved_at=NOW)


def test_non_json_content_type_is_rejected() -> None:
    payload = FetchedPayload(200, "text/html", b"<html></html>")
    with pytest.raises(EgressError):
        parse_search_payload("brave", payload, retrieved_at=NOW)


def test_host_mismatch_is_rejected() -> None:
    sender = build_search_sender("brave", "q", "k", 5, http=FakeHttp(
        _json_payload({"web": {"results": []}})
    ))
    with pytest.raises(EgressUnavailableError):
        sender("web_search", "evil.example", 20_000)


@pytest.mark.parametrize("status", [301, 302, 500])
def test_redirect_and_http_errors_are_explicit(status: int) -> None:
    sender = build_search_sender("brave", "q", "k", 5, http=FakeHttp(
        FetchedPayload(status, "text/html", b"")
    ))
    with pytest.raises(EgressError):
        sender("web_search", "api.search.brave.com", 20_000)


def test_missing_key_is_rejected() -> None:
    with pytest.raises(EgressUnavailableError):
        build_search_sender("brave", "q", "", 5, http=FakeHttp(
            _json_payload({"web": {"results": []}})
        ))


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(EgressUnavailableError):
        provider_of("duckduckgo")


def test_bytes_out_counts_the_outgoing_bytes() -> None:
    assert search_bytes_out("brave", "利率", 5) == len("利率".encode("utf-8"))
    assert search_bytes_out("tavily", "利率", 5) > len("利率".encode("utf-8"))
