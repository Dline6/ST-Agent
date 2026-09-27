"""信息面抓取层（T-L0-010.2–.5）：协议 + 纯解析 + stdlib HTTP 实现。

**分层**：
- :class:`InfoFetcher`——协议，同步引擎只认它（测试注入 Fake，真实管道注入
  :class:`HttpInfoFetcher`）
- ``parse_*``——**纯函数**：源方载荷 → 规范化行。**离线可单测**（CI 覆盖），
  夹具取自**真实载荷形状**（2026-09-27 实测）
- :class:`HttpInfoFetcher`——真实取数（``urllib``，无第三方依赖）。**CI 不联网**，
  该层只在 ``tests/live/`` 单跑（``pytest -m live``）

端点知识移植自开源项目 ``simonlin1212/a-stock-data``（Apache-2.0，**取知识不取
依赖**），并按 2026-09-27 的**真实探测**校正了三处与原文不符之处（见各解析件注释）。

**出网纪律**：一切请求经 L0 出网网关（02 §6）——本层只做「发一次请求并返回
载荷」，审计与限流在网关侧。**查询参数一律经 ``urlencode``**：东财的 ``filter``
含 ``<`` / ``>``，直接拼进 URL 会被拒（实测 400）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from st_agent.l0.info.errors import (
    InfoFetchError,
    InfoFetchUnavailableError,
    InfoValidationError,
)
from st_agent.l0.info.sources import to_stock_id

__all__ = [
    "HttpInfoFetcher",
    "InfoFetcher",
    "parse_announcement_cninfo",
    "parse_announcement_em",
    "parse_announcement_szse",
    "parse_dragon_tiger_em",
    "parse_dragon_tiger_szse",
    "parse_sentiment_hot_ths",
    "parse_sentiment_qa_irm",
    "parse_shareholder_num_em",
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

InfoRows = dict[str, tuple[dict, ...]]
"""一次抓取的成果：``{表名: 行元组}``（一条任务可写多表）。"""


class InfoFetcher(Protocol):
    """信息面抓取器协议（同步引擎唯一依赖；测试注入离线 Fake）。"""

    def fetch_task(self, task_key: str, window: tuple[str, str]) -> InfoRows:
        """抓取一个任务在其窗口内的全部行；不可达 → ``InfoFetchUnavailableError``。"""
        ...


# ───────────────────────── 通用小工具 ─────────────────────────

def _num(value: Any) -> float | None:
    """宽松转数值（``None`` / 空串 / ``-`` / ``"None"`` → ``None``）。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "--", "—", "None", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "None" else text


def _day(value: Any) -> str | None:
    """时间戳/日期串 → ``YYYY-MM-DD``（毫秒整数、ISO 串、``YYYYMMDD`` 均可）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "None":
        return None
    if text.isdigit():
        number = int(text)
        if number > 10_000_000_000:  # 毫秒时间戳
            from datetime import datetime
            return datetime.fromtimestamp(number / 1000).strftime("%Y-%m-%d")
        if len(text) == 8:  # YYYYMMDD
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _stamp(value: Any) -> str | None:
    """毫秒时间戳/ISO 串 → ``YYYY-MM-DD HH:MM``（问答提问时间用）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "None":
        return None
    from datetime import datetime
    if text.isdigit():
        number = int(text)
        seconds = number / 1000 if number > 10_000_000_000 else number
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")
    return text[:16]


def _pythonish_list(value: Any) -> tuple[str, ...]:
    """解 ``"['002670']"`` 这类**字符串化的 Python 列表**（深交所官方接口实测形态）。

    源端把数组序列化成了字符串（含单引号），故不能直接 ``json.loads``。
    """
    text = _text(value)
    if not text:
        return ()
    import ast

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return tuple(str(item).strip() for item in parsed if str(item).strip())
        except (ValueError, SyntaxError):
            pass
    return (text,)


# ───────────────────────── 纯解析：公告 ─────────────────────────

def parse_announcement_cninfo(payload: dict) -> tuple[dict, ...]:
    """巨潮 ``hisAnnouncement/query`` → 公告行（覆盖**全市**）。"""
    rows: list[dict] = []
    for item in (payload.get("announcements") or []):
        code = _text(item.get("secCode"))
        if not code:
            continue
        rows.append({
            "code": code,
            "title": _text(item.get("announcementTitle")) or "",
            "ann_type": _text(item.get("announcementTypeName")),
            "pub_date": _day(item.get("announcementTime")),
            "url": ("https://www.cninfo.com.cn/new/disclosure/detail?annoId="
                    f"{item.get('announcementId', '')}"),
            "text": _text(item.get("announcementContent")) or "",
        })
    return tuple(rows)


def parse_announcement_szse(payload: dict) -> tuple[dict, ...]:
    """深交所官方 ``annList`` → 公告行。

    真实形状（2026-09-27 实测）：顶层 ``{announceCount, data: [...]}``；记录里
    ``secCode`` / ``secName`` 是**字符串化的 Python 列表**（如 ``"['002670']"``），
    故经 :func:`_pythonish_list` 解出代码——**一条记录可能挂多只标的**，逐只展开。
    """
    rows: list[dict] = []
    for item in (payload.get("data") or []):
        title = _text(item.get("title"))
        if not title:
            continue
        attach = _text(item.get("attachPath")) or ""
        pub_date = _day(item.get("publishTime"))
        url = ("https://disc.static.szse.cn/download" + attach) if attach else None
        for code in _pythonish_list(item.get("secCode")):
            rows.append({
                "code": code, "title": title, "ann_type": None,
                "pub_date": pub_date, "url": url, "text": "",
            })
    return tuple(rows)


def parse_announcement_em(payload: dict) -> tuple[dict, ...]:
    """东财公告 ``security/ann`` → 公告行。

    真实形状（2026-09-27 实测）：``data.list[].codes`` 是**字典列表**
    ``[{"stock_code": "600095", "ann_type": "A,SHA", "short_name": ...}]``——
    **不是**分隔字符串；公告类型取自 ``codes[].ann_type``（记录本身无该字段）。
    """
    rows: list[dict] = []
    for item in ((payload.get("data") or {}).get("list") or []):
        title = _text(item.get("title"))
        if not title:
            continue
        art = _text(item.get("art_code")) or ""
        pub_date = _day(item.get("notice_date"))
        url = f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf" if art else None
        codes = item.get("codes") or []
        if not isinstance(codes, list):
            codes = []
        for entry in codes:
            entry = entry if isinstance(entry, dict) else {}
            code = _text(entry.get("stock_code"))
            if not code:
                continue
            rows.append({
                "code": code, "title": title,
                "ann_type": _text(entry.get("ann_type")),
                "pub_date": pub_date, "url": url, "text": "",
            })
    return tuple(rows)


# ───────────────────────── 纯解析：龙虎榜 ─────────────────────────

def parse_dragon_tiger_em(payload: dict) -> InfoRows:
    """东财龙虎榜 → ``{dragon_tiger: 上榜记录, dragon_tiger_seat: 席位}``。

    上榜记录**按 ``(code, trade_date)`` 归一**（同标的同日多原因合并为一行，
    使跨源去重不依赖原因串措辞，D-030）。市场级端点**不返回席位**——
    ``seats`` 缺失时该表为空元组（席位需逐标的另调，本轮未验证）。
    """
    merged: dict[tuple[str, str], dict] = {}
    for item in (payload.get("result", {}).get("data") or payload.get("data") or []):
        code = _text(item.get("SECURITY_CODE")) or _text(item.get("SECUCODE"))
        day = _day(item.get("TRADE_DATE"))
        if not code or not day:
            continue
        key = (to_stock_id(code), day)
        reason = _text(item.get("EXPLANATION")) or _text(item.get("EXPLAIN"))
        row = merged.setdefault(key, {
            "code": key[0], "trade_date": day, "reasons": None,
            "net_amount": None, "buy_amount": None, "sell_amount": None,
            "turnover": None,
        })
        if reason:
            row["reasons"] = f"{row['reasons']}；{reason}" if row["reasons"] else reason
        for target, names in (("net_amount", ("BILLBOARD_NET_AMT", "NET_BUY_AMT")),
                              ("buy_amount", ("BILLBOARD_BUY_AMT",)),
                              ("sell_amount", ("BILLBOARD_SELL_AMT",)),
                              ("turnover", ("TURNOVERRATE", "ACCUM_AMOUNT_RATIO"))):
            for name in names:
                value = _num(item.get(name))
                if value is not None:
                    row[target] = value
                    break
    seats: list[dict] = []
    for item in (payload.get("seats") or []):
        code, day = _text(item.get("code")), _day(item.get("trade_date"))
        if not code or not day:
            continue
        seats.append({
            "code": to_stock_id(code), "trade_date": day,
            "side": item.get("side", "buy"), "rank": _int(item.get("rank")) or 1,
            "seat_name": _text(item.get("seat_name")) or "",
            "buy_amount": _num(item.get("buy_amount")),
            "sell_amount": _num(item.get("sell_amount")),
            "net_amount": _num(item.get("net_amount")),
        })
    return {"dragon_tiger": tuple(merged.values()), "dragon_tiger_seat": tuple(seats)}


def parse_dragon_tiger_szse(payload: Any) -> InfoRows:
    """深交所官方 ``ShowReport/data``（龙虎榜公开信息）→ **上榜记录**。

    真实形状（2026-09-27 实测）：顶层是**列表** ``[{data, error, metadata}]``；
    行字段为 ``dqrq``（日期）/ ``zqdm``（代码）/ ``zqjc``（名称）/ ``cjje``（成交额）/
    ``plyy``（上榜原因）。

    ⚠️ **该端点给的是上榜记录，不含营业部席位**——席位明细须另调
    ``CATALOGID=1842_detal``（本轮未验证）。故本解析件**不产出**
    ``dragon_tiger_seat`` 行；`T-L0-010` 遗留册已登记该缺口。
    """
    tables = payload if isinstance(payload, list) else [payload]
    merged: dict[tuple[str, str], dict] = {}
    for table in tables:
        for item in ((table or {}).get("data") or []) if isinstance(table, dict) else []:
            code = _text(item.get("zqdm"))
            day = _day(item.get("dqrq"))
            if not code or not day:
                continue
            key = (to_stock_id(code, "sz"), day)
            reason = _text(item.get("plyy"))
            row = merged.setdefault(key, {
                "code": key[0], "trade_date": day, "reasons": None,
                "net_amount": None, "buy_amount": None, "sell_amount": None,
                "turnover": None,
            })
            if reason:
                row["reasons"] = f"{row['reasons']}；{reason}" if row["reasons"] else reason
            amount = _num(item.get("cjje"))
            if amount is not None and row["net_amount"] is None:
                row["net_amount"] = amount
    return {"dragon_tiger": tuple(merged.values()), "dragon_tiger_seat": ()}


# ───────────────────────── 纯解析：股东户数 ─────────────────────────

def parse_shareholder_num_em(payload: dict) -> tuple[dict, ...]:
    """东财 ``RPT_HOLDERNUMLATEST`` → 股东户数行。"""
    rows: list[dict] = []
    for item in (payload.get("result", {}).get("data") or payload.get("data") or []):
        code = _text(item.get("SECURITY_CODE"))
        stat = _day(item.get("END_DATE"))
        if not code or not stat:
            continue
        rows.append({
            "code": to_stock_id(code), "stat_date": stat,
            "holder_num": _int(item.get("HOLDER_NUM")),
            "change_num": _int(item.get("HOLDER_NUM_CHANGE")),
            "change_ratio": _num(item.get("HOLDER_NUM_RATIO")),
            "avg_shares": _num(item.get("AVG_FREE_SHARES")),
        })
    return tuple(rows)


# ───────────────────────── 纯解析：舆情 ─────────────────────────

def parse_sentiment_qa_irm(payload: dict) -> tuple[dict, ...]:
    """互动易（深市）→ 问答行。``answer`` 为 ``None`` 表示**尚未回复**（合法态）。

    ⚠️ 该接口是**按公司**的（见 :meth:`HttpInfoFetcher._fetch_info_sentiment_qa_irm`），
    非全市场流。
    """
    rows: list[dict] = []
    for item in (payload.get("rows") or payload.get("data") or []):
        code = _text(item.get("stockCode"))
        question = _text(item.get("mainContent"))
        if not code or not question:
            continue
        rows.append({
            "code": code,
            "question": question,
            "answer": _text(item.get("attachedContent")),
            "answerer": _text(item.get("attachedAuthor")),
            "ask_time": _stamp(item.get("pubDate")),
        })
    return tuple(rows)


def parse_sentiment_hot_ths(payload: dict) -> tuple[dict, ...]:
    """同花顺热榜 → 热度快照行（**易腐**：值是「此刻」排名）。

    真实形状（2026-09-27 实测）：``data.stock_list``；行字段为 ``order``（排名）/
    ``code`` / ``name`` / ``rate``（人气值）/ ``tag.popularity_tag``。
    """
    rows: list[dict] = []
    board = _text(payload.get("board")) or "ths_hot"
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    items = (data or {}).get("stock_list") if data else payload.get("list")
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue
        code = _text(item.get("code")) or _text(item.get("SECURITY_CODE"))
        rank = _int(item.get("order")) or _int(item.get("rank")) or index
        if not code:
            continue
        rows.append({
            "code": code, "board": board, "rank": rank,
            "heat": _num(item.get("rate") or item.get("heat") or item.get("hot")),
        })
    return tuple(rows)


# ───────────────────────── 真实抓取（CI 不联网） ─────────────────────────

_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"


class HttpInfoFetcher:
    """真实 HTTP 抓取器（``urllib``，无第三方依赖）。

    端点与参数按 T-L0-010 各叶子的源定义实现，并经 **2026-09-27 真实探测**校正；
    **真实网络行为只在 ``tests/live/`` 验证**（``pytest -m live``），CI 用注入的 Fake。
    """

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def fetch_task(self, task_key: str, window: tuple[str, str]) -> InfoRows:
        handler = getattr(self, f"_fetch_{task_key}", None)
        if handler is None:
            raise InfoValidationError(f"真实抓取器未实现任务 {task_key!r}")
        try:
            return handler(window)
        except (InfoFetchUnavailableError, InfoValidationError):
            raise  # 源不可达 / 能力缺口（如「按公司接口」）——原样透出，不吞成抓取失败
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise InfoFetchUnavailableError(f"源不可达：{exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 统一翻译为抓取失败
            raise InfoFetchError(f"抓取失败：{exc}") from exc

    # ── HTTP 基元 ──

    def _request(self, url: str, *, params: dict | None = None,
                 data: dict | None = None, json_body: Any = None,
                 referer: str | None = None) -> Any:
        """发一次请求并解析 JSON（JSONP 自动剥壳）。

        **查询参数一律经 ``urlencode``**——东财的 ``filter`` 含 ``<`` / ``>``，
        直接拼进 URL 会被拒（实测 400）。
        """
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        if referer:
            headers["Referer"] = referer
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            raw = response.read().decode("utf-8", "ignore").strip()
        return self._unwrap(raw)

    @staticmethod
    def _unwrap(raw: str) -> Any:
        """JSON / JSONP（``cb(...)``）剥壳。"""
        if raw.startswith(("{", "[")):
            return json.loads(raw)
        start = raw.find("(")
        end = raw.rfind(")")
        if start != -1 and end > start:
            inner = raw[start + 1:end].strip()
            if inner.startswith(("{", "[")):
                return json.loads(inner)
        raise InfoFetchError(f"无法解析源端载荷：{raw[:120]!r}")

    @staticmethod
    def _datacenter(report_name: str, **extra: Any) -> dict:
        """东财数据中心统一查询参数（**filter 含比较符，必须 urlencode**）。"""
        params: dict[str, Any] = {
            "reportName": report_name, "columns": "ALL",
            "pageNumber": "1", "pageSize": "500",
            "source": "WEB", "client": "WEB",
        }
        params.update(extra)
        return params

    # ── 公告（主源 + 覆盖分片备胎） ──

    def _fetch_info_announcement_cninfo(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data={"stock": "", "tabName": "fulltext", "pageSize": "30",
                  "pageNum": "1", "seDate": f"{window[0]}~{window[1]}",
                  "column": "szse", "isHLtitle": "true"},
            referer="https://www.cninfo.com.cn/new/disclosure",
        )
        return {"announcement": parse_announcement_cninfo(payload)}

    def _fetch_info_announcement_szse(self, window: tuple[str, str]) -> InfoRows:
        """深交所官方（**JSON body**；不带 ``stock`` 即全市场，2026-09-27 实测）。"""
        payload = self._request(
            "https://www.szse.cn/api/disc/announcement/annList",
            json_body={"channelCode": ["listedNotice_disc"], "pageSize": 50,
                       "pageNum": 1, "seDate": [window[0], window[1]]},
            referer="https://www.szse.cn/disclosure/listed/notice/index.html",
        )
        return {"announcement": parse_announcement_szse(payload)}

    def _fetch_info_announcement_em(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://np-anotice-stock.eastmoney.com/api/security/ann",
            params={"sr": "-1", "page_size": "50", "page_index": "1",
                    "ann_type": "A", "client_source": "web",
                    "begin_time": window[0], "end_time": window[1]},
        )
        return {"announcement": parse_announcement_em(payload)}

    # ── 龙虎榜 ──

    def _fetch_info_dragon_tiger_em(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(_DATACENTER, params=self._datacenter(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter=f"(TRADE_DATE>='{window[0]}')(TRADE_DATE<='{window[1]}')",
            sortColumns="BILLBOARD_NET_AMT", sortTypes="-1",
        ))
        return parse_dragon_tiger_em(payload)

    def _fetch_info_dragon_tiger_szse(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://www.szse.cn/api/report/ShowReport/data",
            params={"SHOWTYPE": "JSON", "CATALOGID": "1842_xxpl", "TABKEY": "tab1",
                    "txtStart": window[0], "txtEnd": window[1], "random": "0.9"},
            referer="https://www.szse.cn/disclosure/supervision/dealinfo/index.html",
        )
        return parse_dragon_tiger_szse(payload)

    # ── 股东户数 ──

    def _fetch_info_shareholder_num_em(self, window: tuple[str, str]) -> InfoRows:
        _ = window
        payload = self._request(_DATACENTER, params=self._datacenter(
            "RPT_HOLDERNUMLATEST", sortColumns="END_DATE", sortTypes="-1"))
        return {"shareholder_num": parse_shareholder_num_em(payload)}

    # ── 舆情 ──

    def _fetch_info_sentiment_hot(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
            params={"stock_type": "a", "type": "hour", "list_type": "normal"},
        )
        payload = {**payload, "board": "ths_hot"}
        _ = window
        return {"sentiment_hot": parse_sentiment_hot_ths(payload)}

    def _fetch_info_sentiment_qa_irm(self, window: tuple[str, str]) -> InfoRows:
        """互动易——**按公司**的两步流程（先查 ``secid``，再查问答）。

        ⚠️ 本接口**没有全市场流**：需给定股票代码。当前任务形状按「全市场窗口」
        定义，故此处**显式**报告该缺口而非返回空结果（见 T-L0-010 遗留册）。
        """
        _ = window
        raise InfoValidationError(
            "互动易为**按公司**接口（无全市场流）：须给定股票代码全域后逐只查询——"
            "任务形状待调整（已登记 T-L0-010 遗留册）"
        )

    def _fetch_info_sentiment_qa_sse(self, window: tuple[str, str]) -> InfoRows:
        """上证e互动——同样**按公司**，且需先在分页公司列表里定位 ``uid``。"""
        _ = window
        raise InfoValidationError(
            "上证e互动为**按公司**接口（须先在公司列表里定位 uid）：任务形状待调整"
            "（已登记 T-L0-010 遗留册）"
        )
