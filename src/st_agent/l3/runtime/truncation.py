"""结果截断与摘要的**唯一判定**（[`T-AGT-026.1`](../../../../项目管理/tasks/T-AGT-026.1-步骤流与展开详情.md)）。

三条消费面共用同一份口径，避免各算一套：

- 工作上下文装配（[`context.py`](context.py) 的 ``_apply_truncation``）——超阈值换截断形态；
- 运行事件（[`loop.py`](loop.py) 的 ``step_finished``）——标 ``truncated`` 并给结果摘要；
- 循环留痕与终轮证据表（[`report.py`](report.py) / 描述件）——同一判定标「结果已截断」。

本模块**只**依赖契约层的信封模型（无 L1 / L2 / 循环内模块的 import），故可被
``render`` 侧直接引用而不成环（``report`` 反向消费 ``render``）。
"""

from __future__ import annotations

import json
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope

__all__ = [
    "RESULT_SUMMARY_CHARS",
    "RESULT_TRUNCATE_CHARS",
    "json_text",
    "result_will_truncate",
    "summarize_result",
]

RESULT_TRUNCATE_CHARS = 1200
"""单条结果的截断阈值（字符数；超者截断 + 留指针）。

取值属实现口径：在当前缺省预算（16000 字符）下，单条结果占十分之一以上即无谓挤占后续步骤的位置。
"""

RESULT_SUMMARY_CHARS = 200
"""运行面板的结果摘要长度（字符数）——与截断预览同量级，只够判断拿到了什么，不替代完整结果。"""


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
