"""``ask_user`` 内置原语（T-AGT-011.2；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

循环发现缺少关键参数、或无法在现有信息下收敛时，模型调用本原语**终止并交还结构化问项**。
三条口径（与 [05 §3.2](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的澄清协议同源、**不复用**
其 ``ClarificationQuestion`` 类型——那个属派发前澄清）：

- **一问一参**——每问 ``parameter`` + ``prompt``，可选 ``choices`` 与 ``default``；
- **预算 ≤ 3 问**——多给的问项保留前 3 条、其余**显式记入**说明（不静默丢）；
- **不强行猜**——问项形态非法（缺参名 / 缺问句，或零问项且无方向候选）即显式给原因，
  由调用方终止交还，不臆造问项、不代填默认值。

``normalize_questions`` 由 L3 循环复用（L3 向下 import L1），故「什么算合法问项」只此一处。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = [
    "ASK_USER",
    "MAX_ASK_QUESTIONS",
    "NormalizedAsk",
    "normalize_questions",
]

MAX_ASK_QUESTIONS = 3
"""单次提问的问项上限（[05 §3.2](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的澄清预算）。"""

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "prompt": {"type": "string"},
                    "choices": {"type": "array"},
                },
                "required": ["parameter", "prompt"],
            },
        },
        "directions": {"type": "array", "items": {"type": "string"}},
    },
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {"type": "array"},
        "directions": {"type": "array"},
        "note": {"type": "string"},
    },
}


class NormalizedAsk:
    """一次提问的归一化产物（不可变；供回执与留痕共用）。"""

    __slots__ = ("questions", "directions", "note", "error")

    def __init__(
        self,
        *,
        questions: tuple[dict[str, Any], ...] = (),
        directions: tuple[str, ...] = (),
        note: str = "",
        error: str = "",
    ) -> None:
        self.questions = questions
        self.directions = directions
        self.note = note
        self.error = error

    @property
    def ok(self) -> bool:
        return not self.error

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"NormalizedAsk(questions={len(self.questions)}, "
            f"directions={len(self.directions)}, error={self.error!r})"
        )


def _questions_of(raw: Any) -> tuple[tuple[dict[str, Any], ...], str]:
    """问项数组的归一化；不合法即 ``((), 原因)``。"""
    if raw is None:
        return (), ""
    if not isinstance(raw, (list, tuple)):
        return (), f"ask_user 的 questions 须为数组，得到 {type(raw).__name__}"
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return (), f"ask_user 第 {index} 个问项须为对象，得到 {type(item).__name__}"
        parameter = str(item.get("parameter") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not parameter or not prompt:
            return (), f"ask_user 第 {index} 个问项缺少 parameter 或 prompt"
        question: dict[str, Any] = {"parameter": parameter, "prompt": prompt}
        choices = item.get("choices")
        if choices is not None:
            if not isinstance(choices, (list, tuple)):
                return (), f"ask_user 第 {index} 个问项的 choices 须为数组"
            cleaned = tuple(str(choice).strip() for choice in choices if str(choice).strip())
            if cleaned:
                question["choices"] = list(cleaned)
        if "default" in item and item["default"] is not None:
            question["default"] = item["default"]
        out.append(question)
    return tuple(out), ""


def _directions_of(raw: Any) -> tuple[tuple[str, ...], str]:
    """方向候选的归一化；不合法即 ``((), 原因)``。"""
    if raw is None:
        return (), ""
    if not isinstance(raw, (list, tuple)):
        return (), f"ask_user 的 directions 须为数组，得到 {type(raw).__name__}"
    out = tuple(str(item).strip() for item in raw if str(item).strip())
    return out, ""


def normalize_questions(
    raw_questions: Any, raw_directions: Any = None
) -> NormalizedAsk:
    """把模型给的问项与方向候选归一化（保留前 :data:`MAX_ASK_QUESTIONS` 条并记说明）。

    返回值三态：``error`` 非空即形态非法；否则 ``questions`` / ``directions`` 可直接交还，
    ``note`` 记录被截断的问项（有则非空）——**不静默丢**。
    """
    questions, error = _questions_of(raw_questions)
    if error:
        return NormalizedAsk(error=error)
    directions, error = _directions_of(raw_directions)
    if error:
        return NormalizedAsk(error=error)
    note = ""
    if len(questions) > MAX_ASK_QUESTIONS:
        dropped = [item["parameter"] for item in questions[MAX_ASK_QUESTIONS:]]
        questions = questions[:MAX_ASK_QUESTIONS]
        note = (
            f"模型给出 {len(questions) + len(dropped)} 个问项，超过澄清预算 "
            f"{MAX_ASK_QUESTIONS}；已保留前 {MAX_ASK_QUESTIONS} 项，"
            f"其余未展示：{'、'.join(dropped)}"
        )
    if not questions and not directions:
        return NormalizedAsk(
            error="ask_user 既没有有效问项也没有方向候选（不臆造问项，按显式交还处理）"
        )
    return NormalizedAsk(questions=questions, directions=directions, note=note)


def _executor_factory(_context: Any) -> Any:
    """直接调用时的执行器（循环内由 L3 拦截，不走本函数）。"""

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        inputs = dict(getattr(ctx, "inputs", {}) or {})
        ask = normalize_questions(inputs.get("questions"), inputs.get("directions"))
        if not ask.ok:
            return ResultEnvelope.validation_failed(ask.error)
        return ResultEnvelope.ok(
            {
                "questions": list(ask.questions),
                "directions": list(ask.directions),
                "note": ask.note,
            }
        )

    return execute


ASK_USER = BuiltinPrimitive(
    base="sk_ask_user",
    name="向用户提问",
    description=(
        "缺少关键参数或无法收敛时，以结构化问项终止查证并交还用户；"
        "一问一参（参名 + 问句），最多 3 问，可带取值域与方向候选"
    ),
    input_schema=_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=(),
    permissions=(),
    offline_level="full",
    requires=(),
    executor=_executor_factory,
)
