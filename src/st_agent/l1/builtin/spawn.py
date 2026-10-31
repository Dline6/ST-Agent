"""``spawn_agent`` 内置原语（T-AGT-018.2；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

让主循环把一个**探索型子任务**交给隔离子上下文去跑，只回收结论与引用。三条口径：

- **派生是循环控制动作**——由 L3 拦截执行（同 ``sk_plan_update`` / ``sk_ask_user``），
  不产 Trace 步、不产 ``skill_run`` 记录；子循环自己的动作**另起一条 Trace**，不并入主链
  （[01 §4](../../../docs/技术架构-v2/01-平台共享契约.md)）。
- **只允许一层**——子循环的工具目录剔除本原语；子循环内再派生一律按未知工具显式终止，
  不做 Agent 图 / 角色 / 消息板。
- **自身无副作用**——不改盘、不出网、不起命令；子循环内的每一步仍逐步过同一闸门与
  同一 L1 流水线。故出厂风险清单显式登记为 ``autonomous-ok``（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

``normalize_spawn_task`` 由 L3 循环复用（L3 向下 import L1，[铁律 7](../../../项目管理/工程宪法.md)），
故「什么算合法子任务」只此一处。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["MAX_SUBAGENT_TASK_CHARS", "SPAWN_AGENT", "normalize_spawn_task"]

MAX_SUBAGENT_TASK_CHARS = 2000
"""子任务描述的长度上限（防把整段上下文塞进一次派生；超出即显式拒绝，不静默截断）。"""

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"task": {"type": "string"}},
    "required": ["task"],
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string"},
        "agent_run_id": {"type": "string"},
        "trace_id": {"type": "string"},
        "termination": {"type": "string"},
        "answer": {"type": "string"},
        "evidence": {"type": "array"},
    },
}


def normalize_spawn_task(raw: Any) -> tuple[str, str]:
    """把模型给的子任务归一化成文本；不合法即 ``(\"\", 原因)``。

    空 / 非字符串 / 超长都**不猜**——调用方把原因写回工作上下文让模型下一轮纠正，
    到上界仍未纠正即按上界终止（同计划原语的「形态非法不终止」口径）。
    """
    if raw is None:
        return "", "spawn_agent 缺少 task（子任务描述不得为空）"
    if not isinstance(raw, str):
        return "", f"spawn_agent 的 task 须为字符串，得到 {type(raw).__name__}"
    text = raw.strip()
    if not text:
        return "", "spawn_agent 的 task 不得为空白串"
    if len(text) > MAX_SUBAGENT_TASK_CHARS:
        return "", (
            f"spawn_agent 的 task 长度 {len(text)} 超过上限 {MAX_SUBAGENT_TASK_CHARS}"
            "（不静默截断；请给自洽的简短子任务描述）"
        )
    return text, ""


def _executor_factory(_context: Any) -> Any:
    """直接调用时的执行器（循环内由 L3 拦截，不走本函数）。

    派生需要循环的协议端口、执行面与预算，L1 执行器拿不到这些句柄；故直接调用
    **显式不可用**，不假跑一个没有上下文的「子 Agent」。
    """

    def execute(_ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        return ResultEnvelope.validation_failed(
            "spawn_agent 只能在自主查证循环内使用（由 L3 拦截并派生子循环）；"
            "直接调用不派生，也不伪造一个子 Agent 的结果"
        )

    return execute


SPAWN_AGENT = BuiltinPrimitive(
    base="sk_spawn_agent",
    name="派生子 Agent",
    description=(
        "把一段自洽的探索型子任务交给隔离子上下文执行，只取回结论与引用；"
        "子上下文不进入主窗，子任务内不能再派生子 Agent（仅一层）"
    ),
    input_schema=_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=(),
    permissions=(),
    offline_level="full",
    requires=(),
    executor=_executor_factory,
)
