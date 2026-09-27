"""T-L0-010 抓取层测试：解析夹具取自**真实载荷形状**（2026-09-27 实测）。

本文件的价值在于：解析件此前只对着「我猜的形状」写，真实探测暴露了三处不符。
下列夹具逐条复刻真实形状，使「解析对不对」成为**离线可回归**的事实。

覆盖的真实形状（皆来自 2026-09-27 实测）：
- 东财公告：``data.list[].codes`` 是**字典列表**（非分隔字符串）
- 深交所公告：``secCode`` 是**字符串化的 Python 列表**（``"['002670']"``）
- 深交所龙虎榜：顶层是**列表**，行字段为 ``dqrq/zqdm/plyy/cjje``
- 深交所席位明细：``1842_xxpl`` 行内 ``bz`` **自带明细表契约**；``1842_detal`` 的
  ``tab2`` 块给 ``mmlb/zsmc/mrje/mcje``（T-L0-012）
- 上交所每日交易信息：**定宽文本**，分节标题即上榜原因，``证券代码:`` 头 +
  ``买入/卖出营业部名称`` 块（T-L0-012）
- 同花顺热榜：``data.stock_list``，行字段为 ``order/rate/tag``
- 上证e互动全市场流：**HTML 片段**，每条一个 ``div.m_feed_item``（``type=10`` 多类
  ``m_question``、无回答块）；翻到末页给 ``m_feed_note``「此时没有更多内容」（T-L0-013）
- 东财数据中心查询：``filter`` 含 ``<`` / ``>``，**必须 urlencode**（否则 400）
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from st_agent.l0.info.errors import InfoValidationError
from st_agent.l0.info.fetch import (
    HttpInfoFetcher,
    _pythonish_list,  # noqa: PLC2701 - 测试内部工具的行为
    _sse_order_key,  # noqa: PLC2701
    _szse_drill_params,  # noqa: PLC2701
    parse_announcement_em,
    parse_announcement_szse,
    parse_dragon_tiger_em,
    parse_dragon_tiger_sse,
    parse_dragon_tiger_szse,
    parse_dragon_tiger_szse_detail,
    parse_sentiment_hot_ths,
    parse_sentiment_qa_sse,
    parse_shareholder_num_em,
)

# ─────────────────── 真实形状夹具（2026-09-27 实测，仅留结构关键字段） ───────────────────

EM_ANN = {"data": {"list": [{
    "art_code": "AN202609241000000001",
    "title": "松炀股份关于公司相关方收到中国证券监督管理委员会广东监管局行政监管措施事先告知书的公告",
    "notice_date": "2026-09-25 00:00:00",
    "codes": [{"ann_type": "A,SHA", "inner_code": "29211575667637",
               "market_code": "1", "short_name": "松炀股份", "stock_code": "600095"}],
}]}}

SZSE_ANN = {"announceCount": 5, "data": [{
    "id": "31cc6856-b67c-4462-b659-8368edf79eff",
    "annId": "1225583046",
    "title": "国盛证券关于公司相关方收到行政监管措施事先告知书的公告",
    "content": "None",
    "publishTime": "2026-09-25 00:00:00",
    "attachPath": "/disc/disk03/finalpage/2026-09-25/01b4ba.pdf",
    "secCode": "['002670']",
    "secName": "['国盛证券']",
}]}

EM_LHB = {"result": {"data": [{
    "TRADE_DATE": "2026-09-24 00:00:00",
    "SECURITY_CODE": "000592",
    "SECURITY_NAME_ABBR": "平潭发展",
    "EXPLANATION": "非st、*st和s证券连续三个交易日内收盘价格涨幅偏离值累计达到20%的证券",
    "BILLBOARD_NET_AMT": 475076359.6,
    "BILLBOARD_BUY_AMT": 600000000.0,
    "BILLBOARD_SELL_AMT": 124923640.4,
    "TURNOVERRATE": 12.5,
}, {
    "TRADE_DATE": "2026-09-24 00:00:00",
    "SECURITY_CODE": "000592",
    "EXPLANATION": "日涨幅偏离值达到7%的前5只证券",
    "BILLBOARD_NET_AMT": 475076359.6,
}]}}

SZSE_LHB = [{"data": [{
    "dqrq": "2026-09-24", "zqdm": "000504", "zqjc": "南华生物",
    "cjje": "15.25", "cjsl": "11,519.71", "plyy": "日价格涨幅达到20.83%",
    "bz": ("<a href='javascript:void(0);' a-back=1 a-param='/ShowReport/data?"
           "SHOWTYPE=JSON&CATALOGID=1842_detal&TABKEY=tab1,tab2&DQRQ=2026-09-24"
           "&ZQDM=000504&ZBDM=0902'>查看详情</a>"),
}], "error": "", "metadata": {}}]

SZSE_DETAIL = [
    {"data": [{
        "dqrq": "2026-07-10", "ycqj": "无", "zqjc": "国华退&nbsp;(000004)",
        "cjsl": "3,630,589 份/股", "cjje": "1,667,187 元", "plyy": "退市整理期",
    }], "error": "", "metadata": {"tabkey": "tab1", "name": "交易公开信息明细表"}},
    {"data": [
        {"mmlb": "买1", "zsmc": "爱建证券有限责任公司深圳分公司",
         "mrje": "802,800", "mcje": "0"},
        {"mmlb": "买2", "zsmc": "中信证券股份有限公司杭州环城北路证券营业部",
         "mrje": "118,702", "mcje": "460"},
        {"mmlb": "卖1", "zsmc": "东方财富证券股份有限公司拉萨东城区江苏大道证券营业部",
         "mrje": "0", "mcje": "91,300"},
    ], "error": "", "metadata": {"tabkey": "tab2"}},
]

#: 上交所每日交易信息（定宽文本）——按 2026-07-10 真实排版**缩小**（2 只标的、2 节）
SSE_TEXT = (
    "    上海证券交易所每日交易信息",
    "",
    "    交易日期:2026年07月10日",
    "",
    "一、有价格涨跌幅限制的日收盘价格涨幅偏离值达到7%的前五只证券:",
    " 1、A股",
    "        证券代码      证券简称      偏离值%        成交量        成交金额(万元)",
    "    (1)  600664      哈药股份      11.13%       100032231           33687.45",
    "    (2)  600821      金开新能      11.09%        93625745           55509.15",
    "",
    "      证券代码: 600664                                            证券简称: 哈药股份",
    "      -----------------------------------------------------------------------------------",
    "      买入营业部名称:                                            累计买入金额(元):",
    "  (1) 国泰海通证券股份有限公司南京太平南路证券营业部              124048015.00",
    "  (2) 华鑫证券有限责任公司绍兴胜利东路证券营业部                   14792278.00",
    "",
    "      卖出营业部名称:                                            累计卖出金额(元):",
    "  (1) 平安证券股份有限公司深圳深南大道证券营业部                   26712035.40",
    "",
    "      证券代码: 600821                                            证券简称: 金开新能",
    "      -----------------------------------------------------------------------------------",
    "      买入营业部名称:                                            累计买入金额(元):",
    "  (1) 中国银河证券股份有限公司合肥分公司                           89553480.00",
    "",
    "      卖出营业部名称:                                            累计卖出金额(元):",
    "  (1) 沪股通专用                                                  19781145.02",
    "",
    "五、无价格涨跌幅限制首个交易日的证券:",
    " 1、A股",
    "",
)

THS_HOT = {"data": {"stock_list": [{
    "market": 33, "code": "000592", "rate": "99767.0", "rise_and_fall": 10.0251,
    "name": "平潭发展", "hot_rank_chg": 0, "order": 1,
    "tag": {"concept_tag": ["福建自贸区", "海峡两岸"], "popularity_tag": "首板涨停"},
}]}}

EM_HOLDER = {"result": {"data": [{
    "SECURITY_CODE": "301686", "END_DATE": "2026-09-22 00:00:00",
    "HOLDER_NUM": 17520, "HOLDER_NUM_CHANGE": 17510,
    "HOLDER_NUM_RATIO": 175100.0, "AVG_FREE_SHARES": None,
}]}}

# 上证e互动全市场流（T-L0-013）——**HTML 片段**，非 JSON。逐条复刻 2026-09-27 实测形状。
SSE_FEED_ANSWERED = (
    '<div class="m_feed_item" id="item-1790517">'
    '<div class="m_feed_line"></div>'
    '<div class="m_feed_detail m_qa_detail">'
    '<div class="m_feed_face"><a rel="face" uid="120236" href="user.do?uid=120236"'
    ' title="投资者_1522820429000"><img src="inv.png"></a><p>投资者_x</p></div>'
    '<div class="m_feed_cnt "><div class="m_feed_info">'
    '<div class="ask_ico index_ico"></div></div>'
    '<div class="m_feed_txt" id="m_feed_txt-1790517">'
    "<a href='user.do?uid=1800' >:中国交建(601800)</a>董秘您好，请问回款情况如何？"
    '</div><div class="m_feed_media"></div>'
    '<div class="m_feed_func"><div class="m_feed_from">'
    '<span>2026年09月24日 14:24</span><em>来自</em>'
    '<a href="javascript:;">Android</a></div></div></div></div>'
    '<div class="m_feed_detail m_qa">'
    '<div class="a_tit"><em class="S_line1_c">◆</em></div>'
    '<div class="m_feed_face"><a class="ansface" rel="tag" uid="1800"'
    ' href="user.do?uid=1800" title="中国交建"><img src="co.png"></a>'
    '<p>中国交建</p></div>'
    '<div class="m_feed_cnt"><div class="m_feed_info">'
    '<div class="index_ico answer_ico"></div></div>'
    '<div class="m_feed_txt" id="m_feed_txt-1790517">'
    '您好，公司持续强化项目回款管理。</div>'
    '<div class="m_feed_media"></div></div>'
    '<div class="m_feed_func top10"><div class="m_feed_handle">'
    '<a href="javascript:love(1790517);" id="love-1790517"><em></em></a></div>'
    '<div class="m_feed_from"><span>2026年09月24日 18:28</span><em>来自</em>'
    '<a href="javascript:;">网站</a></div></div></div>'
)

SSE_FEED_QUESTION = (
    '<div class="m_feed_item m_question" id="item-1792556">'
    '<div class="m_feed_line"></div>'
    '<div class="m_feed_detail">'
    '<div class="m_feed_face"><a rel="face" uid="308936" href="user.do?uid=308936"'
    ' title="投资者_y"><img src="inv.png"></a><p>投资者_y</p></div>'
    '<div class="m_feed_cnt "><div class="m_feed_info">'
    '<div class="ask_ico index_ico"></div></div>'
    '<div class="m_feed_txt" id="m_feed_txt-1792556">'
    "<a href='user.do?uid=30908' >:华友钴业(603799)</a>本次回购期限到10月19日，"
    '请问是否有计划在剩余时间加快回购？</div>'
    '<div class="m_feed_media"></div>'
    '<div class="m_feed_func clearfix"><div class="m_feed_from">'
    '<span>2026年09月24日 18:18</span><em>来自</em>'
    '<a href="javascript:;">IOS</a></div></div></div></div>'
)

SSE_FEED_END = ('<div class="center">'
                '<a href="javascript:;" class="m_feed_note">此时没有更多内容</a>'
                '</div>')


class TestAnnouncementParsers:
    def test_em_codes_is_dict_list(self):
        """回归：``codes`` 是**字典列表**——早期按字符串 ``split(",")`` 解析出垃圾。"""
        rows = parse_announcement_em(EM_ANN)
        assert len(rows) == 1
        assert rows[0]["code"] == "600095"
        assert rows[0]["ann_type"] == "A,SHA"
        assert rows[0]["pub_date"] == "2026-09-25"
        assert rows[0]["url"].endswith("AN202609241000000001_1.pdf")

    def test_em_multiple_codes_expand_to_rows(self):
        payload = {"data": {"list": [{
            "art_code": "X", "title": "T", "notice_date": "2026-09-25 00:00:00",
            "codes": [{"stock_code": "600000", "ann_type": "A,SHA"},
                      {"stock_code": "600001", "ann_type": "A,SHA"}],
        }]}}
        assert [r["code"] for r in parse_announcement_em(payload)] == ["600000", "600001"]

    def test_szse_sec_code_is_stringified_list(self):
        """回归：``secCode`` 是 ``"['002670']"``——须解出代码，不能留 ``None``。"""
        rows = parse_announcement_szse(SZSE_ANN)
        assert len(rows) == 1
        assert rows[0]["code"] == "002670"
        assert rows[0]["pub_date"] == "2026-09-25"
        assert rows[0]["url"].startswith("https://disc.static.szse.cn/download/")

    def test_szse_multi_code_record_expands(self):
        payload = {"data": [{"title": "T", "publishTime": "2026-09-25 00:00:00",
                             "secCode": "['000001', '000002']"}]}
        assert [r["code"] for r in parse_announcement_szse(payload)] == ["000001", "000002"]

    @pytest.mark.parametrize("raw,expected", [
        ("['002670']", ("002670",)),
        ("['000001', '000002']", ("000001", "000002")),
        ("002670", ("002670",)),
        ("None", ()),
        (None, ()),
    ])
    def test_pythonish_list(self, raw, expected):
        assert _pythonish_list(raw) == expected


class TestDragonTigerParsers:
    def test_em_merges_reasons_per_business_key(self):
        rows = parse_dragon_tiger_em(EM_LHB)["dragon_tiger"]
        assert len(rows) == 1  # 同 (code, trade_date) 两原因 → 一行
        assert rows[0]["code"] == "sz.000592"
        assert rows[0]["trade_date"] == "2026-09-24"
        assert rows[0]["reasons"].count("；") == 1
        assert rows[0]["net_amount"] == 475076359.6

    def test_szse_payload_is_a_list(self):
        """回归：顶层是**列表**——早期按 dict 取会 ``'list' object has no attribute 'get'``。"""
        result = parse_dragon_tiger_szse(SZSE_LHB)
        rows = result["dragon_tiger"]
        assert len(rows) == 1
        assert rows[0]["code"] == "sz.000504"
        assert rows[0]["trade_date"] == "2026-09-24"
        assert rows[0]["reasons"] == "日价格涨幅达到20.83%"

    def test_szse_yields_no_seats(self):
        """``1842_xxpl`` 本体**不含席位**——不产出 `dragon_tiger_seat` 行。

        席位在**另一跳**：抓取器按该行 ``bz`` 自带的契约钻取 ``1842_detal``，
        再交 :func:`parse_dragon_tiger_szse_detail`（见 `TestSzseSeatDrillDown`）。
        """
        assert parse_dragon_tiger_szse(SZSE_LHB)["dragon_tiger_seat"] == ()

    def test_szse_tolerates_dict_payload(self):
        """兼容单表返回（非列表）的形态。"""
        rows = parse_dragon_tiger_szse(SZSE_LHB[0])["dragon_tiger"]
        assert len(rows) == 1


class TestSzseSeatDrillDown:
    """T-L0-012 · F2：席位明细走 ``bz`` 自带的契约钻取 ``1842_detal``。"""

    def test_drill_params_come_from_the_payload(self):
        """回归：契约**由源端携带**——``ZBDM`` 随上榜原因变，不得硬编码。"""
        params = _szse_drill_params(SZSE_LHB[0]["data"][0]["bz"])
        assert params["CATALOGID"] == "1842_detal"
        assert params["TABKEY"] == "tab1,tab2"
        assert params["DQRQ"] == "2026-09-24"
        assert params["ZQDM"] == "000504"
        assert params["ZBDM"] == "0902"

    @pytest.mark.parametrize("markup", [None, "", "无链接", "<a href='x'>查看</a>"])
    def test_drill_params_absent_returns_empty(self, markup):
        assert _szse_drill_params(markup) == {}

    def test_detail_yields_seats_with_side_and_rank(self):
        seats = parse_dragon_tiger_szse_detail(
            SZSE_DETAIL, code="000004", trade_date="2026-07-10")
        assert len(seats) == 3
        first = seats[0]
        assert (first["code"], first["side"], first["rank"]) == ("sz.000004", "buy", 1)
        assert first["seat_name"] == "爱建证券有限责任公司深圳分公司"
        assert first["buy_amount"] == 802800.0
        assert first["sell_amount"] == 0.0
        assert first["net_amount"] == 802800.0
        assert seats[1]["net_amount"] == 118702.0 - 460.0
        assert seats[2]["side"] == "sell" and seats[2]["rank"] == 1

    def test_detail_without_tab2_block_is_a_structure_change(self):
        """缺 ``tab2`` 块 = 结构变更 → 抛错；**不**静默返回空（GWT-4）。"""
        with pytest.raises(InfoValidationError, match="tab2"):
            parse_dragon_tiger_szse_detail(
                [{"data": [{"dqrq": "x"}], "metadata": {"tabkey": "tab1"}}],
                code="000004", trade_date="2026-07-10")

    def test_detail_with_empty_tab2_is_not_an_error(self):
        """``tab2`` 存在但无席位行 = 该标的当日无明细 → 空元组，不抛错。"""
        assert parse_dragon_tiger_szse_detail(
            [{"data": [], "metadata": {"tabkey": "tab2"}}],
            code="000004", trade_date="2026-07-10") == ()


class TestSseDailyDisclosure:
    """T-L0-012 · F3：上交所每日交易信息（定宽文本）→ 上榜记录 + 营业部席位。"""

    def _rows(self):
        return parse_dragon_tiger_sse(SSE_TEXT, trade_date="2026-07-10")

    def test_tops_from_both_anchors_deduped(self):
        """上榜记录来自 ``证券代码:`` 头与 ``(N) 代码`` 表行——同键去重。"""
        tops = self._rows()["dragon_tiger"]
        assert [r["code"] for r in tops] == ["600664", "600821"]
        assert tops[0]["reasons"] == "有价格涨跌幅限制的日收盘价格涨幅偏离值达到7%的前五只证券"
        assert tops[0]["trade_date"] == "2026-07-10"

    def test_market_is_explicitly_sh(self):
        """沪市含 ``1xx`` / ``5xx`` 段——显式标 ``sh``，不靠号段推断（D-032）。"""
        rows = self._rows()
        assert {r["market"] for r in rows["dragon_tiger"]} == {"sh"}
        assert {r["market"] for r in rows["dragon_tiger_seat"]} == {"sh"}

    def test_seats_with_side_rank_name_amount(self):
        seats = {(r["code"], r["side"], r["rank"]): r
                 for r in self._rows()["dragon_tiger_seat"]}
        assert len(seats) == 5
        buy1 = seats[("600664", "buy", 1)]
        assert buy1["seat_name"] == "国泰海通证券股份有限公司南京太平南路证券营业部"
        assert buy1["buy_amount"] == 124048015.0 and buy1["sell_amount"] is None
        sell1 = seats[("600821", "sell", 1)]
        assert sell1["seat_name"] == "沪股通专用"
        assert sell1["sell_amount"] == 19781145.02 and sell1["buy_amount"] is None

    def test_empty_disclosure_is_not_an_error(self):
        """非交易日 / 未披露 → 空 ``fileContents``：空结果，**不**抛错。"""
        rows = parse_dragon_tiger_sse((), trade_date="2026-09-25")
        assert rows == {"dragon_tiger": (), "dragon_tiger_seat": ()}

    def test_text_without_any_section_is_a_structure_change(self):
        """有正文却无「一、…」分节标题 = 排版变了 → 抛错（GWT-4）。"""
        with pytest.raises(InfoValidationError, match="分节标题"):
            parse_dragon_tiger_sse(("随便一段没有分节的文字",), trade_date="2026-07-10")


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _serve(monkeypatch, payloads: list[bytes]) -> list[str]:
    """按序供给载荷（**用尽后重复最后一个**），返回被请求的 URL 列表。"""
    queue = list(payloads)
    seen: list[str] = []

    def _fake_urlopen(request, timeout=None):
        seen.append(request.full_url)
        return _Resp(queue.pop(0) if len(queue) > 1 else queue[0])

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return seen


def _xxpl_page(codes: list[str], day: str = "2026-09-24") -> bytes:
    rows = [{"dqrq": day, "zqdm": code, "zqjc": f"股{code}", "cjje": "1.0",
             "cjsl": "1.0", "plyy": "原因",
             "bz": (f"<a a-param='/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1842_detal"
                    f"&TABKEY=tab1,tab2&DQRQ={day}&ZQDM={code}&ZBDM=0902'>查看</a>")}
            for code in codes]
    return json.dumps([{"data": rows, "error": "", "metadata": {}}]).encode()


class TestDragonTigerFetchers:
    """抖动取器的**请求编排**（URL / 翻页 / 钻取），不联网。"""

    def test_szse_pages_until_short_page_then_drills(self, monkeypatch):
        seen = _serve(monkeypatch, [
            _xxpl_page([f"00000{i}" for i in range(1, 11)]),  # 满页 → 继续翻
            _xxpl_page(["000011"]),                            # 不足一页 → 停
            json.dumps(SZSE_DETAIL).encode(),                  # 钻取载荷（重复供给）
        ])
        rows = HttpInfoFetcher().fetch_task(
            "info_dragon_tiger_szse", ("2026-09-24", "2026-09-24"))
        assert len(rows["dragon_tiger"]) == 11
        assert len(rows["dragon_tiger_seat"]) == 3 * 11
        assert len(seen) == 2 + 11, seen
        assert "PAGENO=2" in seen[1] and "PAGENO=3" not in " ".join(seen)
        assert "CATALOGID=1842_detal" in seen[2] and "ZBDM=0902" in seen[2]

    def test_sse_missing_file_contents_raises(self, monkeypatch):
        _serve(monkeypatch, [b'{"isTradeDate": "false"}'])
        with pytest.raises(InfoValidationError, match="fileContents"):
            HttpInfoFetcher().fetch_task(
                "info_dragon_tiger_sse", ("2026-09-25", "2026-09-25"))

    def test_sse_empty_file_contents_is_empty_not_error(self, monkeypatch):
        _serve(monkeypatch, [b'{"fileContents": [], "isTradeDate": "false"}'])
        rows = HttpInfoFetcher().fetch_task(
            "info_dragon_tiger_sse", ("2026-09-25", "2026-09-25"))
        assert rows == {"dragon_tiger": (), "dragon_tiger_seat": ()}

    def test_sse_uses_the_document_path_with_original_spelling(self, monkeypatch):
        seen = _serve(monkeypatch, [b'{"fileContents": []}'])
        HttpInfoFetcher().fetch_task(
            "info_dragon_tiger_sse", ("2026-07-10", "2026-07-10"))
        assert "infodisplay/showTradePublicFile.do" in seen[0]
        assert "dateTx=2026-07-10" in seen[0]


class TestSentimentAndShareholderParsers:
    def test_ths_hot_real_shape(self):
        rows = parse_sentiment_hot_ths(THS_HOT)
        assert len(rows) == 1
        assert rows[0] == {"code": "000592", "board": "ths_hot", "rank": 1,
                           "heat": 99767.0}

    def test_shareholder_real_shape(self):
        rows = parse_shareholder_num_em(EM_HOLDER)
        assert len(rows) == 1
        assert rows[0]["code"] == "sz.301686"
        assert rows[0]["stat_date"] == "2026-09-22"
        assert rows[0]["holder_num"] == 17520
        assert rows[0]["avg_shares"] is None


class TestUrlEncodingRegression:
    """回归：东财 ``filter`` 含 ``<`` / ``>``——直接拼进 URL 会被拒（实测 400）。"""

    def _captured_url(self, monkeypatch, call) -> str:
        captured: dict[str, str] = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return b'{"result": {"data": []}}'

        def _fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            return _Resp()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        call()
        return captured["url"]

    def test_dragon_tiger_filter_is_percent_encoded(self, monkeypatch):
        url = self._captured_url(
            monkeypatch,
            lambda: HttpInfoFetcher().fetch_task(
                "info_dragon_tiger_em", ("2026-09-24", "2026-09-24")))
        assert "<" not in url and ">" not in url, url
        assert "TRADE_DATE" in urllib.parse.unquote(url)
        assert url.count("filter=") == 1

    def test_shareholder_query_is_encoded(self, monkeypatch):
        url = self._captured_url(
            monkeypatch,
            lambda: HttpInfoFetcher().fetch_task(
                "info_shareholder_num_em", ("2025-01-01", "2026-09-27")))
        assert "reportName=RPT_HOLDERNUMLATEST" in url
        assert "source=WEB" in url

    def test_szse_announcement_uses_json_body(self, monkeypatch):
        from st_agent.l0.info.fetch import HttpInfoFetcher as F

        seen: dict[str, str] = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return b'{"data": []}'

        def _fake_urlopen(request, timeout=None):
            seen["content_type"] = request.get_header("Content-type") or ""
            seen["body"] = (request.data or b"").decode("utf-8")
            return _Resp()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        F().fetch_task("info_announcement_szse", ("2026-09-18", "2026-09-27"))
        assert "application/json" in seen["content_type"]
        assert seen["body"].startswith("{")  # JSON，不是 form-encoded

    def test_ths_hot_uses_documented_url(self, monkeypatch):
        url = self._captured_url(
            monkeypatch,
            lambda: HttpInfoFetcher().fetch_task("info_sentiment_hot", ("2026-09-27",)))
        assert "dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock" in url
        assert "stock_type=a" in url and "list_type=normal" in url


class TestSentimentQaSseFullMarket:
    """上证e互动**全市场流**（T-L0-013）——原「按公司、须先定位 ``uid``」已作废。"""

    def test_answered_item_maps_every_field(self):
        rows = parse_sentiment_qa_sse(SSE_FEED_ANSWERED)
        assert len(rows) == 1
        row = rows[0]
        assert row["code"] == "601800"  # 源码原样——归一（sh.601800）在引擎侧
        assert row["market"] == "sh"    # 显式标沪市（5xx / 1xx 号段会被误判成深市）
        assert row["question"] == "董秘您好，请问回款情况如何？"  # 「:名称(代码)」已摘除
        assert row["answer"] == "您好，公司持续强化项目回款管理。"
        assert row["answerer"] == "中国交建"
        assert row["ask_time"] == "2026-09-24 14:24"
        assert row["answer_time"] == "2026-09-24 18:28"

    def test_unanswered_question_is_a_legal_row(self):
        """``type=10`` 没有回答块——未回复是**合法态**（非缺失）。"""
        rows = parse_sentiment_qa_sse(SSE_FEED_QUESTION)
        assert len(rows) == 1
        assert rows[0]["answer"] is None and rows[0]["answerer"] is None
        assert rows[0]["answer_time"] is None
        assert rows[0]["ask_time"] == "2026-09-24 18:18"

    def test_order_key_is_reply_time_for_answered_feed(self):
        """翻页停止判据依**回复时间**——``ask_time`` 不单调，不能当游标。"""
        answered = parse_sentiment_qa_sse(SSE_FEED_ANSWERED)[0]
        asked = parse_sentiment_qa_sse(SSE_FEED_QUESTION)[0]
        assert _sse_order_key("11", answered) == "2026-09-24 18:28"
        assert _sse_order_key("10", asked) == "2026-09-24 18:18"

    def test_end_of_window_marker_is_not_a_structure_change(self):
        """源端「此时没有更多内容」＝可见窗口耗尽，**不是**结构变更。"""
        assert parse_sentiment_qa_sse(SSE_FEED_END) == ()

    def test_structural_change_fails_loudly(self):
        with pytest.raises(InfoValidationError, match="m_feed_note"):
            parse_sentiment_qa_sse("<html>请先登录</html>")

    def test_missing_stock_anchor_fails_loudly(self):
        broken = SSE_FEED_ANSWERED.replace(":中国交建(601800)</a>", "</a>")
        with pytest.raises(InfoValidationError, match="锚点"):
            parse_sentiment_qa_sse(broken)

    def test_fetcher_pulls_both_feeds_from_fullmarket_endpoint(self, monkeypatch):
        """原「按公司」登记已作废：改走 ``feeds.do``，且 ``type=10`` / ``11`` 两流都拉。"""
        seen: list[str] = []

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return SSE_FEED_END.encode("utf-8")

        def _fake_urlopen(request, timeout=None):
            seen.append(request.full_url)
            return _Resp()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        rows = HttpInfoFetcher().fetch_task(
            "info_sentiment_qa_sse", ("2026-09-01", "2026-09-30"))
        assert rows == {"sentiment_qa": ()}
        assert len(seen) == 2  # 两流各一页即遇末页标记 → 停
        assert all("sns.sseinfo.com/ajax/feeds.do" in url for url in seen)
        assert any("type=10" in url for url in seen)
        assert any("type=11" in url for url in seen)
        assert "pageSize=50" in seen[0]


class TestIrmStillReportsGap:
    """深市互动易**仍**无全市场流——显式报缺口，不返回空结果冒充（D-037）。"""

    def test_per_company_source_reports_gap(self):
        with pytest.raises(InfoValidationError, match="按公司"):
            HttpInfoFetcher().fetch_task(
                "info_sentiment_qa_irm", ("2026-09-01", "2026-09-27"))
