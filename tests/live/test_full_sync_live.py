"""T-L0-007.1 真实同步入口（live，默认排除，不进 CI）。

跑真实网络的全市场首次全量同步；耗时可达数小时、占用磁盘可观，故需显式开关
才执行：

    ST_AGENT_LIVE_FULL=1 ST_AGENT_PASSPHRASE='...' \\
        pytest -m live tests/live/test_full_sync.py

日常回归走 ``tests/l0/test_full_sync.py``（Fake 注入、无需真网、进 CI）。
真实数据只落 ``tmp_path``（pytest 临时目录）与本地加密库，不进 Git。
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live

FULL_ENV = "ST_AGENT_LIVE_FULL"


def _require_full_run_env() -> str:
    """真实长跑的三重门：包可用 / 显式开关 / 口令经环境变量。"""
    pytest.importorskip("baostock")
    if os.environ.get(FULL_ENV) != "1":
        pytest.skip(f"真实全量长跑需显式开关 {FULL_ENV}=1（耗时数小时）")
    passphrase = os.environ.get("ST_AGENT_PASSPHRASE")
    if not passphrase:
        pytest.skip("需环境变量 ST_AGENT_PASSPHRASE（口令不进 argv）")
    return passphrase


def test_full_sync_live(tmp_path) -> None:
    """GWT-1：真实全量跑通、各数据域非 stale、抓取全经网关审计。"""
    passphrase = _require_full_run_env()

    from run_full_sync import summarize_run

    from st_agent.l0.market import BaoStockFetcher, BaoStockSync, MarketDb
    from st_agent.l0.net import EgressGateway
    from st_agent.l0.storage import Store

    store = Store.create(tmp_path / "root", passphrase)
    gateway = EgressGateway(store)
    with BaoStockFetcher() as fetcher:
        sync = BaoStockSync(MarketDb(store), gateway, fetcher)
        summary = summarize_run(sync)

    bad = {k: v for k, v in summary["tasks"].items()
           if v["status"] not in ("ok", "unavailable")}
    assert not bad, f"全量同步未完成：{bad}"
    assert summary["tasks"]["bs_k_daily"]["status"] == "ok"

    for domain in ("kline", "financial", "company_report", "sector", "macro"):
        verdict = sync.freshness_verdict(domain)
        assert verdict.stale is False, f"{domain} 仍 stale：{verdict.detail}"
    assert gateway.query(kind="data_fetch"), "抓取应留审计"
