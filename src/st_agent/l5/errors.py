"""L5 主动触达（Ambient Delivery）的失败形态（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）。

按失败**成因**分类，调用方据此分流——**不合并成一个泛化错误**：

- :class:`SignalAdoptionError`——**采纳侧**失败（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）：
  事件名不是 `SignalEmitted` · 负载缺字段 / 字段类型或取值非法（含非 §1 四类的证据引用）
  · 事件缺 `trace_id` · 结论或逐视角摘要未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
  中性化校验。**不产出半截信号、不臆补缺失字段**。
- :class:`BudgetValidationError`——**注意力预算**配置越界（[07 §2](../../../docs/技术架构-v2/07-L5-主动触达.md)）：
  未知级别 / 内容类型 / 时段 id · 时钟区间非法或时段集合未覆盖全天、相互重叠 ·
  空允许级别集 · 节奏上限 ≤0。越界即拒，**不静默落一个永不命中的格位**。
"""

from __future__ import annotations

__all__ = [
    "BudgetValidationError",
    "L5Error",
    "SignalAdoptionError",
]


class L5Error(Exception):
    """主动触达面的基类（本层自有的失败形态，不外泄 pydantic 的内部结构）。"""


class SignalAdoptionError(L5Error):
    """`SignalEmitted` 负载不合约（缺字段 / 取值非法 / 未过中性化校验）。"""


class BudgetValidationError(L5Error):
    """注意力预算配置越界（条目不落盘、不留痕）。"""
