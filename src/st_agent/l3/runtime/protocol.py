"""工具调用协议端口（T-AGT-004.2；[D-090](../../../项目管理/决策日志.md) ③ D-a）。

**为什么它是端口**：模型「出动作」这一步的线上面各家不同——T1 原生 function calling
（``tools`` 入参 / ``tool_calls`` 出参，[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)）、
T2 ``response_format`` 约束 JSON、T3 prompt 约定 JSON。三者**循环本体的行为完全一致**，
差异全在「怎么问、怎么解析回来」。故把它抽成 :class:`ToolCallProtocol`，本模块交 **T1**
的实现 :class:`NativeToolCallProtocol`；T2 / T3 日后作为**同接口的另两个实现**补入，
**循环本体不改**（GWT-7）。

**端口只有两个方法**（[D-090](../../../项目管理/决策日志.md) ③ 的可靠性增益恰好落在这两处）：

- :meth:`~ToolCallProtocol.tool_calls_supported`——端点是否**声明**支持工具调用。声明
  由启动探测落定（[`T-AGT-002`](../../../项目管理/tasks/T-AGT-002-端点能力启动探测.md)），
  **缺省不得假定为真**（[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)）：把
  ``tools`` 发给不认识它的端点，会拿到「静默无视」——比不门控更糟。
- :meth:`~ToolCallProtocol.invoke_turn`——问一轮，拿回**拼完的**工具调用或结束。

**端口收整个工作上下文**（而非只收一段 prompt）：目录经 ``tools`` 入参下发还是并进
prompt 文本，**由端口按自身形态选**（[`:meth:`WorkContext.render_prompt` <context.py>`] 的
``include_tools``），故 T1 与 T3 共用同一份装配件而不必各自拼上下文。
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import LlmUsageRecord, ToolCall
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.context import WorkContext

__all__ = [
    "DEFAULT_TURN_INITIATOR",
    "DEFAULT_TURN_PURPOSE",
    "NativeToolCallProtocol",
    "ToolCallProtocol",
    "TurnResult",
]

DEFAULT_TURN_INITIATOR = "l3-agent-loop"
"""一次循环内 LLM 调用的用量归属发起方（[01 §3](../../../../docs/技术架构-v2/01-平台共享契约.md) 口径）。"""

DEFAULT_TURN_PURPOSE = "自主查证循环"
"""一次循环内 LLM 调用的用途说明（用量归属；中性措辞）。"""


class TurnResult(BaseModel):
    """一轮的产出（文本 / 工具调用 / 用量 / 端点侧失败）。

    **失败与调用互斥**：``envelope`` 非空即本轮端点侧失败（无 ``done`` 事件，故无用量），
    此时**不得**同时带 ``tool_calls``——把失败与「模型要求调用」混在一轮里，会让调用方
    无法判「该执行还是该停」。
    """

    model_config = ConfigDict(frozen=True)

    text: str = ""
    """本轮模型给出的自由文本（**中间轮次由循环丢弃**，见 ``loop`` 的中性边界）。"""
    tool_calls: tuple[ToolCall, ...] = ()
    """本轮模型给出的**拼完的**工具调用（空即模型给出「结束」）。"""
    usage: LlmUsageRecord | None = None
    """本轮用量（正常结束时由 ``done`` 事件携带）。"""
    envelope: ResultEnvelope | None = None
    """本轮端点侧失败的信封（[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md)）；正常为 ``None``。"""

    @model_validator(mode="after")
    def _failure_excludes_calls(self) -> "TurnResult":
        if self.envelope is not None and self.tool_calls:
            raise AgentRuntimeValidationError(
                "一轮失败（envelope 非空）时不得同时携带工具调用——无法判定该执行还是该停"
            )
        return self


class ToolCallProtocol(Protocol):
    """工具调用协议端口（可替换；循环本体只依赖本接口）。"""

    def tool_calls_supported(self) -> bool:
        """端点是否声明支持本协议所需的工具调用（缺省为假时循环 fail-closed）。"""
        ...

    def invoke_turn(self, context: WorkContext, *, initiator: str, purpose: str) -> TurnResult:
        """问一轮：把工作上下文与工具目录交出去，拿回工具调用或「结束」。"""
        ...


class NativeToolCallProtocol:
    """**T1**：原生 function calling（[D-090](../../../项目管理/决策日志.md) ③）。

    薄包 L0 的 [``LlmClient.invoke(tools=…)``](../../l0/llm/client.py)：工具条目走 ``tools``
    入参（**不并进 prompt 文本**——目录只需说一遍），出参取 ``tool_call`` 事件；``done``
    携带用量、``error`` 携带失败信封。**不作任何判定与兜底**——能力协商、参数 schema
    强制、超时与审计全在 L0 既有链路里。

    :param llm: ``LlmClient``（[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的调用面）
    :param endpoints: ``EndpointRegistry``（读端点声明的能力档；**组合根注入**，
        本模块不自行 new——同 L3 注入其余鸭子端口的既有做法）
    :param endpoint_id: 本次循环使用的端点标识
    """

    def __init__(self, *, llm: Any, endpoints: Any, endpoint_id: str) -> None:
        for name, value in (("llm", llm), ("endpoints", endpoints)):
            if value is None:
                raise AgentRuntimeValidationError(
                    f"原生工具调用协议需要 {name}（组合根注入），得到 None"
                )
        if not isinstance(endpoint_id, str) or not endpoint_id.strip():
            raise AgentRuntimeValidationError("原生工具调用协议需要端点标识（不得为空）")
        self._llm = llm
        self._endpoints = endpoints
        self._endpoint_id = endpoint_id

    def tool_calls_supported(self) -> bool:
        """读端点记录的 ``supports_function_calling``（**探测所得**，非本端口自判）。"""
        endpoint = self._endpoints.get(self._endpoint_id)
        return bool(endpoint.capability.supports_function_calling)

    def invoke_turn(self, context: WorkContext, *, initiator: str, purpose: str) -> TurnResult:
        """发起一轮原生工具调用（工具条目走 ``tools`` 入参）。"""
        specs = context.tool_specs()
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        usage: LlmUsageRecord | None = None
        envelope: ResultEnvelope | None = None
        for event in self._llm.invoke(
            self._endpoint_id,
            context.render_prompt(),
            initiator=initiator,
            purpose=purpose,
            tools=specs or None,
        ):
            if event.kind == "chunk":
                text_parts.append(event.text)
            elif event.kind == "tool_call" and event.tool_call is not None:
                calls.append(event.tool_call)
            elif event.kind == "done":
                usage = event.usage
            elif event.kind == "error":
                envelope = event.error_envelope
        return TurnResult(
            text="".join(text_parts),
            tool_calls=tuple(calls),
            usage=usage,
            envelope=envelope,
        )
