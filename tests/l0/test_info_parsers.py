"""T-L0-010 抓取层测试：解析夹具取自**真实载荷形状**（2026-09-27 实测）。

本文件的价值在于：解析件此前只对着「我猜的形状」写，真实探测暴露了三处不符。
下列夹具逐条复刻真实形状，使「解析对不对」成为**离线可回归**的事实。

覆盖的真实形状（皆来自 2026-09-27 实测）：
- 东财公告：``data.list[].codes`` 是**字典列表**（非分隔字符串）
- 深交所公告：``secCode`` 是**字符串化的 Python 列表**（``"['002670']"``）
- 深交所龙虎榜：顶层是**列表**，行字段为 ``dqrq/zqdm/plyy/cjje``
- 同花顺热榜：``data.stock_list``，行字段为 ``order/rate/tag``
- 东财数据中心查询：``filter`` 含 ``<`` / ``>``，**必须 urlencode**（否则 400）
"""

from __future__ import annotations

import urllib.parse

import pytest

from st_agent.l0.info.fetch import (
    HttpInfoFetcher,
    _pythonish_list,  # noqa: PLC2701 - 测试内部工具的行为
    parse_announcement_em,
    parse_announcement_szse,
    parse_dragon_tiger_em,
    parse_dragon_tiger_szse,
    parse_sentiment_hot_ths,
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
}], "error": "", "metadata": {}}]

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
        """该端点**不含席位**——不产出 `dragon_tiger_seat` 行（缺口已登记遗留册）。"""
        assert parse_dragon_tiger_szse(SZSE_LHB)["dragon_tiger_seat"] == ()

    def test_szse_tolerates_dict_payload(self):
        """兼容单表返回（非列表）的形态。"""
        rows = parse_dragon_tiger_szse(SZSE_LHB[0])["dragon_tiger"]
        assert len(rows) == 1


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


class TestUnverifiedSourcesFailLoudly:
    """舆情问答两源是「按公司」接口——**显式报缺口**，不返回空结果冒充。"""

    @pytest.mark.parametrize("task_key", [
        "info_sentiment_qa_irm", "info_sentiment_qa_sse"])
    def test_per_company_sources_report_gap(self, task_key):
        from st_agent.l0.info.errors import InfoValidationError

        with pytest.raises(InfoValidationError, match="按公司"):
            HttpInfoFetcher().fetch_task(task_key, ("2026-09-01", "2026-09-27"))
