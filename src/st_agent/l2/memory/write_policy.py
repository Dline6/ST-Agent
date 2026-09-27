"""自主写入白名单与写入策略（04 §4 首句）。

「**按节点维度配置**（注册进 01 §7 配置注册表）——哪些维度副驾可自主写入、
哪些必须询问」：一条**推断类**新信息进来时，:meth:`WritePolicy.decide` 判它
``direct``（直写）还是 ``propose``（须询问，进冲突队列）。

粒度＝**节点类型**（[04 §1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的六类），
与 §7「某维度无记忆」的维度口径一致（见任务 `A1`）。

**白名单只管副驾推断面**：``source=user_stated`` 的信息按 §3.2「用户显式…直接
写入」直写，不过本门（见任务 `A3`）——故 :meth:`decide` 对用户显式一律返回
``direct``。

语义冲突检测不在本模块（本模块只做结构化判定），归 ``conflict``。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Literal

from st_agent.contracts.registry_types import ChangePolicy, ChangeRecord, ConfigEntry, PanelField
from st_agent.l2.memory.config_store import MemoryPolicyStore
from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.models import NODE_TYPES, MemoryNode

__all__ = [
    "DEFAULT_WRITE_WHITELIST",
    "WRITE_WHITELIST_CONFIG_ID",
    "WriteDecision",
    "WritePolicy",
]

WRITE_WHITELIST_CONFIG_ID = "memory-policy/write-whitelist"
"""自主写入白名单条目的 ``config_id``（01 §7）。"""

DEFAULT_WRITE_WHITELIST: tuple[str, ...] = ("history", "pattern", "evolution")
"""缺省白名单（见任务 `A2`）；按 §1 六类序书写，与 :func:`_normalized` 的规范序一致。

``pattern`` / ``evolution`` / ``history`` 是「副驾观察到的事实」，§1 对 ``pattern``
的定义即「副驾观察到的行为模式」；``identity`` / ``attention`` / ``thesis`` 是用户
自述面（你是谁 / 你关注什么 / 你的 thesis），取「必须问我」（宪法铁律 6 共同演化
透明 + §3.2 红线取向）。用户可改。
"""

WriteDecision = Literal["direct", "propose"]
"""``direct`` 直写 / ``propose`` 须询问（进冲突队列待用户裁决）。"""


def _entry(dimensions: Iterable[str]) -> ConfigEntry:
    """按 01 §7 七字段组装白名单条目（``default`` 槽＝当前取值）。"""
    return ConfigEntry(
        config_id=WRITE_WHITELIST_CONFIG_ID,
        display_name="副驾可自主写入的记忆维度",
        value_schema={"type": "array", "items": {"type": "string", "enum": list(NODE_TYPES)}},
        default=list(dimensions),
        description_for_chat=(
            "副驾推断出的信息，哪些维度可以直接记下来、哪些必须先来问你；"
            "用户自己说的话不受本项影响，一律直接记录"
        ),
        panel_form_spec=PanelField(
            widget="matrix",
            label="自主写入维度",
            help_text="勾选的维度副驾可自主写入；未勾选的维度，副驾推断到新信息会先来问你",
            choices=NODE_TYPES,
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=True),
    )


def _normalized(dimensions: Iterable[str]) -> tuple[str, ...]:
    """校验并规范化一组维度（按 §1 六类序去重；未知名段一律拒绝）。

    去重按「同一维度只出现一次」处理，不静默忽略；顺序固定为 ``NODE_TYPES`` 序，
    使落盘与比对是确定性的。
    """
    given = list(dimensions)
    unknown = sorted({d for d in given if d not in NODE_TYPES})
    if unknown:
        raise MemoryValidationError(
            f"未知的记忆维度 {unknown}（合法维度：{'/'.join(NODE_TYPES)}）"
        )
    return tuple(t for t in NODE_TYPES if t in set(given))


class WritePolicy:
    """自主写入白名单门面（04 §4；条目落 ``config``，留痕落 ``execution_log``）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self._config = MemoryPolicyStore(store, now=now)

    # ───────────────────────── 读 ─────────────────────────

    def entry(self) -> ConfigEntry:
        """条目的登记形态（01 §7 七字段；供配置注册表面浏览）。"""
        return _entry(self.dimensions())

    def dimensions(self) -> tuple[str, ...]:
        """当前白名单（条目缺失或损坏 → 缺省值，不因一条配置读不动就停摆）。"""
        raw = self._config.current(WRITE_WHITELIST_CONFIG_ID, list(DEFAULT_WRITE_WHITELIST))
        try:
            return _normalized(raw)
        except MemoryValidationError:
            return DEFAULT_WRITE_WHITELIST

    # ───────────────────────── 写 ─────────────────────────

    def set_dimensions(
        self, dimensions: Iterable[str], *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """改一版白名单并留痕（**取值未变则不留痕**，返回 ``None``）。

        :param dimensions: 六类节点的子集（未知名段显式拒绝，不静默丢弃）
        :param trace_ref: 关联推理链（如适用）
        """
        return self._config.set(
            _entry(_normalized(dimensions)),
            previous=list(self.dimensions()),
            trace_ref=trace_ref,
        )

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部条目变更留痕（按 ``change_id`` 升序）。"""
        return self._config.changes()

    # ───────────────────────── 判定 ─────────────────────────

    def decide(self, node: MemoryNode) -> WriteDecision:
        """一条新信息该直写还是须询问（04 §4）。

        用户显式（``source=user_stated``）→ ``direct``（§3.2 直写，不过白名单门）；
        副驾推断（``source=inferred``）→ 视其节点类型是否在白名单内。取值为
        ``propose`` 时**不得**自行写入——须走 ``conflict`` 的提案与裁决路径。
        """
        if node.source == "user_stated":
            return "direct"
        return "direct" if node.type in self.dimensions() else "propose"
