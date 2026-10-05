"""T-L0-007.2 测试：``--universe`` 码表选择与选码前的先导步骤（Fake 注入，不碰真网）。

覆盖三点：
- ``all`` 与 ``hs300`` 都**先跑先导单请求任务再选码**——全新库首跑不再拿到空码表；
- ``hs300`` 只取 ``index_constituent`` 的**最新一期**成分（跨期不混入码表）；
- 先导失败**显式**返回（不静默拿空码表继续）；码表为空由调用方兜（``main`` 中止）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.market import BaoStockSync, MarketDb
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from test_full_sync_batching import CODES, WideFetcher
from test_market import PASS, FakeFetcher

from run_full_sync import _params_block, _select_universe_codes, build_parser, resolve_codes

HS300_ROWS = [["2024-01-01", "sh.600000", "浦发银行"],
              ["2024-01-01", "sz.300750", "宁德时代"]]
HS300_CODES = ["sh.600000", "sz.300750"]


class ConstituentFetcher(WideFetcher):
    """成分接口按给定行返回（``index_key`` 恒为 hs300，测试不区分）。"""

    def __init__(self, rows: list[list[str]]) -> None:
        super().__init__()
        self._rows = rows

    def constituents(self, index_key):
        self._guard("constituents")
        _ = index_key
        return (["updateDate", "code", "code_name"], [list(r) for r in self._rows])


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store, audit=True)


def _db_and_sync(store: Store, gateway: EgressGateway, fetcher) -> tuple[MarketDb, BaoStockSync]:
    db = MarketDb(store)
    return db, BaoStockSync(db, gateway, fetcher)


def _insert_constituent(db: MarketDb, code: str, update_date: str) -> None:
    with db.transact() as con:
        con.execute(
            "INSERT OR IGNORE INTO index_constituent(index_key, code, update_date,"
            " code_name) VALUES (?, ?, ?, ?)",
            ("hs300", code, update_date, "证券"),
        )
        con.commit()


class TestResolveCodes:
    def test_all_bootstraps_master_then_selects(self, store, gateway):
        """全新库 + ``all``：先导填主档 ⇒ 选码不再落空（旧口径的首跑缺口）。"""
        fetcher = WideFetcher()
        db, sync = _db_and_sync(store, gateway, fetcher)

        codes, failure = resolve_codes(db, sync, universe="all", start="2024-01-01")

        assert failure is None
        assert codes == sorted(CODES)                    # 主档全量
        assert "security_basic" in fetcher.calls         # 先导确实跑了

    def test_hs300_bootstraps_master_and_constituents(self, store, gateway):
        fetcher = ConstituentFetcher(HS300_ROWS)
        db, sync = _db_and_sync(store, gateway, fetcher)

        codes, failure = resolve_codes(db, sync, universe="hs300", start="2024-01-01")

        assert failure is None
        assert codes == HS300_CODES
        assert "security_basic" in fetcher.calls         # 主档（外键前置）
        assert "constituents" in fetcher.calls           # 成分（码表来源）

    def test_all_bootstrap_failure_is_explicit(self, store, gateway):
        """先导失败 ⇒ 显式返回失败信封，码表为空（不静默空跑）。"""
        fetcher = FakeFetcher(fail_on=("security_basic",))
        db, sync = _db_and_sync(store, gateway, fetcher)

        codes, failure = resolve_codes(db, sync, universe="all")

        assert codes == []
        assert failure is not None and failure.status != "ok"

    def test_empty_constituents_yield_empty_codes_without_failure(self, store, gateway):
        """成分表为空 ⇒ ``([], None)``——空码表的兜底是调用方（``main``）的职责。"""
        fetcher = ConstituentFetcher([])
        db, sync = _db_and_sync(store, gateway, fetcher)

        codes, failure = resolve_codes(db, sync, universe="hs300")

        assert (codes, failure) == ([], None)


class TestSelectUniverseCodes:
    def test_hs300_takes_latest_snapshot_only(self, store, gateway):
        """两期成分同表 ⇒ 只取 ``max(update_date)`` 那期，旧期不混入码表。"""
        fetcher = ConstituentFetcher([["2024-01-01", "sh.600519", "贵州茅台"]])
        db, sync = _db_and_sync(store, gateway, fetcher)
        sync.setup()
        sync.run_task("bs_security_basic")
        sync.run_task("bs_hs300")
        # 旧一期成分（含一只新一期已调出的码）
        _insert_constituent(db, "sh.600000", "2023-12-01")

        codes = _select_universe_codes(db, "hs300", start="2024-01-01",
                                       skip_delisted=False)

        assert codes == ["sh.600519"]
        assert "sh.600000" not in codes

    def test_all_delegates_to_master(self, store, gateway):
        fetcher = WideFetcher()
        db, sync = _db_and_sync(store, gateway, fetcher)
        sync.setup()
        sync.run_task("bs_security_basic")

        codes = _select_universe_codes(db, "all", start="2024-01-01",
                                       skip_delisted=False)

        assert codes == sorted(CODES)


class TestParamsBlock:
    def test_universe_recorded_in_params(self, tmp_path: Path):
        """``universe`` 进参数块——它是「新鲜 ≠ 全市场覆盖」的唯一机器可读依据。"""
        args = build_parser().parse_args(["--universe", "hs300"])
        block = _params_block(args, tmp_path, HS300_CODES)

        assert block["universe"] == "hs300"
        assert block["codes"].startswith("2:sh.600000:sz.300750:")
