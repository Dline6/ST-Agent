"""真实端点复跑（live，默认排除，不进 CI）——T-L0-014 / T-L0-015 的信息面源。

**目的**：把「巨潮股东户数端点可用、``Accept-Enckey`` 被接受、解析出全市场行」
与「互动易两步端点可达、按关注面解析出行」从「手工探测过一次」变成**可复跑的事实**
（各任务假设的验证口）。只发少量请求，只读、串行、不写库。

跑法：

    python -m pytest -m live -s tests/live/test_info_endpoints_live.py
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


def test_cninfo_shareholder_endpoint_live() -> None:
    """GWT：单期可达 + 鉴权头被接受 + 解析出全市场行（A1 / A2）。"""
    from st_agent.l0.info.fetch import HttpInfoFetcher

    # 单期窗口 → ``_quarter_ends`` 只给 1 个季末 → 只发 1 次请求
    rows = HttpInfoFetcher().fetch_task(
        "info_shareholder_num_cninfo", ("2025-06-30", "2025-06-30")
    )["shareholder_num"]

    assert len(rows) > 3000, f"全市场行数异常偏少：{len(rows)}"      # 实测 5173
    assert {r["stat_date"] for r in rows} == {"2025-06-30"}
    assert all(r["code"].startswith(("sh.", "sz.")) for r in rows)

    sample = next(r for r in rows if r["code"] == "sz.002054")
    assert sample["holder_num"] and sample["avg_shares"] and sample["change_ratio"]


def test_cninfo_irm_two_step_live() -> None:
    """GWT：互动易两步端点可达 + 按关注面解析出行（T-L0-015.2 假设 A1 的验证口）。

    单只深市标的、串行、只读、不写库；**只发 2 次请求**（搜索 + 一页）。
    """
    from st_agent.l0.info.fetch import HttpInfoFetcher

    rows = HttpInfoFetcher().fetch_task(
        "info_sentiment_qa_irm", ("2026-09-01", "2026-09-30"),
        codes=("sz.002475",),  # 立讯精密——实测回复活跃的样例
    )["sentiment_qa"]

    assert rows, "互动易未返回任何问答行——端点契约可能已变"
    assert all(r["code"].startswith("002475") for r in rows)
    assert all(r["market"] == "sz" for r in rows)
    assert all(r["ask_time"] for r in rows)  # pubDate（毫秒）已转成 'YYYY-MM-DD HH:MM'


def test_cninfo_irm_covers_shenzhen_only_live() -> None:
    """GWT：该源只覆盖深市——沪市标的**不发请求**即得空（T-L0-015.2 假设 A3）。"""
    from st_agent.l0.info.fetch import HttpInfoFetcher

    rows = HttpInfoFetcher().fetch_task(
        "info_sentiment_qa_irm", ("2026-09-01", "2026-09-30"),
        codes=("sh.600519",),
    )["sentiment_qa"]

    assert rows == ()
