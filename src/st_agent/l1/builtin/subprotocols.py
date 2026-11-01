"""五个子协议动作的内置描述体（[T-AGT-030](../../../项目管理/tasks/T-AGT-030-子协议动作化.md)；
[05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

`configure` / `memory_op` / `analyze` / `train` / `explain` 作为循环可调用的动作进入
**同一个**工具目录（模型可见面＝注册面，[01 §2](../../../docs/技术架构-v2/01-平台共享契约.md)）。
它们是**控制动作**，与 `sk_plan_update` / `sk_ask_user` / `sk_spawn_agent` 同型：

- 由 L3 在循环内**拦截承接**（`l3/runtime/subprotocols.py`），**不产 `skill_run` / Trace 步**；
- 直接调用（不经循环）一律回 ``unavailable`` 并点名循环——不在循环外另开第二条执行路径；
- 只产卡 / 读卡 / 产未确认复述，不落值；真正的写入仍走各自的既有确认路径。

描述体即模型可见形态：五条描述都写明「只产 / 只读、不落值」，避免模型把动作当成写入。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = [
    "ANALYZE_TOOL",
    "CONFIGURE_TOOL",
    "EXPLAIN_TOOL",
    "MEMORY_OP_TOOL",
    "SUB_PROTOCOL_PRIMITIVES",
    "TRAIN_TOOL",
]

CONFIGURE_TOOL = "sk_configure"
MEMORY_OP_TOOL = "sk_memory_op"
ANALYZE_TOOL = "sk_analyze"
TRAIN_TOOL = "sk_train"
EXPLAIN_TOOL = "sk_explain"

_CARD_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "card": {"type": "object"},
        "cards": {"type": "array"},
        "count": {"type": "integer"},
    },
}

_DATA_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {"session": {"type": "object"}},
}

_CONFIGURE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "values": {"type": "object"},
        "structure": {"type": "object"},
    },
    "required": ["target"],
}

_MEMORY_OP_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {"conflict_id": {"type": "string"}},
}

_ANALYZE_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "mode": {"type": "string"},
    },
    "required": ["topic"],
}

_TRAIN_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "correction": {"type": "string"},
        "feedback_id": {"type": "string"},
        "target": {"type": "string"},
    },
}

_EXPLAIN_INPUT: dict[str, Any] = {"type": "object", "properties": {}}


def _executor_factory(base: str, _context: Any = None) -> Any:
    """直接调用时的执行器：显式不可用（循环内由 L3 拦截，不走本函数）。"""

    def execute(_ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        return ResultEnvelope.unavailable(
            f"{base} 是循环内的子协议动作，只在自主查证循环内承接"
            "（05 §3.1 子协议动作化）；未在循环内调用，未产出任何卡片"
        )

    return execute


def _primitive(
    *,
    base: str,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
) -> BuiltinPrimitive:
    return BuiltinPrimitive(
        base=base,
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        parameters=(),
        permissions=(),
        offline_level="full",
        requires=(),
        executor=partial(_executor_factory, base),
    )


CONFIGURE = _primitive(
    base=CONFIGURE_TOOL,
    name="配置草稿",
    description=(
        "把一次配置意图收敛为配置草稿卡并交还确认；只产草稿、不落值。"
        "target 为目标 Skill / 工作流，values 为参数取值，structure 为工作流结构（可选）。"
    ),
    input_schema=_CONFIGURE_INPUT,
    output_schema=_CARD_OUTPUT,
)

MEMORY_OP = _primitive(
    base=MEMORY_OP_TOOL,
    name="记忆冲突卡",
    description=(
        "列出一条或全部待裁决的记忆冲突卡并交还确认；只读，不写记忆、不代裁决。"
        "conflict_id 为空即列出全部待裁决项。"
    ),
    input_schema=_MEMORY_OP_INPUT,
    output_schema=_CARD_OUTPUT,
)

ANALYZE = _primitive(
    base=ANALYZE_TOOL,
    name="多视角分析",
    description=(
        "对复杂决策触发多视角分析并产出分歧图卡；只读，不给统一建议。"
        "topic 为待分析主题，mode 为 quick / deep（缺省取 quick）。"
    ),
    input_schema=_ANALYZE_INPUT,
    output_schema=_CARD_OUTPUT,
)

TRAIN = _primitive(
    base=TRAIN_TOOL,
    name="训练对话",
    description=(
        "按训练对话协议把用户的修正收敛为复述与模式（未确认、不落账）。"
        "correction 为修正原话，feedback_id 可指一条带原因的反馈，target 为对象锚点（可选）。"
    ),
    input_schema=_TRAIN_INPUT,
    output_schema=_DATA_OUTPUT,
)

EXPLAIN = _primitive(
    base=EXPLAIN_TOOL,
    name="推理链展开",
    description=(
        "展开本次运行的推理链（动作步、状态与留痕指针）；只读，不展开模型中间推理。"
    ),
    input_schema=_EXPLAIN_INPUT,
    output_schema=_CARD_OUTPUT,
)

SUB_PROTOCOL_PRIMITIVES: tuple[BuiltinPrimitive, ...] = (
    CONFIGURE, MEMORY_OP, ANALYZE, TRAIN, EXPLAIN,
)
"""五条子协议动作的描述体（顺序即账本与测试的固定序）。"""
