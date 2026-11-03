"""结果截断与摘要的**唯一判定**（[`T-AGT-026.1`](../../../../项目管理/tasks/T-AGT-026.1-步骤流与展开详情.md)）。

三条消费面共用同一份口径，避免各算一套：

- 工作上下文装配（[`context.py`](context.py) 的 ``_apply_truncation``）——超阈值换截断形态；
- 运行事件（[`loop.py`](loop.py) 的 ``step_finished``）——标 ``truncated`` 并给结果摘要；
- 循环留痕与终轮证据表（[`report.py`](report.py) / 描述件）——同一判定标「结果已截断」。

阈值**由本轮预算派生**（[`T-AGT-033.5`](../../../项目管理/tasks/T-AGT-033.5-截断回读与预览派生.md)）：
单条上限 = ``clamp(步骤预算 / 预期剩余步数, 400, 4000)``，预览 = ``min(400, 上限/3)``，
不再写死 1200 / 200。本模块**只**依赖契约层的信封模型（无 L1 / L2 / 循环内模块的 import），
故可被 ``render`` 侧直接引用而不成环（``report`` 反向消费 ``render``）。
"""

from __future__ import annotations

import json
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope

__all__ = [
    "RESULT_SUMMARY_CHARS",
    "RESULT_TRUNCATE_MAX",
    "RESULT_TRUNCATE_MIN",
    "derive_result_chars",
    "json_text",
    "preview_chars_for",
    "result_will_truncate",
    "summarize_result",
]

RESULT_TRUNCATE_MIN = 400
"""单条结果上限的下限（字符）——再紧也要让模型看清一条证据的头部。"""

RESULT_TRUNCATE_MAX = 4000
"""单条结果上限的上限（字符）——再多就该用 ``sk_result_read`` 分片回读。"""

RESULT_SUMMARY_CHARS = 200
"""运行面板的结果摘要长度（字符数）——只够判断拿到了什么，不替代完整结果。"""

PREVIEW_MAX_CHARS = 400
"""截断预览的上限（字符数；实际取 ``min(400, 单条上限/3)``）。"""


def derive_result_chars(step_budget_chars: int, remaining_steps: int) -> int:
    """由**本轮步骤预算**与**预期剩余步数**派生单条结果上限（确定性）。"""
    budget = max(1, int(step_budget_chars))
    steps = max(1, int(remaining_steps))
    return max(RESULT_TRUNCATE_MIN, min(RESULT_TRUNCATE_MAX, budget // (steps + 1)))


def preview_chars_for(max_result_chars: int) -> int:
    """截断预览长度：``min(400, 单条上限/3)``（随预算变化，不写死）。"""
    return max(1, min(PREVIEW_MAX_CHARS, int(max_result_chars) // 3))


def json_text(value: Any) -> str:
    """确定性的 JSON 文本（排序键 + 不转义非 ASCII），供 prompt 与规模口径共用。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)


def result_will_truncate(envelope: ResultEnvelope, max_result_chars: int) -> bool:
    """该结果进上下文时是否会被截断（三处消费面的**同一判定**）。"""
    if envelope.status != "ok":
        return False                      # 非 ok 无载荷（01 §5），不存在截断体量
    return len(json_text(envelope.data)) > max_result_chars


def summarize_result(envelope: ResultEnvelope) -> str:
    """结果的一行摘要（≤:data:`RESULT_SUMMARY_CHARS`）：非 ok 取原因，ok 取载荷头部。

    只是**指向性的摘要**——完整结果仍只在 ``execution_log`` 的 ``skill_run`` 记录里，
    消费方据 ``log_ref`` 下钻，不把摘要当完整结果（同 TruncatedResult 的取向）。
    """
    if envelope.status != "ok":
        return (envelope.reason or "").strip()
    if envelope.data is None:
        return ""
    text = json_text(envelope.data)
    return text if len(text) <= RESULT_SUMMARY_CHARS else text[:RESULT_SUMMARY_CHARS] + "…"
