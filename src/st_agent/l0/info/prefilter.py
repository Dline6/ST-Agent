"""主档预过滤基件（T-L0-010.1 ④；口径见 D-032）。

域表 ``code`` 对 ``security`` 是**硬外键**，且建库脚本首行
``PRAGMA foreign_keys = ON``——**一条未收录记录即可炸掉整批**。故同步必须
**先按主档成员预过滤并计数**，**不得**靠 DB 报错兜底。

过滤结果须**可见**（铁律 5，不静默丢）：

- 载荷携 ``filtered_unmapped`` 计数与样例代码
- 同步任务按 ``sync_state.last_status='partial'``（「任务完成但校验未全部通过」）
  登记

**已知空集 ≠ 数据缺失**：未覆盖交易所（如北交所）的标的在任何域表中恒为空，
消费面须能区分「被主档过滤」与「源端确实没有该记录」——前者由本模块的计数
说明，而非报「查到 0 条」。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, NamedTuple

__all__ = [
    "UnmappedCount",
    "filter_unmapped",
    "master_codes",
]


class UnmappedCount(NamedTuple):
    """一次过滤的未收录统计（`filtered_unmapped` 载荷的机器可读形态）。"""

    rows_filtered: int
    """被过滤的**行数**。"""

    codes_distinct: int
    """被过滤的**不同代码数**。"""

    sample: tuple[str, ...]
    """样例代码（去重、有上限，供诊断；不无界增长）。"""

    @property
    def is_clean(self) -> bool:
        """全部落在主档内（无过滤）——同步可记 ``ok`` 而非 ``partial``。"""
        return self.rows_filtered == 0

    def describe(self) -> str:
        """人可读说明（进 ``sync_state.last_error`` 与载荷 ``reason``）。"""
        if self.is_clean:
            return ""
        shown = "、".join(self.sample)
        more = "" if self.codes_distinct <= len(self.sample) else " 等"
        return (f"过滤未收录代码 {self.codes_distinct} 个（{self.rows_filtered} 行）："
                f"{shown}{more}——不在 security 主档内，不入库（见 D-032）")


def master_codes(db) -> frozenset[str] | None:
    """取 ``security`` 主档的全部代码；**主档不可用 → ``None``**。

    刻意**不**返回空集——那会让下游「全部过滤掉」，把「主档缺失」伪装成
    「源端无数据」（正是 D-032 要防的误读）。调用方拿到 ``None`` 应中止并走
    ``unavailable``。
    """
    env = db.query("SELECT code FROM security")
    if env.status != "ok":
        return frozenset() if env.status == "empty" else None
    return frozenset(str(r["code"]) for r in env.data["rows"])


def filter_unmapped(rows: Iterable[Any], code_of: Callable[[Any], str],
                    known: frozenset[str], *,
                    sample_limit: int = 20) -> tuple[list[Any], UnmappedCount]:
    """按主档成员过滤行；返回 ``(保留行, 未收录计数)``。

    :param known: :func:`master_codes` 的结果（**须非 ``None``**，调用方先判）
    :param code_of: 从一行取代码（元组用 ``itemgetter``，字典用 ``itemgetter``）
    """
    kept: list[Any] = []
    sample: list[str] = []
    seen: set[str] = set()
    rows_filtered = 0
    for row in rows:
        code = str(code_of(row))
        if code in known:
            kept.append(row)
            continue
        rows_filtered += 1
        if code not in seen:
            seen.add(code)
            if len(sample) < sample_limit:
                sample.append(code)
    return kept, UnmappedCount(rows_filtered, len(seen), tuple(sample))
