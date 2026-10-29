"""网页抓取与正文抽取（T-AGT-015.2；[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

只做三件事：

1. **URL 形态校验**——只允许 http / https、必须有主机、拒绝 URL 内嵌凭据
   （``user:pass@``）。域名名单的核对在 L1 原语侧（用户可运行期配置）。
2. **一次 GET 的按次 sender**——交给 ``EgressGateway.stream``；不自动跟随
   重定向（3xx 显式拒绝），否则重定向可绕过域名名单。
3. **正文抽取**——按 ``Content-Type`` 只接受文本类；HTML 用标准库
   ``HTMLParser`` 去标签、跳过脚本 / 样式，输出纯文本。非文本 / 二进制 /
   解码失败一律显式失败，**不返回乱码**。

网页正文是**不可信数据**：本模块只把它变成文本，不解释、不执行其中的指令。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from st_agent.l0.net.errors import EgressError, EgressUnavailableError
from st_agent.l0.net.gateway import Sender
from st_agent.l0.net.web_http import (
    DEFAULT_MAX_BODY_BYTES,
    FetchedPayload,
    HttpClient,
    default_http,
)

__all__ = [
    "build_fetch_sender",
    "extract_fetched_text",
    "parse_fetch_url",
]

_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.I)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.I
)


def parse_fetch_url(url: str) -> tuple[str, str]:
    """校验并返回 ``(原 URL, 主机小写)``；非法 → ``EgressUnavailableError``。"""
    text = str(url or "").strip()
    if not text:
        raise EgressUnavailableError("网页抓取需要非空 URL")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise EgressUnavailableError(f"只允许 http / https URL：{text!r}")
    if not parts.hostname:
        raise EgressUnavailableError(f"URL 缺少主机：{text!r}")
    if parts.username or parts.password:
        raise EgressUnavailableError("URL 不得内嵌凭据（user:pass@）")
    return text, parts.hostname.lower()


def build_fetch_sender(
    url: str,
    *,
    http: HttpClient | None = None,
    timeout_ms: int = 20_000,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> Sender:
    """构造一次网页抓取的按次 sender（交给 ``EgressGateway.stream``）。"""
    request_url, host = parse_fetch_url(url)
    do_http: HttpClient = http if http is not None else default_http
    headers = {
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json",
        "User-Agent": "ST-Agent/1.0 (local-first web fetch)",
    }

    def _sender(kind: str, target_host: str, t_ms: int) -> tuple:
        if target_host.strip().lower() != host:
            raise EgressUnavailableError(
                f"目标主机不一致：审计记 {target_host!r}，请求面为 {host!r}"
            )
        timeout_s = max(int(t_ms or timeout_ms), 1) / 1000
        payload = do_http("GET", request_url, headers, None, timeout_s, max_bytes)
        if 300 <= payload.status < 400:
            raise EgressError("网页返回重定向（不自动跟随；请直接给出目标地址）")
        if payload.status >= 400:
            raise EgressError(f"网页返回 HTTP {payload.status}")
        return (0, len(payload.body), (payload,))

    return _sender


def extract_fetched_text(payload: FetchedPayload) -> dict:
    """响应体 → 纯文本（含标题）；非文本 / 解码失败 → ``EgressError``。"""
    if not isinstance(payload, FetchedPayload):
        raise EgressError("抓取结果形态不符（缺 HTTP 载荷）")
    content_type = (payload.content_type or "").strip()
    lowered = content_type.lower()
    if not lowered:
        raise EgressError("网页未标注 Content-Type，无法确认是文本")
    is_html = "html" in lowered or "xml" in lowered
    if not (lowered.startswith("text/") or lowered.startswith("application/")
            and ("json" in lowered or "xhtml" in lowered or "xml" in lowered)):
        raise EgressError(f"不支持的响应类型：{content_type}（只抓取文本）")
    if b"\x00" in payload.body:
        raise EgressError("响应含二进制内容（NUL 字节），不作为文本返回")

    text = _decode(payload.body, content_type)
    if is_html:
        parser = _HtmlToText()
        parser.feed(text)
        parser.close()
        return {
            "title": parser.title_text(),
            "text": parser.body_text(),
            "content_type": content_type,
        }
    return {
        "title": "",
        "text": _normalize(text),
        "content_type": content_type,
    }


def _decode(body: bytes, content_type: str) -> str:
    charset = ""
    match = _CHARSET_RE.search(content_type)
    if match:
        charset = match.group(1)
    if not charset:
        meta = _META_CHARSET_RE.search(body[:4096])
        if meta:
            charset = meta.group(1).decode("ascii", errors="ignore")
    charset = charset or "utf-8"
    try:
        return body.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise EgressError(
            f"响应无法按 {charset} 解码为文本（不是可信的纯文本）"
        ) from exc


class _HtmlToText(HTMLParser):
    """最小 HTML→文本：去标签、跳过脚本 / 样式 / 模板、保留块级换行。"""

    _SKIP = {"script", "style", "noscript", "template"}
    _BLOCK = {
        "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4",
        "h5", "h6", "section", "article", "header", "footer", "blockquote",
        "pre", "table", "ul", "ol", "nav", "main", "aside", "figure",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._body_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        name = tag.lower()
        if name in self._SKIP:
            self._skip_depth += 1
            return
        if name == "title":
            self._in_title = True
            return
        if name in self._BLOCK:
            self._body_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if name == "title":
            self._in_title = False
            return
        if name in self._BLOCK:
            self._body_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        else:
            self._body_parts.append(data)

    def title_text(self) -> str:
        return _normalize("".join(self._title_parts))

    def body_text(self) -> str:
        return _normalize("".join(self._body_parts))


def _normalize(text: str) -> str:
    """折叠空白：行内连续空白变单空格、多余空行压成至多一个空行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
