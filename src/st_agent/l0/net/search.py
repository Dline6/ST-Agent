"""联网搜索提供方适配（T-AGT-015.1；[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

两个 JSON REST 提供方，各自一个请求面与响应归一化：

- ``brave``——``GET https://api.search.brave.com/res/v1/web/search``，
  ``X-Subscription-Token`` 头携密钥；结果在 ``web.results``。
- ``tavily``——``POST https://api.tavily.com/search``，``Authorization: Bearer``；
  结果在 ``results``。

本模块**不直接发包**：它只构造一次请求并返回一个按次 ``Sender`` 闭包，由调用方
交给 ``EgressGateway.stream``——网关是唯一出口与唯一审计点，查询词只进 sender
闭包、不进网关签名，审计只见字节数（02 §6）。响应形态不符即显式失败，不把
畸形响应当成空结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from st_agent.l0.net.errors import EgressError, EgressUnavailableError
from st_agent.l0.net.gateway import Sender
from st_agent.l0.net.web_http import (
    DEFAULT_MAX_BODY_BYTES,
    FetchedPayload,
    HttpClient,
    default_http,
)
from st_agent.l0.net.web_config import SEARCH_PROVIDERS

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "SEARCH_MAX_BYTES",
    "SearchProvider",
    "build_search_sender",
    "parse_search_payload",
    "provider_of",
    "search_bytes_out",
]

DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 10
SEARCH_MAX_BYTES = 1 * 1024 * 1024
"""搜索响应体上限（1 MiB）；搜索结果元数据远小于网页正文。"""


@dataclass(frozen=True)
class SearchProvider:
    """一个搜索提供方的请求面与响应解析（不含密钥明文）。"""

    provider_id: str
    host: str
    method: str
    path: str

    def request(
        self, query: str, limit: int, key: str
    ) -> tuple[str, dict[str, str], bytes | None]:
        """构造 (url, headers, body)；密钥只进请求头 / 体，不进返回值以外的位置。"""
        if self.provider_id == "brave":
            params = urlencode({"q": query, "count": limit})
            return (
                f"https://{self.host}{self.path}?{params}",
                {
                    "Accept": "application/json",
                    "X-Subscription-Token": key,
                },
                None,
            )
        if self.provider_id == "tavily":
            body = json.dumps(
                {"query": query, "max_results": limit}, ensure_ascii=False
            ).encode("utf-8")
            return (
                f"https://{self.host}{self.path}",
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                body,
            )
        raise EgressUnavailableError(f"未登记的搜索提供方：{self.provider_id!r}")

    def results(self, body: bytes) -> list:
        """从响应体取结果列表（形态不符 → ``EgressError``）。"""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise EgressError(f"{self.provider_id} 搜索响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise EgressError(f"{self.provider_id} 搜索响应形态不符")
        if self.provider_id == "brave":
            web = payload.get("web")
            items = web.get("results") if isinstance(web, dict) else None
        else:
            items = payload.get("results")
        if not isinstance(items, list):
            raise EgressError(f"{self.provider_id} 搜索响应缺少结果列表")
        return items


_PROVIDERS: dict[str, SearchProvider] = {
    "brave": SearchProvider("brave", "api.search.brave.com", "GET",
                            "/res/v1/web/search"),
    "tavily": SearchProvider("tavily", "api.tavily.com", "POST", "/search"),
}


def provider_of(provider_id: str) -> SearchProvider:
    """按提供方 id 取请求面（未登记 → ``EgressUnavailableError``）。"""
    provider = _PROVIDERS.get(provider_id)
    if provider is None:
        raise EgressUnavailableError(
            f"未登记的搜索提供方 {provider_id!r}；可选 = {list(SEARCH_PROVIDERS)}"
        )
    return provider


def search_bytes_out(provider_id: str, query: str, limit: int) -> int:
    """一次搜索请求的发送字节数（审计 ``bytes_out`` 口径；不含密钥）。"""
    provider = provider_of(provider_id)
    _, _, body = provider.request(query, limit, "")
    return len(body) if body is not None else len(query.encode("utf-8"))


def build_search_sender(    provider_id: str,
    query: str,
    key: str,
    limit: int,
    *,
    http: HttpClient | None = None,
    timeout_ms: int = 20_000,
    max_bytes: int = SEARCH_MAX_BYTES,
) -> Sender:
    """构造一次搜索的按次 sender（交给 ``EgressGateway.stream``）。

    审计主机与实际请求主机同源：sender 会核对 ``target_host`` 与提供方主机一致，
    不一致即拒——否则「网络活动透明化」会失真。
    """
    provider = provider_of(provider_id)
    if not str(key or "").strip():
        raise EgressUnavailableError("搜索密钥未配置")
    url, headers, body = provider.request(query, limit, key)
    do_http: HttpClient = http if http is not None else default_http
    bytes_out = len(body) if body is not None else len(query.encode("utf-8"))

    def _sender(kind: str, target_host: str, t_ms: int) -> tuple:
        if target_host.strip().lower() != provider.host:
            raise EgressUnavailableError(
                f"目标主机不一致：审计记 {target_host!r}，请求面为 {provider.host!r}"
            )
        timeout_s = max(int(t_ms or timeout_ms), 1) / 1000
        payload = do_http(
            provider.method, url, headers, body, timeout_s, max_bytes
        )
        if 300 <= payload.status < 400:
            raise EgressError(
                f"{provider.provider_id} 搜索返回重定向（不自动跟随）"
            )
        if payload.status >= 400:
            raise EgressError(
                f"{provider.provider_id} 搜索返回 HTTP {payload.status}"
            )
        return (bytes_out, len(payload.body), (payload,))

    return _sender


def parse_search_payload(
    provider_id: str,
    payload: FetchedPayload,
    *,
    retrieved_at: datetime,
) -> dict:
    """响应体 → 归一化结果（只保留标题 / 链接 / 摘要 / 来源 / 时间提示）。"""
    provider = provider_of(provider_id)
    if not isinstance(payload, FetchedPayload):
        raise EgressError("搜索响应形态不符（缺 HTTP 载荷）")
    if "json" not in (payload.content_type or "").lower():
        raise EgressError(
            f"{provider.provider_id} 搜索响应不是 JSON（Content-Type: "
            f"{payload.content_type or '未标注'}）"
        )
    results: list[dict] = []
    for item in provider.results(payload.body):
        if not isinstance(item, dict):
            raise EgressError(f"{provider.provider_id} 搜索结果条目形态不符")
        if provider_id == "brave":
            snippet = item.get("description", "")
            published = item.get("page_age") or item.get("age") or ""
        else:
            snippet = item.get("content", "")
            published = item.get("published_date") or ""
        results.append(
            {
                "title": _text(item.get("title")),
                "url": _text(item.get("url")),
                "snippet": _text(snippet),
                "source": provider.provider_id,
                "published_hint": _text(published),
            }
        )
    return {
        "provider": provider.provider_id,
        "results": results,
        "count": len(results),
        "retrieved_at": retrieved_at.isoformat(),
    }


def _text(value: object) -> str:
    return str(value or "").strip()
