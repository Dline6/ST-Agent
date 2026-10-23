"""T-INT-007 · M6 集成关卡的装配 rig（**真装配 + 真回环面**，离线可跑）。

与 M3–M5 各关卡的 rig 同一取向：真 ``Store`` + 真 ``MarketDb`` + 真官方 Pack + 真 L2–L6
编排件，只把**出网那一跳**（``llm_post``）换成脚本化替身——本关卡验的是**表现层装配**，
故在 ``rig_m5.seeded_m5`` 之上再加一层：按**生产入口的端口映射**（``ui_ports``）起一个
**真回环服务**，全部断言经真 HTTP 连接（带启动令牌）发出，不经任何替身端口。

三处与既有 ``tests/integration/test_ui_*_wiring.py`` 的分工：

1. 那些叶各自钉**一个面**的读写往返（面内行为）；本 rig 服务**跨面**的装配断言——
   页面集 / 写表 / 拒绝路径 / 出站点闸门在**同一个真服务**上成立；
2. 起服务的端口取自 ``ui_ports``（**生产入口的那份映射**），不在测试里另抄一张——
   于是「入口漏注一个面」这类装配缺口在本关卡可被断言（见 ``serve_port_names``）；
3. 本 rig 同时提供 ``dev=True`` 的第二类服务（六态里两态只在 dev 样本面可达，见
   ``test_m6_ui_complete.py`` 的 GWT-2）。
"""

from __future__ import annotations

import http.client
import inspect
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from rig_m5 import M5Rig, seeded_m5

from st_agent.__main__ import ui_ports
from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import RunningUi, serve

__all__ = [
    "SHELL_PATHS",
    "UiClient",
    "declared_pages_js_paths",
    "declared_pages_js_status_paths",
    "seeded_m6",
    "serve_port_names",
    "store_paths",
    "ui_server",
]

SHELL_PATHS: frozenset[str] = PAGE_PATHS | {"/"}
"""声明页面集：``/`` ＋ ``PAGE_PATHS`` 的 18 条 ＝ [11-sitemap §2.2] 的 19 条（[`T-UI-011.2`]）。"""

_NON_PORT_PARAMS = frozenset({"host", "port", "dev", "token", "shell", "shell_control"})
"""``serve`` 上不属于「面端口」的形参（绑定 / 开发开关 / 启动令牌 / 壳会话与意图通道）。"""


def serve_port_names() -> frozenset[str]:
    """``serve`` 声明的**全部面端口形参名**（由签名取，不另抄一张表）。

    GWT-6 用它把「生产入口注入的端口集」与「表现层声明的端口集」对起来——两处各自漂移
    （加了面没注入、或注入了没有的键）都会在此现形。
    """
    return frozenset(
        name
        for name, param in inspect.signature(serve).parameters.items()
        if name not in _NON_PORT_PARAMS
        and param.kind is inspect.Parameter.KEYWORD_ONLY
    )


def store_paths(root: Path) -> set[str]:
    """存储根下的**文件相对路径集**——「有没有落下副作用」的判据。

    比断言某个读端点更直接：写面一旦真跑过，无论写到哪个分区、落到哪张表，路径集都会变
    （含空目录不算，只算文件）。
    """
    return {
        str(path.relative_to(root)).replace("\\", "/")
        for path in Path(root).rglob("*")
        if path.is_file()
    }


_PATH_LITERAL = re.compile(r"""\bpath:\s*['"]([^'"]+)['"]""")
_STATUS_LITERAL = re.compile(r"""\bstatusPath:\s*['"]([^'"]+)['"]""")


def declared_pages_js_paths(source: str) -> set[str]:
    """从 ``pages.js`` 源码里取出**登记的页面路径**集合（`path: "..."` 字面量）。

    宽松解析：只认 ``path: "…"`` 形态的键值，不把 JS 求值一遍——本关卡要的是「前端登记的
    路径集」这个集合本身。
    """
    return set(_PATH_LITERAL.findall(source))


def declared_pages_js_status_paths(source: str) -> set[str]:
    """``pages.js`` 每条登记项声明的 ``statusPath``（页面打开时的取数端点）。"""
    return set(_STATUS_LITERAL.findall(source))


@dataclass
class UiClient:
    """真回环服务的客户端：带启动令牌、可直接看状态码与原始体、支持**连接复用**。

    ``app`` 是**该服务真实持有的** :class:`UiApp` 实例（``serve`` 装出来的那一个）——
    出站点闸门一类断言直接投给它，验的就是装配结果本身，不是另造一份等价物。
    """

    host: str
    port: int
    token: str
    app: Any = None

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, self.port, timeout=15)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        token: bool = True,
        connection: http.client.HTTPConnection | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """发一次请求。``connection`` 传入即**复用**该连接（不传则一次一连接）。

        复用是 GWT-5 的判据：拒绝路径若没把请求体读净，复用同一条连接的下一次请求会拿到
        连接复位（本机 Windows 实测），而不是一个可读的响应。
        """
        own = connection is None
        conn = connection if connection is not None else self.connection()
        try:
            sent: dict[str, str] = {}
            if token:
                sent[TOKEN_HEADER] = self.token
            sent.update(headers or {})
            payload: bytes | None
            if body is None:
                payload = None
            elif isinstance(body, bytes):
                payload = body
            else:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if payload is not None:
                sent["Content-Length"] = str(len(payload))
            conn.request(method, path, body=payload, headers=sent)
            response = conn.getresponse()
            raw = response.read()
            head = {key.lower(): value for key, value in response.getheaders()}
            return response.status, head, raw
        finally:
            if own:
                conn.close()

    def get(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        status, _head, raw = self.request("GET", path, **kwargs)
        return status, _maybe_json(raw)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> tuple[int, Any]:
        status, _head, raw = self.request("POST", path, body=body, **kwargs)
        return status, _maybe_json(raw)

    def text(self, path: str, **kwargs: Any) -> tuple[int, str]:
        status, _head, raw = self.request("GET", path, **kwargs)
        return status, raw.decode("utf-8")


def _maybe_json(raw: bytes) -> Any:
    """JSON 体解成对象；不是 JSON（如静态件 / ``http.server`` 的 HTML 错误页）则回原文。"""
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


@contextmanager
def ui_server(
    m5: M5Rig,
    *,
    dev: bool = False,
    **overrides: Any,
) -> Iterator[UiClient]:
    """按**生产入口的端口映射**起一个真回环服务，产出带令牌的客户端。

    ``dev`` 只影响 dev 样本面是否可达（[`T-UI-008`]：发布包剔除该面）；``overrides`` 可
    逐个替换端口（用于「某个面未接线」的对照）。
    """
    ports = {**ui_ports(m5.m5), **overrides}
    running: RunningUi = serve(dev=dev, **ports)
    try:
        yield UiClient(running.host, running.port, running.token, app=running.app)
    finally:
        running.shutdown()


def seeded_m6(root: Path) -> M5Rig:
    """已播种数据面的 M6 全栈装配（真组合根；只脚本化出网那一跳）。

    组合根仍是 ``build_m5_runtime``——表现在层没有独立的「M6 组合根」，M6 的交付是**挂在
    同一个根上的端点与门面**，这一点由本关卡如实按生产装配来验（见 ``ui_ports``）。
    """
    return seeded_m5(root)
