"""真实端点复跑（live，默认排除，不进 CI）——T-L0-014 的信息面源。

**目的**：把「巨潮股东户数端点可用、``Accept-Enckey`` 被接受、解析出全市场行」
从「2026-09-27 手工探测过一次」变成**可复跑的事实**（任务假设 A1 / A2 的验证口）。
只发 1 次请求（单期窗口），只读、串行、不写库。

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
