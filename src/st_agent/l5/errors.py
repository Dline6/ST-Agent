"""L5 主动触达（Ambient Delivery）的失败形态（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）。

按失败**成因**分类，调用方据此分流——**不合并成一个泛化错误**：

- :class:`SignalAdoptionError`——**采纳侧**失败（[07 §1](../../../docs/技术架构-v2/07-L5-主动触达.md)）：
  事件名不是 `SignalEmitted` · 负载缺字段 / 字段类型或取值非法（含非 §1 四类的证据引用）
  · 事件缺 `trace_id` · 结论或逐视角摘要未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
  中性化校验。**不产出半截信号、不臆补缺失字段**。
- :class:`BudgetValidationError`——**注意力预算**配置越界（[07 §2](../../../docs/技术架构-v2/07-L5-主动触达.md)）：
  未知级别 / 内容类型 / 时段 id · 时钟区间非法或时段集合未覆盖全天、相互重叠 ·
  空允许级别集 · 节奏上限 ≤0。越界即拒，**不静默落一个永不命中的格位**。
- :class:`ChannelValidationError`——**渠道偏好**配置越界（[07 §3](../../../docs/技术架构-v2/07-L5-主动触达.md)）：
  未知级别 / 未知渠道 / 空链 / 链内重复 / 未读等待与链长不匹配或时长 ≤0。
  越界即拒，**不静默落一个永不生效的渠道链**。
- :class:`ChannelNotWiredError`——**渠道未接线**（无适配器、或适配器缺原生能力 /
  传输端口）：显式点名缺口，**不假装能投递**。
- :class:`DeliveryValidationError`——**投递编排**的入参不合法（[07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md)）：
  负等待 / 未知渠道 / 时刻非法 / 未读对象不存在。留痕与升级链的形态校验失败同走此型。
- :class:`FrequencyValidationError`——**去重与频控**的计数状态 / 条目越界或损坏
  （[07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md)）：窗口时长 ≤0 · 计数状态不可解析。
  **损坏即抛**——空表会被读成「没有超限」，正是静默失败（[00 §6](../../../docs/技术架构-v2/00-架构总览.md)）。
- :class:`DailyReportValidationError` / :class:`FatigueValidationError`——**每日报告**与
  **推送疲劳监控**的条目 / 状态 / 答复越界（[07 §5](../../../docs/技术架构-v2/07-L5-主动触达.md) /
  [§7](../../../docs/技术架构-v2/07-L5-主动触达.md)）：越界即拒、不落盘不留痕（同预算 / 渠道偏好的取向）。
"""

from __future__ import annotations

__all__ = [
    "BudgetValidationError",
    "ChannelNotWiredError",
    "ChannelValidationError",
    "DailyReportValidationError",
    "DeliveryValidationError",
    "FatigueValidationError",
    "FrequencyValidationError",
    "L5Error",
    "SignalAdoptionError",
    "SignalEmissionError",
]


class L5Error(Exception):
    """主动触达面的基类（本层自有的失败形态，不外泄 pydantic 的内部结构）。"""


class SignalAdoptionError(L5Error):
    """`SignalEmitted` 负载不合约（缺字段 / 取值非法 / 未过中性化校验）。"""


class BudgetValidationError(L5Error):
    """注意力预算配置越界（条目不落盘、不留痕）。"""


class ChannelValidationError(L5Error):
    """渠道偏好配置越界（条目不落盘、不留痕）。"""


class ChannelNotWiredError(L5Error):
    """渠道未接线（缺适配器 / 缺原生能力端口 / 缺传输端口）——显式点名缺口。"""


class DeliveryValidationError(L5Error):
    """投递编排入参或留痕形态不合法。"""


class FrequencyValidationError(L5Error):
    """去重与频控的计数状态 / 条目越界或损坏（**损坏即抛**，不返回空表）。"""


class DailyReportValidationError(L5Error):
    """每日报告的模板条目 / 报告形态越界。"""


class FatigueValidationError(L5Error):
    """推送疲劳监控的阈值条目 / 计数状态 / 答复越界。"""


class SignalEmissionError(L5Error):
    """上游产出 → `SignalEmitted` 的产生规则求值失败（缺字段 / 条目非映射 / 无溯源锚点 /
    生成的文案未过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)）——**不把不合规的信号投出去**。"""
