"""T-L0-007.2 真实链路冒烟（小样本 live 用例，默认排除，不进 CI）。

**目的**：用真网络 + 真实加密库把「Store → MarketDb → EgressGateway →
BaoStockFetcher → BaoStockSync → 落盘/审计/新鲜度」整条链路走通一次，
证明管道可用（不追求全市场覆盖，那是 T-L0-007.2 的全量长跑）。

小样本：元数据/日历/全量快照全量 + 3 只真实 A 股的日线全历史（约 2 分钟）。

跑法（口令只经环境变量，不进 argv）：

    ST_AGENT_PASSPHRASE='...' python -m pytest -m live -s \\
        tests/live/test_smoke_sync.py

真实数据只落 pytest 临时目录里的加密分区，不进 Git。
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.live

SMOKE_CODES = ["sh.600000", "sz.000001", "sh.601398"]  # 浦发 / 平安 / 工商
PLAINTEXT_MARKER = "浦发银行"


def _one(db, sql: str):
    return db.query(sql).data["rows"][0]


def test_real_chain_smoke(tmp_path) -> None:
    pytest.importorskip("baostock")
    passphrase = os.environ.get("ST_AGENT_PASSPHRASE")
    if not passphrase:
        pytest.skip("需环境变量 ST_AGENT_PASSPHRASE（口令不进 argv）")

    from st_agent.l0.market import BaoStockFetcher, BaoStockSync, MarketDb
    from st_agent.l0.net import EgressGateway
    from st_agent.l0.storage import Store

    timings: dict[str, float] = {}

    def run(sync, task_key, **kw):
        t0 = time.perf_counter()
        env = sync.run_task(task_key, **kw)
        timings[task_key] = round(time.perf_counter() - t0, 2)
        return env

    root = tmp_path / "root"
    t0 = time.perf_counter()
    store = Store.create(root, passphrase)          # 真实 PBKDF2 600k + 七分区
    keyfile_cost = round(time.perf_counter() - t0, 2)

    db = MarketDb(store)
    gateway = EgressGateway(store, audit=True)

    with BaoStockFetcher() as fetcher:              # 真实 login/logout
        sync = BaoStockSync(db, gateway, fetcher)

        # 建库 + 元数据种子（run_task 前置；run_all 内部也会调，直调则须显式）
        t0 = time.perf_counter()
        env_setup = sync.setup()
        assert env_setup.status == "ok", env_setup.reason
        timings["setup"] = round(time.perf_counter() - t0, 2)

        env_cal = run(sync, "bs_calendar")
        assert env_cal.status == "ok", env_cal.reason

        env_sec = run(sync, "bs_security_basic")
        assert env_sec.status == "ok", env_sec.reason

        day = _one(db, "SELECT max(calendar_date) AS d FROM trade_calendar "
                       "WHERE is_trading_day = 1")["d"]
        env_all = run(sync, "bs_all_stock", day=day)
        assert env_all.status == "ok", env_all.reason

        env_k = run(sync, "bs_k_daily", codes=SMOKE_CODES)
        assert env_k.status == "ok", env_k.reason

        # —— 数据面 ——
        sec_rows = _one(db, "SELECT count(*) AS n FROM security")["n"]
        cal_rows = _one(db, "SELECT count(*) AS n FROM trade_calendar")["n"]
        k_codes = _one(db, "SELECT count(DISTINCT code) AS n FROM k_line_daily")["n"]
        k_rows = _one(db, "SELECT count(*) AS n FROM k_line_daily")["n"]
        assert sec_rows > 5000, f"证券主档过少：{sec_rows}"
        assert cal_rows > 10000, f"交易日历过少：{cal_rows}"
        assert k_codes == len(SMOKE_CODES), f"日线覆盖 {k_codes} 只，应 {len(SMOKE_CODES)}"
        assert k_rows > 0

        # —— 审计：每次抓取都经网关留痕 ——
        events = gateway.query(kind="data_fetch")
        assert len(events) >= 6, f"审计条数异常：{len(events)}"
        assert all(e.target_host == "baostock" for e in events)

        # —— 新鲜度：口径可查（kline 仍 stale，因 bs_k_period 未跑，但非"无数据"）——
        verdict = sync.freshness_verdict("kline")
        assert verdict.stale is True
        assert "bs_k_period" in verdict.detail, verdict.detail

        snapshot = sync.dataset_snapshot()
        assert snapshot.startswith("snap_") and len(snapshot) == 25   # 01 §1 形态（T-L0-011）
        assert "bs_k_daily:" in db.snapshot_components()             # 可读水位原文走复核面

    # —— 零泄漏：分区内无明文（主档/行情都应在加密 blob 里）——
    leaked = []
    for p in root.rglob("*"):
        if p.is_file() and PLAINTEXT_MARKER.encode("utf-8") in p.read_bytes():
            leaked.append(str(p.relative_to(root)))
    assert not leaked, f"盘上检出明文：{leaked}"

    print("\n──────── 真实链路冒烟证据 ────────")
    print(f"密钥派生+建库: {keyfile_cost}s")
    for key in ("setup", "bs_calendar", "bs_security_basic", "bs_all_stock", "bs_k_daily"):
        print(f"  {key:18s} {timings.get(key, '-')}s")
    print(f"证券主档 {sec_rows} 行 · 交易日历 {cal_rows} 行")
    print(f"日线 {k_codes} 只 / {k_rows} 行（窗口起点 1990-12-19，全历史）")
    print(f"网关 data_fetch 审计 {len(events)} 条 · 快照 {snapshot}")
    print(f"盘上明文检出：0（扫描 {sum(1 for _ in root.rglob('*'))} 个条目）")
    print("────────────────────────────────")


def test_real_window_and_batch_smoke(tmp_path) -> None:
    """T-L0-017.1 真网复核：**显式窗口 + 按码分片 + 批内合并写**。

    上一条走「全历史、不分片」；本条走「近一年、分批、分两片」——验证新参数在
    **真实源**上确实收窄窗口（行数落到一年量级而非十几年）、分片 2 不被全库水位
    跳成空窗口（抓取次数照旧），且请求仍逐次经网关审计、盘上仍无明文。
    """
    pytest.importorskip("baostock")
    passphrase = os.environ.get("ST_AGENT_PASSPHRASE")
    if not passphrase:
        pytest.skip("需环境变量 ST_AGENT_PASSPHRASE（口令不进 argv）")

    from datetime import date, timedelta

    from st_agent.l0.market import BaoStockFetcher, BaoStockSync, MarketDb
    from st_agent.l0.net import EgressGateway
    from st_agent.l0.storage import Store

    window_start = (date.today() - timedelta(days=365)).isoformat()
    root = tmp_path / "root"
    store = Store.create(root, passphrase)
    db = MarketDb(store)
    gateway = EgressGateway(store, audit=True)

    with BaoStockFetcher() as fetcher:
        sync = BaoStockSync(db, gateway, fetcher)
        assert sync.setup().status == "ok"
        assert sync.run_task("bs_calendar").status == "ok"
        assert sync.run_task("bs_security_basic").status == "ok"
        day = _one(db, "SELECT max(calendar_date) AS d FROM trade_calendar "
                       "WHERE is_trading_day = 1")["d"]
        assert sync.run_task("bs_all_stock", day=day).status == "ok"

        # ① 显式窗口 + 批内合并写（批大小 2 ⇒ 3 只码走 2 次写事务）
        env = sync.run_task("bs_k_daily", codes=SMOKE_CODES,
                            start=window_start, batch_size=2)
        assert env.status == "ok", env.reason
        rows = _one(db, "SELECT count(*) AS n FROM k_line_daily")["n"]
        earliest = _one(db, "SELECT min(trade_date) AS d FROM k_line_daily")["d"]
        assert 0 < rows <= len(SMOKE_CODES) * 260, f"近一年行数异常：{rows}"
        assert earliest >= window_start, f"窗口起点未生效：{earliest} < {window_start}"

        # ② 分片 2：全库水位已在窗口末端，显式起点仍让每只码各发一次抓取
        before = sync.fetch_attempts
        env2 = sync.run_task("bs_k_daily", codes=SMOKE_CODES,
                            start=window_start, batch_size=3)
        assert env2.status == "ok", env2.reason
        assert sync.fetch_attempts - before == len(SMOKE_CODES), "分片被全库水位跳成空窗口"

        events = gateway.query(kind="data_fetch")
        assert all(e.target_host == "baostock" for e in events)
        assert sync.fetch_attempts == len(events)

    print("\n──────── 窗口/分片/分批 真网复核 ────────")
    print(f"窗口起点 {window_start} · 日线 {rows} 行 · 最早 {earliest}")
    print(f"抓取合计 {sync.fetch_attempts} 次 · 审计 {len(events)} 条（同源同数）")
    print("────────────────────────────────")
