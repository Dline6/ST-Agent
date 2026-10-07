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
- :class:`TrainingError`——**训练对话协议**（[08 §3](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  会话**未经用户确认**即落账 · 修正说明缺失 · 理解端口返回非法结构 · 会话落盘损坏。
- :class:`ProposalError`——**主动提案与 A/B 实验**（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  阈值 / 频率上限条目越界 · 观察面返回非法结构 · 提案落盘损坏 · 实验范围不在低风险清单内。
- :class:`ExperimentError`——实验的启用 / 结算 / 判定面（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  范围不合法 · 实验不存在 · 落盘形态损坏。
- :class:`AuthorizationError`——演进授权档位与风险分级清单（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  档位取值非法 · 风险分级规则形态非法 / 前缀重复 / 清单为空 · 条目落盘损坏。
- :class:`ChangeFlowError`——变更流与回滚（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) / [§6](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  提案形态非法 · 待批准项不存在 · 未接判据 / 未接门面即要求生效 · 变更落盘损坏。
- :class:`FactoryResetError`——出厂重置（[08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md)）：
  确认次数不足 · 重置留痕损坏 · 未接回放面。

**文案失败**（未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 中性化校验）复用**各自面**的错误类型
（周报文案 → :class:`WeeklyReportError`，训练对话文案 → :class:`TrainingError`，以此类推）——
生成性文案面自本层第二批起不止一处，按面归类比汇到一个类型更贴合「按失败成因分类」的取向。
"""

from __future__ import annotations

__all__ = [
    "AuthorizationError",
    "ChangeFlowError",
    "ExperimentError",
    "FactoryResetError",
    "FeedbackPoolError",
    "L6Error",
    "ProposalError",
    "TrainingError",
    "WeeklyReportError",
]


class L6Error(Exception):
    """反思演进面的基类（本层自有的失败形态，不外泄 pydantic 的内部结构）。"""


class FeedbackPoolError(L6Error):
    """反思数据池的负载不合约 / 池内文件损坏。"""


class WeeklyReportError(L6Error):
    """每周反思报告的周键 / 条目 / 落盘形态越界（含文案未过 01 §6）。"""


class TrainingError(L6Error):
    """训练对话协议：未确认即落账 / 输入缺失 / 端口结构非法 / 落盘损坏（含文案未过 01 §6）。"""


class ProposalError(L6Error):
    """主动提案：条目越界 / 观察面结构非法 / 提案落盘损坏（含文案未过 01 §6）。"""


class ExperimentError(L6Error):
    """A/B 实验：范围不在低风险清单内 / 实验不存在 / 落盘形态损坏（含文案未过 01 §6）。"""


class AuthorizationError(L6Error):
    """演进授权：档位非法 / 风险分级规则非法 / 条目落盘损坏。"""


class ChangeFlowError(L6Error):
    """变更流：提案形态非法 / 待批准项不存在 / 未接判据或门面即要求生效 / 变更落盘损坏。"""


class FactoryResetError(L6Error):
    """出厂重置：确认次数不足 / 重置留痕损坏 / 未接回放面。"""
