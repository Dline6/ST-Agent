"""OpenAI 兼容端点的真实发送器（02 §4 / §6；T-L0-016）。

把「一次 LLM 调用」组装成真实出网请求，并经 [出网网关](../net/gateway.py)
发出——网关仍是唯一出口与唯一审计点，**只见字节数**：prompt 与 key 放进
**按次 sender 闭包**（02 §6 的按次注入通道）。

三件交付：

- :class:`OpenAiRoute`：一个提供方的请求面（``base_url`` + ``model``）
- :func:`openai_compat_sender_factory`：产出 ``llm_transport`` 要的按次工厂
  （``(endpoint, prompt, key, timeout_ms) -> Sender``）
- :func:`provider_hosts_of`：由 routes 派生 ``provider → host``——审计记录的
  目标主机与实际请求的目标主机**同源**，不会一处改了另一处没改

协议面（``POST {base_url}/chat/completions``，SSE 流式）：请求体
``{"model", "messages", "stream": true}``；鉴权走 ``Authorization: Bearer <key>``
（``key`` 为空则不带该头——本地推理端点无凭据）。响应逐行 ``data: {...}``，
取 ``choices[0].delta.content``；``data: [DONE]`` 结束。

失败一律以 [``Egress*``](../net/errors.py) 异常上报——网关原样透出、由
``EgressGateway.llm_transport`` 映射为 ``Transport*`` 体系：连接失败 / 无路由
→ ``EgressUnavailableError``；超时 → ``EgressTimeoutError``；其余（HTTP 错误
状态、提供方错误载荷）→ ``EgressError``。故「提供方回了 error 或 4xx」**不会**
被当成空响应静默成功。

HTTP 发送口可注入（``post`` 参数，与 [MCP 的 ``HttpSseTransport``](../../l1/mcp/transports.py)
同款）：缺省用 ``urllib``，测试注入替身即可离线覆盖全部分支。明文
（prompt / key / response）只在内存短暂持有，不落盘、不进审计。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from st_agent.l0.net.errors import (
    EgressError,
    EgressTimeoutError,
    EgressUnavailableError,
)
from st_agent.l0.net.gateway import Sender

__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "OpenAiRoute",
    "Post",
    "openai_compat_sender_factory",
    "provider_hosts_of",
]

CHAT_COMPLETIONS_PATH = "/chat/completions"
"""OpenAI 兼容的对话补全路径（拼在 ``base_url`` 之后）。"""

#: 注入式 HTTP 发送口：(url, headers, body, timeout_s) -> 响应行可迭代
Post = Callable[[str, Mapping[str, str], bytes, float], Iterable[str]]


@dataclass(frozen=True)
class OpenAiRoute:
    """一个提供方的请求面（02 §4；不含任何密钥明文）。"""

    base_url: str
    model: str
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    """随请求附带的自定义头（如某些网关要求的路由头）；不得含凭据明文。"""

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        if not self.base_url.strip() or not parts.netloc or not parts.hostname:
            raise ValueError(f"base_url 非法（须为含主机的绝对 URL）：{self.base_url!r}")
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"base_url 仅支持 http/https：{self.base_url!r}")
        if not self.model.strip():
            raise ValueError("model 不得为空")

    @property
    def url(self) -> str:
        """对话补全的完整 URL。"""
        return self.base_url.rstrip("/") + CHAT_COMPLETIONS_PATH

    @property
    def host(self) -> str:
        """目标主机（**不含端口**）——出网审计的 ``target_host``。

        02 §6 与 ``NetworkEvent`` 只登记主机本身（禁 scheme/路径/query），
        故本地端点 ``127.0.0.1:11434`` 的审计主机为 ``127.0.0.1``；实际请求
        仍打到带端口的 URL。
        """
        return urlsplit(self.base_url).hostname or ""


def provider_hosts_of(routes: Mapping[str, OpenAiRoute]) -> dict[str, str]:
    """由 routes 派生 ``provider → host`` 映射（审计与实际请求同源）。"""
    return {provider: route.host for provider, route in routes.items()}


def openai_compat_sender_factory(
    routes: Mapping[str, OpenAiRoute],
    *,
    post: Post | None = None,
) -> Callable[[object, str, str | None, int], Sender]:
    """产出 ``llm_transport(..., sender_factory=...)`` 要的按次 sender 工厂。

    :param routes: ``provider → OpenAiRoute``；端点 ``provider`` 未登记即
        ``EgressUnavailableError``（fail-closed，不猜 URL）。
    :param post: HTTP 发送口（缺省 ``urllib`` 实现）；测试注入替身以离线覆盖。
    """
    routes = dict(routes)
    do_post: Post = post if post is not None else _urllib_post

    def _factory(endpoint, prompt: str, key: str | None, timeout_ms: int) -> Sender:
        provider = getattr(endpoint, "provider", None)
        route = routes.get(provider)
        if route is None:
            raise EgressUnavailableError(
                f"提供方 {provider!r} 未登记请求面（base_url / model）"
            )
        body = json.dumps(
            {
                "model": route.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **dict(route.extra_headers),
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"

        def _sender(kind: str, target_host: str, t_ms: int) -> tuple:
            # 审计主机与实际目标必须同源，否则「网络活动透明化」失真
            if target_host != route.host:
                raise EgressUnavailableError(
                    f"目标主机不一致：审计记 {target_host!r}，请求面为 {route.host!r}"
                    "（provider_hosts 须经 provider_hosts_of 由 routes 派生）"
                )
            return (len(body), 0, _content_chunks(do_post(route.url, headers, body,
                                                          max(t_ms, 1) / 1000)))

        return _sender

    return _factory


def _urllib_post(
    url: str, headers: Mapping[str, str], body: bytes, timeout_s: float
) -> Iterable[str]:
    """缺省 HTTP 发送口（``urllib``，无第三方依赖）；产出响应行。"""
    request = urllib.request.Request(  # noqa: S310 - URL 由 OpenAiRoute 限定 http/https
        url, data=body, headers=dict(headers), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            for raw in response:
                yield raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # URLError 的子类，须先捕
        raise EgressError(
            f"提供方返回 HTTP {exc.code}（{exc.reason}）"
        ) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise EgressTimeoutError(f"请求超时（>{timeout_s}s）") from exc
        raise EgressUnavailableError(f"目标不可达：{exc.reason}") from exc
    except TimeoutError as exc:
        raise EgressTimeoutError(f"请求超时（>{timeout_s}s）") from exc
    except OSError as exc:
        raise EgressUnavailableError(f"出网失败：{exc}") from exc


def _content_chunks(lines: Iterable[str]) -> Iterable[str]:
    """SSE 行 → 增量文本块（``data:`` 行取 ``delta.content``；``[DONE]`` 收尾）。

    非 JSON 的 ``data:`` 行（心跳 / 注释 / 提供方自定格式）忽略；而**携带
    ``error`` 的载荷**一律抛 ``EgressError``——「提供方报错」不得呈现为
    「空回复的成功」。
    """
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("error"):
            raise EgressError(f"提供方返回错误：{obj['error']}")
        for choice in obj.get("choices") or ():
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            piece = delta.get("content") if isinstance(delta, dict) else None
            if piece:
                yield piece
