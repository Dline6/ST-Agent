"""官方数据维度声明（本地数据域 → 产出该域的官方 Skill）。

[06 §3](../../../../docs/技术架构-v2/06-L4-多视角推理.md) 的**盲点**判定 ＝「本地缓存中
与主题相关的数据维度」×「参与视角的 `skill_bundle`」无交集者。前半句的取值面由
[`DimensionCatalog`](../../l4/divergence.py) 端口给出，「维度 → 产出 Skill」这一映射**在仓库里
没有结构性来源**——官方执行器内嵌 SQL 是闭包（不可内省），`SkillDescriptor`
（[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md)）亦无此字段。故由**官方 Pack 旁**的
本声明承载：它与 [`OFFICIAL_PACK`](pack.py) 同处一包、**随官方 Skill 增删同步维护**，
不触 [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 契约（[铁律 8](../../../项目管理/工程宪法.md) 不触发）。

维度键取 [L0 数据域](../../l0/market/sync.py) 同名口径（`kline` / `financial` / …），
`tables` 即该域的本地缓存承载表——组合根的维度目录据此判「该域是否存在于本地数据面」，
故**读库是为了判存在性，不是为了取维度全集**（维度全集是本声明）。

`skill_bases` 为**空**表示该域官方 Skill 尚未产出（如龙虎榜 / 股东变化 / 舆情 / 宏观
尚无官方执行器读取）。空产出**不是**盲点的同义词：对照件按既有口径把
`skill_ids` 为空者记「覆盖不可判」并如实标注，不臆断为盲点（[`crosscheck`](../../l4/crosscheck.py)）。
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["OFFICIAL_DIMENSIONS", "DimensionSeed"]


class DimensionSeed(NamedTuple):
    """一条官方数据维度声明：键 / 中性标签 / 承载表 / 产出该域的官方 Skill base。"""

    key: str
    """维度键（与 [L0 数据域](../../l0/market/sync.py) 同名，稳定、非展示用）。"""
    label: str
    """中性标签（展示用；不含拟人名 / 性格 / 情感词，[铁律 2](../../../项目管理/工程宪法.md)）。"""
    tables: tuple[str, ...]
    """该域的本地缓存承载表 / 视图名（存在其一即视该域在本地数据面可用）。"""
    skill_bases: tuple[str, ...]
    """产出该域的官方 Skill base 名（[`OFFICIAL_PACK`](pack.py) 的 `base` 口径）；
    空表示官方 Skill 尚未产出该域。"""


OFFICIAL_DIMENSIONS: tuple[DimensionSeed, ...] = (
    DimensionSeed(
        key="kline", label="行情",
        tables=("k_line_daily", "k_line_period", "k_line_minute", "adjust_factor"),
        skill_bases=(
            "sk_st_list_sync", "sk_delisting_risk_scan", "sk_sector_heatmap",
            "sk_sentiment_flow_analysis", "sk_fundamental_screening", "sk_stock_watch",
            "sk_risk_alert", "sk_opportunity_mine", "sk_portfolio_stress_test",
            "sk_strategy_design",
        ),
    ),
    DimensionSeed(
        key="financial", label="财务",
        tables=("financial_quarter",),
        skill_bases=(
            "sk_delisting_risk_scan", "sk_unhat_eligibility_check",
            "sk_fundamental_screening", "sk_stock_watch",
        ),
    ),
    DimensionSeed(
        key="company_report", label="公司报告",
        tables=("performance_express", "profit_forecast"),
        skill_bases=(
            "sk_unhat_eligibility_check", "sk_delisting_risk_scan", "sk_stock_watch",
        ),
    ),
    DimensionSeed(
        key="sector", label="行业板块",
        tables=("stock_industry", "index_constituent"),
        skill_bases=("sk_sector_heatmap", "sk_opportunity_mine"),
    ),
    DimensionSeed(
        key="security", label="证券主档",
        tables=("security",),
        skill_bases=("sk_fundamental_screening",),
    ),
    DimensionSeed(
        key="announcement", label="公告",
        tables=("announcement",),
        skill_bases=("sk_stock_watch",),
    ),
    # 以下域的官方 Skill 尚未产出（本地缓存有表、无执行器读取）——
    # 声明出来是为了让「官方能力覆盖缺口」可见（对照件按其口径记「覆盖不可判」）。
    DimensionSeed(
        key="macro", label="宏观",
        tables=("macro_deposit_rate", "macro_loan_rate", "macro_reserve_ratio",
                "macro_money_supply_month", "macro_money_supply_year"),
        skill_bases=(),
    ),
    DimensionSeed(
        key="dragon_tiger", label="龙虎榜",
        tables=("dragon_tiger", "dragon_tiger_seat"),
        skill_bases=(),
    ),
    DimensionSeed(
        key="shareholder", label="股东变化",
        tables=("shareholder_num",),
        skill_bases=(),
    ),
    DimensionSeed(
        key="sentiment", label="舆情",
        tables=("sentiment_hot", "sentiment_qa"),
        skill_bases=(),
    ),
    DimensionSeed(
        key="dividend", label="分红送转",
        tables=("dividend",),
        skill_bases=(),
    ),
)


def _assert_seed_consistency() -> None:
    """声明自洽：维度键唯一 · 承载表不跨维度重复（一张表归一个域）。"""
    keys = [d.key for d in OFFICIAL_DIMENSIONS]
    assert len(keys) == len(set(keys)), f"维度键重复：{keys}"
    seen: dict[str, str] = {}
    for dim in OFFICIAL_DIMENSIONS:
        for table in dim.tables:
            assert table not in seen, (
                f"表 {table!r} 同时归入 {seen[table]!r} 与 {dim.key!r}"
            )
            seen[table] = dim.key


_assert_seed_consistency()
