"""信息面（T-L0-010）测试共用件（helper 模块；conftest 的 fixture 由它构造）。

放独立模块而非 ``conftest`` 里，是为了让各用例文件能直接 ``from info_helpers
import ...``（pytest 的 prepend 导入模式会把本目录加入 ``sys.path``）。
"""

from __future__ import annotations

from st_agent.l0.info import (
    InfoFetchError,
    InfoFetchUnavailableError,
    InfoSync,
)
from st_agent.l0.market import BaoStockSync, MarketDb
from st_agent.l0.storage import Store

PASS = "info-rig-passphrase"
LAST_SUCCESS = "2026-09-25T08:00:00+00:00"

#: 主档样本（另有北交所代码不在主档内，用于过滤口径）
MASTER = (
    ("sh.600000", "浦发银行"),
    ("sh.600001", "甲材料"),
    ("sz.000001", "平安银行"),
    ("sz.300750", "宁德时代"),
)

UNMAPPED_CODE = "bj.920002"
"""不在主档内的北交所代码（D-032 预过滤的样本）。"""


class FakeInfoFetcher:
    """可注入的信息面抓取器：按任务键返回预置行（未预置 → 空成果）。"""

    def __init__(self, plan: dict | None = None, *,
                 unavailable_on: tuple[str, ...] = (),
                 fail_on: tuple[str, ...] = ()) -> None:
        self.plan = dict(plan or {})
        self.unavailable_on = set(unavailable_on)
        self.fail_on = set(fail_on)
        self.calls: list[tuple[str, tuple[str, str]]] = []

    def fetch_task(self, task_key: str, window: tuple[str, str]):
        self.calls.append((task_key, window))
        if task_key in self.unavailable_on:
            raise InfoFetchUnavailableError("源不可达（测试注入）")
        if task_key in self.fail_on:
            raise InfoFetchError("抓取失败（测试注入）")
        return self.plan.get(task_key, {})


def announcement_row(code: str = "600000", *, title: str = "关于回购公司股份的公告",
                     pub_date: str = "2026-09-20", text: str = "回购正文……") -> dict:
    return {"code": code, "title": title, "ann_type": "回购",
            "pub_date": pub_date, "url": "https://example.invalid/a", "text": text}


def build_db(store: Store, gateway) -> MarketDb:
    """建库 + 登记 baostock 源 + 灌主档（信息面 setup 的前置）。"""
    db = MarketDb(store)
    db.init_db()
    BaoStockSync(db, gateway, fetcher=object()).setup()
    with db.transact() as con:
        con.executemany(
            "INSERT OR IGNORE INTO security(code, code_name, ipo_date, out_date,"
            " type, status) VALUES (?, ?, '2010-01-01', NULL, 1, 1)",
            MASTER,
        )
        con.commit()
    return db


def open_sync(db: MarketDb, gateway, fetcher) -> InfoSync:
    """构造并 setup 一个 ``InfoSync``。"""
    sync = InfoSync(db, gateway, fetcher)
    assert sync.setup().status == "ok"
    return sync
