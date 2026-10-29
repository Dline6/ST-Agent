"""``plan_update`` 内置原语（T-AGT-011.1；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

让模型显式维护本次查证的步骤计划，供工作上下文渲染与 ``agent_run`` 留痕。三条口径：

- **计划是决策**——不产 Trace 步、不产 ``skill_run`` 记录（[01 §4](../../../docs/技术架构-v2/01-平台共享契约.md)）；
  本原语在循环内由 L3 **拦截**执行，执行器只在被直接调用时做同一份归一化。
- **空计划即显式清空**——``plan=[]`` 表示「清空」，与「尚未给出计划」在渲染上分开。
- **形态非法不猜**——非数组 / 步缺 ``title`` / 状态不在枚举一律给原因，由调用方决定
  终止还是让模型纠正；本模块只做**判定与归一化**，不改写模型给的内容。

``normalize_plan`` 由 L3 循环复用（L3 向下 import L1，[铁律 7](../../../项目管理/工程宪法.md)），
故「什么算合法计划」只此一处。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["DEFAULT_MAX_PLAN_STEPS", "PLAN_STATUSES", "PLAN_UPDATE", "normalize_plan"]

PLAN_STATUSES: tuple[str, ...] = ("pending", "active", "done")
"""计划步骤的状态枚举（与任务账本的 todo / doing / done 同序，但**不复用**其字面量）。"""

DEFAULT_MAX_PLAN_STEPS = 12
"""单次计划的步数上限（防跑飞的实现口径；超出即显式拒绝，不静默截断）。"""

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {"type": "array"},
        "count": {"type": "integer"},
        "cleared": {"type": "boolean"},
    },
}

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
}


def normalize_plan(raw: Any) -> tuple[tuple[dict[str, str], ...], str]:
    """把模型给的计划归一化成 ``({title, status}, …)``；不合法即 ``((), 原因)``。

    ``default`` 状态取 ``pending``（模型省略状态即「未开始」）；不合法时**不返回半个计划**
    ——调用方要么整份采用、要么整份当错误处理，不拿半份计划去渲染。
    """
    if raw is None:
        return (), "plan_update 缺少 plan（空计划请显式给空数组）"
    if not isinstance(raw, (list, tuple)):
        return (), f"plan_update 的 plan 须为数组，得到 {type(raw).__name__}"
    if len(raw) > DEFAULT_MAX_PLAN_STEPS:
        return (), (
            f"plan_update 的计划步数 {len(raw)} 超过上限 {DEFAULT_MAX_PLAN_STEPS}"
            "（不静默截断；请精简后再给）"
        )
    steps: list[dict[str, str]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return (), f"plan_update 第 {index} 步须为对象，得到 {type(item).__name__}"
        title = str(item.get("title") or "").strip()
        if not title:
            return (), f"plan_update 第 {index} 步缺少 title（步骤名不得为空）"
        status = str(item.get("status") or "pending").strip()
        if status not in PLAN_STATUSES:
            return (), (
                f"plan_update 第 {index} 步的 status {status!r} 不在枚举 "
                f"{PLAN_STATUSES} 内"
            )
        steps.append({"title": title, "status": status})
    return tuple(steps), ""


def _executor_factory(_context: Any) -> Any:
    """直接调用时的执行器（循环内由 L3 拦截，不走本函数）。"""

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        raw = dict(getattr(ctx, "inputs", {}) or {}).get("plan")
        steps, error = normalize_plan(raw)
        if error:
            return ResultEnvelope.validation_failed(error)
        return ResultEnvelope.ok(
            {"plan": list(steps), "count": len(steps), "cleared": len(steps) == 0}
        )

    return execute


PLAN_UPDATE = BuiltinPrimitive(
    base="sk_plan_update",
    name="计划更新",
    description=(
        "维护本次查证的步骤计划（每步一个标题与状态 pending/active/done），"
        "空数组即清空计划；计划只用于组织后续查证，不执行任何动作"
    ),
    input_schema=_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=(),
    permissions=(),
    offline_level="full",
    requires=(),
    executor=_executor_factory,
)
