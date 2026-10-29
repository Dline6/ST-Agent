"""开放网络的 HTTP 发送口（T-AGT-015 批；02 §6 按次 sender）。

本模块只负责**一次 HTTP 往返**：请求方法 / URL / 头 / 体 / 超时 → 状态码、
``Content-Type`` 与响应体。调用方（:mod:`st_agent.l0.net.search` /
:mod:`st_agent.l0.net.fetch`）把结果包进按次 ``Sender``，经
:class:`~st_agent.l0.net.gateway.EgressGateway` 的 ``stream`` 发出——网关仍是
唯一出口与唯一审计点，且**只见字节数**。

三条纪律：

- **不自动跟随重定向**——3xx 原样返回状态，由调用方显式拒绝；否则重定向可
  绕过域名名单。
- **响应体上限**——超过 ``max_bytes`` 显式失败，不返回半截正文。
- **错误信息不含正文 / 查询词**——只报状态码与中性原因，避免把用户内容带进
  审计的 ``detail`` 或工具错误里。
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from st_agent.l0.net.errors import (
    EgressError,
    EgressTimeoutError,
    EgressUnavailableError,
)

__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "FetchedPayload",
    "HttpClient",
    "default_http",
]

DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
"""单次响应体上限（2 MiB）；超限显式失败。"""


@dataclass(frozen=True)
class FetchedPayload:
    """一次 HTTP 往返的结果，同时是流式审计的「自报字节」项。"""

    status: int
    content_type: str
    body: bytes

    def to_wire_bytes(self) -> bytes:
        """该项在返回流中占的字节（网关审计口径 = 响应体长度）。"""
        return self.body


HttpClient = Callable[[str, str, Mapping[str, str], bytes | None, float, int],
                      FetchedPayload]
"""HTTP 发送口：(method, url, headers, body, timeout_s, max_bytes) → 结果。

测试注入替身即可离线覆盖全部分支；缺省实现见 :func:`default_http`。
"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动重定向：返回 ``None`` 即让 ``urlopen`` 以 ``HTTPError`` 上报 3xx。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _read_limited(response, max_bytes: int) -> bytes:
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise EgressError(f"响应超过 {max_bytes} 字节上限，已拒绝（不返回半截正文）")
    return body


def default_http(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_s: float,
    max_bytes: int,
) -> FetchedPayload:
    """缺省 HTTP 发送口（``urllib``，无第三方依赖）。"""
    request = urllib.request.Request(
        url, data=body, headers=dict(headers), method=method,
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_s) as response:  # noqa: S310
            return FetchedPayload(
                status=int(response.status),
                content_type=response.headers.get("Content-Type", ""),
                body=_read_limited(response, max_bytes),
            )
    except urllib.error.HTTPError as exc:
        # 3xx（不跟随）/ 4xx / 5xx 都原样交给调用方判定；错误页正文不返回、
        # 不读进内存（审计与错误信息都不需要它）。
        return FetchedPayload(
            status=int(exc.code),
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
            body=b"",
        )
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise EgressTimeoutError(f"请求超时（>{timeout_s}s）") from exc
        raise EgressUnavailableError(f"目标不可达：{exc.reason}") from exc
    except TimeoutError as exc:
        raise EgressTimeoutError(f"请求超时（>{timeout_s}s）") from exc
    except OSError as exc:
        raise EgressUnavailableError(f"出网失败：{exc}") from exc
