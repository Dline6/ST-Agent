"""L5 主动触达门面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeliveryFacade:
    """L5 主动触达面：持注意力预算 / 渠道策略 / 投递编排 / 日报 / 疲劳 / 反馈采集面。

    :param budget: 注意力预算门面（[07 §2]；对象类条目「格位 / 时段」的**写面**）
    :param policies: 渠道偏好门面（[07 §3]；渠道链的**写面**）
    :param delivery: 投递编排（推送历史留痕与询问推送的读面）
    :param reports: 每日报告门面（[07 §5]）
    :param fatigue: 推送疲劳监控门面（[07 §7]；逐类计数 / 静音 / 答复的读面）
    :param feedback: L3 反馈采集面的 `record` 绑定（[01 §1]：`feedback_id` 产生方＝交互层）
        ——疲劳「调整询问」的答复经它铸 `FeedbackEvent` 并发布 `FeedbackRecorded`（[07 §7]）；
        与 `api_reflection_feedback` 走的是**同一个**采集面，不另造反馈写通道
    :param store: 本地存储句柄——只为**列出已生成的日报**（`execution_log/daily_report/` 前缀，
        同 SkillsFacade 的 `skill-run/` 列表读法），不用于别的落盘
    """

    budget: Any
    policies: Any = None
    delivery: Any = None
    reports: Any = None
    fatigue: Any = None
    feedback: Any = None
    store: Any = None
