"""L5 投递留痕的形态与落盘（[07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

升级链的**每级**投递都留一条 :class:`DeliveryRecord`，落 ``execution_log`` 分区
``delivery/<delivery_id>.json``（该分区在
[02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的分区表里本就标注为
「L1 / L5 / L6」的落点，**不新造分区**）。

留痕的形态（:class:`DeliveryRecord`）与它的键（`delivery_id` 的摘要输入）都住在**本模块**，
[`delivery`](delivery.py) 的编排面在其上工作——与 [`budget`](budget.py) / [`budget_store`](budget_store.py)
同一分层（状态载体与落盘机制一处、编排另一处），也免去两个模块互相 import。

三条口径：

- **`delivery_id` 是确定性摘要**（[01 §1](../../docs/技术架构-v2/01-平台共享契约.md) 的
  `dlv_` 形态，`T-L5-002.2` 同批收窄）——故同一 `(信号, 渠道, 级次)` 的**重放 / 补发写同一路径**，
  升级链可安全重入、补发不产生重复投递记录。
- **留痕自足**——连**推送文案**一起落盘，故升级链的续推、补发与日报汇总
  （[T-L5-003](../../../项目管理/tasks/T-L5-003-每日报告去重频控推送疲劳监控.md)）**只凭留痕即可完成**。
- **记录是可重写的状态载体**（不是 append-only 审计）——「已读 / 未确认」是同一级投递的
  **状态推进**，就地更新即幂等；审计语义由 L0 出网网关那段承担（[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)），两者不混。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.identifiers import DeliveryId, digest_id
from st_agent.l5.errors import DeliveryValidationError
from st_agent.l5.signal import SignalLevel

__all__ = [
    "DAILY_REPORT_CHANNEL",
    "DELIVERY_PREFIX",
    "DeliveryRecord",
    "DeliveryRoute",
    "DeliveryStatus",
    "DeliveryStore",
    "delivery_digest_key",
    "delivery_id_for",
]

DELIVERY_PREFIX = "delivery/"
"""``execution_log`` 分区内投递留痕的目录前缀。"""

DAILY_REPORT_CHANNEL = "daily_report"
"""「汇总进日报」这一路由在留痕里的渠道位（它**不是**一个可投递渠道）。"""

DeliveryRoute = Literal["separate", "daily_report"]

DeliveryStatus = Literal[
    "delivered", "unavailable", "failed", "read", "unacknowledged", "queued",
]
"""留痕状态：

- `delivered` 已投出、等已读（**链的开态**）
- `unavailable` / `failed` 本级及其余链尝试均未成（**终态**：待补发或转日报）
- `read` 用户已读（结算）
- `unacknowledged` 链尽未读（结算）
- `queued` 汇总进日报的待汇总项（不丢弃）
"""


class DeliveryRecord(BaseModel):
    """一条投递留痕（升级链的一级；也是「待汇总项」的载体）。"""

    model_config = ConfigDict(frozen=True)

    delivery_id: str
    """01 §1 `delivery_id`（（信号 + 渠道 + 级次）的确定性摘要）。"""
    signal_id: str
    """本投向哪条信号（01 §1）。"""
    level: SignalLevel
    step: int = Field(ge=0)
    """升级级次（0 起）；即渠道链上的下标。"""
    route: DeliveryRoute
    """`separate` 单独触达 / `daily_report` 汇总进日报。"""
    channel: str = ""
    """本级的渠道（`route="daily_report"` 时为 :data:`DAILY_REPORT_CHANNEL`）。"""
    status: DeliveryStatus
    detail: str = ""
    """中性说明（逐渠道的失败原因等；不含用户原文）。"""
    pending_reconnect: bool = False
    """离线拦截 ⇒ 待网络恢复后按开关补发（02 §7）。"""
    title: Annotated[str, Field(min_length=1)]
    """本次推送的标题（已过 01 §6；`daily_report` 时供日报面复用）。"""
    body: Annotated[str, Field(min_length=1)]
    """本次推送的正文（同上）。"""
    evidence_refs: tuple[str, ...] = ()
    """证据引用（重发 / 升级时重建载荷要用；01 §3 的四类 ID 形态）。"""
    trace_id: str = ""
    """关联推理链（01 §11：投递须可追溯）。"""
    created_at: datetime
    settled_at: datetime | None = None
    """结算时刻（`read` / `unacknowledged` 时非空）。"""


def delivery_digest_key(signal_id: str, channel: str, step: int) -> tuple[str, ...]:
    """`delivery_id` 的摘要输入——**全层唯一**一份推导。

    片段里**不含时刻**，故「同一信号的同一级」在何时重跑都指向同一 ID
    （升级链重入、补发、幂等留痕共用它）。
    """
    return (signal_id, channel, str(step))


def delivery_id_for(signal_id: str, channel: str, step: int) -> str:
    """按（信号 + 渠道 + 级次）铸造一枚 ``delivery_id``（[01 §1](../../docs/技术架构-v2/01-平台共享契约.md)）。

    形态是**确定性摘要**（`dlv_` ＋ 20 位十六进制）——与 :func:`~st_agent.contracts.identifiers.digest_id`
    是同一份实现，不另造副本。
    """
    return DeliveryId.of(
        digest_id("dlv", *delivery_digest_key(signal_id, channel, step))
    ).value


class DeliveryStore:
    """投递留痕的读写（``execution_log`` 分区；一层薄封装，判据都在
    :class:`DeliveryRecord` 的形态校验里）。"""

    def __init__(self, store: Any) -> None:
        self._store = store

    def put(self, record: DeliveryRecord) -> DeliveryRecord:
        """写一条留痕（同 `delivery_id` 即**覆盖**——状态推进幂等，见模块 docstring）。"""
        self._store.put(
            "execution_log", self.path_for(record.delivery_id),
            record.model_dump_json().encode("utf-8"),
        )
        return record

    def get(self, delivery_id: str) -> DeliveryRecord | None:
        """取一条留痕（不存在 → ``None``；存在但损坏 → 校验错，不静默）。"""
        try:
            raw = self._store.get("execution_log", self.path_for(delivery_id))
        except KeyError:
            return None
        try:
            return DeliveryRecord(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise DeliveryValidationError(f"投递留痕损坏无法解析（{delivery_id}）：{exc}") from exc

    def all(self) -> tuple[DeliveryRecord, ...]:
        """全部留痕（按 `delivery_id` 升序；逐条独立解析，损坏即显式报错）。"""
        records: list[DeliveryRecord] = []
        for name in self._store.list_files("execution_log"):
            if not (name.startswith(DELIVERY_PREFIX) and name.endswith(".json")):
                continue
            delivery_id = name[len(DELIVERY_PREFIX):-len(".json")]
            record = self.get(delivery_id)
            if record is not None:
                records.append(record)
        return tuple(sorted(records, key=lambda r: r.delivery_id))

    @staticmethod
    def path_for(delivery_id: str) -> str:
        """留痕的分区内相对路径（由 `delivery_id` 确定性派生）。"""
        return f"{DELIVERY_PREFIX}{delivery_id}.json"

