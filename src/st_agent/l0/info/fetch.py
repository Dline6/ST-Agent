"""信息面抓取层（T-L0-010.2–.5）：协议 + 纯解析 + stdlib HTTP 实现。

**分层**：
- :class:`InfoFetcher`——协议，同步引擎只认它（测试注入 Fake，真实管道注入
  :class:`HttpInfoFetcher`）
- ``parse_*``——**纯函数**：源方载荷 → 规范化行。**离线可单测**（CI 覆盖）
- :class:`HttpInfoFetcher`——真实取数（``urllib``，无第三方依赖）。**CI 不联网**，
  该层只在 ``tests/live/`` 单跑（``pytest -m live``）

端点知识移植自开源项目 ``simonlin1212/a-stock-data``（Apache-2.0，**取知识不取
依赖**）：其四域端点均为纯 HTTP + JSON，主备映射与防封参数见
``项目管理/遗留问题/L1-遗留问题.md`` 的 `C1` 补充。

**出网纪律**：一切请求经 L0 出网网关（02 §6）——本层只做「发一次请求并返回
载荷」，审计与限流在网关侧。
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
    "parse_dragon_tiger_exchange",
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
    """宽松转数值（``None`` / 空串 / ``-`` → ``None``；不可解析 → ``None``）。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "--", "—"):
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
    return text or None


def _day(value: Any) -> str | None:
    """时间戳/日期串 → ``YYYY-MM-DD``（毫秒整数、ISO 串、``YYYYMMDD`` 均可）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
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
    if not text:
        return None
    from datetime import datetime
    if text.isdigit():
        number = int(text)
        seconds = number / 1000 if number > 10_000_000_000 else number
        return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")
    return text[:16]


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
    """深交所官方 ``annList`` → 公告行（覆盖**仅深市**）。"""
    rows: list[dict] = []
    for item in (payload.get("data") or []):
        title = _text(item.get("title"))
        if not title:
            continue
        attach = _text(item.get("attachPath")) or ""
        rows.append({
            "code": None,  # 深交所载荷以 ``stock`` 参数为准，由调用方回填
            "title": title,
            "ann_type": None,
            "pub_date": _day(item.get("publishTime")),
            "url": ("https://disc.static.szse.cn/download" + attach) if attach else None,
            "text": "",
        })
    return tuple(rows)


def parse_announcement_em(payload: dict) -> tuple[dict, ...]:
    """东财公告 ``security/ann`` → 公告行（覆盖**仅沪市**）。"""
    rows: list[dict] = []
    for item in ((payload.get("data") or {}).get("list") or []):
        code = _text(item.get("codes")) or ""
        code = code.split(",")[0] if code else None
        title = _text(item.get("title"))
        if not title:
            continue
        art = _text(item.get("art_code")) or ""
        rows.append({
            "code": code,
            "title": title,
            "ann_type": _text(item.get("notice_type")),
            "pub_date": _day(item.get("notice_date")),
            "url": f"https://pdf.dfcfw.com/pdf/H2_{art}_1.pdf" if art else None,
            "text": "",
        })
    return tuple(rows)


# ───────────────────────── 纯解析：龙虎榜 ─────────────────────────

def parse_dragon_tiger_em(payload: dict) -> InfoRows:
    """东财龙虎榜 → ``{dragon_tiger: 上榜记录, dragon_tiger_seat: 席位 TOP5}``。

    上榜记录**按 ``(code, trade_date)`` 归一**（同标的同日多原因合并为一行，
    使跨源去重不依赖原因串，D-030）。
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


def parse_dragon_tiger_exchange(payload: dict) -> InfoRows:
    """沪深交易所官方 → ``{dragon_tiger_seat: 明细, dragon_tiger: 榜首行}``。

    官方给**一手营业部明细**，排名由金额降序推导（故席位以官方为主源）。
    """
    seats: list[dict] = []
    tops: dict[tuple[str, str], dict] = {}
    for item in (payload.get("rows") or []):
        code = _text(item.get("code"))
        day = _day(item.get("trade_date"))
        if not code or not day:
            continue
        stock_id = to_stock_id(code, _text(item.get("market")))
        side = item.get("side", "buy")
        rank = _int(item.get("rank")) or 1
        buy, sell = _num(item.get("buy_amount")), _num(item.get("sell_amount"))
        seats.append({
            "code": stock_id, "trade_date": day, "side": side, "rank": rank,
            "seat_name": _text(item.get("seat_name")) or "",
            "buy_amount": buy, "sell_amount": sell,
            "net_amount": None if buy is None and sell is None
            else (buy or 0.0) - (sell or 0.0),
        })
        row = tops.setdefault((stock_id, day), {
            "code": stock_id, "trade_date": day, "reasons": None,
            "net_amount": None, "buy_amount": None, "sell_amount": None,
            "turnover": None,
        })
        reason = _text(item.get("reason"))
        if reason:
            row["reasons"] = f"{row['reasons']}；{reason}" if row["reasons"] else reason
    return {"dragon_tiger_seat": tuple(seats), "dragon_tiger": tuple(tops.values())}


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
    """互动易（深市）→ 问答行。``answer`` 为 ``None`` 表示**尚未回复**（合法态）。"""
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
    """同花顺热榜 / 东财人气榜 → 热度快照行（**易腐**：值是「此刻」排名）。"""
    rows: list[dict] = []
    board = _text(payload.get("board")) or "ths_hot"
    stamp = _stamp(payload.get("snapshot_at"))
    for item in (payload.get("data") or payload.get("list") or []):
        code = _text(item.get("code")) or _text(item.get("SECURITY_CODE"))
        rank = _int(item.get("rank")) or _int(item.get("order"))
        if not code or rank is None:
            continue
        rows.append({
            "code": code, "board": board, "rank": rank,
            "heat": _num(item.get("heat") or item.get("hot")),
        })
    return tuple(rows)


# ───────────────────────── 真实抓取（CI 不联网） ─────────────────────────

class HttpInfoFetcher:
    """真实 HTTP 抓取器（``urllib``，无第三方依赖）。

    端点与参数按 T-L0-010 各叶子的源定义实现；**真实网络行为只在
    ``tests/live/`` 验证**（``pytest -m live``），CI 用注入的 Fake。
    """

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def fetch_task(self, task_key: str, window: tuple[str, str]) -> InfoRows:
        handler = getattr(self, f"_fetch_{task_key}", None)
        if handler is None:
            raise InfoValidationError(f"真实抓取器未实现任务 {task_key!r}")
        try:
            return handler(window)
        except InfoFetchUnavailableError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise InfoFetchUnavailableError(f"源不可达：{exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 统一翻译为抓取失败
            raise InfoFetchError(f"抓取失败：{exc}") from exc

    # ── HTTP 基元 ──

    def _request(self, url: str, *, data: dict | None = None,
                 referer: str | None = None) -> Any:
        headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        if referer:
            headers["Referer"] = referer
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
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

    # ── 公告（主源 + 两个覆盖分片备胎） ──

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
        payload = self._request(
            "https://www.szse.cn/api/disc/announcement/annList",
            data={"channelCode": ["listedNotice_disc"], "pageSize": 30,
                  "pageNum": 1, "seDate": list(window)},
            referer="https://www.szse.cn/disclosure/listed/notice/index.html",
        )
        return {"announcement": parse_announcement_szse(payload)}

    def _fetch_info_announcement_em(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://np-anotice-stock.eastmoney.com/api/security/ann"
            f"?sr=-1&page_size=50&page_index=1&ann_type=A&client_source=web"
            f"&f_node=0&s_node=0&begin_time={window[0]}&end_time={window[1]}"
        )
        return {"announcement": parse_announcement_em(payload)}

    # ── 龙虎榜 ──

    def _fetch_info_dragon_tiger_em(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://datacenter-web.eastmoney.com/api/data/v1/get"
            "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL"
            f"&filter=(TRADE_DATE<='{window[1]}')(TRADE_DATE>='{window[0]}')"
            "&pageNumber=1&pageSize=500&sortColumns=TRADE_DATE&sortTypes=-1"
        )
        return parse_dragon_tiger_em(payload)

    def _fetch_info_dragon_tiger_exchange(self, window: tuple[str, str]) -> InfoRows:
        day = window[0]
        szse = self._request(
            "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON"
            f"&CATALOGID=1842_xxpl&TABKEY=tab1&txtStart={day}&txtEnd={day}&random=0.9",
            referer="https://www.szse.cn/disclosure/supervision/dealinfo/index.html",
        )
        sse = self._request(
            "https://query.sse.com.cn/infodisplay/showTradePublicFile.do"
            f"?jsonCallBack=cb&isPagination=false&dateTx={day}",
            referer="https://www.sse.com.cn/disclosure/diclosure/public/",
        )
        return parse_dragon_tiger_exchange(_merge_exchange(szse, sse))

    # ── 股东户数 ──

    def _fetch_info_shareholder_num_em(self, window: tuple[str, str]) -> InfoRows:
        _ = window
        payload = self._request(
            "https://datacenter-web.eastmoney.com/api/data/v1/get"
            "?reportName=RPT_HOLDERNUMLATEST&columns=ALL"
            "&pageNumber=1&pageSize=500&sortColumns=END_DATE&sortTypes=-1"
        )
        return {"shareholder_num": parse_shareholder_num_em(payload)}

    # ── 舆情 ──

    def _fetch_info_sentiment_qa_irm(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://irm.cninfo.com.cn/newircs/company/question"
            f"?_t=1&pageSize=50&pageNum=1&startDay={window[0]}&endDay={window[1]}"
        )
        return {"sentiment_qa": parse_sentiment_qa_irm(payload)}

    def _fetch_info_sentiment_qa_sse(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://sns.sseinfo.com/ajax/feeds.do"
            f"?type=11&pageSize=50&page=1&startDate={window[0]}&endDate={window[1]}"
        )
        return {"sentiment_qa": parse_sentiment_qa_irm(payload)}

    def _fetch_info_sentiment_hot(self, window: tuple[str, str]) -> InfoRows:
        payload = self._request(
            "https://eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/hour"
            "/stock_list.json"
        )
        payload = {**payload, "board": "ths_hot", "snapshot_at": window[0]}
        return {"sentiment_hot": parse_sentiment_hot_ths(payload)}


def _merge_exchange(szse: Any, sse: Any) -> dict:
    """把沪深两交易所的官方龙虎榜载荷并成统一 ``rows`` 形态。"""
    rows: list[dict] = []
    for item in ((szse or {}).get("data") or []):
        rows.append({
            "code": _text(item.get("zqdm")), "market": "sz",
            "trade_date": (szse or {}).get("date"), "side": "buy", "rank": 1,
            "seat_name": _text(item.get("zqjc")) or "",
            "buy_amount": _num(item.get("cjje")), "sell_amount": None,
            "reason": _text(item.get("plyy")),
        })
    text = ""
    if isinstance(sse, dict):
        text = "\n".join(sse.get("fileContents") or [])
    elif isinstance(sse, str):
        text = sse
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",") if c.strip()]
        if len(cells) >= 3 and cells[0][:2].isdigit():
            rows.append({
                "code": _text(cells[0]), "market": "sh", "trade_date": None,
                "side": "buy", "rank": 1, "seat_name": cells[1],
                "buy_amount": _num(cells[2]), "sell_amount": None, "reason": None,
            })
    return {"rows": rows}
