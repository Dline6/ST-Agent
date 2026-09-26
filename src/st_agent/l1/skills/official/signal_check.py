"""未来函数检测（T-L1-004.3；03 §2.2 ``strategy-design`` 特殊契约）。

「用户设计的信号若依赖未来数据，校验即报错并指出问题」——本模块是**公开纯函数**
（独立于执行器可调用、可单测）：输入一份信号定义，输出逐字段的检测结论。

**信号定义形态**（03 §2.2 只写要求、未给格式，故由此处定义，见假设 A1）：

```python
{"fields": ["close", "roe_avg"],          # 信号引用的数据字段（必填）
 "align": {"close": "trade_date",         # 逐字段的对齐口径（必填）
           "roe_avg": "stat_date"}}
```

**判据**：每个字段有一个「最早可得时点」（:data:`FIELD_AVAILABILITY`）——
行情字段随当日日线可得（``trade_date``），财务 / 公司报告字段须到披露日才可得
（``pub_date`` / ``update_date``）。若信号把字段对齐到**早于**该时点的口径
（如财务字段对齐到报告期 ``stat_date``），即在该数据尚不可得时使用了它——判为
未来函数并逐条指出。缺失 ``align`` 声明同样报错（无从判定即不放行）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ALIGN_RANK",
    "FIELD_AVAILABILITY",
    "SignalCheck",
    "SignalIssue",
    "check_future_function",
]

FIELD_AVAILABILITY: dict[str, str] = {
    # 行情域：当日日线收盘后可得
    "open": "trade_date", "high": "trade_date", "low": "trade_date",
    "close": "trade_date", "preclose": "trade_date", "turn": "trade_date",
    "amount": "trade_date", "pct_chg": "trade_date",
    "pe_ttm": "trade_date", "pb_mrq": "trade_date",
    # 财务域：定期报告披露后可得
    "roe_avg": "pub_date", "np_margin": "pub_date", "gp_margin": "pub_date",
    "net_profit": "pub_date", "mb_revenue": "pub_date",
    "liability_to_asset": "pub_date", "yoy_ni": "pub_date",
    # 公司报告域：披露 / 更新后可得
    "forecast_type": "pub_date", "net_asset": "update_date",
}

ALIGN_RANK: dict[str, int] = {
    "stat_date": 0,      # 报告期（数据本体所属期，尚未披露）
    "trade_date": 1,     # 交易日收盘后
    "pub_date": 1,       # 披露日
    "update_date": 1,    # 更新日
}
"""对齐口径的时点序（越小越早）——判据只拦「早于可得时点」，不拦更晚的对齐。"""

SignalIssueKind = Literal["future_function", "missing_align", "unknown_field", "unknown_align"]


class SignalIssue(BaseModel):
    """一条信号定义问题（逐条指出，供用户按字段修正）。"""

    model_config = ConfigDict(frozen=True)

    field: str
    kind: SignalIssueKind
    detail: str


class SignalCheck(BaseModel):
    """信号定义校验结论（``passed=None`` 表「未提供信号定义」，不假装通过）。"""

    model_config = ConfigDict(frozen=True)

    passed: bool | None
    issues: tuple[SignalIssue, ...] = ()
    reason: str = ""

    @classmethod
    def not_provided(cls) -> "SignalCheck":
        """未提供信号定义时的结论——显式标注，不用「通过」冒充。"""
        return cls(passed=None, reason="未提供信号定义")


def check_future_function(spec: Mapping[str, Any] | None) -> SignalCheck:
    """检测一份信号定义是否含未来函数 → :class:`SignalCheck`。

    ``spec`` 为 ``None`` → :meth:`SignalCheck.not_provided`（不判通过）。
    """
    if spec is None:
        return SignalCheck.not_provided()
    fields = list(spec.get("fields") or ())
    align = dict(spec.get("align") or {})
    if not fields:
        return SignalCheck(
            passed=False,
            issues=(SignalIssue(field="", kind="unknown_field",
                                detail="信号定义未声明任何字段（fields 为空）"),),
        )
    issues: list[SignalIssue] = []
    for field in fields:
        available = FIELD_AVAILABILITY.get(field)
        if available is None:
            issues.append(
                SignalIssue(field=field, kind="unknown_field",
                            detail=f"未知字段 {field!r}（无可得时点口径，无法判定）")
            )
            continue
        declared = align.get(field)
        if declared is None:
            issues.append(
                SignalIssue(field=field, kind="missing_align",
                            detail=f"字段 {field!r} 未声明对齐口径（须显式给出可得时点 {available}）")
            )
            continue
        if declared not in ALIGN_RANK:
            issues.append(
                SignalIssue(field=field, kind="unknown_align",
                            detail=f"字段 {field!r} 的对齐口径 {declared!r} 非法")
            )
            continue
        known = ALIGN_RANK[available]
        if ALIGN_RANK[declared] < known:
            issues.append(
                SignalIssue(
                    field=field, kind="future_function",
                    detail=(f"字段 {field!r} 最早在 {available} 可得，"
                            f"信号却对齐到 {declared} —— 在该数据可得前使用了它"),
                )
            )
    return SignalCheck(passed=not issues, issues=tuple(issues))
