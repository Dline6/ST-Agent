"""信息面抓取层（T-L0-010.2–.5）：协议 + 纯解析 + stdlib HTTP 实现。

**分层**：
- :class:`InfoFetcher`——协议，同步引擎只认它（测试注入 Fake，真实管道注入
  :class:`HttpInfoFetcher`）
- ``parse_*``——**纯函数**：源方载荷 → 规范化行。**离线可单测**（CI 覆盖），
  夹具取自**真实载荷形状**（2026-09-27 实测）
- :class:`HttpInfoFetcher`——真实取数（``urllib``，无第三方依赖）。**CI 不联网**，
  该层只在 ``tests/live/`` 单跑（``pytest -m live``）。**唯一例外**：巨潮数据中心的
  ``Accept-Enckey`` 签名用 ``cryptography``（**已是本项目核心依赖**，见
  ``l0/storage/crypto.py``）——不引入新依赖，也不引 JS 引擎（见 T-L0-014 段）。

端点知识移植自开源项目 ``simonlin1212/a-stock-data``（Apache-2.0，**取知识不取
依赖**），并按 2026-09-27 的**真实探测**校正了三处与原文不符之处（见各解析件注释）。
同日**复核**（T-L0-012）又补齐两处：深交所席位明细走 ``1842_detal``（契约由
``1842_xxpl`` 行内 ``bz`` 自带）、沪市官方走上交所每日交易信息（**定宽文本**）
——原记「席位明细暂无已验证源」与「沪市官方未接入」均已作废。

T-L0-013 再校正两处口径：

- **沪市舆情有全市场流**——``sns.sseinfo.com/ajax/feeds.do`` 按 ``type`` 直接分页，
  原记「上证e互动为**按公司**接口、须先定位 ``uid``」对沪市**不成立**（深市互动易
  仍无全市场流，本轮不覆盖，见 [D-037](../../../../项目管理/决策日志.md)）。
- **``announcement.ann_type`` 无统一语义**——三源各给各的（东财＝**证券类别/板块码**、
  巨潮＝恒 ``NULL``、深交所＝大类字段可空），**不是**「公告内容类型」；内容类型若需要，
  走巨潮**请求侧 ``category``**（26 类），见 [05](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)
  与 [D-038](../../../../项目管理/决策日志.md)。各解析件的 ``ann_type`` 取值处已就地注明。

T-L0-014 补一处股东户数口径（2026-09-27 实测）：

- **巨潮有股东户数源、按报告期全市场**——``p_sysapi1034?rdate=<YYYYMMDD>``（``rdate``
  仅季末、起点 2017Q1），鉴权头 ``Accept-Enckey`` ＝ 固定密钥 AES-128-CBC-PKCS7
  加密当前 unix 秒（算法出自 akshare ``data/cninfo.js`` 的 ``getResCode1()``，
  **取知识不取依赖**）→ 该源作**备胎**，回补东财 ``RPT_HOLDERNUMLATEST``
  （latest-only、无报告期参数）拿不到的历史季末。
- **东财 ``avg_shares`` 原先恒 ``NULL``**——解析件读的 ``AVG_FREE_SHARES`` 在真实
  载荷中**不存在**（真实键为 ``AVG_HOLD_NUM``）；已校正，且与巨潮 ``F004N``
  实测**同口径**（总股本/户数）。

**出网纪律**：一切请求经 L0 出网网关（02 §6）——本层只做「发一次请求并返回
载荷」，审计与限流在网关侧。**查询参数一律经 ``urlencode``**：东财的 ``filter``
含 ``<`` / ``>``，直接拼进 URL 会被拒（实测 400）。
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any, Protocol

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
    "parse_dragon_tiger_sse",
    "parse_dragon_tiger_szse",
    "parse_dragon_tiger_szse_detail",
    "parse_sentiment_hot_ths",
    "parse_sentiment_qa_irm",
    "parse_sentiment_qa_sse",
    "parse_shareholder_num_cninfo",
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
    """巨潮 ``hisAnnouncement/query`` → 公告行（覆盖**全市**）。

    ⚠️ 本源 ``ann_type`` **恒为 ``None``**：响应的 ``announcementTypeName`` 实测
    30 行全空（2026-09-27），列表返回**不含公告内容类型**；另给的
    ``announcementType`` 是**数字码串**且各类目**重叠**（``01010503`` 出现在全部
    类目）→ 无法由响应反推类目。内容类型须走**请求侧 ``category``**（26 类），
    见 [05](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)
    与 [D-038](../../../../项目管理/决策日志.md)。
    """
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

    ⚠️ 本源 ``ann_type`` **恒为 ``None``**：实测该窗口 20 行的 ``bigCategoryId`` /
    ``smallCategoryId`` 全为 null；即便有值，那也是**源方大类字段**（非统一口径的
    内容类型），故不填。见 [D-038](../../../../项目管理/决策日志.md)。
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
    **不是**分隔字符串。

    ⚠️ ``codes[].ann_type`` 是**证券类别/板块码**（实测 ``A,SHA`` 沪深A·沪市 /
    ``A,SZA`` 深市 / ``A,CYB`` 创业板 / ``A,KCB`` 科创板 / ``A,BJA`` 北交所），
    **不是公告内容类型**（[F5] 的原意即此）——本源**不承载**内容类型。该值仍落
    ``announcement.ann_type`` 列（列上已有的信息不丢弃），但**不得**按「公告内容
    类型」消费；内容类型须走巨潮请求侧 ``category``，见 [D-038](../../../../项目管理/决策日志.md)。
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
    ``plyy``（上榜原因）/ ``bz``（内嵌明细表钻取链接）。

    ⚠️ 该端点**只给上榜记录**；席位明细在**另一跳**——每行的 ``bz`` 里带着
    ``CATALOGID=1842_detal&…&DQRQ=&ZQDM=&ZBDM=``，须由抓取器逐条钻取后再交给
    :func:`parse_dragon_tiger_szse_detail`。故本解析件**不产出**
    ``dragon_tiger_seat`` 行（那是钻取件的产物）。
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


def _szse_table_rows(payload: Any) -> list[dict]:
    """``ShowReport/data`` 响应 → 行列表（顶层实测为**列表**，元素含 ``data``）。"""
    if isinstance(payload, list):
        blocks = payload
    elif isinstance(payload, dict):
        blocks = [payload]
    else:
        raise InfoValidationError(
            f"深交所 ShowReport 载荷形态不符：期望列表或字典，得到 {type(payload).__name__}")
    rows: list[dict] = []
    for block in blocks:
        if isinstance(block, dict):
            rows.extend(r for r in (block.get("data") or []) if isinstance(r, dict))
    return rows


def _szse_drill_params(markup: Any) -> dict[str, str]:
    """从 ``1842_xxpl`` 行的 ``bz`` 里解出**明细表调用参数**。

    实测形态（2026-07-10）：``bz`` 是一段 HTML，其中
    ``a-param='/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1842_detal&TABKEY=tab1,tab2&DQRQ=…&ZQDM=…&ZBDM=…'``
    ——**端点与参数由源端自带**，故不得硬编码（``ZBDM`` 随上榜原因变）。
    解不出时返回空字典（该行没有钻取入口）。
    """
    text = _text(markup) or ""
    match = re.search(r"a-param=['\"]([^'\"]+)['\"]", text)
    if not match:
        return {}
    query = match.group(1).partition("?")[2]
    return dict(urllib.parse.parse_qsl(query, keep_blank_values=True))


_SEAT_SIDES = {"买": "buy", "卖": "sell"}


def _seat_key(label: Any) -> tuple[str, int] | None:
    """``mmlb``（如 ``买1`` / ``卖3``）→ ``(side, rank)``；认不出返回 ``None``。"""
    text = _text(label) or ""
    side = _SEAT_SIDES.get(text[:1])
    rank = _int(text[1:])
    if side is None or not rank:
        return None
    return (side, rank)


def parse_dragon_tiger_szse_detail(payload: Any, *, code: str,
                                   trade_date: str) -> tuple[dict, ...]:
    """深交所 ``1842_detal``（``TABKEY=tab1,tab2``）→ **席位明细**行。

    实测形态（2026-07-10）：顶层是**列表**，``tab1`` 给该
    (证券, 日期, 上榜原因) 的明细表头，**``tab2`` 给营业部席位**——字段
    ``mmlb``（``买1`` … ``卖5``）/ ``zsmc``（会员营业部名称）/
    ``mrje``（买入金额，元）/ ``mcje``（卖出金额，元）。

    按 ``metadata.tabkey == 'tab2'`` 定位席位块：**缺该块即结构变更**（抛错），
    而**该块存在但为空**是「这只标的当日没有席位明细」的正常情形（返回空元组）。
    """
    blocks = payload if isinstance(payload, list) else [payload]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str((block.get("metadata") or {}).get("tabkey")) != "tab2":
            continue
        seats: list[dict] = []
        for row in (block.get("data") or []):
            if not isinstance(row, dict):
                continue
            key = _seat_key(row.get("mmlb"))
            if key is None:
                raise InfoValidationError(
                    f"深交所席位明细的 mmlb 认不出买卖方向：{row.get('mmlb')!r}")
            side, rank = key
            buy, sell = _num(row.get("mrje")), _num(row.get("mcje"))
            name = _text(row.get("zsmc"))
            if not name:
                raise InfoValidationError(
                    f"深交所席位明细缺少 zsmc（营业部名称）：{row!r}")
            seats.append({
                "code": to_stock_id(code, "sz"), "trade_date": trade_date,
                "side": side, "rank": rank, "seat_name": name,
                "buy_amount": buy, "sell_amount": sell,
                "net_amount": None if buy is None or sell is None else buy - sell,
            })
        return tuple(seats)
    raise InfoValidationError(
        f"深交所席位明细载荷里没有 tab2 块（{code} {trade_date}）——"
        "端点结构可能已变（期望 metadata.tabkey = 'tab2'）")


# ───────────────────────── 纯解析：上交所每日交易信息 ─────────────────────────

_SSE_SECTION = re.compile(r"^\s*[一二三四五六七八九十]+、")
_SSE_STOCK_HEADER = re.compile(r"证券代码[:：]\s*(\d{6})")
_SSE_TABLE_ROW = re.compile(r"^\s*\(\d+\)\s+(\d{6})\s")
_SSE_SEAT_ROW = re.compile(r"^\s*\((\d+)\)\s+(.+)\s+([\d,]+\.\d{2})\s*$")
_SSE_BUY_HEADER = re.compile(r"买入营业部名称")
_SSE_SELL_HEADER = re.compile(r"卖出营业部名称")


def parse_dragon_tiger_sse(lines, *, trade_date: str) -> InfoRows:
    """上交所「每日交易信息」（**定宽文本**）→ ``{上榜记录, 席位明细}``。

    实测形态（2026-07-10，510 行）：分节标题即**上榜原因**（``一、…`` 到 ``十四、…``）；
    每只有明细的证券出现 ``证券代码: 600664 … 证券简称: …`` 头，其后是
    ``买入营业部名称: … 累计买入金额(元):`` 与 ``卖出营业部名称: … 累计卖出金额(元):``
    两个块，席位列形如 ``(1) 某某证券营业部   124048015.00``。

    上榜记录取**两处**：明细块的 ``证券代码:`` 头，以及「前五只证券」节的
    ``(N) 600664 …`` 表行——两者同键（``code`` + 节标题为原因），由引擎按业务键合并。

    行的 ``market`` 显式标 ``sh``：沪市代码段含 ``5xx`` / ``1xx``（ETF / 可转债），
    靠号段推断会把 ``1xx`` 误判为深市（[D-032](../../../../项目管理/决策日志.md) 的
    不误标原则）。
    """
    tops: dict[str, dict] = {}
    seats: dict[tuple[str, str, int], dict] = {}
    section, side, code = "", None, ""
    for line in lines or ():
        stripped = line.strip()
        if not stripped:
            continue
        if _SSE_SECTION.match(line):
            section = _SSE_SECTION.sub("", stripped).rstrip(":：")
            side, code = None, ""
            continue
        header = _SSE_STOCK_HEADER.search(line)
        if header:
            code, side = header.group(1), None
            tops.setdefault(code, _sse_top(code, trade_date, section))
            continue
        if _SSE_BUY_HEADER.match(stripped):
            side = "buy"
            continue
        if _SSE_SELL_HEADER.match(stripped):
            side = "sell"
            continue
        table_row = _SSE_TABLE_ROW.match(line)
        if table_row:
            found = table_row.group(1)
            tops.setdefault(found, _sse_top(found, trade_date, section))
            continue
        if side is None or not code:
            continue
        seat_row = _SSE_SEAT_ROW.match(line)
        if not seat_row:
            continue
        rank = _int(seat_row.group(1))
        name = _text(seat_row.group(2))
        amount = _num(seat_row.group(3))
        if not rank or not name:
            raise InfoValidationError(f"上交所席位列解析不出排名或名称：{line!r}")
        seats.setdefault((code, side, rank), {
            "code": code, "trade_date": trade_date, "side": side, "rank": rank,
            "seat_name": name, "market": "sh",
            "buy_amount": amount if side == "buy" else None,
            "sell_amount": amount if side == "sell" else None,
            "net_amount": None,
        })
    if lines and not section:
        raise InfoValidationError(
            "上交所每日交易信息里没有「一、…」分节标题——页面结构可能已变")
    return {"dragon_tiger": tuple(tops.values()),
            "dragon_tiger_seat": tuple(seats.values())}


def _sse_top(code: str, trade_date: str, section: str) -> dict:
    """上交所上榜记录行（金额列语义与净值不同，**不臆造**，留空）。"""
    return {"code": code, "trade_date": trade_date, "market": "sh",
            "reasons": section or None, "net_amount": None, "buy_amount": None,
            "sell_amount": None, "turnover": None}


# ───────────────────────── 纯解析：股东户数 ─────────────────────────

def parse_shareholder_num_em(payload: dict) -> tuple[dict, ...]:
    """东财 ``RPT_HOLDERNUMLATEST`` → 股东户数行（**每票最新一期**）。

    ⚠️ 该报告是 **latest**——**无报告期参数**，故只能给每票最新一期；更早季末的
    回补由 :func:`parse_shareholder_num_cninfo`（巨潮按报告期）承担（T-L0-014）。
    ``END_DATE`` 可为**期中变动日**（非季末），合法。

    ``avg_shares`` 取 ``AVG_HOLD_NUM``（户均持股）——原实现读的 ``AVG_FREE_SHARES``
    在真实载荷中**不存在**，致该列恒 ``NULL``（T-L0-014 实测校正）。
    """
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
            "avg_shares": _num(item.get("AVG_HOLD_NUM")),
        })
    return tuple(rows)


def parse_shareholder_num_cninfo(payload: dict) -> tuple[dict, ...]:
    """巨潮 ``p_sysapi1034`` → 股东户数行（**按报告期全市场**）。

    真实形状（2026-09-27 实测）：``{total, count, resultcode, records: [...]}``，
    行字段为**匿名列名** ``F001N``…``F006N``——按位置与量纲对应：``F001N``＝本期
    股东人数 / ``F002N``＝上期 / ``F003N``＝增幅% / ``F004N``＝人均持股（与东财
    ``AVG_HOLD_NUM`` **实测同口径**，皆为总股本/户数）。``change_num`` 由
    本期 − 上期推导（与东财 ``HOLDER_NUM_CHANGE`` 同义）。

    ``records`` **缺失**是结构变更（抛错）；**存在但为空**是该报告期无数据
    ——两态必须分开，否则「源挂了」会被读成「没数据」（沿用 T-L0-012 范式）。
    """
    if not isinstance(payload, dict) or "records" not in payload:
        raise InfoValidationError(
            "巨潮股东户数载荷缺少 records——页面结构可能已变"
            f"（resultcode={payload.get('resultcode') if isinstance(payload, dict) else None}）"
        )
    rows: list[dict] = []
    for item in payload.get("records") or ():
        code = _text(item.get("SECCODE"))
        stat = _day(item.get("ENDDATE"))
        if not code or not stat:
            continue
        holders = _int(item.get("F001N"))
        previous = _int(item.get("F002N"))
        rows.append({
            "code": to_stock_id(code), "stat_date": stat,
            "holder_num": holders,
            "change_num": (None if holders is None or previous is None
                           else holders - previous),
            "change_ratio": _num(item.get("F003N")),
            "avg_shares": _num(item.get("F004N")),
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


_SSE_FEED_ITEM = '<div class="m_feed_item'
_SSE_FEED_NOTE = "m_feed_note"
"""源端在翻到末页时给的「此时没有更多内容」标记（实测：载荷仅约 108 字节）。"""

_SSE_ANSWER_SPLIT = 'class="m_feed_detail m_qa"'
"""回答块的开头（带引号，故**不会**误命中提问块的 ``m_qa_detail``）。"""

_SSE_FEED_TXT = re.compile(r'class="m_feed_txt"[^>]*>(.*?)</div>', re.S)
_SSE_FEED_STOCK = re.compile(r"<a [^>]*>:([^<]*?)\((\d{6})\)</a>")
_SSE_FEED_TIME = re.compile(r"<span>(\d{4})年(\d{2})月(\d{2})日 (\d{2}:\d{2})</span>")
_SSE_ANSWER_FACE = re.compile(r'<a class="ansface"[^>]*\btitle="([^"]*)"')
_HTML_TAG = re.compile(r"<[^>]+>")


def _sse_stamp(parts: tuple[str, str, str, str]) -> str:
    """``(年, 月, 日, 时:分)`` → ``YYYY-MM-DD HH:MM``。"""
    year, month, day, clock = parts
    return f"{year}-{month}-{day} {clock}"


def _sse_order_key(kind: str, row: dict) -> str:
    """该流的**排序键**（即翻页停止判据所依）：``11`` 按回复时间、``10`` 按提问时间。

    入库的 ``ask_time`` 对 ``type=11`` **不单调**（旧提问可能刚被回复），故不能拿它
    当游标——这条区分是「翻页至窗口起点即停」能否成立的关键。
    """
    if kind == "11":
        return row.get("answer_time") or row["ask_time"]
    return row["ask_time"]


def _html_text(fragment: str) -> str:
    """HTML 片段 → 纯文本（去标签、解实体、折叠空白）。"""
    return " ".join(unescape(_HTML_TAG.sub(" ", fragment)).split())


def parse_sentiment_qa_sse(payload: str) -> tuple[dict, ...]:
    """上证e互动**全市场流**（``ajax/feeds.do``）→ 问答行（覆盖沪市）。

    真实形状（2026-09-27 实测）：响应是 **HTML 片段**（非 JSON），每条问答一个
    ``div.m_feed_item``。``type=10``（最新提问）的条目多一个类 ``m_question``，
    且**没有回答块**——未回复是合法态（``answer=None``）；``type=11``（最新已回复）
    另有 :data:`_SSE_ANSWER_SPLIT` 的回答块（回答者取自 ``a.ansface`` 的 ``title``）。
    标的由提问正文首部的 ``:名称(代码)`` 锚点给出；时间为 ``YYYY年MM月DD日 HH:MM``。

    ``market`` 显式标 ``sh``：该口为沪市专用，而沪市代码段含 ``5xx`` / ``1xx``
    （ETF / 可转债），靠号段推断会误判为深市（同 :func:`parse_dragon_tiger_sse`
    的不误标原则，D-032）。

    ``answer_time`` 在 ``sentiment_qa`` 表里**没有列**——它只作抓取器的**翻页停止
    判据**（该流按回复时间倒序，而入库的 ``ask_time`` 不单调，见
    :meth:`HttpInfoFetcher._fetch_info_sentiment_qa_sse`）。

    **fail-fast**：既无 ``m_feed_item`` 又无 ``m_feed_note`` → 载荷结构已变，抛错
    而**不**返回空——「窗口耗尽」与「结构变更」必须可区分（对齐 T-L0-012 的两态判据）。
    """
    blocks = payload.split(_SSE_FEED_ITEM)[1:] if payload else []
    if not blocks:
        if _SSE_FEED_NOTE in (payload or ""):
            return ()  # 源端显式告知「没有更多内容」——窗口耗尽，非结构变更
        raise InfoValidationError(
            "上证e互动 feeds.do 载荷既无 m_feed_item 又无 m_feed_note"
            "——页面结构可能已变")
    rows: list[dict] = []
    for block in blocks:
        question_html, _, answer_html = block.partition(_SSE_ANSWER_SPLIT)
        stock = _SSE_FEED_STOCK.search(question_html)
        if stock is None:
            raise InfoValidationError(
                "上证e互动问答块里找不到「:名称(代码)」锚点——页面结构可能已变")
        text_match = _SSE_FEED_TXT.search(question_html)
        if text_match is None:
            raise InfoValidationError(
                "上证e互动问答块里找不到 m_feed_txt 正文——页面结构可能已变")
        question = _html_text(
            _SSE_FEED_STOCK.sub("", text_match.group(1), count=1))
        if not question:
            raise InfoValidationError(
                "上证e互动问答块的提问正文为空——页面结构可能已变")
        times = _SSE_FEED_TIME.findall(question_html)
        if not times:
            raise InfoValidationError(
                "上证e互动问答块里找不到提问时间——页面结构可能已变")
        answer: str | None = None
        answerer: str | None = None
        answer_time: str | None = None
        if answer_html:
            answered = _SSE_FEED_TXT.search(answer_html)
            if answered is not None:
                answer = _html_text(answered.group(1)) or None
            face = _SSE_ANSWER_FACE.search(answer_html)
            if face is not None:
                answerer = _text(unescape(face.group(1)))
            answer_times = _SSE_FEED_TIME.findall(answer_html)
            if answer_times:
                answer_time = _sse_stamp(answer_times[0])
        rows.append({
            "code": stock.group(2), "question": question, "answer": answer,
            "answerer": answerer, "ask_time": _sse_stamp(times[0]),
            "answer_time": answer_time, "market": "sh",
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

_SZSE_SHOWREPORT = "https://www.szse.cn/api/report/ShowReport/data"
_SZSE_REFERER = "https://www.szse.cn/disclosure/supervision/dealinfo/index.html"
_SZSE_PAGE_SIZE = 10
"""深交所 ``ShowReport`` 的分页步长——**实测固定 10**（``PAGESIZE`` 被忽略）。"""

_SZSE_MAX_PAGES = 50
"""翻页上限：源端分页行为若变化，宁可少拉也不要无限翻。"""

_SSE_TRADE_PUBLIC = "https://query.sse.com.cn/infodisplay/showTradePublicFile.do"
_SSE_REFERER = "https://www.sse.com.cn/disclosure/diclosure/public/"
"""上交所每日交易信息（路径里的 ``diclosure`` 是**源端原文拼写**，不可「纠正」）。"""

_SSE_FEED = "https://sns.sseinfo.com/ajax/feeds.do"
_SSE_FEED_REFERER = "https://sns.sseinfo.com/"
"""上证e互动**全市场流**（沪市专用投资者关系问答；响应为 HTML 片段）。"""

_SSE_FEED_PAGE_SIZE = 50
"""每页条数——**实测被源端尊重**（非固定步长：``pageSize=30`` 实测给 30 条）。"""

_SSE_FEED_MAX_PAGES = 120
"""翻页上限（兜底）：按每页 50 计覆盖 6000 条 ≥ 实测可见窗口（``type=11`` 约
5675 条 / ``type=10`` 约 4381 条——**条数上限**而非日期下界，更早条目不可回溯）。
日常一律按窗口起点提前停止，故此上限只在首轮触及。"""

_SSE_FEED_TYPES = ("10", "11")
"""``10`` 最新提问 / ``11`` 最新已回复。**先 10 后 11**：同一问答尚未回复时先入
``answer=None``，稍后被回复 → 本次抓取内由后到的「有回答」行覆盖同键行
（键 = ``(code, question, ask_time)``）。"""

_CNINFO_API = "https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1034"
"""巨潮数据中心「股东人数及持股集中度」（专题统计）——**按报告期**全市场。"""

_CNINFO_REFERER = "https://webapi.cninfo.com.cn/"
_CNINFO_KEY = b"1234567887654321"
"""``Accept-Enckey`` 的固定密钥（同时用作 IV）——AES-128-CBC。

出处：akshare ``data/cninfo.js`` 的 ``getResCode1()``（**取知识不取依赖**，
原实现用 ``py_mini_racer`` 跑 JS；此处以 ``cryptography`` 等价重写）。
"""

_CNINFO_EARLIEST = "2017-03-31"
"""源端起点（该专题只到 2017Q1）——早于此的季末**不请求**，避免必然落空。"""


def _cninfo_enckey(now: float | None = None) -> str:
    """``Accept-Enckey`` ＝ ``base64(AES-128-CBC-PKCS7(unix 秒))``（key = IV）。"""
    stamp = str(int(time.time() if now is None else now)).encode("ascii")
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    data = padder.update(stamp) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_CNINFO_KEY), modes.CBC(_CNINFO_KEY)).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode("ascii")


def _quarter_ends(window: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    """窗口内的季末 ``(YYYY-MM-DD, YYYYMMDD)``——巨潮 ``rdate`` **只接受季末**。

    下界取 ``_CNINFO_EARLIEST``（源端起点），故正常 2 年窗口恒得 8 期；即便调用方
    传入更早的窗口，也只会在源端真实存在的季末上请求。
    """
    start, end = window
    start = max(start, _CNINFO_EARLIEST)
    periods: list[tuple[str, str]] = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        for month, day in (("03", "31"), ("06", "30"), ("09", "30"), ("12", "31")):
            iso = f"{year}-{month}-{day}"
            if start <= iso <= end:
                periods.append((iso, f"{year}{month}{day}"))
    return tuple(periods)


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
                 referer: str | None = None,
                 headers: dict | None = None) -> Any:
        """发一次请求并解析 JSON（JSONP 自动剥壳）。

        **查询参数一律经 ``urlencode``**——东财的 ``filter`` 含 ``<`` / ``>``，
        直接拼进 URL 会被拒（实测 400）。

        ``headers`` 供源端要求的特殊头（巨潮 ``Accept-Enckey``）；**不得**用它
        覆盖出网纪律意义上的 UA / Accept 缺省值——缺省值先铺，自定义随后覆盖。
        """
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        merged = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*"}
        if referer:
            merged["Referer"] = referer
        if headers:
            merged.update(headers)
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            merged["Content-Type"] = "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            merged["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=merged)
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

    def _request_text(self, url: str, *, params: dict | None = None,
                      referer: str | None = None) -> str:
        """发一次请求并返回**文本**载荷（非 JSON 源，如上证e互动的 HTML 片段）。

        :meth:`_request` 只解 JSON / JSONP，撞上 HTML 会判「无法解析源端载荷」——
        故文本源走这条。
        """
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        headers = {"User-Agent": _UA, "Accept": "text/html, */*"}
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return response.read().decode("utf-8", "ignore")

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
        """深交所官方：``1842_xxpl``（上榜记录，**须翻页**）→ 逐条钻取 ``1842_detal`` 取席位。

        明细分两跳、且**由源端自带契约驱动**：每条上榜记录的 ``bz`` 里内嵌
        ``CATALOGID=1842_detal&…&DQRQ=&ZQDM=&ZBDM=``，``ZBDM`` 随上榜原因变
        （实测 ``0901`` / ``0902`` / ``0921`` / ``1001``），故不得硬编码。
        """
        day = window[0]
        raw_rows: list[dict] = []
        for page in range(1, _SZSE_MAX_PAGES + 1):
            payload = self._request(_SZSE_SHOWREPORT, params={
                "SHOWTYPE": "JSON", "CATALOGID": "1842_xxpl", "TABKEY": "tab1",
                "txtStart": day, "txtEnd": window[1], "PAGENO": str(page),
                "random": "0.9",
            }, referer=_SZSE_REFERER)
            page_rows = _szse_table_rows(payload)
            raw_rows.extend(page_rows)
            if len(page_rows) < _SZSE_PAGE_SIZE:
                break
        tops = parse_dragon_tiger_szse({"data": raw_rows})["dragon_tiger"]
        seats: list[dict] = []
        for row in raw_rows:
            params = _szse_drill_params(row.get("bz"))
            if not params.get("ZQDM"):
                continue
            detail = self._request(_SZSE_SHOWREPORT, params={
                "SHOWTYPE": "JSON",
                "CATALOGID": params.get("CATALOGID") or "1842_detal",
                "TABKEY": params.get("TABKEY") or "tab1,tab2",
                "DQRQ": params.get("DQRQ") or day,
                "ZQDM": params["ZQDM"], "ZBDM": params.get("ZBDM", ""),
                "random": "0.9",
            }, referer=_SZSE_REFERER)
            seats.extend(parse_dragon_tiger_szse_detail(
                detail, code=params["ZQDM"], trade_date=params.get("DQRQ") or day))
        return {"dragon_tiger": tops, "dragon_tiger_seat": tuple(seats)}

    def _fetch_info_dragon_tiger_sse(self, window: tuple[str, str]) -> InfoRows:
        """上交所官方每日交易信息（零鉴权）→ 上榜记录 + 营业部席位。

        ``fileContents`` **缺失**是结构变更（抛错）；**存在但为空**是该日无披露
        （非交易日 / 尚未发布）——两者必须分开，否则「源挂了」会被读成「没数据」。
        """
        payload = self._request(_SSE_TRADE_PUBLIC, params={
            "jsonCallBack": "cb", "isPagination": "false", "dateTx": window[0],
        }, referer=_SSE_REFERER)
        if not isinstance(payload, dict) or "fileContents" not in payload:
            raise InfoValidationError(
                "上交所每日交易信息载荷缺少 fileContents——页面结构可能已变")
        lines = tuple(str(x) for x in (payload.get("fileContents") or []))
        return parse_dragon_tiger_sse(lines, trade_date=window[0])

    # ── 股东户数 ──

    def _fetch_info_shareholder_num_em(self, window: tuple[str, str]) -> InfoRows:
        _ = window
        payload = self._request(_DATACENTER, params=self._datacenter(
            "RPT_HOLDERNUMLATEST", sortColumns="END_DATE", sortTypes="-1"))
        return {"shareholder_num": parse_shareholder_num_em(payload)}

    def _fetch_info_shareholder_num_cninfo(self, window: tuple[str, str]) -> InfoRows:
        """巨潮数据中心股东户数（**按报告期**全市场）→ 股东户数行。

        ``rdate`` 只接受**季末**，故按窗口内各季末逐期取数（2 年窗口 = 8 期，每期
        一次请求）。该源作**备胎**：写入走 ``INSERT OR IGNORE``，只补主源空缺的
        ``(code, stat_date)``，**不覆盖**主源口径（D-030）。
        """
        rows: list[dict] = []
        for _iso, compact in _quarter_ends(window):
            payload = self._request(
                _CNINFO_API, params={"rdate": compact},
                referer=_CNINFO_REFERER,
                headers={"Accept-Enckey": _cninfo_enckey(),
                         "X-Requested-With": "XMLHttpRequest"},
            )
            rows.extend(parse_shareholder_num_cninfo(payload))
        return {"shareholder_num": tuple(rows)}

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
        """上证e互动**全市场流**（``ajax/feeds.do``）→ 问答行（覆盖沪市）。

        实测（2026-09-27）：该流**无须逐公司定位 ``uid``**——按 ``type`` 直接分页，
        且两条流各按**自己的时间键倒序**（``11`` 按回复时间、``10`` 按提问时间），
        故「翻页至本页最早时间 ≤ 窗口起点」即停；**单调**正是该判据成立的前提
        （入库的 ``ask_time`` 不单调，见 :func:`_sse_order_key`）。另设页数上限兜底。
        """
        start = window[0]
        merged: dict[tuple[str, str, str], dict] = {}
        for kind in _SSE_FEED_TYPES:
            for page in range(1, _SSE_FEED_MAX_PAGES + 1):
                payload = self._request_text(_SSE_FEED, params={
                    "type": kind, "pageSize": str(_SSE_FEED_PAGE_SIZE),
                    "lastid": "-1", "show": "1", "page": str(page),
                }, referer=_SSE_FEED_REFERER)
                batch = parse_sentiment_qa_sse(payload)
                if not batch:
                    break  # 源端显式「没有更多内容」——可见窗口已耗尽
                for row in batch:
                    key = (row["code"], row["question"], row["ask_time"])
                    prev = merged.get(key)
                    if prev is None or (prev.get("answer") is None
                                        and row.get("answer")):
                        merged[key] = row
                if min(_sse_order_key(kind, r) for r in batch)[:10] <= start:
                    break  # 已翻到窗口起点
        return {"sentiment_qa": tuple(merged.values())}
