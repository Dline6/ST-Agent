"""L6 反思演进（Reflection Loop）的失败形态（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）。

按失败**成因**分类，调用方据此分流——**不合并成一个泛化错误**：

- :class:`FeedbackPoolError`——**反思数据池**（[08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  `FeedbackRecorded` 负载不合 [08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md) 六字段契约
  （缺字段 / `target.ref` 不合 [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 形态 /
  `rejected` 缺 `reason` / 时间不带时区）· 池内文件不可解析。
  **拒收即记因、损坏即抛**——空池会被周报读成「本周无反馈」，正是静默失败。
- :class:`WeeklyReportError`——**每周反思报告**（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  周键形态非法 · 时段 / 阈值 / 模板条目越界 · 落盘报告形态损坏。
  越界即拒、不落盘不留痕（同 [L5 各面](../../../src/st_agent/l5/errors.py) 的取向）。

**文案失败**（未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 中性化校验）复用
:class:`WeeklyReportError`——它是本层唯一的生成性文案面（反驳：不该为一种成因单立类型）。
"""

from __future__ import annotations

__all__ = [
    "FeedbackPoolError",
    "L6Error",
    "WeeklyReportError",
]


class L6Error(Exception):
    """反思演进面的基类（本层自有的失败形态，不外泄 pydantic 的内部结构）。"""


class FeedbackPoolError(L6Error):
    """反思数据池的负载不合约 / 池内文件损坏。"""


class WeeklyReportError(L6Error):
    """每周反思报告的周键 / 条目 / 落盘形态越界（含文案未过 01 §6）。"""
