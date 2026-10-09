"""`table` 槽的**唯一漏斗**（[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md)；[D-064] 的槽词汇表）。

**为什么要有这个模块**：`table` 的槽形状由产出方与渲染件共同约定（[D-064]），而两处曾
**各自演化**——渲染件（[`web/js/components/table.js`](web/js/components/table.js)）读**键控**
形状，本层六处产出方却给**位置**形状（``columns: ["名"]`` / ``rows: [[值]]``），于是那六张表在
生产面上**全是空的**（2026-10-09 实机实测，[D-105] ①）。形状本就是**渲染面**的权威——
[`l3/render/describe.py`](../../l3/render/describe.py) 的 ``AGENT_RUN_COLUMNS`` 旁注原文即
「``table`` 渲染件按 ``[{key, label}]`` 取值——渲染面是形态的权威」。故本模块把构造点收到
**一处**：形状写在这里一次、各产出方都经它出表，再要漂移就得同时改两处（另有恒常用例把
两侧钉在一起）。

**形状**（键控，不是位置）::

    columns: [{"key": str, "label": str, "kind": "text" | "number" | "direction"}]
    rows:    [{"<key>": value, ...}]

``kind`` 是**列级**的呈现角色，决定渲染件怎么出这一列：

- ``text``（缺省）——原样呈现；
- ``number``——右对齐 + ``--font-num`` + ``tabular-nums``（财务对齐是功能不是风格，
  [13-visual-design §3](../../../docs/PRD-v2-Agent/13-visual-design.md)）；
- ``direction``——**涨跌**。值取**带符号百分数**（或 ``None`` 表示无行情），渲染件据
  [§2.2](../../../docs/PRD-v2-Agent/13-visual-design.md) 产出**三路冗余编码**：颜色 +
  ``▲`` / ``▼`` / ``—`` + 带符号数值。

**编码归渲染面**（[D-105] ②）：产出方只给**数**，不拼成品文案、不判方向。三路因此是
**结构上**同时出现的（同一条分支一次产出），规范 §2.2 的灰阶判据不会退化成一条各页自觉的
纪律。同理，小数位（``digits``，缺省 2）也由渲染面统一取——免得 Python 与 JS 各写一份取整
口径，两处慢慢分叉。

**本模块不做的事**：不校验业务值、不落盘、不引各层——它只把给定的列与行摆成已约定的形状。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "COLUMN_KINDS",
    "DEFAULT_DIGITS",
    "Column",
    "ColumnKind",
    "table_slots",
    "table_text_kinds",
]

ColumnKind = Literal["text", "number", "direction"]
"""列级呈现角色（渲染件 ``table.js`` 的 ``COLUMN_KINDS`` 与之**逐值一致**——由守卫断言）。"""

COLUMN_KINDS: tuple[str, ...] = ("text", "number", "direction")
"""上者的机器可读副本（同 ``ui/registry.py`` 与契约枚举的关系：单一真相源 + 派生副本）。"""

DEFAULT_DIGITS = 2
"""数字 / 涨跌列的小数位缺省（A 股行情的惯例口径）。"""


@dataclass(frozen=True)
class Column:
    """`table` 的一列（键 + 列名 + 呈现角色）。

    ``kind`` 为 ``text`` 时 ``digits`` 不参与、也**不出现**在槽里——小数位只对数字与涨跌列
    有意义，这一点由形状本身表达，不靠约定。
    """

    key: str
    """列键：行对象按它取值，也是渲染件的取值面（``row[column.key]``）。"""
    label: str
    """列名——**生成文案**（过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2）。"""
    kind: ColumnKind = "text"
    digits: int = DEFAULT_DIGITS

    def __post_init__(self) -> None:
        if not self.key or not self.key.isidentifier():
            raise ValueError(f"列键须为稳定标识符（渲染件按名取值），得到 {self.key!r}")
        if self.kind not in COLUMN_KINDS:
            raise ValueError(f"列级呈现角色只认 {COLUMN_KINDS}，得到 {self.kind!r}")
        if self.digits < 0:
            raise ValueError(f"小数位不得为负，得到 {self.digits}")

    def as_slot(self) -> dict[str, Any]:
        """本列在 ``slots.columns`` 里的形状。"""
        slot: dict[str, Any] = {"key": self.key, "label": self.label, "kind": self.kind}
        if self.kind != "text":
            slot["digits"] = self.digits
        return slot


def table_slots(columns: Sequence[Column], rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """按已约定的形状摆好 ``table`` 的 slots。

    行**不做**补键 / 改键：缺列键的单元格由渲染件的取值面（``row[column.key]`` 为
    ``undefined``）落到「无数据」中性呈现，不在这里悄悄补一个空格。
    """
    return {
        "columns": [column.as_slot() for column in columns],
        "rows": [dict(row) for row in rows],
    }


def table_text_kinds() -> dict[str, str]:
    """`table` 的槽文本分栏：列名是**生成文案**、行是**数据展示**（[D-053]）。

    整槽标注、不做同槽混合——行里可能有用户原话（如训练留痕），标 ``generated`` 会把它
    误判为违规，标 ``data`` 又会让列名漏检（[D-064] 的切分）。
    """
    return {"columns": "generated", "rows": "data"}
