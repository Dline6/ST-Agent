"""``sk_budget_request`` 内置原语（[`T-AGT-033.6`](../../../项目管理/tasks/T-AGT-033.6-Agent自调申请与进展检查.md)）。

在硬信封内给模型**自调空间**：申请更多步数 / LLM 调用次数 / 上下文 token。它是
**控制动作**——不产 ``skill_run`` / Trace 步（同计划 / 提问 / 派生子 Agent），由 L3
循环拦截承接；执行器只在被直接调用时做同一份归一化，不自行批准任何预算。

三条口径：

- **只能申请，不能抬高**：总资源边界由运行时信封与双上界定义；本原语只把请求
  归一化成客观字段，批准 / 拒绝由循环给出，并进运行事件与 ``agent_run`` 留痕。
- **在信封内直接批**：请求量不超过当前可用余量即批准；超出则显式拒绝并说明
  还差多少，交还用户决定。
- **形态非法不猜**：三项至少给一项；非整数 / 负值一律显式拒绝。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["BUDGET_REQUEST", "normalize_budget_request"]

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "extra_steps": {
            "type": "integer",
            "minimum": 1,
            "description": "希望额外获得的步数（运行时会与步数上界的剩余量比较）",
        },
        "extra_llm_calls": {
            "type": "integer",
            "minimum": 1,
            "description": "希望额外获得的 LLM 调用次数（同上）",
        },
        "context_tokens": {
            "type": "integer",
            "minimum": 1,
            "description": "希望本轮上下文可用的 token 数（不能超过端点窗口派生的可用量）",
        },
    },
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "extra_steps": {"type": "integer"},
        "extra_llm_calls": {"type": "integer"},
        "context_tokens": {"type": "integer"},
    },
}


def normalize_budget_request(raw: Any) -> tuple[dict[str, int], str]:
    """把申请归一化为 ``{extra_steps, extra_llm_calls, context_tokens}``；非法即 ``({}, 原因)``。

    未给出的项不出现（区别于「申请 0」）；三项都缺即拒绝——没有请求就没有可批的内容。
    """
    if not isinstance(raw, dict):
        return {}, f"sk_budget_request 的参数须为对象，得到 {type(raw).__name__}"
    out: dict[str, int] = {}
    for key in ("extra_steps", "extra_llm_calls", "context_tokens"):
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return {}, f"sk_budget_request 的 {key} 须为 ≥1 的整数，得到 {value!r}"
        out[key] = value
    if not out:
        return {}, "sk_budget_request 至少给一项（extra_steps / extra_llm_calls / context_tokens）"
    return out, ""


def _executor_factory(_context: Any) -> Any:
    """直接调用时的执行器（循环内由 L3 拦截，不走本函数）。"""

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        request, error = normalize_budget_request(dict(getattr(ctx, "inputs", {}) or {}))
        if error:
            return ResultEnvelope.validation_failed(error)
        return ResultEnvelope.ok(request)

    return execute


BUDGET_REQUEST = BuiltinPrimitive(
    base="sk_budget_request",
    name="预算申请",
    description=(
        "在总资源信封内申请更多步数 / LLM 调用次数 / 上下文 token；"
        "运行时在信封内直接批准，超出则显式拒绝并交还用户，不自行抬高总边界"
    ),
    input_schema=_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=(),
    permissions=(),
    offline_level="full",
    requires=(),
    executor=_executor_factory,
)
