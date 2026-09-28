"""T-L0-015.2 测试：深市互动易真实抓取器与解析（两步流程 + 翻页）。

夹具取自**真实载荷形状**（两个独立第三方实现一致：``akshare`` 的
``stock_irm_cninfo.py`` 与 ``a-stock-data`` §10.1）：

- 第一步 ``queryKeyboardInfo``：``data[0].secid`` 即第二步的 ``orgId``
- 第二步 ``company/question``：``rows[]``，**参数在 query string 且以 POST 发出**
- ``pubDate`` 是**毫秒**时间戳；``attachedContent=None`` ＝ 公司尚未回复

GWT 对照（任务文件 6 条）见各类 docstring。真实端点只在 ``tests/live/`` 单跑。
"""

from __future__ import annotations

import json
import re
import urllib.parse

import pytest

from st_agent.l0.info.errors import InfoValidationError
from st_agent.l0.info.fetch import (
    HttpInfoFetcher,
    _stamp,  # noqa: PLC2701 - 测试内部工具的口径
    parse_sentiment_qa_irm,
)

IRM = "info_sentiment_qa_irm"
WINDOW = ("2026-09-01", "2026-09-30")

IRM_SEARCH = {"data": [{"secid": "gssz0000001", "stockCode": "000001",
                        "companyShortName": "平安银行"}]}
"""第一步 ``queryKeyboardInfo`` 的命中形状——``secid`` 即第二步要的 ``orgId``。"""

IRM_QUESTION = {"totalPage": 1, "rows": [
    {"stockCode": "000001", "companyShortName": "平安银行",
     "mainContent": "公司如何回应近期传闻？",
     "attachedContent": None, "attachedAuthor": None, "pubDate": 1789000000000},
    {"stockCode": "000001", "companyShortName": "平安银行",
     "mainContent": "分红计划是否有变化？",
     "attachedContent": "请以公司公告为准。", "attachedAuthor": "董事会办公室",
     "pubDate": 1789000060000},
]}
"""第二步 ``company/question`` 的形状：``rows[]``；``attachedContent=None`` ＝ 未回复。"""


def _minutes(stamp: str) -> int:
    from datetime import datetime

    return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp() // 60)


class TestParse:
    """GWT-4 / GWT-5：解析稳健（未回复不丢行、结构变更 fail-fast）。"""

    def test_rows_are_mapped_with_explicit_sz_market(self):
        rows = parse_sentiment_qa_irm(IRM_QUESTION)
        assert [r["question"] for r in rows] == [
            "公司如何回应近期传闻？", "分红计划是否有变化？"]
        assert all(r["market"] == "sz" for r in rows)  # 显式标市，不靠号段推断
        assert rows[1]["answer"] == "请以公司公告为准。"
        assert rows[1]["answerer"] == "董事会办公室"

    def test_unanswered_row_is_kept(self):
        """未回复（``attachedContent=None``）**不丢行**——否则漏掉「公司尚未回应」信号。"""
        rows = parse_sentiment_qa_irm(IRM_QUESTION)
        assert rows[0]["answer"] is None and rows[0]["answerer"] is None

    def test_pubdate_is_read_as_milliseconds(self):
        """``pubDate`` 是**毫秒**时间戳：相差 60 000 ms 的两行必须相差 1 分钟。

        把毫秒当秒读会差约 55 年——故只断言「非空」不足以钉住该口径。
        """
        base, later = _stamp(1789000000000), _stamp(1789000060000)
        assert base and later and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", base)
        assert _minutes(later) - _minutes(base) == 1

    def test_missing_rows_key_fails_loudly(self):
        """缺 ``rows`` 键 ＝ **结构变更**（抛错），不得与「该公司无问答」混同。"""
        with pytest.raises(InfoValidationError, match="缺 rows"):
            parse_sentiment_qa_irm({"totalPage": 0})

    def test_empty_rows_is_a_legal_empty(self):
        assert parse_sentiment_qa_irm({"totalPage": 0, "rows": []}) == ()


class TestFetcher:
    """GWT-1 / GWT-2 / GWT-3：两步流程、翻页、覆盖域＝关注面 ∩ 深市。"""

    @staticmethod
    def _install(monkeypatch, handler):
        """注入假 ``urlopen``；返回 ``[(method, url), ...]`` 供断言。"""
        seen: list[tuple[str, str]] = []

        class _Resp:
            def __init__(self, payload: dict) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

        def _fake_urlopen(request, timeout=None):
            seen.append((request.get_method(), request.full_url))
            return _Resp(handler(request.full_url))

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        return seen

    @staticmethod
    def _fetch(codes):
        return HttpInfoFetcher().fetch_task(
            IRM, WINDOW, codes=codes)["sentiment_qa"]

    def test_two_step_then_rows(self, monkeypatch):
        seen = self._install(
            monkeypatch,
            lambda url: IRM_SEARCH if "queryKeyboardInfo" in url else IRM_QUESTION)
        rows = self._fetch(("sz.000001",))
        assert len(rows) == 2 and {r["code"] for r in rows} == {"000001"}
        assert len(seen) == 2
        assert seen[0][0] == "POST" and "queryKeyboardInfo" in seen[0][1]
        # 第二步：**POST + 参数在 query string**（放进 body 会被源端 400）
        assert seen[1][0] == "POST" and "company/question" in seen[1][1]
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(seen[1][1]).query))
        assert params["stockcode"] == "000001"
        assert params["orgId"] == "gssz0000001"  # 取自第一步的 secid
        assert params["pageNum"] == "1" and params["pageSize"] == "1000"

    def test_pages_until_total_page(self, monkeypatch):
        def _handler(url):
            if "queryKeyboardInfo" in url:
                return IRM_SEARCH
            query = urllib.parse.urlparse(url).query
            page = dict(urllib.parse.parse_qsl(query))["pageNum"]
            return {"totalPage": 2, "rows": [
                {**IRM_QUESTION["rows"][0], "mainContent": f"第 {page} 页提问"}]}

        seen = self._install(monkeypatch, _handler)
        assert [r["question"] for r in self._fetch(("sz.000001",))] == [
            "第 1 页提问", "第 2 页提问"]
        assert len(seen) == 3  # 搜索 + 两页

    def test_non_sz_symbols_are_not_requested(self, monkeypatch):
        """该源只覆盖深市（实测沪市返 0 条）——非 ``sz.`` 标的一个请求都不发。"""
        seen = self._install(monkeypatch, lambda url: IRM_SEARCH)
        assert self._fetch(("sh.600000", "bj.920002")) == ()
        assert seen == []

    def test_search_miss_skips_the_company(self, monkeypatch):
        """源端搜索无命中（退市等）＝ **合法空**，不是结构变更。"""
        seen = self._install(monkeypatch, lambda url: {"data": []})
        assert self._fetch(("sz.000001",)) == ()
        assert len(seen) == 1  # 只发了搜索，未发第二步

    def test_search_payload_without_data_key_fails_loudly(self, monkeypatch):
        self._install(monkeypatch, lambda url: {"code": 0})
        with pytest.raises(InfoValidationError, match="缺 data"):
            self._fetch(("sz.000001",))

    def test_question_payload_without_rows_key_fails_loudly(self, monkeypatch):
        self._install(
            monkeypatch,
            lambda url: IRM_SEARCH if "queryKeyboardInfo" in url else {"code": 0})
        with pytest.raises(InfoValidationError, match="缺 rows"):
            self._fetch(("sz.000001",))
