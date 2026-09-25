"""数据源缓存子系统（02-L0 §5）。

- 本地市场数据库：BaoStock 首个源的 SQLite 单库（``data_cache`` 分区）
- 同步水位与新鲜度：``sync_state`` / ``data_source`` 元数据（05 文档口径）
- 抓取唯一出口：一切抓取经 ``EgressGateway``（``data_fetch``/``baostock``）

对外统一从 ``st_agent.l0.market`` import。
"""

from st_agent.l0.market.cleaning import (
    clean_int,
    clean_num,
    clean_str,
    normalize_date,
    normalize_minute_ts,
)
from st_agent.l0.market.db import MARKET_DB_NAME, MarketDb
from st_agent.l0.market.errors import (
    FetchError,
    FetchUnavailableError,
    MarketError,
    MarketValidationError,
)
from st_agent.l0.market.fetch import Fetcher, FetchResult
from st_agent.l0.market.sync import DOMAIN_TASKS, BaoStockSync
from st_agent.l0.market.tasks import (
    SYNC_TASKS,
    TASK_COUNT,
    TASK_KEYS,
    TaskSpec,
    get_task,
    window_for,
)

__all__ = [
    "DOMAIN_TASKS",
    "MARKET_DB_NAME",
    "SYNC_TASKS",
    "TASK_COUNT",
    "TASK_KEYS",
    "BaoStockSync",
    "FetchError",
    "FetchResult",
    "FetchUnavailableError",
    "Fetcher",
    "MarketDb",
    "MarketError",
    "MarketValidationError",
    "TaskSpec",
    "clean_int",
    "clean_num",
    "clean_str",
    "get_task",
    "normalize_date",
    "normalize_minute_ts",
    "window_for",
]
