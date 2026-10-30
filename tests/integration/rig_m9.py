"""T-INT-010 · M9 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M8 的 ``rig_m8.py`` 同一取向：真 ``Store``（口令派生、落盘加密、清单校验）+
真 ``MarketDb`` + 真官方 Pack + 真 L0 LLM 链路（``LlmClient`` / transport / 出网网关 /
能力协商）+ **真 L1 内置原语面**（取数 / 文档 / 文件 / 开放网络 / 命令）+ 真 L3 循环与
L6 授权闸门。只替换三处**网络跳**：

1. ``llm_post`` —— 脚本化模型端点（``rig_m5.ScriptedEndpoint``），与 M5 / M8 同款；
2. ``web_http`` —— 开放网络原语的 HTTP 发送口替身（搜索 / 抓取都在这一跳）；
3. **信息面抓取器** —— ``DataSyncRunner`` 换绑假源工厂（``info_factory``），
   ``InfoSync`` 的预过滤 / 入表 / 正文落 ``data_cache`` / 水位回写全部真跑。

``audit=True`` 显式打开出网审计（产品缺省关，[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) /
[D-073](../决策日志.md)）——GWT-4 的「出网有审计」要看得见记录才能断言；缺省关的语义由
既有套件覆盖，不由本 rig 体现。

**为什么换绑 ``DataSyncRunner``**：生产组合根造的 runner 用真实 ``BaoStockFetcher`` /
``HttpInfoFetcher`` 出网；关卡不能联网。换绑后的 runner 仍经 ``L1Runtime.bind_data_sync``
接进同一栈，走的是**同一条**原语执行路径（参数 / 沙箱 / 留痕 / 审计都不变）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import DeterministicUnderstander
from rig_m5 import ENV, ScriptedEndpoint

from st_agent.app import M5Runtime, build_m5_runtime
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.info import InfoSync
from st_agent.l0.market import MarketDb
from st_agent.l0.net.web_config import (
    SEARCH_CREDENTIAL_ID,
    set_egress_enabled,
    set_search_provider,
)
from st_agent.l0.net.web_http import FetchedPayload
from st_agent.l0.storage import Store
from st_agent.l0.sync_runner import DataSyncRunner
from st_agent.l3.intent import IntentDraft

__all__ = [
    "ANNOUNCEMENT_DATASET",
    "ANNOUNCEMENT_TASK",
    "DRAGON_TIGER_DATASET",
    "DRAGON_TIGER_TASK",
    "FakeHttp",
    "FakeInfoFetcher",
    "FakeMarketSync",
    "M9Rig",
    "TASK_TEXT",
    "brave_payload",
    "default_info_plan",
    "enable_web_search",
    "seeded_m9",
]

TASK_TEXT = "查一下 ST 股 sh.600000 的最新公告与龙虎榜"
"""GWT-1 的用户原话（经 `investigate` 意图的 `target` 承载任务文本）。"""

ANNOUNCEMENT_DATASET = "announcement_cninfo"
DRAGON_TIGER_DATASET = "dragon_tiger_em"
"""`data_fetch` / `data_sync` 的**数据集 id**（`l0/market/datasets.py::DATASET_IDS`）。"""

ANNOUNCEMENT_TASK = "info_announcement_cninfo"
DRAGON_TIGER_TASK = "info_dragon_tiger_em"
"""底层同步**任务键**（``sync_state`` 行与抓取器 plan 的键）。"""

_AS_OF = "2026-09-25T08:00:00+08:00"
"""播种数据面的时间锚（与 ``rig.SEED_SQL`` 一致）。"""


def _ok(rows: int = 1) -> ResultEnvelope:
    return ResultEnvelope.ok({"rows": rows}, as_of=datetime.now().astimezone())


class FakeInfoFetcher:
    """可注入的信息面抓取器（同 ``tests/l0/info_helpers.FakeInfoFetcher`` 的最小形态）。

    按任务键返回预置行；未预置 → 空成果。``InfoSync`` 的窗口 / 预过滤 / 入表 / 水位
    都是真的，只有源端这一跳是替身。
    """

    def __init__(self, plan: dict | None = None) -> None:
        self.plan = dict(plan or {})
        self.calls: list[tuple[str, tuple[str, str], tuple[str, ...] | None]] = []

    def fetch_task(
        self, task_key: str, window: tuple[str, str], *,
        codes: tuple[str, ...] | None = None,
    ) -> dict:
        self.calls.append((task_key, window, codes))
        return self.plan.get(task_key, {})


class FakeMarketSync:
    """行情面抓取器替身（本关卡的取数域全是信息面，行情路径不会被走到）。

    存在只为让 ``DataSyncRunner`` 的 ``market_factory`` 有可注入实现——万一预检自动
    补了主档任务，也不会落到真实 ``BaoStockFetcher`` 上出网。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def setup(self) -> ResultEnvelope:
        return _ok()

    def run_task(self, task_key: str, **_kwargs: Any) -> ResultEnvelope:
        self.calls.append(task_key)
        return _ok()


class FakeHttp:
    """开放网络原语的 HTTP 发送口替身（``urllib`` 的位置）。

    返回 ``FetchedPayload`` 并记录调用；GWT-2 断言「关闭态零请求」即看 ``calls``。
    """

    def __init__(self) -> None:
        self.payload = FetchedPayload(
            200, "application/json", b'{"web": {"results": []}}'
        )
        self.calls: list[tuple[Any, ...]] = []

    def __call__(
        self, method: str, url: str, headers: Any, body: Any,
        timeout_s: float, max_bytes: int,
    ) -> FetchedPayload:
        self.calls.append((method, url, headers, body, timeout_s, max_bytes))
        return self.payload


def brave_payload(*items: dict) -> FetchedPayload:
    """Brave 搜索响应体（形状同 T-AGT-015.1 的归一化输入）。"""
    body = json.dumps({"web": {"results": list(items)}}, ensure_ascii=False)
    return FetchedPayload(200, "application/json", body.encode("utf-8"))


def _announcement_row(
    code: str = "600000", *, title: str = "关于回购公司股份的公告",
    pub_date: str = "2026-09-20", text: str = "回购正文……",
) -> dict:
    return {
        "code": code, "title": title, "ann_type": "回购",
        "pub_date": pub_date, "url": "https://example.invalid/a", "text": text,
    }


def _dragon_tiger_row(
    code: str = "600000", *, trade_date: str = "2026-09-25",
    reasons: str = "日涨幅偏离值达7%", net: float = 1.0e7,
) -> dict:
    return {
        "code": code, "trade_date": trade_date, "reasons": reasons,
        "net_amount": net, "buy_amount": 2.0e7, "sell_amount": 1.0e7,
        "turnover": 8.5,
    }


def default_info_plan() -> dict:
    """默认播种：一条公告 + 一条龙虎榜上榜记录（都属 ``sh.600000``）。"""
    return {
        ANNOUNCEMENT_TASK: {"announcement": (_announcement_row(),)},
        DRAGON_TIGER_TASK: {
            "dragon_tiger": (_dragon_tiger_row(),),
            "dragon_tiger_seat": (),
        },
    }


@dataclass(frozen=True)
class M9Rig:
    """一次 M9 装配的全部句柄。"""

    root: Path
    m9: M5Runtime
    feed: MarketData
    sender: RecordingSender
    understander: DeterministicUnderstander
    endpoint: ScriptedEndpoint
    http: FakeHttp
    info: FakeInfoFetcher
    sync: DataSyncRunner

    @property
    def store(self) -> Store:
        return self.m9.store

    @property
    def gateway(self):
        return self.m9.m1.runtime.gateway

    @property
    def db(self) -> MarketDb:
        return MarketDb(self.m9.store)


def enable_web_search(
    rig: M9Rig, *, provider: str = "brave", key: str = "sk-search-test"
) -> None:
    """打开开放网络并配置搜索提供方 / 密钥（落真实配置区与加密凭据库）。"""
    set_egress_enabled(rig.store, True)
    set_search_provider(rig.store, provider)
    rig.m9.m1.runtime.vault.add(SEARCH_CREDENTIAL_ID, "search_api_key", key)


def _assemble(
    root: Path,
    *,
    script: list[tuple[str, dict]] | None,
    rules: list[tuple[str, IntentDraft]] | None,
    finish: str,
    info_plan: dict | None,
    **l1_kwargs: Any,
) -> M9Rig:
    feed = MarketData()
    sender = RecordingSender()
    http = FakeHttp()
    endpoint = ScriptedEndpoint(script=script, finish=finish)
    understander = DeterministicUnderstander(
        _RULES if rules is None else rules
    )
    info = FakeInfoFetcher(default_info_plan() if info_plan is None else info_plan)
    l1_kwargs.setdefault("dotenv_path", root.parent / "test.env")
    l1_kwargs.setdefault("bootstrap_file", root.parent / "test-bootstrap.json")
    runtime = build_m5_runtime(
        root, PASS,
        market_query=feed,
        sender=sender,
        llm_env=ENV,
        llm_post=endpoint,
        understander=understander,
        web_http=http,
        audit=True,
        **l1_kwargs,
    )
    # 换绑同步执行面：同一栈、同一原语入口，只把源端抓取器换成离线替身。
    sync = DataSyncRunner(
        store=runtime.store,
        gateway=runtime.m1.runtime.gateway,
        watchlist=lambda: (),
        market_factory=lambda _db, _gw: (FakeMarketSync(), lambda: None),
        info_factory=lambda db, gw: (InfoSync(db, gw, info), lambda: None),
    )
    runtime.m1.runtime.bind_data_sync(sync)
    return M9Rig(
        root=root, m9=runtime, feed=feed, sender=sender,
        understander=understander, endpoint=endpoint, http=http,
        info=info, sync=sync,
    )


_RULES: list[tuple[str, IntentDraft]] = [
    ("公告", IntentDraft(intent="investigate", target=TASK_TEXT)),
    ("龙虎榜", IntentDraft(intent="investigate", target=TASK_TEXT)),
]


def seeded_m9(
    root: Path,
    *,
    script: list[tuple[str, dict]] | None = None,
    rules: list[tuple[str, IntentDraft]] | None = None,
    finish: str = "已按本地缓存的最新数据作答",
    info_plan: dict | None = None,
    **l1_kwargs: Any,
) -> M9Rig:
    """已播种市场主档的 M9 全栈装配（生产组合根 ``build_m5_runtime``）。

    播种沿用 ``rig.seed_market_db``：``security`` 主档（含 ``sh.600000``）与一条行情
    水位；公告 / 龙虎榜在取数前**为空**——正是 GWT-1 要补的「本地缺」。
    """
    seed_market_db(root, PASS)
    return _assemble(
        root, script=script, rules=rules, finish=finish,
        info_plan=info_plan, **l1_kwargs,
    )
