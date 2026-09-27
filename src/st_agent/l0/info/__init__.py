"""L0 信息面（互补域）——公告 / 龙虎榜 / 股东变化 / 舆情（T-L0-010）。

BaoStock 之外的**互补信息面**：采集通道一律 **L0 网关直抓**（D-029），
落同一 ``data_cache`` 分区的 ``market.db`` 与文本目录。

- :mod:`~st_agent.l0.info.sources`——源定义与 :data:`INFO_TASKS` 注册表
- :mod:`~st_agent.l0.info.fetch`——:class:`InfoFetcher` 协议 + 纯解析 + HTTP 实现
- :mod:`~st_agent.l0.info.docs`——文本两段承载（:class:`DocStore`）与文档取数面
- :mod:`~st_agent.l0.info.prefilter`——主档预过滤 + ``filtered_unmapped`` 计数
- :mod:`~st_agent.l0.info.sync`——:class:`InfoSync` 同步引擎
"""

from st_agent.l0.info.docs import (
    DOC_TEXT_DOMAINS,
    DocConsistency,
    DocSource,
    DocStore,
    LocalDocSource,
    audit_docs,
    text_path,
)
from st_agent.l0.info.errors import (
    InfoError,
    InfoFetchError,
    InfoFetchUnavailableError,
    InfoValidationError,
)
from st_agent.l0.info.fetch import HttpInfoFetcher, InfoFetcher
from st_agent.l0.info.prefilter import UnmappedCount, filter_unmapped, master_codes
from st_agent.l0.info.sources import (
    INFO_SOURCES,
    INFO_TASKS,
    INFO_TASK_KEYS,
    InfoSource,
    InfoTask,
    get_info_task,
    to_stock_id,
    window_for,
)
from st_agent.l0.info.sync import ARCHIVAL_DOMAINS, DOMAIN_INFO_TASKS, InfoSync, digest_id

__all__ = [
    "ARCHIVAL_DOMAINS",
    "DOC_TEXT_DOMAINS",
    "DOMAIN_INFO_TASKS",
    "DocConsistency",
    "DocSource",
    "DocStore",
    "HttpInfoFetcher",
    "INFO_SOURCES",
    "INFO_TASKS",
    "INFO_TASK_KEYS",
    "InfoError",
    "InfoFetchError",
    "InfoFetchUnavailableError",
    "InfoFetcher",
    "InfoSource",
    "InfoSync",
    "InfoTask",
    "InfoValidationError",
    "LocalDocSource",
    "UnmappedCount",
    "audit_docs",
    "digest_id",
    "filter_unmapped",
    "get_info_task",
    "master_codes",
    "text_path",
    "to_stock_id",
    "window_for",
]
