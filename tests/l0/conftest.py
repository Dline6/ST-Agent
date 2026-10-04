"""信息面（T-L0-010）测试 fixture。

真 ``Store`` + 真 ``MarketDb`` + 真 ``EgressGateway``，只把**抓取器**换成可注入
Fake——故 SQL、外键、加密、网关审计都被真实校验，离线可跑（CI 不联网）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from info_helpers import FakeInfoFetcher, build_db, open_sync
from st_agent.l0.info import InfoSync
from st_agent.l0.market import MarketDb
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store

PASS = "info-rig-passphrase"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store, audit=True)


@pytest.fixture()
def db(store: Store, gateway: EgressGateway) -> MarketDb:
    """已建库 + 已登记 baostock 源 + 已灌主档（信息面 setup 的前置）。"""
    return build_db(store, gateway)


@pytest.fixture()
def sync_factory(db: MarketDb, gateway: EgressGateway):
    """``(fetcher) -> 已 setup 的 InfoSync``（每个用例可注入不同 Fake）。"""
    def _make(fetcher) -> InfoSync:
        return open_sync(db, gateway, fetcher)
    return _make


@pytest.fixture()
def sync(sync_factory) -> InfoSync:
    """默认 rig（空预置的 Fake；用例需要数据时用 ``sync_factory`` 自建）。"""
    return sync_factory(FakeInfoFetcher({}))
