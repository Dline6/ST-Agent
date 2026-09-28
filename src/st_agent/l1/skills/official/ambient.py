"""主动服务 Bundle 的执行器（T-L1-004.3；03 §2.2）。

6 个 Skill：``stock-watch`` / ``data-aggregate`` / ``risk-alert`` / ``opportunity-mine``
/ ``portfolio-stress-test`` / ``strategy-design``。

- **取数**：一律经 :mod:`st_agent.l1.skills.official.common` 的单一入口进入 L0
  数据源缓存；``empty`` / ``unavailable`` 信封原样成为执行结果
- **联动**：`risk-alert` / `opportunity-mine` / `strategy-design` 按描述体声明的
  ``dependencies`` 消费上游输出（`ctx.upstream`）；上游非 `ok` / `empty` 时下游由
  运行器的依赖门拦为 ``dependency_failed``，本模块不自行兜底
- **输入缺口显式化**：对象类输入（用户组合 / 风格板块偏好 / 回测标的池）在
  声明参数里承载不了——由**调用方**经 ``ctx.inputs``（01 §2 的运行期输入通道）
  注入；执行器取不到相关记忆时**回落可解释默认口径**并**显式标注来源**，
  不冒充用户输入（`portfolio_source` / `preference` / `universe_source`）。
  L1 **不**读 L2（铁律 7）——「读记忆 → 汇成 inputs」是调用方的事
- **信号定义来源**：`strategy-design` 的 `signal_spec` 仍只来自上游载荷
  （记忆六类节点无信号定义一类）；`report.signal_check` 记其提供方
- **未来函数检测**：`strategy-design` 的 §2.2 特殊契约，检测件为
  :mod:`st_agent.l1.skills.official.signal_check` 的公开纯函数
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner.runner import SkillContext, SkillExecutor
from st_agent.l1.skills.official.common import (
    MarketQuerySource,
    empty_envelope,
    evidence_of,
    fetch_rows,
    ok_envelope,
    query_rows,
    round_of,
    stopping_factory,
)
from st_agent.l1.skills.official.signal_check import check_future_function

__all__ = [
    "DEFAULT_PORTFOLIO_SOURCE",
    "DEFAULT_PREFERENCE",
    "DEFAULT_UNIVERSE_SOURCE",
    "EXECUTORS",
    "PORTFOLIO_INPUT_KEY",
    "PREFERENCE_INPUT_KEY",
    "SIGNAL_SPEC_KEY",
    "STRESS_MULTIPLIERS",
    "UNIVERSE_INPUT_KEY",
]

# ───────────────────────── 规则常量（可解释口径，集中复核处） ─────────────────────────

PRICE_MOVE_ALERT = 5.0
"""盯盘口径：当日涨跌幅绝对值不低于 5%（%）即视为异动。"""

TURN_WATCH = 10.0
"""盯盘口径：当日换手率不低于 10%（%）。"""

COUNTDOWN_LIMIT_DAYS = 20
"""退市倒计时口径：连续 20 个交易日满足条件即触达阈值（面值 / 流动性共用）。"""

COUNTDOWN_WINDOW_DAYS = 30
"""倒计时回看窗口（个交易日）：只统计窗口内的连续天数。"""

LIQUIDITY_FLOOR_AMOUNT = 1e6
"""流动性枯竭口径：当日成交额低于 100 万元（元）。"""

FACE_VALUE = 1.0
"""面值口径：收盘价低于 1 元（元）。与公共认知 Bundle 同口径。"""

STRESS_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("温和回调", 0.5), ("设定冲击", 1.0), ("极端", 2.0),
)
"""压力测试的情景倍数（相对 ``shock`` 参数）。"""

TRADING_DAYS_PER_YEAR = 244
"""回测窗口换算：``lookback_years`` 年 ≈ 244 个交易日/年。"""

MAX_CARDS = 20
MAX_ROWS = 50
MAX_LINES = 5
MAX_FIELDS_PER_LINE = 5
"""聚合卡片的条数 / 行数 / 摘要字段上限（本地缓存全量时的载荷边界）。"""

DEP_RISK_SCAN = "sk_delisting_risk_scan_v1.0"
DEP_FUNDAMENTAL = "sk_fundamental_screening_v1.0"
DEP_DATA_AGGREGATE = "sk_data_aggregate_v1.0"
"""上游依赖的 ``skill_id``（与描述体种子的 ``dependencies`` 逐字一致）。"""

SIGNAL_SPEC_KEY = "signal_spec"
"""上游载荷里信号定义的键（`strategy-design` 的唯一信号来源；无生产者时显式标注未提供）。"""

PORTFOLIO_INPUT_KEY = "portfolio"
PREFERENCE_INPUT_KEY = "preference"
UNIVERSE_INPUT_KEY = "universe"
"""对象类输入在 ``ctx.inputs`` 里的键——与各描述体 ``input_schema`` 的声明逐字一致。"""

DEFAULT_PORTFOLIO_SOURCE = "local-cache-equal-weight"
"""无组合注入时的默认口径来源标注（本地缓存最新交易日在市标的等权）。"""

DEFAULT_PREFERENCE = "none"
"""无偏好注入时的默认口径标注（原优选池按分数排序）。"""

DEFAULT_UNIVERSE_SOURCE = "local-cache-all"
"""无标的池注入时的默认口径标注（全市场等权）。"""

SIGNAL_SOURCE_NONE = "未提供"
"""`signal_check` 的来源标注：上游无生产者时的取值（不假装有信号定义）。"""


# ───────────────────────── 对象类输入（01 §2 运行期输入通道） ─────────────────────────


def _source_label(raw: Mapping[str, Any], key: str) -> str:
    """来源标注：注入对象自带的 ``source``（调用方写明记忆维度），缺省 ``inputs.<键>``。

    执行器**不臆测**数据来源——「来自哪个记忆维度」只有调用方知道（铁律 5 可解释
    的落点：追溯链由调用方在注入对象里给出，执行器如实回显）。
    """
    declared = raw.get("source")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return f"inputs.{key}"


def _string_list(value: Any) -> tuple[str, ...]:
    """把输入里的列表项规整成非空字符串元组（去重保序）。"""
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


# ───────────────────────── 上游载荷的读取 ─────────────────────────


def _upstream_data(ctx: SkillContext, skill_id: str) -> dict[str, Any] | None:
    """取上游 ``ok`` 载荷（未执行 / 非 ok / 非对象 → ``None``）。"""
    envelope = ctx.upstream.get(skill_id)
    if envelope is None or envelope.status != "ok":
        return None
    return envelope.data if isinstance(envelope.data, dict) else None


def _records(data: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """取载荷里第一个「对象列表」值作为记录集（聚合卡片的通用适配口径）。"""
    for value in data.values():
        if isinstance(value, (list, tuple)) and value \
                and all(isinstance(item, dict) for item in value):
            return tuple(value)
    return ()


def _summarize(mapping: Mapping[str, Any]) -> str:
    """把一条记录压成一行「键=值」（跳过空值，字段数有上限）。"""
    parts = [
        f"{key}={value}"
        for key, value in mapping.items()
        if value is not None and isinstance(value, (str, int, float, bool))
    ]
    return "；".join(parts[:MAX_FIELDS_PER_LINE])


def _first_number(mapping: Mapping[str, Any]) -> float | None:
    for value in mapping.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


# ───────────────────────── stock-watch ─────────────────────────

_WATCH_SQL = (
    "SELECT k.code AS code, k.trade_date AS trade_date, k.pct_chg AS pct_chg,"
    " k.turn AS turn, k.amount AS amount,"
    " (SELECT COUNT(*) FROM profit_forecast p"
    "   WHERE p.code = k.code AND p.pub_date = k.trade_date) AS forecast_n,"
    " (SELECT COUNT(*) FROM performance_express e"
    "   WHERE e.code = k.code AND e.pub_date = k.trade_date) AS express_n,"
    " (SELECT COUNT(*) FROM financial_quarter f"
    "   WHERE f.code = k.code AND f.pub_date = k.trade_date) AS report_n"
    " FROM v_k_line_latest k ORDER BY k.code"
)

KEYWORD_LOOKBACK_DAYS = 30
"""关键词条件在公告域的**回看窗口**（自然日）——盯盘看的是「近期有无相关披露」。"""

_ANNOUNCEMENT_PRESENT_SQL = (
    "SELECT COUNT(*) AS n FROM sqlite_master"
    " WHERE type = 'table' AND name = 'announcement'"
)
"""公告域数据面是否就绪（信息面机制未落地 / 尚未同步时该表不存在）。"""


def _split_keywords(raw: Any) -> tuple[str, ...]:
    """关键词参数 → 词元组（中英文逗号 / 顿号 / 空白皆可分隔；去重保序）。"""
    if not raw:
        return ()
    text = str(raw).replace("，", ",").replace("、", ",")
    out: list[str] = []
    for token in text.split(","):
        token = token.strip()
        if token and token not in out:
            out.append(token)
    return tuple(out)


@stopping_factory
def _stock_watch(source, ctx, params) -> ResultEnvelope:
    keywords = _split_keywords(params.get("keywords"))
    has_announcement = bool(
        fetch_rows(source, _ANNOUNCEMENT_PRESENT_SQL).rows[0]["n"]
    )
    evaluate_keywords = bool(keywords) and has_announcement
    if evaluate_keywords:
        sql = _WATCH_SQL.replace(
            " FROM v_k_line_latest k ORDER BY k.code",
            ", (SELECT COUNT(*) FROM announcement a"
            "   WHERE a.code = k.code"
            f"   AND a.pub_date >= date(k.trade_date, '-{KEYWORD_LOOKBACK_DAYS} day')"
            "   AND (" + " OR ".join("a.title LIKE ?" for _ in keywords)
            + ")) AS keyword_n"
            " FROM v_k_line_latest k ORDER BY k.code",
        )
        got = fetch_rows(source, sql, tuple(f"%{k}%" for k in keywords))
    else:
        got = fetch_rows(source, _WATCH_SQL)
    if not got.rows:
        return empty_envelope("本地缓存无最新日线数据", as_of=got.as_of)
    triggered: list[dict[str, Any]] = []
    for row in got.rows:
        date = row["trade_date"]
        pct_chg, turn = row.get("pct_chg"), row.get("turn")
        if evaluate_keywords:
            hits = int(row.get("keyword_n") or 0)
            if hits:
                triggered.append({
                    "code": row["code"], "condition": "关键词",
                    "detail": (
                        f"{date} 近 {KEYWORD_LOOKBACK_DAYS} 日有 {hits} 条匹配公告"
                        f"（关键词：{'、'.join(keywords)}）"
                    ),
                })
        if pct_chg is not None and abs(float(pct_chg)) >= PRICE_MOVE_ALERT:
            triggered.append({
                "code": row["code"], "condition": "异动",
                "detail": f"{date} 涨跌幅 {float(pct_chg):+.2f}%",
            })
        if turn is not None and float(turn) >= TURN_WATCH:
            triggered.append({
                "code": row["code"], "condition": "换手率",
                "detail": f"{date} 换手率 {float(turn):.2f}%",
            })
        reports = sum(int(row.get(key) or 0) for key in ("forecast_n", "express_n", "report_n"))
        if reports:
            triggered.append({
                "code": row["code"], "condition": "财报日",
                "detail": (
                    f"{date} 有财报披露（预告 {int(row.get('forecast_n') or 0)} 条 / "
                    f"快报 {int(row.get('express_n') or 0)} 条 / "
                    f"定期报告 {int(row.get('report_n') or 0)} 条）"
                ),
            })
    conditions = ["异动", "换手率", "财报日"]
    unevaluated: list[str] = []
    reasons: dict[str, str] = {}
    if evaluate_keywords:
        conditions.append("关键词")
    else:
        unevaluated.append("关键词")
        reasons["关键词"] = (
            "未提供关键词参数，该条件不评估" if not keywords
            else "公告域数据面未就绪（本地缓存无 announcement 表），该条件不评估"
        )
    if not triggered:
        return empty_envelope(
            f"最新交易日 {got.rows[0]['trade_date']} 无标的触发盯盘条件"
            f"（{' / '.join(conditions)}）",
            as_of=got.as_of,
        )
    return ok_envelope(
        {
            "triggered": triggered,
            "frequency_minutes": int(params["frequency_minutes"]),
            "conditions": conditions,
            "unevaluated_conditions": unevaluated,
            "unevaluated_reasons": reasons,
            "keywords": list(keywords),
        },
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── data-aggregate ─────────────────────────


def _card(fmt: str, skill_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    records = _records(data)
    if fmt == "table":
        payload: dict[str, Any] = {
            "columns": list(records[0]) if records else sorted(data),
            "rows": [dict(record) for record in records[:MAX_ROWS]],
        }
    elif fmt == "trend":
        payload = {
            "points": [
                {
                    "label": str(record.get("code") or record.get("industry") or index),
                    "value": _first_number(record),
                }
                for index, record in enumerate(records[:MAX_ROWS])
            ]
        }
    else:
        if records:
            lines = [f"{len(records)} 条记录"]
            lines.extend(_summarize(record) for record in records[:MAX_LINES])
        else:
            lines = [_summarize(data)]
        payload = {"lines": lines}
    return {"kind": fmt, "title": skill_id, "payload": payload}


@stopping_factory
def _data_aggregate(source, ctx, params) -> ResultEnvelope:
    fmt = params["format"]
    sources: list[tuple[str, Mapping[str, Any]]] = []
    for skill_id in sorted(ctx.upstream):
        data = _upstream_data(ctx, skill_id)
        if data is not None:
            sources.append((skill_id, data))
    if not sources:
        return empty_envelope("无上游数据可聚合（上游未产出 ok 载荷）")
    cards = [_card(fmt, skill_id, data) for skill_id, data in sources[:MAX_CARDS]]
    return ok_envelope(
        {"cards": cards, "format": fmt, "sources": [skill_id for skill_id, _ in sources]}
    )


# ───────────────────────── risk-alert ─────────────────────────

_COUNTDOWN_SQL = (
    "SELECT code, trade_date, close, amount FROM k_line_daily"
    " WHERE code IN ({codes})"
    "   AND trade_date >= (SELECT MIN(trade_date) FROM ("
    "     SELECT DISTINCT trade_date FROM k_line_daily"
    "     ORDER BY trade_date DESC LIMIT ?))"
    " ORDER BY code, trade_date DESC"
)


def _consecutive(rows: list[dict[str, Any]], predicate) -> int:
    """自最新一日向前数连续满足 ``predicate`` 的交易日数。"""
    count = 0
    for row in rows:
        if not predicate(row):
            break
        count += 1
    return count


def _face_days(rows: list[dict[str, Any]]) -> int:
    return _consecutive(
        rows, lambda r: r.get("close") is not None and float(r["close"]) < FACE_VALUE
    )


def _illiquid_days(rows: list[dict[str, Any]]) -> int:
    return _consecutive(
        rows,
        lambda r: r.get("amount") is not None and float(r["amount"]) < LIQUIDITY_FLOOR_AMOUNT,
    )


@stopping_factory
def _risk_alert(source, ctx, params) -> ResultEnvelope:
    lookahead = int(params["lookahead_days"])
    upstream = _upstream_data(ctx, DEP_RISK_SCAN)
    if upstream is None:
        return empty_envelope("上游退市风险扫描未产出风险清单，无预警维度可评估")
    triggers = list(upstream.get("triggers") or ())
    if not triggers:
        return empty_envelope("上游风险清单为空，无预警维度可评估")
    codes = [str(t["code"]) for t in triggers]
    got = fetch_rows(
        source,
        _COUNTDOWN_SQL.format(codes=",".join("?" * len(codes))),
        (*codes, COUNTDOWN_WINDOW_DAYS),
    )
    series: dict[str, list[dict[str, Any]]] = {}
    for row in got.rows:
        series.setdefault(str(row["code"]), []).append(row)
    alerts: list[dict[str, Any]] = []
    for trigger in triggers:
        code = str(trigger["code"])
        rows = series.get(code, [])
        for dimension, days in (("退市倒计时", _face_days(rows)),
                                ("流动性枯竭", _illiquid_days(rows))):
            if days <= 0:
                continue
            remaining = max(0, COUNTDOWN_LIMIT_DAYS - days)
            if remaining > lookahead:
                continue
            alerts.append({
                "code": code,
                "dimension": dimension,
                "detail": (
                    f"已连续 {days} 个交易日触发 {dimension} 条件，"
                    f"距 {COUNTDOWN_LIMIT_DAYS} 日阈值剩余 {remaining} 个交易日"
                ),
                "remaining_days": remaining,
                "upstream_risk_level": trigger.get("risk_level"),
            })
    if not alerts:
        return empty_envelope(
            f"前瞻 {lookahead} 个交易日内无维度触达阈值（扫描 {len(triggers)} 只风险标的）",
            as_of=got.as_of,
        )
    alerts.sort(key=lambda a: (a["remaining_days"], a["code"], a["dimension"]))
    return ok_envelope(
        {"alerts": alerts, "lookahead_days": lookahead, "scanned": len(triggers)},
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── opportunity-mine ─────────────────────────


_SECTOR_SQL = (
    "SELECT i.code AS code, i.industry AS industry FROM stock_industry i"
    " WHERE i.code IN ({codes})"
    "   AND i.update_date = (SELECT MAX(x.update_date) FROM stock_industry x"
    "                        WHERE x.code = i.code)"
)
"""候选所属行业（最新快照）——板块偏好过滤的取材面。"""


def _preference(ctx: SkillContext) -> Mapping[str, Any] | None:
    """``inputs.preference``（调用方注入的风格 / 板块偏好）；无有效注入 → ``None``。"""
    raw = ctx.inputs.get(PREFERENCE_INPUT_KEY)
    return raw if isinstance(raw, Mapping) else None


def _industries(source, codes: tuple[str, ...]) -> dict[str, str] | ResultEnvelope:
    """候选代码 → 行业。行业面非 ``ok`` 时**原样透出同一信封**（不假装过滤过）。"""
    got = query_rows(source, _SECTOR_SQL.format(codes=",".join("?" * len(codes))), codes)
    if isinstance(got, ResultEnvelope):
        return got
    return {str(row["code"]): str(row.get("industry") or "") for row in got.rows}


def _theme_hit(item: Mapping[str, Any], themes: tuple[str, ...]) -> bool:
    """题材命中：候选的 ``reasons`` 文本含任一题材词。"""
    text = " ".join(str(reason) for reason in (item.get("reasons") or ()))
    return any(theme in text for theme in themes)


@stopping_factory
def _opportunity_mine(source, ctx, params) -> ResultEnvelope:
    max_results = int(params["max_results"])
    upstream = _upstream_data(ctx, DEP_FUNDAMENTAL)
    if upstream is None:
        return empty_envelope("上游基本面优选池未产出，无候选可挖掘")
    pool = list(upstream.get("pool") or ())
    if not pool:
        return empty_envelope("上游优选池为空，无候选可挖掘")
    preference = _preference(ctx)
    sectors = _string_list(preference.get("sectors")) if preference is not None else ()
    themes = _string_list(preference.get("themes")) if preference is not None else ()
    kept = pool
    if sectors or themes:
        industries: dict[str, str] = {}
        if sectors:
            lookup = _industries(source, tuple(str(item["code"]) for item in pool))
            if isinstance(lookup, ResultEnvelope):
                return lookup
            industries = lookup
        kept = [
            item for item in pool
            if (sectors and industries.get(str(item["code"])) in sectors)
            or (themes and _theme_hit(item, themes))
        ]
        if not kept:
            parts = [f"{label} {'、'.join(values)}"
                     for label, values in (("板块", sectors), ("题材", themes)) if values]
            return empty_envelope("偏好过滤后无候选（" + "；".join(parts) + "）")
    ranked = sorted(kept, key=lambda item: (-float(item.get("score") or 0.0), str(item["code"])))
    candidates = [
        {
            "code": item["code"],
            "code_name": item.get("code_name"),
            "score": item.get("score"),
            "reasons": list(item.get("reasons") or ()),
        }
        for item in ranked[:max_results]
    ]
    preference_label: Any = DEFAULT_PREFERENCE
    if preference is not None:
        preference_label = {
            **preference,
            "source": _source_label(preference, PREFERENCE_INPUT_KEY),
        }
    return ok_envelope(
        {
            "candidates": candidates,
            "preference": preference_label,
            "max_results": max_results,
            "source_pool_size": len(pool),
        },
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── portfolio-stress-test ─────────────────────────

_PORTFOLIO_SQL = (
    "SELECT k.code AS code, k.is_st AS is_st FROM v_k_line_latest k"
    " JOIN security s ON s.code = k.code AND s.type = 1 AND s.status = 1"
)


def _portfolio_codes(ctx: SkillContext) -> tuple[str, ...] | None:
    """``inputs.portfolio.codes``（调用方注入的用户组合）；无有效注入 → ``None``。

    空表（注入但无标的）同样判 ``None``——[04 §7](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)
    的「某维度无记忆」表达为没有该键，故两者都走默认口径（降级语义，见任务 `A2`）。
    """
    raw = ctx.inputs.get(PORTFOLIO_INPUT_KEY)
    if not isinstance(raw, Mapping):
        return None
    return _string_list(raw.get("codes")) or None


@stopping_factory
def _portfolio_stress_test(source, ctx, params) -> ResultEnvelope:
    shock = float(params["shock"])
    codes = _portfolio_codes(ctx)
    if codes is None:
        got = fetch_rows(source, _PORTFOLIO_SQL)
        portfolio_source = DEFAULT_PORTFOLIO_SOURCE
    else:
        got = fetch_rows(
            source,
            _PORTFOLIO_SQL + " WHERE k.code IN ({codes})".format(
                codes=",".join("?" * len(codes))),
            codes,
        )
        portfolio_source = _source_label(ctx.inputs[PORTFOLIO_INPUT_KEY], PORTFOLIO_INPUT_KEY)
    if not got.rows:
        return empty_envelope(
            "本地缓存无在市标的，无组合可推演" if codes is None
            else "注入组合内的标的在本地缓存无最新日线，无组合可推演",
            as_of=got.as_of,
        )
    holdings = len(got.rows)
    st_count = sum(1 for row in got.rows if int(row.get("is_st") or 0) == 1)
    st_ratio = st_count / holdings
    beta = 1.0 + st_ratio
    scenarios = [
        {
            "name": name,
            "shock_pct": round_of(-shock * multiplier * 100, 4),
            "impact_pct": round_of(-shock * multiplier * beta * 100, 4),
            "detail": (
                f"冲击 {shock * multiplier * 100:.2f}%，"
                f"组合冲击 {shock * multiplier * beta * 100:.2f}%（风险放大系数 {beta:.4f}）"
            ),
        }
        for name, multiplier in STRESS_MULTIPLIERS
    ]
    return ok_envelope(
        {
            "scenarios": scenarios,
            "portfolio_source": portfolio_source,
            "holdings": holdings,
            "st_ratio": round_of(st_ratio),
            "beta": round_of(beta),
        },
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── strategy-design ─────────────────────────

_BACKTEST_SQL = (
    "SELECT code, trade_date, close FROM k_line_daily"
    " WHERE trade_date >= (SELECT MIN(trade_date) FROM ("
    "   SELECT DISTINCT trade_date FROM k_line_daily ORDER BY trade_date DESC LIMIT ?))"
    " ORDER BY code, trade_date"
)

_BACKTEST_CODES_SQL = (
    "SELECT code, trade_date, close FROM k_line_daily"
    " WHERE code IN ({codes})"
    "   AND trade_date >= (SELECT MIN(trade_date) FROM ("
    "     SELECT DISTINCT trade_date FROM k_line_daily"
    "     ORDER BY trade_date DESC LIMIT ?))"
    " ORDER BY code, trade_date"
)
"""注入标的池后的回测取数——逗号分隔的 ``stock_id`` 串（同 `keywords` 的串约定）。"""


def _universe_codes(ctx: SkillContext) -> tuple[str, ...] | None:
    """``inputs.universe``（逗号分隔的标的代码）；无有效注入 → ``None``。"""
    raw = ctx.inputs.get(UNIVERSE_INPUT_KEY)
    if not isinstance(raw, str):
        return None
    return _string_list(raw.replace("，", ",").split(",")) or None


def _signal_spec(ctx: SkillContext) -> tuple[Mapping[str, Any] | None, str | None]:
    """上游载荷里的信号定义**及其提供方**（首个提供者胜出；无生产者 → ``(None, None)``）。

    信号定义**不经** ``ctx.inputs``：记忆六类节点无信号定义一类，其唯一来源是上游
    载荷（见任务 `A3`），故此处一并回报提供方供来源标注。
    """
    for skill_id in sorted(ctx.upstream):
        data = _upstream_data(ctx, skill_id)
        if data is not None and isinstance(data.get(SIGNAL_SPEC_KEY), Mapping):
            return data[SIGNAL_SPEC_KEY], skill_id
    return None, None


@stopping_factory
def _strategy_design(source, ctx, params) -> ResultEnvelope:
    lookback_years = int(params["lookback_years"])
    window = lookback_years * TRADING_DAYS_PER_YEAR
    codes = _universe_codes(ctx)
    if codes is None:
        got = fetch_rows(source, _BACKTEST_SQL, (window,))
        universe_source = DEFAULT_UNIVERSE_SOURCE
        scope = "全市场"
    else:
        got = fetch_rows(
            source,
            _BACKTEST_CODES_SQL.format(codes=",".join("?" * len(codes))),
            (*codes, window),
        )
        universe_source = f"inputs.{UNIVERSE_INPUT_KEY}"
        scope = "注入标的池"
    if not got.rows:
        return empty_envelope("本地缓存无回测窗口内的日线数据", as_of=got.as_of)
    closes: dict[str, list[tuple[str, float]]] = {}
    for row in got.rows:
        if row.get("close") is None:
            continue
        closes.setdefault(str(row["code"]), []).append(
            (str(row["trade_date"]), float(row["close"]))
        )
    returns = [
        series[-1][1] / series[0][1] - 1.0
        for series in closes.values()
        if len(series) >= 2 and series[0][1] != 0.0
    ]
    if not returns:
        return empty_envelope("回测窗口内无足两个交易日的标的序列", as_of=got.as_of)
    dates = sorted({date for series in closes.values() for date, _ in series})
    average = sum(returns) / len(returns)
    spec, provider = _signal_spec(ctx)
    check = check_future_function(spec).model_dump()
    check["source"] = provider if provider is not None else SIGNAL_SOURCE_NONE
    steps = [
        {"step": "选股",
         "detail": f"{len(returns)} 只标的纳入回测窗口（近 {lookback_years} 年；{scope}）"},
        {"step": "信号", "detail": "以窗口内等权组合的日线收益率为信号基础"},
        {"step": "策略", "detail": "等权买入持有（规则化口径，不做参数寻优）"},
        {
            "step": "回测",
            "detail": f"窗口累计收益率 {average * 100:.2f}%（{dates[0]} → {dates[-1]}）",
        },
    ]
    return ok_envelope(
        {
            "report": {
                "steps": steps,
                "signal_check": check,
                "universe": len(returns),
                "universe_source": universe_source,
                "window": {
                    "lookback_years": lookback_years,
                    "start": dates[0],
                    "end": dates[-1],
                },
                "return_pct": round_of(average * 100),
            }
        },
        as_of=got.as_of,
        evidence_refs=evidence_of(source),
    )


# ───────────────────────── 登记表（装载入口按 base 取用） ─────────────────────────

EXECUTORS: dict[str, Callable[[MarketQuerySource], SkillExecutor]] = {
    "sk_stock_watch": _stock_watch,
    "sk_data_aggregate": _data_aggregate,
    "sk_risk_alert": _risk_alert,
    "sk_opportunity_mine": _opportunity_mine,
    "sk_portfolio_stress_test": _portfolio_stress_test,
    "sk_strategy_design": _strategy_design,
}
