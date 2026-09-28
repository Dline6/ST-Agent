"""T-INT-001 · M0 集成关卡的装配 rig（**真装配**，离线可跑）。

装配链：``Store``（落盘加密）→ ``MarketDb``（``data_cache`` 单库）→
``open_runtime``（组合根：端点 / 凭据 / 出网网关 / 官方 Pack / 沙箱 /
流水线 / 调度）。

只把**发包**（``sender``）与**取数面的绑定时机**换成可观测实现——口令派生、
落盘加密、manifest 校验、SQL、外键、审计落盘、流水线留痕全部真实。
**不联网**（``sender`` 是注入的，真实抓取器不在本套件内）。

本套件是「跨层廉价套件」的一员（同 ``tests/contracts``、``tests/test_layering.py``）：
任何代码变更都应随跑，故 `.github/workflows/ci.yml` 的路径映射把它列入基表。

放独立模块而非 ``conftest.py`` 里，是为了让用例文件能直接 ``from rig import ...``
取常量与装配函数（同 ``tests/l0/info_helpers.py`` 的取向）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, open_runtime

PASS = "m0-gate-passphrase"
"""装配用口令（每个用例各自的 ``tmp_path``，无跨用例共享）。"""

ROOT_NAME = "root"

#: 播种数据面：两个交易日 + ST 标记 + 一条水位。
#:
#: - ``sk_st_list_sync``：``2026-09-25`` 相对 ``2026-09-24`` 新增 ``sz.000001``
#:   （``is_st``）→ ``ok`` 且带 diff
#: - ``sk_delisting_risk_scan``：``v_st_universe`` 取最新交易日的 ST 标的，
#:   ``sh.600000`` 收盘 0.50 元 < 面值 1.00 元 → 命中「面值风险」→ ``ok``
_SEED_SQL = """
INSERT INTO data_source (source_id, name, manual_ref)
  VALUES ('baostock', 'BaoStock', NULL);
INSERT INTO security (code, code_name, ipo_date, out_date, type, status) VALUES
  ('sh.600000', '浦发银行', '1999-11-10', NULL, 1, 1),
  ('sz.000001', '平安银行', '1991-04-03', NULL, 1, 1);
INSERT INTO k_line_daily (code, trade_date, close, trade_status, is_st) VALUES
  ('sh.600000', '2026-09-24', 0.60, 1, 1),
  ('sz.000001', '2026-09-24', 12.00, 1, 0),
  ('sh.600000', '2026-09-25', 0.50, 1, 1),
  ('sz.000001', '2026-09-25', 12.30, 1, 1);
INSERT INTO sync_state (
    task_key, table_name, source_id, mode, schedule_desc,
    watermark, last_success_at, last_row_count, last_status, last_error
  ) VALUES (
    'bs_k_daily', 'k_line_daily', 'baostock', 'incremental', '每日收盘后',
    '2026-09-25', '2026-09-25T08:00:00+00:00', 4, 'ok', NULL);
"""


class MarketData:
    """官方 Pack 的取数面：**延迟绑定**到运行时自持的 ``Store``。

    ``open_runtime`` 把「解锁 + 装配」作一体（不接受外部 ``Store``），而官方
    执行器在装配期就要拿到取数源，故此处存根在首次调用时把 ``MarketDb`` 建在
    运行时自己的句柄上——**单一 Store 属主**，避免两个实例各持一份清单副本、
    相互看不见对方的写入。

    实现 ``query(sql, params) -> ResultEnvelope`` 与 ``snapshot_id()``，
    即 ``MarketQuerySource`` 鸭子类型 + 快照证据面。
    """

    def __init__(self) -> None:
        self._db: MarketDb | None = None

    def bind(self, store: Store) -> None:
        """把取数面绑到运行时的 ``Store``（``open_runtime`` 返回后调用）。"""
        self._db = MarketDb(store)

    @property
    def bound(self) -> bool:
        return self._db is not None

    def query(self, sql: str, params: tuple = ()):  # noqa: ANN201（鸭子类型）
        return self._require().query(sql, tuple(params))

    def snapshot_id(self) -> str:
        return self._require().snapshot_id()

    def _require(self) -> MarketDb:
        if self._db is None:
            raise RuntimeError("取数面未绑定（运行时尚未装配）")
        return self._db


class RecordingSender:
    """可观测的发包实现：记下每次调用并恒成功。

    既作 GWT-2 出网审计的**真实发包**，也作 GWT-4 的**未触碰探针**——
    沙箱拦截在网关之前，故越界用例断言 ``calls == []``。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def __call__(self, kind: str, target_host: str, timeout_ms: int):
        self.calls.append((kind, target_host, timeout_ms))
        return (0, 0, ())


@dataclass(frozen=True)
class Rig:
    """一次装配的全部句柄（根目录 / 运行时 / 取数面 / 发包探针）。"""

    root: Path
    runtime: L1Runtime
    feed: MarketData
    sender: RecordingSender


def seed_market_db(root: Path, passphrase: str) -> None:
    """建库 + 播种最小真实数据面（自有 ``Store``，播种完即弃用）。

    播种在 ``open_runtime`` 之前完成，故运行时打开存储时读到的是最终清单；
    之后**只有一个 Store 属主**（运行时自己）。
    """
    store = Store.create(root, passphrase)
    db = MarketDb(store)
    db.init_db()
    with db.transact() as con:
        con.executescript(_SEED_SQL)
        con.commit()


def assemble(root: Path, *, create: bool = False) -> Rig:
    """装配一次运行时（``create=True`` 即盘上无存储时先初始化）。

    ``llm_env={}`` **显式关闭** LLM 引导装载（T-L1-011）——本套件是**离线**
    装配，不能随开发机 ``.env`` 的有无而变（否则 CI 无 ``.env`` 过、本机挂）；
    LLM 面由各用例按需自注入。
    """
    feed = MarketData()
    sender = RecordingSender()
    runtime = open_runtime(
        root, PASS, create=create, market_query=feed, sender=sender, llm_env={},
    )
    feed.bind(runtime.store)
    return Rig(root=root, runtime=runtime, feed=feed, sender=sender)


def seeded(root: Path) -> Rig:
    """已播种数据面的装配（``MarketDb`` 已建库；GWT-1/2/4/5 用）。"""
    seed_market_db(root, PASS)
    return assemble(root)


def bare(root: Path) -> Rig:
    """**无数据面**的装配（``MarketDb`` 未建库 → 查询走 ``unavailable``）。

    GWT-1 的「全新空目录 + 口令」与 GWT-3 的「数据源不可用」入口。
    """
    return assemble(root, create=True)
