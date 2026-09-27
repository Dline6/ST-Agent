"""信息面源定义与同步任务注册表（T-L0-010.2–.5）。

**主备与覆盖口径**来自 `Q-003` 决议 + D-029 / D-030 / D-032：

| 域 | 主源 | 备胎 / 并行 | 口径 |
|---|---|---|---|
| 公告 | 巨潮（**覆盖全市**） | 深交所官方（**仅深市**）· 东财（**仅沪市**） | **权威源优先**；备胎按**覆盖分片**，`coverage` 列显式标注 |
| 龙虎榜 | 东财（上榜记录） | 深交所官方（**仅深市**，亦为上榜记录） | **按实体分两表**；席位明细**暂无已验证源**（官方明细端点未验证） |
| 股东户数 | 东财 | **无**（不可用即 `unavailable`） | **单源**——A / B / C 在此退化为同一结果 |
| 舆情问答 | **双市并行互补**：互动易（深市）+ 上证e互动（沪市） | —— | 非主备：同语义、按市场分片，故**同表**、`market` 列标注 |
| 热度榜 | 同花顺热榜（+ 东财人气榜作并列榜单） | —— | **易腐**数据，独立实体与契约 |

**一任务可写多表**：`table_name` 记主表，`tables` 记该任务写到的**全部**表
（龙虎榜的上榜记录与席位明细同源同次抓取，拆成两条任务只会重复抓取）。
每张表仍**必有一条**登记在案的同步任务可判其新鲜度（05 §设计原则 5 的意图）。

**窗口**：公告 / 问答按水位续跑；龙虎榜按交易日单日；股东户数全量对账；
热度榜为「此刻」快照。水位语义见 :func:`window_for`。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import NamedTuple

from st_agent.l0.info.errors import InfoValidationError

__all__ = [
    "INFO_SOURCES",
    "INFO_TASKS",
    "INFO_TASK_KEYS",
    "InfoSource",
    "InfoTask",
    "SOURCE_IDS",
    "coverage_of",
    "get_info_task",
    "hosts_of",
    "market_of",
    "to_stock_id",
    "window_for",
]


def _today() -> date:
    return datetime.now().astimezone().date()


class InfoSource(NamedTuple):
    """一个数据源的注册信息（映射 ``data_source`` 一行）。"""

    source_id: str
    name: str
    manual_ref: str
    hosts: tuple[str, ...]
    """该源出网涉及的主机（经网关审计时按实际请求逐条登记）。"""


class InfoTask(NamedTuple):
    """一个同步任务（映射 ``sync_state`` 一行）。"""

    task_key: str
    domain: str
    table_name: str
    source_id: str
    mode: str
    """``incremental``（按水位追加）/ ``upsert_window``（滚动重拉 + UPSERT）/
    ``snapshot``（追加新快照，历史不动）。"""

    schedule_desc: str
    coverage: str
    """``all`` / ``sh`` / ``sz``——**覆盖范围显式化**（D-030：不得让「换了源」
    看起来像「没有数据」）。"""

    role: str
    """``primary``（权威源，`INSERT OR REPLACE`）/ ``backup``（备胎，让位于
    已存在的主源行）/ ``parallel``（并行互补，按分片各行其是）/
    ``sole``（单源）。"""

    tables: tuple[str, ...] = ()
    """本任务写到的全部表（空 = 仅 ``table_name``）。"""

    watermark_desc: str = ""
    """水位语义（人类可读，进 05 文档与 ``schedule_desc`` 同列展示）。"""

    @property
    def written_tables(self) -> tuple[str, ...]:
        return self.tables or (self.table_name,)


INFO_SOURCES: tuple[InfoSource, ...] = (
    InfoSource("cninfo", "巨潮资讯网（证监会指定信息披露平台）",
               "https://www.cninfo.com.cn", ("www.cninfo.com.cn",)),
    InfoSource("szse", "深圳证券交易所",
               "https://www.szse.cn", ("www.szse.cn",)),
    InfoSource("eastmoney", "东方财富",
               "https://datacenter-web.eastmoney.com",
               ("datacenter-web.eastmoney.com", "push2his.eastmoney.com",
                "np-anotice-stock.eastmoney.com")),
    InfoSource("cninfo_irm", "互动易（巨潮，深市投资者关系）",
               "https://irm.cninfo.com.cn", ("irm.cninfo.com.cn",)),
    InfoSource("sse_e", "上证e互动（沪市投资者关系）",
               "https://sns.sseinfo.com", ("sns.sseinfo.com",)),
    InfoSource("ths", "同花顺", "https://d.10jqka.com.cn",
               ("d.10jqka.com.cn", "eq.10jqka.com.cn")),
)

SOURCE_IDS: tuple[str, ...] = tuple(s.source_id for s in INFO_SOURCES)

_SOURCES_BY_ID: dict[str, InfoSource] = {s.source_id: s for s in INFO_SOURCES}


def hosts_of(source_id: str) -> tuple[str, ...]:
    """该源的出网主机（**审计**用，``02 §6`` 的 ``target_host`` 须是真实主机名）。

    多主机源按序返回、**首项为主**；同步引擎对一次抓取只登记一条 ``NetworkEvent``
    （``purpose`` 已写明域与源），故取首项即可——**不得**拿 ``source_id`` 冒充主机
    （源标识可以含下划线，主机名不行）。
    """
    source = _SOURCES_BY_ID.get(source_id)
    if source is None:
        raise InfoValidationError(f"未注册的信息面源 {source_id!r}")
    return source.hosts

INFO_TASKS: tuple[InfoTask, ...] = (
    # ── 公告域：权威源优先 + 覆盖分片兜底（D-030 / Q-003） ──
    InfoTask("info_announcement_cninfo", "announcement", "announcement",
             "cninfo", "upsert_window", "每交易日（盘后）", "all", "primary",
             watermark_desc="最近已拉披露日"),
    InfoTask("info_announcement_szse", "announcement", "announcement",
             "szse", "upsert_window", "每交易日（主源不可用时；覆盖仅深市）",
             "sz", "backup", watermark_desc="最近已拉披露日（深市）"),
    InfoTask("info_announcement_em", "announcement", "announcement",
             "eastmoney", "upsert_window", "每交易日（主源不可用时；覆盖仅沪市）",
             "sh", "backup", watermark_desc="最近已拉披露日（沪市）"),
    # ── 龙虎榜域：按实体分两表；席位以交易所官方为主 ──
    InfoTask("info_dragon_tiger_em", "dragon_tiger", "dragon_tiger",
             "eastmoney", "incremental", "每交易日盘后", "all", "primary",
             ("dragon_tiger", "dragon_tiger_seat"),
             watermark_desc="最近已拉交易日"),
    # 官方端点实测（2026-09-27）只给**上榜记录**、且仅深市；席位明细须另调
    # CATALOGID=1842_detal（未验证）——见 T-L0-010 遗留册。
    InfoTask("info_dragon_tiger_szse", "dragon_tiger", "dragon_tiger",
             "szse", "incremental", "每交易日盘后（仅深市；上榜记录兜底）",
             "sz", "backup",
             watermark_desc="最近已拉交易日（深市）"),
    # ── 股东户数域：单源无备胎 ──
    InfoTask("info_shareholder_num_em", "shareholder_num", "shareholder_num",
             "eastmoney", "upsert_window", "每周（季频数据，重拉最近 8 个季度）",
             "all", "sole", watermark_desc="已覆盖的最近统计截止日"),
    # ── 舆情域：双市并行互补（非主备）+ 热度榜易腐 ──
    InfoTask("info_sentiment_qa_irm", "sentiment_qa", "sentiment_qa",
             "cninfo_irm", "upsert_window", "每交易日（深市）", "sz", "parallel",
             watermark_desc="最近已拉提问时间（深市）"),
    InfoTask("info_sentiment_qa_sse", "sentiment_qa", "sentiment_qa",
             "sse_e", "upsert_window", "每交易日（沪市）", "sh", "parallel",
             watermark_desc="最近已拉提问时间（沪市）"),
    InfoTask("info_sentiment_hot", "sentiment_hot", "sentiment_hot",
             "ths", "snapshot", "每交易日（盘后；易腐数据，快照式追加）",
             "all", "sole", watermark_desc="无（快照式，每次追加新快照）"),
)

INFO_TASK_KEYS: tuple[str, ...] = tuple(t.task_key for t in INFO_TASKS)

_TASKS_BY_KEY: dict[str, InfoTask] = {t.task_key: t for t in INFO_TASKS}

_BASELINE_DAYS = 90
"""水位缺失（首次）时的回看天数——信息面为事件流，不回溯全历史。"""


def get_info_task(task_key: str) -> InfoTask:
    """取任务登记项（未知 → ``InfoValidationError``）。"""
    try:
        return _TASKS_BY_KEY[task_key]
    except KeyError:
        raise InfoValidationError(
            f"未知信息面任务 {task_key!r}；合法任务 = {list(INFO_TASK_KEYS)}"
        ) from None


def coverage_of(task_key: str) -> str:
    return get_info_task(task_key).coverage


def to_stock_id(code: str, market: str | None = None) -> str:
    """源方代码 → 全库统一 ``<exchange>.<六位>`` 形态（多源扩展规约 §1）。

    - 已带前缀（``sh.600000`` / ``SZ.000001``）→ 小写归一
    - 裸六位 + ``market``（``sh`` / ``sz``）→ 直接拼
    - 裸六位无 ``market`` → 按号段判：**北交所**（``920`` / ``83`` / ``87`` /
      ``88`` / ``43`` 开头）→ ``bj.``；``5`` / ``6`` / ``9`` 开头 → ``sh.``；
      其余 → ``sz.``
    - **主档外号段**（北交所）**照实标 ``bj.``** 后交由预过滤处置（D-032：
      过滤 + 计数，不在此静默丢弃）——**不得**误标成 ``sh.`` / ``sz.``，
      否则「不在主档」这一事实会被一个错误的交易所前缀掩盖
    """
    text = str(code or "").strip()
    if not text:
        raise InfoValidationError("证券代码不可为空")
    if "." in text:
        prefix, _, digits = text.partition(".")
        return f"{prefix.lower()}.{digits}"
    digits = text.zfill(6)
    side = (market or "").lower()
    if side not in ("sh", "sz", "bj"):
        if digits.startswith(("920", "83", "87", "88", "43")):
            side = "bj"
        else:
            side = "sh" if digits[:1] in ("5", "6", "9") else "sz"
    return f"{side}.{digits}"


def market_of(stock_id: str) -> str:
    """``sh.600000`` → ``'sh'``（``sentiment_qa.market`` 等分片列的取值）。"""
    return stock_id.split(".", 1)[0]


def window_for(task_key: str, watermark: str | None, *,
               day: str | None = None, today: date | None = None) -> tuple[str, str]:
    """推导任务拉取窗口 ``(start, end)``。

    - 公告 / 问答（``upsert_window``）：水位次日 → 今日；水位缺失 → 回看
      ``_BASELINE_DAYS`` 天（事件流不回溯全历史）
    - 龙虎榜（``incremental``）：**单日**窗口（``day`` 缺省今日）
    - 股东户数（``upsert_window``）：按季度重拉最近 8 个季度
    - 热度榜（``snapshot``）：单日窗口（仅作快照时刻锚，不按窗口过滤）
    """
    spec = get_info_task(task_key)
    today = today or _today()
    end = (day or today.isoformat())
    if spec.domain == "dragon_tiger" or spec.mode == "snapshot":
        return (end, end)
    if spec.domain == "shareholder_num":
        oldest = today - timedelta(days=730)
        return (oldest.isoformat(), today.isoformat())
    start = (date.fromisoformat(watermark) + timedelta(days=1)).isoformat() \
        if watermark else (today - timedelta(days=_BASELINE_DAYS)).isoformat()
    return (start, today.isoformat())
