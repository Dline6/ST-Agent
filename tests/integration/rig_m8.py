"""T-INT-009 · M8 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M5 的 ``rig_m5.py`` 同一取向：真 ``Store``（口令派生、落盘加密、清单校验）+
真 ``MarketDb`` + 真官方 Pack + 真 L0 LLM 链路（``LlmClient`` / transport / 出网网关 /
能力协商），只把**HTTP 发送口**（``llm_post``）换成脚本化替身、把**意图理解器**换成
确定性规则表——故启动探测、工具目录、闸门、L1 流水线与留痕**全是真跑的**。

三处与 M5 rig 的差别：

1. **ST + 行业种子**（:func:`seed_sector_market_db`）——在 M0 种子上补四条证券、
   行业快照与涨跌幅，使 ``stock_industry`` × ``k_line_daily.is_st`` 的板块聚合可复算；
2. **``sector_query`` 常量**——GWT-1 / GWT-4 共用的那条聚合 SQL（带一个绑定参数，
   故 ``inputs["params"]`` 也进留痕）；
3. **``unseeded_m8``**——无数据面的装配（GWT-2 的「数据缺失」分支），不播种、只建根。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import DeterministicUnderstander
from rig_m5 import ENV, ScriptedEndpoint, register_demo_skills

from st_agent.app import M5Runtime, build_m5_runtime
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.l3.intent import IntentDraft

__all__ = [
    "SECTOR_QUERY",
    "SECTOR_SQL",
    "STALE_AS_OF",
    "TASK_TEXT",
    "M8Rig",
    "seeded_m8",
    "seed_sector_market_db",
    "unseeded_m8",
]

TASK_TEXT = "分析最近 ST 股票有哪些热门板块"
"""GWT-1 的用户原话（经 `investigate` 意图的 `target` 承载任务文本）。"""

STALE_AS_OF = "2026-09-25"
"""种子的数据截止日。

相对当前日期（关卡跑于 2026-10 之后）必然陈旧——GWT-2 的「陈旧」分支不依赖墙钟断言，
只断言这个日期被原样带出（见测试文件的口径）。
"""

SECTOR_SQL = """\
SELECT i.industry AS industry,
       COUNT(*) AS st_count,
       ROUND(AVG(k.pct_chg), 2) AS avg_pct_chg
  FROM k_line_daily k
  JOIN stock_industry i
    ON i.code = k.code
   AND i.update_date = (SELECT MAX(update_date) FROM stock_industry)
 WHERE k.trade_date = (SELECT MAX(trade_date) FROM k_line_daily)
   AND k.is_st = ?
 GROUP BY i.industry
 ORDER BY st_count DESC, avg_pct_chg DESC
"""
"""GWT-1 的板块聚合（带**一个**绑定参数，故 ``inputs["params"]`` 也在留痕里）。

``k_line_daily.is_st`` × ``stock_industry.industry`` 即「ST 股票按板块聚合」，
不新增表 / 视图（关卡 A3）。
"""

SECTOR_QUERY: tuple[str, dict[str, Any]] = ("sk_local_sql", {"sql": SECTOR_SQL, "params": [1]})

_EXTEND_SQL = f"""\
INSERT INTO security (code, code_name, ipo_date, out_date, type, status) VALUES
  ('sz.000002', '万科电子', '1991-01-29', NULL, 1, 1),
  ('sh.600002', '华能电子', '1998-06-05', NULL, 1, 1),
  ('sz.000003', '国药医药', '1993-08-09', NULL, 1, 1),
  ('sh.600003', '上药医药', '2000-03-11', NULL, 1, 1);

INSERT INTO k_line_daily (code, trade_date, close, pct_chg, trade_status, is_st) VALUES
  ('sz.000002', '{STALE_AS_OF}', 8.80, 5.00, 1, 1),
  ('sh.600002', '{STALE_AS_OF}', 4.20, 3.00, 1, 0),
  ('sz.000003', '{STALE_AS_OF}', 6.10, -1.00, 1, 1),
  ('sh.600003', '{STALE_AS_OF}', 9.30, 0.50, 1, 0);

UPDATE k_line_daily SET pct_chg = 2.00 WHERE code = 'sh.600000' AND trade_date = '{STALE_AS_OF}';
UPDATE k_line_daily SET pct_chg = 1.00 WHERE code = 'sz.000001' AND trade_date = '{STALE_AS_OF}';

INSERT INTO stock_industry (code, update_date, code_name, industry, industry_classification)
  VALUES
  ('sh.600000', '2026-09-21', '浦发银行', '银行', '申万一级行业'),
  ('sz.000001', '2026-09-21', '平安银行', '银行', '申万一级行业'),
  ('sz.000002', '2026-09-21', '万科电子', '电子', '申万一级行业'),
  ('sh.600002', '2026-09-21', '华能电子', '电子', '申万一级行业'),
  ('sz.000003', '2026-09-21', '国药医药', '医药', '申万一级行业'),
  ('sh.600003', '2026-09-21', '上药医药', '医药', '申万一级行业');

INSERT INTO sync_state (
    task_key, table_name, source_id, mode, schedule_desc,
    watermark, last_success_at, last_row_count, last_status, last_error
  ) VALUES (
    'bs_stock_industry', 'stock_industry', 'baostock', 'full', '每周一',
    '2026-09-21', '2026-09-26T08:00:00+08:00', 6, 'ok', NULL);
"""
"""ST + 行业种子（在 M0 种子上**追加**，不重复插已存在的码）。

聚合预期（``{STALE_AS_OF}`` 的 ``is_st = 1``）：

- 银行 → 2 只（sh.600000 / sz.000001），均涨 1.50
- 电子 → 1 只（sz.000002），均涨 5.00
- 医药 → 1 只（sz.000003），均涨 -1.00
"""


def seed_sector_market_db(root: Path, passphrase: str = PASS) -> None:
    """先跑 M0 种子，再追加 ST + 行业面（播种完即弃用 Store；运行时自己开）。"""
    seed_market_db(root, passphrase)
    store = Store.open(root, passphrase)
    db = MarketDb(store)
    with db.transact() as con:
        con.executescript(_EXTEND_SQL)
        con.commit()


@dataclass(frozen=True)
class M8Rig:
    """一次 M8 装配的全部句柄。"""

    root: Path
    m8: M5Runtime
    feed: MarketData
    sender: RecordingSender
    understander: DeterministicUnderstander
    endpoint: ScriptedEndpoint


def _assemble(
    root: Path,
    *,
    script: list[tuple[str, dict]] | None,
    rules: list[tuple[str, IntentDraft]] | None,
    create: bool,
    finish: str = "证据已收集完毕",
    **l1_kwargs: Any,
) -> M8Rig:
    feed = MarketData()
    sender = RecordingSender()
    endpoint = ScriptedEndpoint(script=script, finish=finish)
    understander = DeterministicUnderstander(
        _RULES if rules is None else rules
    )
    l1_kwargs.setdefault("dotenv_path", root.parent / "test.env")
    l1_kwargs.setdefault("bootstrap_file", root.parent / "test-bootstrap.json")
    kwargs: dict[str, Any] = {
        "market_query": feed,
        "sender": sender,
        "llm_env": ENV,
        "llm_post": endpoint,
        "understander": understander,
        **l1_kwargs,
    }
    if create:
        kwargs["create"] = True
    runtime = build_m5_runtime(root, PASS, **kwargs)
    register_demo_skills(runtime)
    return M8Rig(
        root=root, m8=runtime, feed=feed, sender=sender,
        understander=understander, endpoint=endpoint,
    )


_RULES: list[tuple[str, IntentDraft]] = [
    ("分析", IntentDraft(intent="investigate", target=TASK_TEXT)),
]


def seeded_m8(
    root: Path,
    *,
    script: list[tuple[str, dict]] | None = None,
    rules: list[tuple[str, IntentDraft]] | None = None,
    finish: str = "证据已收集完毕",
    **l1_kwargs: Any,
) -> M8Rig:
    """已播种 ST + 行业面的 M8 全栈装配（生产组合根 ``build_m5_runtime``）。"""
    seed_sector_market_db(root, PASS)
    return _assemble(
        root, script=script, rules=rules, create=False, finish=finish, **l1_kwargs
    )


def unseeded_m8(
    root: Path,
    *,
    script: list[tuple[str, dict]] | None = None,
    rules: list[tuple[str, IntentDraft]] | None = None,
    finish: str = "证据已收集完毕",
    **l1_kwargs: Any,
) -> M8Rig:
    """**无数据面**的装配（``MarketDb`` 未建库 → 查询走 ``unavailable``）。"""
    return _assemble(
        root, script=script, rules=rules, create=True, finish=finish, **l1_kwargs
    )
