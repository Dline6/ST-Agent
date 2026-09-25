"""01-平台共享契约 §1 标识（ID）体系。

契约要点（§1）：
- 12 类 ID，全局唯一、生成后不可变、可本地持久化
- ``stock_id`` 首个落地形态 = 本地市场数据库 ``security`` 主档主键
  （BaoStock ``sh.``/``sz.`` 格式，数据库设计 01-核心实体层）——由 L0 数据缓存
  按交易所代码映射产生，**不由本地随机生成**
- 其余 11 类由本机产生（``generate()``：``<prefix>_<uuid4 前 20 位>``），
  无中心分配方，与本地优先原则一致

实现约定（④ 对齐确认，决策记执行日志）：
- 每类 ID 是一个 frozen pydantic 值对象（``value`` + ``id_kind`` 判别字段），
  不可变由模型保证；``id_kind`` 防跨层串用（如把 trace_id 当 signal_id 传）
- 12 类结构同构：格式校验统一继承自 ``PlatformId._check_format``，
  各类型只覆写 ``_pattern`` 类属性（含 stock_id 的交易所格式特例）——
  任何构造路径（of / model_validate）都走同一校验管道
- `§1` 表的「指代对象/产生方」以 ``ID_REGISTRY`` 登记，作为 §1 的机器可读副本
"""

from __future__ import annotations

import re
import uuid
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from st_agent.contracts.errors import ContractViolation

__all__ = [
    "ID_ALIASES",
    "ID_KINDS",
    "ID_REGISTRY",
    "AnnouncementId",
    "ChangeId",
    "DatasetSnapshotId",
    "DeliveryId",
    "FeedbackId",
    "LensId",
    "MemoryNodeId",
    "PlatformId",
    "SignalId",
    "SkillId",
    "SkillRunId",
    "StockId",
    "TraceId",
]


def new_id(prefix: str) -> str:
    """按契约生成一个本机产生的 ID 字符串：``<prefix>_<uuid4hex 前 20 位>``。"""
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class PlatformId(BaseModel):
    """12 类契约 ID 的公共基类（值对象）。

    - frozen：生成后不可变（§1）
    - ``id_kind``：契约 ID 名判别字段，防止跨层串用
    - ``value``：ID 字符串本体，本地可持久化、跨进程传输
    """

    model_config = ConfigDict(frozen=True)

    id_kind: ClassVar[str] = "platform"
    """该 ID 类型的契约名（如 ``trace_id``）。"""
    _prefix: ClassVar[str] = "id"
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"^id_[0-9a-f]{20}$")

    value: str

    @field_validator("value")
    @classmethod
    def _check_format(cls, v: str) -> str:
        """格式校验走 pydantic 管道——任何构造路径都得到统一的
        ValidationError（ContractViolation 留在异常链中）。"""
        if not cls._pattern.match(v):
            raise ContractViolation(
                f"{cls.id_kind} 格式非法: {v!r}（期望匹配 {cls._pattern.pattern}）"
            )
        return v

    @classmethod
    def generate(cls) -> "PlatformId":
        """生成一个新 ID（仅限本机产生的 11 类；``StockId`` 不适用）。"""
        if cls is StockId:
            raise ContractViolation(
                "stock_id 由 L0 数据缓存按交易所代码映射产生（security 主档主键），"
                "不得本地随机生成；用 StockId.of('sh.600000') 构造"
            )
        return cls(value=new_id(cls._prefix))  # type: ignore[return-value, call-arg]

    @classmethod
    def of(cls, raw: str) -> "PlatformId":
        """由原始字符串构造（生成方分配的 ID 入口），格式非法即拒。"""
        return cls(value=raw)


_STOCK_PATTERN = re.compile(r"^(sh|sz|bj)\.\d{6}$")
"""stock_id 特例：BaoStock 交易所格式（数据库设计 01-核心实体层）。"""


def _make_id_type(kind: str, prefix: str, *, exchange: bool = False) -> type[PlatformId]:
    """动态构造一类契约 ID 值对象（12 类结构同构，仅 kind/prefix/pattern 不同）。"""
    if exchange:
        pattern, err = _STOCK_PATTERN, "stock_id 须为交易所代码（sh./sz./bj. + 6 位数字）"
    else:
        pattern = re.compile(rf"^{re.escape(prefix)}_[0-9a-f]{{20}}$")
        err = f"{kind} 格式非法（期望前缀 {prefix!r}）"

    ns: dict = {"__module__": __name__, "__qualname__": f"Id[{kind}]",
                "id_kind": kind, "_prefix": prefix, "_pattern": pattern,
                "_format_error": err}
    cls = type(f"Id_{kind}", (PlatformId,), ns)
    return cls


StockId = _make_id_type("stock_id", "sh", exchange=True)
AnnouncementId = _make_id_type("announcement_id", "ann")
DatasetSnapshotId = _make_id_type("dataset_snapshot_id", "snap")
SkillId = _make_id_type("skill_id", "sk")
SkillRunId = _make_id_type("skill_run_id", "run")
MemoryNodeId = _make_id_type("memory_node_id", "mn")
TraceId = _make_id_type("trace_id", "tr")
SignalId = _make_id_type("signal_id", "sig")
DeliveryId = _make_id_type("delivery_id", "dlv")
FeedbackId = _make_id_type("feedback_id", "fb")
ChangeId = _make_id_type("change_id", "chg")
LensId = _make_id_type("lens_id", "lens")

# 契约 §1 表的机器可读副本：id → (指代对象, 产生方)。§1 增删类型时同步此表。
ID_REGISTRY: dict[str, tuple[str, str]] = {
    "stock_id": ("证券标的", "L0 数据缓存"),
    "announcement_id": ("单条公告/披露", "L0 数据缓存"),
    "dataset_snapshot_id": ("一次数据快照", "L0 数据缓存"),
    "skill_id": ("一个 Skill 的某个版本", "L1"),
    "skill_run_id": ("一次 Skill 执行", "L1"),
    "memory_node_id": ("一个记忆节点", "L2"),
    "trace_id": ("一次完整推理链", "L1 调度器创建"),
    "signal_id": ("一条待触达信号", "L5"),
    "delivery_id": ("一次渠道投递", "L5"),
    "feedback_id": ("一条用户反馈", "L6"),
    "change_id": ("一次配置/演进变更", "配置注册表"),
    "lens_id": ("一个视角定义", "L4"),
}
ID_KINDS: tuple[str, ...] = tuple(ID_REGISTRY)
ID_ALIASES: dict[str, type[PlatformId]] = {
    t.id_kind: t  # type: ignore[attr-defined]
    for t in (StockId, AnnouncementId, DatasetSnapshotId, SkillId, SkillRunId,
              MemoryNodeId, TraceId, SignalId, DeliveryId, FeedbackId, ChangeId, LensId)
}
"""契约名 → ID 类型登记表（§1 全表，供上层按名取类型）。"""
