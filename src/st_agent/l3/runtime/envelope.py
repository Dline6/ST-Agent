"""运行期**上下文信封**（[`T-AGT-033.1`](../../../项目管理/tasks/T-AGT-033.1-预算信封与计量口径.md)）。

[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的规模预算只保留一条硬边界：
**端点真实窗口 × 安全比例**，扣除输出预留、系统提示词与工具目录后得到本轮可用预算。
信封是**运行期派生值**，不是用户可编辑配置——用户只能封顶（可选）或直接调端点。

计量口径与 :func:`st_agent.l0.llm.estimate_tokens` 同一份（中文保守比例）；本模块
把 token 预算换算成**字符预算**时取「1 字符 ≤ 1 token」的保守下界（``budget_chars =
available_tokens``），因此 :func:`estimate_tokens` 对任何输入都不会低估实际占用。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.l0.llm import estimate_tokens

__all__ = [
    "CATALOG_BUDGET_SHARE",
    "CONTEXT_SAFETY_RATIO",
    "FALLBACK_CONTEXT_WINDOW_TOKENS",
    "MIN_AVAILABLE_TOKENS",
    "OUTPUT_RESERVE_TOKENS",
    "ContextEnvelope",
    "derive_context_envelope",
]

CATALOG_BUDGET_SHARE = 0.45
"""工具目录可占的预算比例（超过即进入惰性态——[`T-AGT-033.2`](../../../项目管理/tasks/T-AGT-033.2-工具目录预算与惰性阈值.md)）。"""

CONTEXT_SAFETY_RATIO = 0.8
"""窗口的安全比例——留出提供方侧模板 / 特殊 token 与估算误差的余量。"""

OUTPUT_RESERVE_TOKENS = 2048
"""输出预留（模型本轮回复与工具调用参数的 token 数）。"""

MIN_AVAILABLE_TOKENS = 1024
"""可用预算的下限——低于它时连最小任务都装不下，直接按超限收口。"""

FALLBACK_CONTEXT_WINDOW_TOKENS = 32768
"""端点能力未知时的保守窗口（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)：
未知即显式标注，不假装知道）。"""

EnvelopeSource = Literal["endpoint", "fallback"]

class ContextEnvelope(BaseModel):
    """一次运行的上下文硬边界与派生预算（纯数据；同输入逐字节一致）。"""

    model_config = ConfigDict(frozen=True)

    window_tokens: int = Field(ge=1)
    """端点声明的真实上下文窗口（未知时为保守 fallback）。"""
    safety_ratio: float = Field(gt=0, le=1)
    output_reserve_tokens: int = Field(ge=0)
    tools_tokens: int = Field(ge=0)
    """工具目录在模型可见面上的 token 估算（端点上限检查与装配预算同源）。"""
    system_prompt_tokens: int = Field(ge=0)
    available_tokens: int = Field(ge=0)
    """扣除输出预留、工具目录与系统提示词后可供任务 / 历史 / 记忆 / 步骤的 token。"""
    budget_chars: int = Field(ge=1)
    """由 ``available_tokens`` 换算的字符预算（1 字符 ≤ 1 token 的保守下界）。"""
    catalog_budget_chars: int = Field(ge=1)
    """工具目录可占的字符配额（``budget_chars × CATALOG_BUDGET_SHARE``；超即惰性态）。"""
    source: EnvelopeSource = "endpoint"
    user_cap_tokens: int = Field(default=0, ge=0)
    """用户显式封顶（0 即不封顶；来自 `investigate.context_budget`）。"""
    note: str = ""
    """显式标注（端点窗口未知 / 用户封顶生效 / 可用预算触底）。"""


def _tokens_for(value: Any) -> int:
    """单条目录 / 提示词的 token 估算（pydantic 模型取其确定性 JSON）。"""
    if value is None:
        return 0
    if isinstance(value, str):
        return estimate_tokens(value)
    dump = getattr(value, "model_dump_json", None)
    if callable(dump):
        return estimate_tokens(dump())
    return estimate_tokens(str(value))


def derive_context_envelope(
    *,
    window_tokens: int | None,
    tools: Sequence[Any] = (),
    system_prompt: str = "",
    user_cap_tokens: int = 0,
    explicit_budget_chars: int | None = None,
) -> ContextEnvelope:
    """由端点窗口派生本轮信封（纯函数；不读时钟、不读存储）。

    :param window_tokens: 端点声明的 ``max_context_tokens``；``None`` ⇒ 保守 fallback
        并在 ``note`` 里显式标注端点能力未知
    :param tools: 本轮工具目录（``ToolEntry`` / ``ToolSpec`` 均可）
    :param system_prompt: 本轮系统提示词（为空即不计）
    :param user_cap_tokens: 用户显式封顶的 token 数（0 即不封顶）
    :param explicit_budget_chars: 直接指定字符预算（测试 / 既有调用方的显式覆盖）；
        给出时以它为准，信封其余字段照常派生
    """
    if window_tokens is not None and (
        not isinstance(window_tokens, int) or isinstance(window_tokens, bool)
        or window_tokens < 1
    ):
        raise ValueError(f"端点窗口须为 ≥1 的整数或 None，得到 {window_tokens!r}")
    if not isinstance(user_cap_tokens, int) or isinstance(user_cap_tokens, bool) \
            or user_cap_tokens < 0:
        raise ValueError(f"用户封顶须为 ≥0 的整数，得到 {user_cap_tokens!r}")

    notes: list[str] = []
    if window_tokens is None:
        window = FALLBACK_CONTEXT_WINDOW_TOKENS
        source: EnvelopeSource = "fallback"
        notes.append(
            f"端点能力未知，按保守窗口 {FALLBACK_CONTEXT_WINDOW_TOKENS} tokens 派生"
        )
    else:
        window = window_tokens
        source = "endpoint"

    tools_tokens = sum(_tokens_for(entry) for entry in tools)
    system_tokens = _tokens_for(system_prompt)
    usable = int(window * CONTEXT_SAFETY_RATIO)
    computed = usable - OUTPUT_RESERVE_TOKENS - tools_tokens - system_tokens
    if user_cap_tokens > 0:
        capped = min(computed, user_cap_tokens)
        if capped < computed:
            notes.append(f"用户封顶 {user_cap_tokens} tokens 生效")
        computed = capped
    if computed < MIN_AVAILABLE_TOKENS:
        notes.append(
            f"可用预算 {max(0, computed)} tokens 低于下限 {MIN_AVAILABLE_TOKENS}"
            "（工具目录与预留已占满窗口）"
        )
    available = max(0, computed)
    budget_chars = (
        int(explicit_budget_chars) if explicit_budget_chars is not None
        else max(1, available)
    )
    catalog_budget_chars = max(1, int(budget_chars * CATALOG_BUDGET_SHARE))
    return ContextEnvelope(
        window_tokens=window,
        safety_ratio=CONTEXT_SAFETY_RATIO,
        output_reserve_tokens=OUTPUT_RESERVE_TOKENS,
        tools_tokens=tools_tokens,
        system_prompt_tokens=system_tokens,
        available_tokens=available,
        budget_chars=budget_chars,
        catalog_budget_chars=catalog_budget_chars,
        source=source,
        user_cap_tokens=user_cap_tokens,
        note="；".join(notes),
    )
