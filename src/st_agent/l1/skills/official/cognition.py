"""公共认知 Bundle 的执行器（T-L1-004.2；03 §2.1）。

6 个 Skill：``st-list-sync`` / ``delisting-risk-scan`` / ``unhat-eligibility-check``
/ ``sector-heatmap`` / ``sentiment-flow-analysis`` / ``fundamental-screening``。

- **取数**：一律经 :mod:`st_agent.l1.skills.official.common` 的单一入口
  （``query_rows``）进入 L0 数据源缓存；``empty`` / ``unavailable`` 信封**原样**
  成为执行结果（判定权在 L0，不编造替代数据）
- **输入口径**：只取「声明的参数 + ``ctx.upstream``」两条既有通道；默认口径为
  「本地缓存全量」，参数用于收窄（T-L1-004.1 假设 A1/A2）
- **阈值**：全部集中在本模块常量区（可解释规则，不引入黑箱打分），便于复核
- **措辞**：输出文案为陈述式，过 01 §6 ``check_output``（无第一人称 / 情感 / 对话体）
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner.runner import SkillContext, SkillExecutor
from st_agent.l1.skills.official.common import (
    MarketQuerySource,
    empty_envelope,
    evidence_of,
    fetch_rows,
    ok_envelope,
    round_of,
    stopping_factory,
)

__all__ = ["EXECUTORS", "FUTURE_YEAR_SLACK", "RISK_RULE_WEIGHTS"]

# ───────────────────────── 规则常量（可解释口径，集中复核处） ─────────────────────────

FACE_VALUE = 1.0
"""面值退市口径：收盘价低于 1 元（元）。"""

LIABILITY_ALERT = 80.0
"""偿债风险口径：资产负债率高于 80%（%）。"""

LIABILITY_MAX_SCREEN = 70.0
"""基本面筛选的资产负债率上限（%）。"""

LIABILITY_UNHAT_MAX = 100.0
"""摘帽条件口径：资产负债率须低于 100%（%）。"""

ROE_MIN_SCREEN = 8.0
"""基本面筛选的 ROE(平均) 下限（%）。"""

REVENUE_UNHAT_MIN = 1e8
"""摘帽条件口径：营业收入不低于 1 亿元（元）。"""

TURN_SPIKE_RATIO = 1.5
"""情绪资金流向口径：当日换手率 ≥ 窗口均值的 1.5 倍 → 高换手。"""

AMOUNT_SPIKE_RATIO = 2.0
"""情绪资金流向口径：当日成交额 ≥ 窗口均值的 2 倍 → 高热度。"""

HEATMAP_HIGH_RATIO = 0.5
HEATMAP_MEDIUM_RATIO = 0.25
"""板块热力评级口径：ST 占比 ≥ 50% 为 high、≥ 25% 为 medium，其余 low。"""

MAX_WARNINGS = 50
MAX_POOL = 100
"""输出条数上限（本地缓存全量扫描时的载荷边界，超出按排序截断）。"""

_RISK_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("面值风险", 0.4),
    ("盈利风险", 0.3),
    ("业绩预告", 0.2),
    ("偿债风险", 0.1),
)
RISK_RULE_WEIGHTS: dict[str, float] = dict(_RISK_WEIGHTS)
"""退市高危信号的加权重（合计 1.0）——``threshold`` 参数即判定 high 的分位。"""

NORMALIZATION_CAPS: dict[str, float] = {
    "roe_avg": 30.0, "np_margin": 30.0, "yoy_ni": 100.0,
}
"""基本面评分归一化上限（%）：各项按 上限 归一后等权平均。"""

FUTURE_YEAR_SLACK = "%-12-31"
"""财报年端口径（``stat_date`` 的年度形态，用于「连续 N 个财年盈利」判定）。"""


# ───────────────────────── st-list-sync ─────────────────────────


def _st_codes(source: MarketQuerySource, trade_date: str, exchange: str) -> list[str]:
    got = fetch_rows(
        source,
        "SELECT code FROM k_line_daily WHERE trade_date = ? AND is_st = 1 ORDER BY code",
        (trade_date,),
    )
    codes = [str(r["code"]) for r in got.rows]
    if exchange != "all":
        codes = [c for c in codes if c.startswith(f"{exchange}.")]
    return codes


@stopping_factory
def _st_list_sync(source, ctx, params) -> ResultEnvelope:
    exchange = params["exchange"]
    dates = fetch_rows(
        source,
        "SELECT trade_date FROM ("
        " SELECT DISTINCT trade_date FROM k_line_daily ORDER BY trade_date DESC LIMIT 2)",
    )
    if len(dates.rows) < 2:
        return empty_envelope(
            "本地缓存不足两个交易日，无法给出 ST 名单变更对照", as_of=dates.as_of
        )
    latest = str(dates.rows[0]["trade_date"])
    previous = str(dates.rows[1]["trade_date"])
    codes_latest = _st_codes(source, latest, exchange)
    codes_previous = _st_codes(source, previous, exchange)
    added = sorted(set(codes_latest) - set(codes_previous))
    removed = sorted(set(codes_previous) - set(codes_latest))
    if not codes_latest and not codes_previous:
        return empty_envelope(
            f"{latest} 与 {previous} 均无 is_st=1 标记行，本地缓存无 ST 名单数据",
            as_of=dates.as_of,
        )
    if not added and not removed:
        return empty_envelope(
            f"{latest} 相对 {previous} 的 ST 名单无变更（{len(codes_latest)} 只）",
            as_of=dates.as_of,
        )
    return ok_envelope(
        {
            "diff": {
                "added": added,
                "removed": removed,
                "counts": {
                    "added": len(added),
                    "removed": len(removed),
                    "total": len(codes_latest),
                },
                "as_of_date": latest,
                "previous_date": previous,
                "exchange": exchange,
            }
        },
        as_of=dates.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── delisting-risk-scan ─────────────────────────

_RISK_SCAN_SQL = (
    "SELECT u.code AS code, u.close AS close,"
    " f.net_profit AS net_profit, f.liability_to_asset AS liability_to_asset,"
    " p.forecast_type AS forecast_type"
    " FROM v_st_universe u"
    " LEFT JOIN (SELECT code, net_profit, liability_to_asset,"
    "     ROW_NUMBER() OVER (PARTITION BY code ORDER BY stat_date DESC) AS rn"
    "   FROM financial_quarter) f ON f.code = u.code AND f.rn = 1"
    " LEFT JOIN (SELECT code, forecast_type,"
    "     ROW_NUMBER() OVER (PARTITION BY code ORDER BY stat_date DESC) AS rn"
    "   FROM profit_forecast) p ON p.code = u.code AND p.rn = 1"
    " ORDER BY u.code"
)


def _risk_hits(row: dict[str, Any]) -> list[tuple[str, str]]:
    """单个标的触发的高危信号 → ``[(信号名, 人可读说明)]``。"""
    hits: list[tuple[str, str]] = []
    close = row.get("close")
    if close is not None and float(close) < FACE_VALUE:
        hits.append(("面值风险", f"最新收盘价 {float(close):.2f} 元低于面值 {FACE_VALUE:.2f} 元"))
    net_profit = row.get("net_profit")
    if net_profit is not None and float(net_profit) < 0:
        hits.append(("盈利风险", f"最近报告期净利润 {float(net_profit):.0f} 元为负"))
    forecast = row.get("forecast_type")
    if isinstance(forecast, str) and "亏" in forecast:
        hits.append(("业绩预告", f"业绩预告类型为 {forecast}"))
    liability = row.get("liability_to_asset")
    if liability is not None and float(liability) > LIABILITY_ALERT:
        hits.append(("偿债风险", f"资产负债率 {float(liability):.2f}% 高于 {LIABILITY_ALERT:.0f}%"))
    return hits


def _level_of(score: float, threshold: float) -> str:
    if score >= threshold:
        return "high"
    if score >= threshold / 2:
        return "medium"
    return "low"


@stopping_factory
def _delisting_risk_scan(source, ctx, params) -> ResultEnvelope:
    threshold = float(params["threshold"])
    got = fetch_rows(source, _RISK_SCAN_SQL)
    if not got.rows:
        return empty_envelope(
            "本地缓存无 ST 池数据（v_st_universe 为空）", as_of=got.as_of
        )
    triggers: list[dict[str, Any]] = []
    for row in got.rows:
        hits = _risk_hits(row)
        if not hits:
            continue
        score = round(sum(RISK_RULE_WEIGHTS[name] for name, _ in hits), 4)
        triggers.append(
            {
                "code": row["code"],
                "reason": "、".join(name for name, _ in hits),
                "detail": "；".join(detail for _, detail in hits),
                "risk_level": _level_of(score, threshold),
                "score": score,
            }
        )
    triggers.sort(key=lambda t: (-t["score"], t["code"]))
    if not triggers:
        return empty_envelope(
            f"已扫描 {len(got.rows)} 只 ST 标的，未命中退市高危信号", as_of=got.as_of
        )
    return ok_envelope(
        {
            "risk_level": triggers[0]["risk_level"],
            "score": triggers[0]["score"],
            "triggers": triggers,
            "scanned": len(got.rows),
            "threshold": threshold,
        },
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── unhat-eligibility-check ─────────────────────────

_UNHAT_SQL = (
    "SELECT u.code AS code,"
    " f.net_profit AS net_profit, f.mb_revenue AS mb_revenue,"
    " f.liability_to_asset AS liability_to_asset, e.net_asset AS net_asset"
    " FROM v_st_universe u"
    " LEFT JOIN (SELECT code, net_profit, mb_revenue, liability_to_asset,"
    "     ROW_NUMBER() OVER (PARTITION BY code ORDER BY stat_date DESC) AS rn"
    "   FROM financial_quarter) f ON f.code = u.code AND f.rn = 1"
    " LEFT JOIN (SELECT code, net_asset,"
    "     ROW_NUMBER() OVER (PARTITION BY code ORDER BY stat_date DESC) AS rn"
    "   FROM performance_express) e ON e.code = u.code AND e.rn = 1"
    " ORDER BY u.code"
)

_ANNUAL_PROFIT_SQL = (
    "SELECT code, stat_date, net_profit FROM financial_quarter"
    f" WHERE stat_date LIKE '{FUTURE_YEAR_SLACK}'"
    " ORDER BY code, stat_date DESC"
)


def _annual_profit_by_code(source: MarketQuerySource) -> dict[str, list[float]]:
    got = fetch_rows(source, _ANNUAL_PROFIT_SQL)
    out: dict[str, list[float]] = {}
    for row in got.rows:
        if row.get("net_profit") is None:
            continue
        out.setdefault(str(row["code"]), []).append(float(row["net_profit"]))
    return out


def _unhat_condition(name: str, satisfied: bool | None, detail: str) -> dict[str, Any]:
    return {"name": name, "satisfied": satisfied, "detail": detail}


def _unhat_conditions(row: dict[str, Any], profits: list[float], fiscal_years: int) -> list[dict]:
    code = row["code"]
    window = profits[:fiscal_years]
    positive = sum(1 for p in window if p > 0)
    if not window:
        yearly = _unhat_condition(
            f"连续 {fiscal_years} 个财年净利润为正", None, "本地缓存无年度净利润记录"
        )
    else:
        yearly = _unhat_condition(
            f"连续 {fiscal_years} 个财年净利润为正",
            len(window) == fiscal_years and positive == fiscal_years,
            f"最近 {len(window)} 个财年中 {positive} 个为正",
        )
    net_profit = row.get("net_profit")
    latest = _unhat_condition(
        "最近报告期净利润为正",
        None if net_profit is None else float(net_profit) > 0,
        "本地缓存无报告期净利润" if net_profit is None else f"净利润 {float(net_profit):.0f} 元",
    )
    net_asset = row.get("net_asset")
    asset = _unhat_condition(
        "净资产为正",
        None if net_asset is None else float(net_asset) > 0,
        "本地缓存无净资产记录" if net_asset is None else f"净资产 {float(net_asset):.0f} 元",
    )
    revenue = row.get("mb_revenue")
    gross = _unhat_condition(
        f"营业收入不低于 {REVENUE_UNHAT_MIN / 1e8:.0f} 亿元",
        None if revenue is None else float(revenue) >= REVENUE_UNHAT_MIN,
        "本地缓存无营业收入" if revenue is None else f"营业收入 {float(revenue):.0f} 元",
    )
    liability = row.get("liability_to_asset")
    debt = _unhat_condition(
        f"资产负债率低于 {LIABILITY_UNHAT_MAX:.0f}%",
        None if liability is None else float(liability) < LIABILITY_UNHAT_MAX,
        "本地缓存无资产负债率" if liability is None else f"资产负债率 {float(liability):.2f}%",
    )
    return [dict(item, code=code) for item in (yearly, latest, asset, gross, debt)]


@stopping_factory
def _unhat_eligibility_check(source, ctx, params) -> ResultEnvelope:
    fiscal_years = int(params["fiscal_years"])
    got = fetch_rows(source, _UNHAT_SQL)
    if not got.rows:
        return empty_envelope(
            "本地缓存无 ST 池数据（v_st_universe 为空）", as_of=got.as_of
        )
    profits = _annual_profit_by_code(source)
    conditions: list[dict] = []
    for row in got.rows:
        conditions.extend(
            _unhat_conditions(row, profits.get(str(row["code"]), []), fiscal_years)
        )
    met = sum(1 for c in conditions if c["satisfied"] is True)
    return ok_envelope(
        {
            "conditions": conditions,
            "score": round_of(met / len(conditions)) if conditions else 0.0,
            "st_count": len(got.rows),
            "fiscal_years": fiscal_years,
        },
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── sector-heatmap ─────────────────────────

_HEATMAP_SQL = (
    "SELECT i.industry AS industry, COUNT(*) AS total,"
    " SUM(CASE WHEN k.is_st = 1 THEN 1 ELSE 0 END) AS st_count,"
    " AVG(k.pct_chg) AS avg_pct_chg"
    " FROM stock_industry i"
    " JOIN (SELECT MAX(update_date) AS d FROM stock_industry) li"
    "   ON i.update_date = li.d"
    " JOIN v_k_line_latest k ON k.code = i.code"
    " WHERE i.industry IS NOT NULL AND i.industry <> ''"
    " GROUP BY i.industry"
    " ORDER BY st_count DESC, avg_pct_chg ASC"
    " LIMIT ?"
)


def _heat_rating(ratio: float) -> str:
    if ratio >= HEATMAP_HIGH_RATIO:
        return "high"
    if ratio >= HEATMAP_MEDIUM_RATIO:
        return "medium"
    return "low"


@stopping_factory
def _sector_heatmap(source, ctx, params) -> ResultEnvelope:
    top_n = int(params["top_n"])
    got = fetch_rows(source, _HEATMAP_SQL, (top_n,))
    if not got.rows:
        return empty_envelope(
            "本地缓存无行业快照与行情可交叉（stock_industry 与日线均需有数据）",
            as_of=got.as_of,
        )
    industries = []
    for row in got.rows:
        total = int(row["total"] or 0)
        st_count = int(row["st_count"] or 0)
        ratio = (st_count / total) if total else 0.0
        industries.append(
            {
                "industry": row["industry"],
                "total": total,
                "st_count": st_count,
                "st_ratio": round_of(ratio),
                "avg_pct_chg": round_of(row["avg_pct_chg"]),
                "rating": _heat_rating(ratio),
            }
        )
    order = {"high": 2, "medium": 1, "low": 0}
    return ok_envelope(
        {
            "heatmap": {"industries": industries, "industry_count": len(industries)},
            "rating": max((i["rating"] for i in industries), key=lambda r: order[r]),
        },
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── sentiment-flow-analysis ─────────────────────────

_SENTIMENT_SQL = (
    "WITH spike_window AS ("
    "  SELECT code, AVG(turn) AS avg_turn, AVG(amount) AS avg_amount"
    "  FROM k_line_daily"
    "  WHERE trade_date >= (SELECT MIN(trade_date) FROM ("
    "    SELECT DISTINCT trade_date FROM k_line_daily ORDER BY trade_date DESC LIMIT ?))"
    "  GROUP BY code)"
    " SELECT k.code AS code, k.trade_date AS trade_date, k.turn AS turn,"
    "  k.amount AS amount, k.pct_chg AS pct_chg,"
    "  w.avg_turn AS avg_turn, w.avg_amount AS avg_amount"
    " FROM k_line_daily k"
    " JOIN (SELECT MAX(trade_date) AS d FROM k_line_daily) l ON l.d = k.trade_date"
    " LEFT JOIN spike_window w ON w.code = k.code"
    " WHERE (k.turn IS NOT NULL AND w.avg_turn > 0 AND k.turn >= w.avg_turn * ?)"
    "    OR (w.avg_amount > 0 AND k.amount >= w.avg_amount * ?)"
    " ORDER BY k.code LIMIT ?"
)


def _sentiment_warnings(row: dict[str, Any], window_days: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    turn, avg_turn = row.get("turn"), row.get("avg_turn")
    if turn is not None and avg_turn and float(turn) >= float(avg_turn) * TURN_SPIKE_RATIO:
        ratio = float(turn) / float(avg_turn)
        out.append(
            {
                "code": row["code"],
                "kind": "high-turnover",
                "detail": (
                    f"{row['trade_date']} 换手率 {float(turn):.2f}%，"
                    f"为近 {window_days} 日均值 {float(avg_turn):.2f}% 的 {ratio:.2f} 倍"
                ),
            }
        )
    amount, avg_amount = row.get("amount"), row.get("avg_amount")
    if amount is not None and avg_amount and float(amount) >= float(avg_amount) * AMOUNT_SPIKE_RATIO:
        ratio = float(amount) / float(avg_amount)
        out.append(
            {
                "code": row["code"],
                "kind": "high-heat",
                "detail": (
                    f"{row['trade_date']} 成交额 {float(amount):.0f} 元，"
                    f"为近 {window_days} 日均值 {float(avg_amount):.0f} 元的 {ratio:.2f} 倍"
                ),
            }
        )
    return out


@stopping_factory
def _sentiment_flow_analysis(source, ctx, params) -> ResultEnvelope:
    window_days = int(params["window_days"])
    got = fetch_rows(
        source, _SENTIMENT_SQL,
        (window_days, TURN_SPIKE_RATIO, AMOUNT_SPIKE_RATIO, MAX_WARNINGS),
    )
    warnings: list[dict[str, Any]] = []
    for row in got.rows:
        warnings.extend(_sentiment_warnings(row, window_days))
    if not warnings:
        return empty_envelope(
            f"近 {window_days} 日无换手率或成交额显著偏离自身均值的标的",
            as_of=got.as_of,
        )
    return ok_envelope(
        {"warnings": warnings, "window_days": window_days, "count": len(warnings)},
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── fundamental-screening ─────────────────────────

_SCREEN_SQL = (
    "SELECT f.code AS code, s.code_name AS code_name,"
    " f.roe_avg AS roe_avg, f.np_margin AS np_margin, f.net_profit AS net_profit,"
    " f.yoy_ni AS yoy_ni, f.liability_to_asset AS liability_to_asset,"
    " k.pe_ttm AS pe_ttm, k.pb_mrq AS pb_mrq"
    " FROM (SELECT code, roe_avg, np_margin, net_profit, yoy_ni, liability_to_asset,"
    "     ROW_NUMBER() OVER (PARTITION BY code ORDER BY stat_date DESC) AS rn"
    "   FROM financial_quarter) f"
    " JOIN security s ON s.code = f.code AND s.type = 1 AND s.status = 1"
    " LEFT JOIN v_k_line_latest k ON k.code = f.code"
    " WHERE f.rn = 1"
)


def _screen_score(row: dict[str, Any]) -> float | None:
    caps = NORMALIZATION_CAPS
    values = []
    for field in ("roe_avg", "np_margin", "yoy_ni"):
        raw = row.get(field)
        if raw is None:
            return None
        values.append(min(float(raw), caps[field]) / caps[field])
    return round(sum(values) / len(values), 4)


def _screen_pass(row: dict[str, Any], score: float, margin: float) -> bool:
    roe = row.get("roe_avg")
    np_margin = row.get("np_margin")
    yoy_ni = row.get("yoy_ni")
    liability = row.get("liability_to_asset")
    if roe is None or np_margin is None or yoy_ni is None or liability is None:
        return False
    return (
        float(roe) >= ROE_MIN_SCREEN
        and float(np_margin) > 0
        and float(yoy_ni) > 0
        and float(liability) < LIABILITY_MAX_SCREEN
        and score >= margin
    )


def _screen_reasons(row: dict[str, Any]) -> list[str]:
    return [
        f"ROE(平均) {float(row['roe_avg']):.2f}% 不低于 {ROE_MIN_SCREEN:.0f}%",
        f"销售净利率 {float(row['np_margin']):.2f}% 为正",
        f"净利润同比 {float(row['yoy_ni']):.2f}% 为正",
        f"资产负债率 {float(row['liability_to_asset']):.2f}% 低于 {LIABILITY_MAX_SCREEN:.0f}%",
    ]


@stopping_factory
def _fundamental_screening(source, ctx, params) -> ResultEnvelope:
    margin = float(params["margin"])
    got = fetch_rows(source, _SCREEN_SQL)
    pool: list[dict[str, Any]] = []
    for row in got.rows:
        score = _screen_score(row)
        if score is None or not _screen_pass(row, score, margin):
            continue
        pool.append(
            {
                "code": row["code"],
                "code_name": row.get("code_name"),
                "score": score,
                "pe_ttm": round_of(row.get("pe_ttm")),
                "pb_mrq": round_of(row.get("pb_mrq")),
                "reasons": _screen_reasons(row),
            }
        )
    pool.sort(key=lambda item: (-item["score"], item["code"]))
    pool = pool[:MAX_POOL]
    if not pool:
        return empty_envelope(
            f"全市场 {len(got.rows)} 只标的均未通过筛选（安全边际下限 {margin}）",
            as_of=got.as_of,
        )
    return ok_envelope(
        {"pool": pool, "scanned": len(got.rows), "margin": margin},
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── 登记表（装载入口按 base 取用） ─────────────────────────

EXECUTORS: dict[str, Callable[[MarketQuerySource], SkillExecutor]] = {
    "sk_st_list_sync": _st_list_sync,
    "sk_delisting_risk_scan": _delisting_risk_scan,
    "sk_unhat_eligibility_check": _unhat_eligibility_check,
    "sk_sector_heatmap": _sector_heatmap,
    "sk_sentiment_flow_analysis": _sentiment_flow_analysis,
    "sk_fundamental_screening": _fundamental_screening,
}
