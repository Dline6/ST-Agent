"""数据同步页的中文展示目录（[D-130]）。

**唯一展示词表**：服务端表格与前端目录共用这一份，前端经 `GET /api/settings/data`
的 `tasks` / `tables` 载荷消费，不在 JS 里另抄一套。

分工：
- L0 只给任务键、表名、能力位（`per_code` / `supports_range` / `window_desc`）；
- 本模块把它们映射成「中文大类 → 数据项 → 底层任务」的展示结构；
- 数据项是**选择单位**，一个数据项可对应多条任务（如指数成分三条快照任务）。
  一张表可由多条任务写入（公告主备、`security` 两任务、`index_constituent` 三任务），
  故一个 `task_key` **恰属一个**数据项；备源单独成项并默认收进「高级」。

**不在这里做取数或判定**——行数、状态、水位仍由 `DataSyncRunner.overview()` 给。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from st_agent.l0.info.sources import INFO_TASKS
from st_agent.l0.market.datasets import DATASETS, validate_datasets
from st_agent.l0.market.tasks import SYNC_TASKS

__all__ = [
    "CATEGORY_ORDER",
    "DOMAIN_LABELS",
    "ITEMS",
    "DataItem",
    "TABLE_LABELS",
    "TASK_LABELS",
    "catalog_payload",
    "category_label",
    "domain_label",
    "item_for_task",
    "sync_catalog_payload",
    "table_label",
    "task_label",
    "validate_catalog",
]

DOMAIN_LABELS: dict[str, str] = {
    "kline": "行情",
    "security": "基础主档",
    "sector": "板块",
    "corporate_action": "公司行为",
    "financial": "财务",
    "company_report": "公司报告",
    "macro": "宏观",
    "announcement": "公告",
    "dragon_tiger": "交易异动",
    "shareholder_num": "股东户数",
    "sentiment_qa": "舆情问答",
    "sentiment_hot": "市场热度",
}
"""L0 数据域 → 中文名（表清单的「所属域」列）。"""


def domain_label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain, domain)

CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    ("basic", "基础主档"),
    ("quotes", "行情"),
    ("corporate_action", "公司行为"),
    ("financial", "财务与公司报告"),
    ("sector", "板块"),
    ("macro", "宏观"),
    ("announcement", "公告"),
    ("dragon_tiger", "交易异动"),
    ("shareholder_sentiment", "股东与舆情"),
)
_CATEGORY_INDEX = {key: i for i, (key, _label) in enumerate(CATEGORY_ORDER)}
_CATEGORY_LABELS = dict(CATEGORY_ORDER)

TASK_LABELS: dict[str, tuple[str, str]] = {
    # 基础主档
    "bs_calendar": ("交易日历", "A 股交易日与休市日，1990 年至今"),
    "bs_security_basic": (
        "证券主档（全量）", "股票 / 指数 / 可转债 / ETF 的基本资料与上市状态"
    ),
    "bs_st_list": (
        "ST 名单（名称匹配）", "BaoStock 名称查询快照，供 ST 标的解析"
    ),
    "bs_all_stock": ("每日在市证券", "指定交易日的在市证券清单与名称"),
    # 行情
    "bs_k_daily": (
        "日线行情", "全市场日 K 线，含 ST 标记、估值与成交指标"
    ),
    "bs_k_period": ("周线 / 月线行情", "周线与月线的开高低收与成交"),
    "bs_adjust_factor": ("复权因子", "前复权 / 后复权因子，用于复权价格推导"),
    "bs_k_minute": (
        "分钟线行情（默认关闭）", "5 / 15 / 30 / 60 分钟线，仅关注池或指定代码"
    ),
    # 公司行为
    "bs_dividend": ("分红送转", "每股分红、送转与除权除息日"),
    # 财务与公司报告
    "bs_fin_quarter": (
        "季度财务报表", "盈利、营运、成长、偿债、现金流与杜邦指标"
    ),
    "bs_perf_express": ("业绩快报", "公司业绩快报的总资产、净资产、EPS 等"),
    "bs_forecast": ("业绩预告", "预增 / 预亏 / 扭亏等业绩预告"),
    # 板块
    "bs_industry": ("行业分类", "申万等行业分类每周快照"),
    "bs_sz50": ("上证50 成分", "上证50 指数成分每周快照"),
    "bs_hs300": ("沪深300 成分", "沪深300 指数成分每周快照"),
    "bs_zz500": ("中证500 成分", "中证500 指数成分每周快照"),
    # 宏观
    "bs_macro_deposit": ("存款基准利率", "央行存款基准利率"),
    "bs_macro_loan": ("贷款基准利率", "央行贷款基准利率"),
    "bs_macro_reserve": ("存款准备金率", "央行存款准备金率"),
    "bs_money_month": ("货币供应量（月度）", "M0 / M1 / M2 月度数据"),
    "bs_money_year": ("货币供应量（年度）", "M0 / M1 / M2 年度数据"),
    # 公告
    "info_announcement_cninfo": (
        "公告（巨潮 · 全市场主源）", "沪深全市场公告，权威源优先"
    ),
    "info_announcement_szse": (
        "公告（深交所 · 深市备源）", "仅深市公告，主源不可用时兜底"
    ),
    "info_announcement_em": (
        "公告（东财 · 沪市备源）", "仅沪市公告，主源不可用时兜底"
    ),
    # 交易异动
    "info_dragon_tiger_em": (
        "龙虎榜（东财 · 全市场）", "全市场上榜记录与席位明细"
    ),
    "info_dragon_tiger_szse": (
        "龙虎榜（深交所 · 深市）", "深市上榜记录与官方席位明细"
    ),
    "info_dragon_tiger_sse": (
        "龙虎榜（上交所 · 沪市）", "沪市上榜记录与官方席位明细"
    ),
    # 股东与舆情
    "info_shareholder_num_em": (
        "股东户数（东财主源）", "最新一期股东户数与户均持股"
    ),
    "info_shareholder_num_cninfo": (
        "股东户数（巨潮备源）", "按报告期回补更早季末"
    ),
    "info_sentiment_qa_sse": (
        "投资者问答（沪市全市场）", "上证 e 互动全市场问答流"
    ),
    "info_sentiment_qa_irm": (
        "投资者问答（深市关注池）", "互动易按关注池采集，默认关闭"
    ),
    "info_sentiment_hot": ("市场热度榜", "同花顺 / 东财热度榜快照"),
}
"""逐任务中文名与一句话说明（31 条）。"""

TABLE_LABELS: dict[str, tuple[str, str]] = {
    "trade_calendar": ("交易日历", "交易日与休市日标记"),
    "security": ("证券主档", "证券基本资料与上市状态"),
    "st_membership": ("ST 名单", "名称匹配的同步标的池快照"),
    "k_line_daily": ("日线行情", "每日 K 线与估值指标"),
    "k_line_period": ("周线 / 月线行情", "周线与月线行情"),
    "k_line_minute": ("分钟线行情", "5 / 15 / 30 / 60 分钟线"),
    "adjust_factor": ("复权因子", "前复权 / 后复权因子"),
    "dividend": ("分红送转", "分红、送转与除权除息"),
    "financial_quarter": ("季度财务报表", "季度财务与杜邦指标"),
    "performance_express": ("业绩快报", "公司业绩快报"),
    "profit_forecast": ("业绩预告", "业绩预增 / 预亏 / 扭亏"),
    "stock_industry": ("行业分类", "行业分类每周快照"),
    "index_constituent": ("指数成分", "宽基指数成分每周快照"),
    "macro_deposit_rate": ("存款基准利率", "央行存款基准利率"),
    "macro_loan_rate": ("贷款基准利率", "央行贷款基准利率"),
    "macro_reserve_ratio": ("存款准备金率", "央行存款准备金率"),
    "macro_money_supply_month": ("货币供应量（月度）", "M0 / M1 / M2 月度"),
    "macro_money_supply_year": ("货币供应量（年度）", "M0 / M1 / M2 年度"),
    "announcement": ("公告", "公司公告元数据与正文索引"),
    "dragon_tiger": ("龙虎榜上榜记录", "每日上榜记录"),
    "dragon_tiger_seat": ("龙虎榜席位明细", "营业部买卖席位"),
    "shareholder_num": ("股东户数", "股东户数与户均持股"),
    "sentiment_qa": ("投资者问答", "沪深投资者关系问答"),
    "sentiment_hot": ("市场热度榜", "同花顺 / 东财热度快照"),
}
"""逐表中文名与一句话说明（23 张业务表）。"""


@dataclass(frozen=True)
class DataItem:
    """一个可选数据项（选择单位；可对应多条底层任务）。"""

    item_id: str
    category: str
    display_name: str
    description: str
    task_keys: tuple[str, ...]
    advanced: bool = False


# 顺序即展示顺序；category 必须落在 CATEGORY_ORDER 内且不交叉。
ITEMS: tuple[DataItem, ...] = (
    # 基础主档
    DataItem("calendar", "basic", "交易日历", "A 股交易日与休市日，1990 年至今",
             ("bs_calendar",)),
    DataItem("security", "basic", "证券主档",
             "股票 / 指数 / 可转债 / ETF 的基本资料与上市状态",
             ("bs_security_basic",)),
    DataItem("st_list", "basic", "ST 名单（名称匹配）",
             "BaoStock 名称查询快照，供 ST 标的解析", ("bs_st_list",),
             advanced=True),
    DataItem("daily_listing", "basic", "每日在市证券",
             "指定交易日的在市证券清单与名称", ("bs_all_stock",)),
    # 行情
    DataItem("k_daily", "quotes", "日线行情",
             "全市场日 K 线，含 ST 标记、估值与成交指标", ("bs_k_daily",)),
    DataItem("k_period", "quotes", "周线 / 月线行情",
             "周线与月线的开高低收与成交", ("bs_k_period",)),
    DataItem("adjust_factor", "quotes", "复权因子",
             "前复权 / 后复权因子，用于复权价格推导", ("bs_adjust_factor",)),
    DataItem("k_minute", "quotes", "分钟线行情（默认关闭）",
             "5 / 15 / 30 / 60 分钟线，仅关注池或指定代码",
             ("bs_k_minute",), advanced=True),
    # 公司行为
    DataItem("dividend", "corporate_action", "分红送转",
             "每股分红、送转与除权除息日", ("bs_dividend",)),
    # 财务与公司报告
    DataItem("fin_quarter", "financial", "季度财务报表",
             "盈利、营运、成长、偿债、现金流与杜邦指标", ("bs_fin_quarter",)),
    DataItem("perf_express", "financial", "业绩快报",
             "公司业绩快报的总资产、净资产、EPS 等", ("bs_perf_express",)),
    DataItem("forecast", "financial", "业绩预告",
             "预增 / 预亏 / 扭亏等业绩预告", ("bs_forecast",)),
    # 板块
    DataItem("industry", "sector", "行业分类", "申万等行业分类每周快照",
             ("bs_industry",)),
    DataItem("index_constituent", "sector", "指数成分",
             "上证50 / 沪深300 / 中证500 成分每周快照",
             ("bs_sz50", "bs_hs300", "bs_zz500")),
    # 宏观
    DataItem("macro_deposit", "macro", "存款基准利率", "央行存款基准利率",
             ("bs_macro_deposit",)),
    DataItem("macro_loan", "macro", "贷款基准利率", "央行贷款基准利率",
             ("bs_macro_loan",)),
    DataItem("macro_reserve", "macro", "存款准备金率", "央行存款准备金率",
             ("bs_macro_reserve",)),
    DataItem("money_month", "macro", "货币供应量（月度）", "M0 / M1 / M2 月度数据",
             ("bs_money_month",)),
    DataItem("money_year", "macro", "货币供应量（年度）", "M0 / M1 / M2 年度数据",
             ("bs_money_year",)),
    # 公告
    DataItem("announcement_cninfo", "announcement", "公告（巨潮主源）",
             "沪深全市场公告，权威源优先", ("info_announcement_cninfo",)),
    DataItem("announcement_szse", "announcement", "公告（深交所备源）",
             "仅深市公告，主源不可用时兜底", ("info_announcement_szse",),
             advanced=True),
    DataItem("announcement_em", "announcement", "公告（东财备源）",
             "仅沪市公告，主源不可用时兜底", ("info_announcement_em",),
             advanced=True),
    # 交易异动
    DataItem("dragon_tiger_em", "dragon_tiger", "龙虎榜（东财主源）",
             "全市场上榜记录与席位明细", ("info_dragon_tiger_em",)),
    DataItem("dragon_tiger_szse", "dragon_tiger", "龙虎榜（深交所 · 深市）",
             "深市上榜记录与官方席位明细", ("info_dragon_tiger_szse",),
             advanced=True),
    DataItem("dragon_tiger_sse", "dragon_tiger", "龙虎榜（上交所 · 沪市）",
             "沪市上榜记录与官方席位明细", ("info_dragon_tiger_sse",),
             advanced=True),
    # 股东与舆情
    DataItem("shareholder_em", "shareholder_sentiment", "股东户数（东财主源）",
             "最新一期股东户数与户均持股", ("info_shareholder_num_em",)),
    DataItem("shareholder_cninfo", "shareholder_sentiment", "股东户数（巨潮备源）",
             "按报告期回补更早季末", ("info_shareholder_num_cninfo",),
             advanced=True),
    DataItem("sentiment_qa_sse", "shareholder_sentiment",
             "投资者问答（沪市全市场）", "上证 e 互动全市场问答流",
             ("info_sentiment_qa_sse",)),
    DataItem("sentiment_qa_irm", "shareholder_sentiment",
             "投资者问答（深市关注池）", "互动易按关注池采集，默认关闭",
             ("info_sentiment_qa_irm",), advanced=True),
    DataItem("sentiment_hot", "shareholder_sentiment", "市场热度榜",
             "同花顺 / 东财热度榜快照", ("info_sentiment_hot",)),
)

_ITEM_BY_TASK: dict[str, DataItem] = {
    task_key: item for item in ITEMS for task_key in item.task_keys
}


def task_label(task_key: str) -> tuple[str, str]:
    """任务中文名与说明（未登记 → 回退任务键，不静默伪装成已登记）。"""
    return TASK_LABELS.get(task_key, (task_key, ""))


def table_label(table: str) -> tuple[str, str]:
    """表中文名与说明（未登记 → 回退表名）。"""
    return TABLE_LABELS.get(table, (table, ""))


def category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category)


def item_for_task(task_key: str) -> DataItem | None:
    return _ITEM_BY_TASK.get(task_key)


def _enrich_task(task: dict[str, Any]) -> dict[str, Any]:
    """给一条 L0 任务行补上目录字段（不改原行）。"""
    item = _ITEM_BY_TASK.get(str(task.get("task_key")))
    display_name, description = task_label(str(task.get("task_key")))
    table_rows = []
    for row in task.get("table_rows") or []:
        table = str(row.get("table"))
        table_name, table_desc = table_label(table)
        table_rows.append({
            **row,
            "display_name": table_name,
            "description": table_desc,
        })
    return {
        **task,
        "display_name": display_name,
        "description": description,
        "item_id": item.item_id if item else "",
        "item_display_name": item.display_name if item else display_name,
        "item_description": item.description if item else description,
        "advanced": bool(item.advanced) if item else False,
        "category": item.category if item else "",
        "category_label": category_label(item.category) if item else "",
        "category_order": _CATEGORY_INDEX.get(item.category, 999) if item else 999,
        "table_rows": table_rows,
    }


def _item_status(tasks: list[dict[str, Any]]) -> str:
    statuses = [str(task.get("status") or "") for task in tasks]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "partial" for status in statuses):
        return "partial"
    if statuses and all(status == "ok" for status in statuses):
        return "ok"
    return ""


def _items_from(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 `ITEMS` 顺序聚合数据项（状态 / 行数 / 最近成功 / 错误）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        grouped.setdefault(str(task.get("item_id") or ""), []).append(task)
    items: list[dict[str, Any]] = []
    for spec in ITEMS:
        members = grouped.get(spec.item_id, [])
        if not members:
            continue
        tables: dict[str, dict[str, Any]] = {}
        for task in members:
            for row in task.get("table_rows") or []:
                table = str(row.get("table"))
                if table not in tables:
                    table_name, table_desc = table_label(table)
                    tables[table] = {
                        "table": table,
                        "display_name": table_name,
                        "description": table_desc,
                        "rows": row.get("rows"),
                    }
                elif row.get("rows") is not None and tables[table]["rows"] is None:
                    tables[table]["rows"] = row.get("rows")
        rows = sum(
            int(item["rows"]) for item in tables.values()
            if isinstance(item.get("rows"), int)
        )
        successes = [
            str(task.get("last_success_at")) for task in members
            if task.get("last_success_at")
        ]
        errors = [str(task.get("last_error")) for task in members if task.get("last_error")]
        watermarks = [
            str(task.get("watermark")) for task in members if task.get("watermark")
        ]
        items.append({
            "item_id": spec.item_id,
            "category": spec.category,
            "category_label": category_label(spec.category),
            "category_order": _CATEGORY_INDEX.get(spec.category, 999),
            "display_name": spec.display_name,
            "description": spec.description,
            "advanced": spec.advanced,
            "task_keys": list(spec.task_keys),
            "tasks": [
                {
                    "task_key": task.get("task_key"),
                    "display_name": task.get("display_name"),
                    "status": task.get("status"),
                    "last_success_at": task.get("last_success_at"),
                    "last_error": task.get("last_error"),
                    "watermark": task.get("watermark"),
                    "default_disabled": task.get("default_disabled"),
                    "coverage": task.get("coverage"),
                    "role": task.get("role"),
                    "mode": task.get("mode"),
                    "schedule": task.get("schedule"),
                    "window_desc": task.get("window_desc"),
                    "per_code": task.get("per_code"),
                    "supports_range": task.get("supports_range"),
                }
                for task in members
            ],
            "tables": list(tables.values()),
            "rows": rows,
            "status": _item_status(members),
            "last_success_at": max(successes, default=None),
            "last_error": errors[0] if errors else "",
            "watermark": " / ".join(dict.fromkeys(watermarks)),
        })
    return items


def catalog_payload(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """把 L0 的逐任务行变成前端目录载荷（数据项 + 逐任务）。"""
    enriched = [_enrich_task(task) for task in tasks]
    return {"items": _items_from(enriched), "tasks": enriched}


def sync_catalog_payload(overview: dict[str, Any]) -> dict[str, Any]:
    """新同步面板的目录载荷：标的列表 + 数据集能力位 + 各组状态（[D-132]）。

    ``overview`` 为 ``DataSyncRunner.overview()`` 的返回；本函数只做展示富化，
    不读写库。
    """
    task_rows = {
        str(row.get("task_key")): row for row in overview.get("tasks") or []
    }
    targets = (
        overview.get("summary", {}).get("targets")
        or overview.get("targets")
        or {}
    )
    datasets: list[dict[str, Any]] = []
    for spec in DATASETS:
        members = [task_rows[key] for key in spec.task_keys if key in task_rows]
        tables: dict[str, dict[str, Any]] = {}
        for task in members:
            for row in task.get("table_rows") or []:
                table = str(row.get("table"))
                if table not in tables:
                    table_name, table_desc = table_label(table)
                    tables[table] = {
                        "table": table,
                        "display_name": table_name,
                        "description": table_desc,
                        "rows": row.get("rows"),
                    }
        rows = sum(
            int(item["rows"]) for item in tables.values()
            if isinstance(item.get("rows"), int)
        )
        successes = [
            str(task.get("last_success_at")) for task in members
            if task.get("last_success_at")
        ]
        errors = [
            str(task.get("last_error")) for task in members
            if task.get("last_error")
        ]
        watermarks = [
            str(task.get("watermark")) for task in members if task.get("watermark")
        ]
        datasets.append({
            "dataset_id": spec.dataset_id,
            "label": spec.label,
            "description": spec.description,
            "category": spec.category,
            "category_label": category_label(spec.category),
            "target_kind": spec.target_kind,
            "task_keys": list(spec.task_keys),
            "indicator_groups": [
                {"key": group.key, "label": group.label, "default": group.default}
                for group in spec.indicator_groups
            ],
            "time_kind": spec.time_kind,
            "default_time": spec.default_time,
            "coverage_unit": spec.coverage_unit,
            "coverage_variants": list(spec.coverage_variants),
            "advanced": spec.advanced,
            "default_disabled": spec.default_disabled,
            "per_code": any(bool(task.get("per_code")) for task in members),
            "supports_range": any(
                bool(task.get("supports_range")) for task in members
            ),
            "tables": list(tables.values()),
            "rows": rows,
            "status": _item_status(members),
            "last_success_at": max(successes, default=None),
            "last_error": errors[0] if errors else "",
            "watermark": " / ".join(dict.fromkeys(watermarks)),
        })
    groups = [
        {"key": key, "label": label}
        for key, label in CATEGORY_ORDER
        if any(dataset["category"] == key for dataset in datasets)
    ]
    return {
        "targets": list(targets.values()),
        "datasets": datasets,
        "groups": groups,
    }


def validate_catalog() -> list[str]:
    """目录完整性自检（测试与收工检查用；空列表＝通过）。"""
    problems: list[str] = list(validate_datasets())
    registered = [t.task_key for t in SYNC_TASKS] + [t.task_key for t in INFO_TASKS]
    covered = [key for item in ITEMS for key in item.task_keys]
    duplicates = sorted({key for key in covered if covered.count(key) > 1})
    if duplicates:
        problems.append(f"任务被多个数据项重复覆盖：{duplicates}")
    missing_tasks = sorted(set(registered) - set(covered))
    if missing_tasks:
        problems.append(f"任务未进入任何数据项：{missing_tasks}")
    extra_tasks = sorted(set(covered) - set(registered))
    if extra_tasks:
        problems.append(f"数据项登记了不存在的任务：{extra_tasks}")
    item_ids = [item.item_id for item in ITEMS]
    if len(item_ids) != len(set(item_ids)):
        problems.append("数据项 item_id 重复")
    bad_categories = sorted({item.category for item in ITEMS} - set(_CATEGORY_LABELS))
    if bad_categories:
        problems.append(f"数据项引用了未登记的大类：{bad_categories}")
    # 大类顺序不得交叉（同一 category 的数据项必须连续）
    seen: list[str] = []
    for item in ITEMS:
        if not seen or seen[-1] != item.category:
            if item.category in seen:
                problems.append(f"大类 {item.category} 的数据项不连续")
            seen.append(item.category)
    missing_labels = sorted(set(registered) - set(TASK_LABELS))
    if missing_labels:
        problems.append(f"任务缺少中文名：{missing_labels}")
    tables = {t.table_name for t in SYNC_TASKS}
    tables.update(table for task in INFO_TASKS for table in task.written_tables)
    missing_tables = sorted(tables - set(TABLE_LABELS))
    if missing_tables:
        problems.append(f"表缺少中文名：{missing_tables}")
    return problems
