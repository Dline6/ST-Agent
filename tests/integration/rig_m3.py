"""T-INT-004 · M3 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M1 的 ``rig_m1.py`` / M2 的 ``rig_m2.py`` 同一取向：真实 ``Store`` + 真实 ``MarketDb``
+ 真实官方 Pack + 真实 L2/L3/L4/L5 编排件 + **真实进程内事件总线**；只把**出网面**换成
脚本化注入，使 CI 不随本机 ``.env`` 有无而变：

- 意图理解器（L3）→ :class:`DeterministicUnderstander`（复用 `rig_m1`）
- 观点方向合成器 / 证据重研判（L4）→ `rig_m2` 的脚本件（真研判由 `tests/live/` 兜）
- 渠道（L5）→ :class:`RecordingChannel`（真渠道是「经 L0 网关出网」那条路，见 `tests/live/`）

**本 rig 的调度目标是真跑的**：播种数据面上的最新交易日被改成「异动 + 高换手」，
于是 ``sk_stock_watch``（官方 Pack 里**唯一**带 ``frequency_minutes`` 的可调度监控）
在首次 ``tick`` 时真的到期、真的执行（经 L1 `Scheduler` → `SkillRunner` → 官方执行器
→ 本地市场库），产出 ``triggered[]``。信号产生规则、总线、L5 采纳与投递留痕全部真实 ——
这是 [00 §5](../../docs/技术架构-v2/00-架构总览.md) 步 4 的端到端链路。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rig import PASS, MarketData, RecordingSender, seed_market_db
from rig_m1 import (
    CONFIGURE_QUERY,
    MEMORY_OP_QUERY,
    RISK_QUERY,
    DeterministicUnderstander,
)
from rig_m2 import (
    ANALYZE_QUERY,
    SameThreadExecutor,
    ScriptedReviewer,
    ScriptedSynthesizer,
)

from st_agent.app import M3Runtime, build_m3_runtime
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.l3.intent import IntentDraft
from st_agent.l5.channels import ChannelHealth, ChannelResult

__all__ = [
    "ANALYZE_QUERY",
    "DEFAULT_RULES",
    "NOW",
    "Clock",
    "RecordingChannel",
    "RecordingTransport",
    "M3Rig",
    "WATCH_SEED_SQL",
    "seeded_m3",
]

NOW = datetime(2026, 9, 28, 8, 30, tzinfo=timezone(timedelta(hours=8)))
"""本关卡的固定时钟（周一 08:30）——已过缺省日报时刻 08:00，故 `due` 为真。"""

WATCH_SEED_SQL = (
    "UPDATE k_line_daily SET pct_chg = 7.5, turn = 12.0"
    " WHERE trade_date = '2026-09-25'"
)
"""把播种的**最新交易日**改成「异动（+7.5%）+ 高换手（12%）」——盯盘因此命中两条。

改的是本 rig 自己在 ``tmp_path`` 上的行（`rig.seed_market_db` 的公共播种不动），
于是 ``sk_stock_watch`` 的每一次触发都**可复算**（阈值 5.0 / 10.0，见官方执行器）。
"""

DEFAULT_RULES: list[tuple[str, IntentDraft]] = [
    ("退市", RISK_QUERY),
    ("阈值", CONFIGURE_QUERY),
    ("分析", ANALYZE_QUERY),
    ("冲突", MEMORY_OP_QUERY),
]


class Clock:
    """**可推进的**固定时钟（运行时各面按它取「此刻」）。

    `tick` 仍按次显式传时刻（判定可离线复算）；本钟只服务那些**由运行时自己取时**的面
    ——去重窗口（[07 §6]）与格位周期（[07 §2]）——故用例要「让时间过去」时推它，
    而不是改信号上的 `occurred_at`（后者是上游给的发生时刻，不是「被裁决的时刻」）。
    """

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def set(self, moment: datetime) -> None:
        self.moment = moment

    def advance(self, **kwargs: Any) -> datetime:
        self.moment = self.moment + timedelta(**kwargs)
        return self.moment


class RecordingChannel:
    """记录载荷的渠道替身（可配置成功 / 失败，用于降级与升级链的显式分支）。"""

    def __init__(self, kind: str, *, ok: bool = True, available: bool = True) -> None:
        self.channel = kind
        self.ok = ok
        self.available = available
        self.payloads: list[Any] = []

    def deliver(self, incoming: Any) -> ChannelResult:
        self.payloads.append(incoming)
        if self.ok:
            return ChannelResult(channel=self.channel, status="ok")
        return ChannelResult(
            channel=self.channel, status="unavailable",
            detail=f"替身：{self.channel} 不可达", pending_reconnect=True,
        )

    def health(self) -> ChannelHealth:
        return ChannelHealth(
            channel=self.channel, available=self.available, offline_level="full",
            reason="" if self.available else f"替身：{self.channel} 未接线",
        )

    def degrade(self, reason: str) -> ChannelResult:
        return ChannelResult(channel=self.channel, status="unavailable", detail=reason)


class RecordingTransport:
    """云端传输替身（`ChannelTransport` 鸭子类型：``(target_host, message) -> bytes_in``）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    def __call__(self, target_host: str, message: bytes) -> int:
        self.calls.append((target_host, message))
        return 0


_LOCAL_CHANNELS = ("desktop", "email", "im_webhook", "tts")


@dataclass(frozen=True)
class M3Rig:
    """一次 M3 装配的全部句柄。"""

    root: Path
    m3: M3Runtime
    feed: MarketData
    sender: RecordingSender
    clock: Clock
    understander: DeterministicUnderstander
    synthesizer: ScriptedSynthesizer
    reviewer: ScriptedReviewer
    channels: dict[str, RecordingChannel] = field(default_factory=dict)


def _seed_watch_data(root: Path, passphrase: str) -> None:
    """播种市场数据面后，把最新交易日改成触发盯盘条件的那一组值。"""
    seed_market_db(root, passphrase)
    store = Store.open(root, passphrase)
    db = MarketDb(store)
    with db.transact() as con:
        con.execute(WATCH_SEED_SQL)
        con.commit()


def seeded_m3(
    root: Path,
    *,
    rules: list[tuple[str, IntentDraft]] | None = None,
    stances: dict[str, str] | None = None,
    channels: dict[str, RecordingChannel] | None = None,
    fail: tuple[str, ...] = (),
    clock: Clock | None = None,
    **l1_kwargs: Any,
) -> M3Rig:
    """已播种数据面的 M3 全栈装配（生产组合根 `build_m3_runtime` + 脚本化出网面替身）。

    :param channels: 渠道映射覆写（**给了即不与内置替身合并**——替身说了算；缺项由
        `_build_l5` 按原生端口 / 云端参数自行接线，故「只给三个」能造出**未接线**的 TTS）
    :param fail: 让这些渠道的投递失败（验升级链与显式降级）
    :param l1_kwargs: 透传 `build_m3_runtime`（如 `audit=True` / `cloud_transport` /
        `email_host` / `credentials`——GWT-9 的云端真接线走这里）
    """
    _seed_watch_data(root, PASS)
    feed = MarketData()
    sender = RecordingSender()
    moment = Clock() if clock is None else clock
    understander = DeterministicUnderstander(DEFAULT_RULES if rules is None else rules)
    synthesizer = ScriptedSynthesizer(stances)
    reviewer = ScriptedReviewer()
    built = channels if channels is not None else {
        kind: RecordingChannel(kind, ok=kind not in fail) for kind in _LOCAL_CHANNELS
    }
    m3 = build_m3_runtime(
        root, PASS, market_query=feed, sender=sender, llm_env={}, now=moment,
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        executor=SameThreadExecutor(), channels=built, **l1_kwargs,
    )
    return M3Rig(
        root=root, m3=m3, feed=feed, sender=sender, clock=moment,
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        channels=dict(built),
    )

