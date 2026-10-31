"""循环工作上下文与工具目录的**装配件**（T-AGT-004.1；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 定义循环的两样输入——**工具目录**
（[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 描述体的投影，唯一来源）与**循环工作
上下文**（任务 + 工具目录 + 已执行动作与结果）。本模块把这两样装配成**纯函数件**：无 LLM、无
副作用、同一输入两次装配逐字节一致（GWT-6），故可单测。

**工作上下文不是会话上下文**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 明文，
[D-090](../../../项目管理/决策日志.md) ②）：[`assemble_context`](../chat/session.py) 装的是
**会话**上下文（近端消息 + 记忆切片，共享 token 预算），本模块装的是**工作**上下文（任务 +
工具目录 + 动作与结果）。两者可共用预算口径，但**装配逻辑不同、不得互指为同一件**。

四条口径：

- **模型可见面与执行面分开**——工具条目进模型上下文只取 ``name`` / ``description`` /
  ``parameters``（＝L0 ``ToolSpec`` 三字段，[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)
  的线上形态）；``skill_id`` 是**执行目标**，留在本地供执行面用，不占模型窗口。来源因此不丢：
  上下文仍持 ``ToolEntry`` 原形（``tools``），模型可见面由 :meth:`WorkContext.tool_specs` 派生。
- **截断 + 外置指针**（[D-090](../../../项目管理/决策日志.md) ⑧）——单条结果超阈值即换成
  :class:`TruncatedResult`（含 ``log_ref``），**不新建存储**：指针指向 ``SkillRunner`` 已落盘的
  ``execution_log`` 记录（路径口径见 :func:`log_ref_of`）。未超阈值者**逐字节原样**。
- **空目录显式化**（GWT-2）——注册表尚无 Skill 时不装配上下文，交 ``empty`` + 原因：让模型
  「可调用但无工具」地空转比显式告知目录为空更糟。
- **超限如实标注、不压缩**——本模块给出上下文的**规模口径**（模型可见形态的字符数）与
  ``over_budget`` 标记；本期**不做**上下文压缩（[D-090](../../../项目管理/决策日志.md) 冻结
  范围），故超限的唯一尺寸控制手段是单条结果的截断，其余如实标注、不静默丢弃任何一步。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolSpec
from st_agent.l1.runner import RUN_PREFIX
from st_agent.l1.skills.tool_catalog import ToolEntry
from st_agent.l2.memory.reader import MemorySlice
from st_agent.l3.chat.session import ROLE_LABELS, ChatMessage
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.tool_search import (
    SUMMARY_CHARS,
    TOOL_SEARCH_TOOL,
    tool_search_description,
    tool_search_parameters,
    tool_search_spec,
)

__all__ = [
    "CATALOG_EMPTY_REASON",
    "DEFAULT_WORK_CONTEXT_BUDGET",
    "RESULT_TRUNCATE_CHARS",
    "TRUNCATED_PREVIEW_CHARS",
    "AskQuestion",
    "PlanStep",
    "TruncatedResult",
    "WorkContext",
    "WorkStep",
    "assemble_work_context",
    "log_ref_of",
    "new_work_step",
]

DEFAULT_WORK_CONTEXT_BUDGET = 12000
"""工作上下文的默认规模预算（**字符数**近似）。

取与 [`:data:`DEFAULT_CONTEXT_BUDGET <../chat/session.py>`] 同一量级（§1 未定默认值）
——两处**共用口径**而**不共用装配逻辑**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。
取值属实现口径，可经参数覆盖。

2026-10-29 由 4000 提到 6000（[D-144](../../../项目管理/决策日志.md)），再提到 **12000**
（[D-145](../../../项目管理/决策日志.md)）：M9 取数 / 同步 / 文档三条原语进场后，出厂工具
目录为 **19 个能力**（12 官方 Skill + 7 内置原语），其模型可见形态实测约 **7.75k 字符**
——6000 的余量不足 1.3k，历史与记忆在出厂配置下会被整条舍弃。12000 留出约 4.2k 给两者
（与 D-144 时 6000 留出的余量同量级）；仍远低于 `investigate.context_budget` 的 32000 上限。
"""

RESULT_TRUNCATE_CHARS = 1200
"""单条结果的截断阈值（字符数；超者截断 + 留指针）。

取值属实现口径：在 12000 字符的预算下，单条结果占十分之一以上即无谓挤占后续步骤的位置。
"""

TRUNCATED_PREVIEW_CHARS = 200
"""截断形态保留的预览长度（字符数）——够让模型判断「这步拿到了什么量级的东西」。"""

CATALOG_EMPTY_REASON = "工具目录为空（L1 注册表尚无 Skill），本次循环无可调用的能力（05 §10）"
"""空目录时的显式原因（GWT-2：不留「可调用但无工具」的上下文让模型空转）。"""


def log_ref_of(skill_run_id: str) -> str:
    """一次执行的留痕指针（``execution_log`` 分区内的相对路径）。

    路径口径取自 L1 自身的单一真相源（``RUN_PREFIX``），**不另造一套**——指针因此
    天然指向 ``SkillRunner`` 落下的那条真实记录（[D-090](../../../项目管理/决策日志.md) ⑧）。
    """
    return f"{RUN_PREFIX}{skill_run_id}.json"


class TruncatedResult(BaseModel):
    """被截断的结果载荷（GWT-3；替代原载荷进上下文，原件仍在 ``log_ref`` 处可查）。

    ``preview`` 是原载荷的**头部**（不含省略号之外的任何加工），``original_chars`` 让
    消费方知道丢掉的是多大一块——不做「看起来像完整结果」的伪装。
    """

    model_config = ConfigDict(frozen=True)

    truncated: bool = True
    log_ref: str = Field(min_length=1)
    """外置指针（:func:`log_ref_of`；指向真实存在的 ``execution_log`` 记录）。"""
    skill_run_id: str = ""
    """产生该结果的执行标识——回读原语按它定位记录（T-AGT-017.2）。"""
    read_tool: str = "sk_result_read"
    """回读原语名（模型可经它按 ``offset`` 分片取回完整载荷）。"""
    next_offset: int = Field(default=0, ge=0)
    """默认回读起点（从载荷头部开始；一次取 ``max_chars``，续读用返回的 ``next_offset``）。"""
    original_chars: int = Field(ge=0)
    """原载荷的字符数（截断前的规模）。"""
    preview: str = ""
    """原载荷头部预览（原样截取，不改写）。"""


class WorkStep(BaseModel):
    """一次**已执行**的动作及其结果（循环工作上下文的第四样，GWT-5）。

    「当时依据什么」＝``arguments``（模型给出的调用参数）与 ``tool_name``（它点的能力）；
    「拿到什么」＝``envelope``（L1 流水线的信封，失败与空结果**照实**在此）。``skill_id``
    是本次执行**实际命中**的目标（该 base 的最高版本），故工具名与执行目标**双留存**——
    版本升级换执行目标，不换工具名（[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md)）。
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    """模型当时调用的**工具名**（``skill_id`` 的 base）。"""
    skill_id: str = Field(min_length=3)
    """本次执行的实际目标（执行面据此调用）。"""
    arguments: dict[str, Any] = Field(default_factory=dict)
    """当时依据的调用参数（模型给出、未经循环改写）。"""
    skill_run_id: str = Field(min_length=1)
    """本次执行的 ``skill_run_id``（[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    log_ref: str = Field(min_length=1)
    """留痕指针（:func:`log_ref_of`）——**恒等于**该 ``skill_run_id`` 的记录路径。"""
    envelope: ResultEnvelope
    """本次拿到的结果（超阈值者已换 :class:`TruncatedResult` 形态）。"""
    truncated: bool = False
    """结果是否被截断（真时 ``envelope.data`` 为 :class:`TruncatedResult`）。"""


def new_work_step(
    *,
    tool_name: str,
    skill_id: str,
    arguments: dict[str, Any] | None,
    skill_run_id: str,
    envelope: ResultEnvelope,
) -> WorkStep:
    """构造一步**未截断**的执行记录（纯函数；截断由 :func:`assemble_work_context` 施加）。

    指针由 ``skill_run_id`` 派生（:func:`log_ref_of`），**不由调用方给**——调用方无从编造
    一个指向不存在记录的指针。
    """
    if not isinstance(skill_run_id, str) or not skill_run_id.strip():
        raise AgentRuntimeValidationError("执行步骤须给出 skill_run_id（留痕指针由它派生）")
    if not isinstance(envelope, ResultEnvelope):
        raise AgentRuntimeValidationError(
            f"执行步骤的结果须为 ResultEnvelope，得到 {type(envelope).__name__}"
        )
    return WorkStep(
        tool_name=tool_name,
        skill_id=skill_id,
        arguments=dict(arguments or {}),
        skill_run_id=skill_run_id,
        log_ref=log_ref_of(skill_run_id),
        envelope=envelope,
    )


class PlanStep(BaseModel):
    """计划里的一步（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的计划原语）。

    状态词与任务账本同序但**不复用**其字面量（``pending`` / ``active`` / ``done``）——
    计划是运行期决策，不是持久化的任务状态。
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    status: Literal["pending", "active", "done"] = "pending"


class AskQuestion(BaseModel):
    """循环内提问的一问（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的提问原语）。

    与 [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的澄清问项同形（一问一参、
    取值域明确），但**不复用** ``ClarificationQuestion``：那个属派发前澄清，本型属循环交还。
    """

    model_config = ConfigDict(frozen=True)

    parameter: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    choices: tuple[str, ...] = ()
    default: Any = None


class WorkContext(BaseModel):
    """循环工作上下文（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：任务 + 工具目录 + 已执行动作与结果）。

    模型可见形态有两份、按**传输通道**分用（由协议端口选，见 ``runtime.protocol``）：
    :meth:`tool_specs`（工具条目，走原生工具调用通道的 ``tools`` 入参）与
    :meth:`render_prompt`（任务与已执行动作，走 prompt 文本）。二者同源，故不漂移。
    """

    model_config = ConfigDict(frozen=True)

    task: str = Field(min_length=1)
    """本次循环要查证的任务（用户意图的中性转述）。"""
    tools: tuple[ToolEntry, ...] = ()
    """工具目录（**模型可见面与执行目标的共同来源**；每条可回指 ``skill_id``，GWT-1）。"""
    steps: tuple[WorkStep, ...] = ()
    """已执行的动作与结果，**按发生顺序**（GWT-5）。"""
    history: tuple[ChatMessage, ...] = ()
    """**近端会话历史**（按时间序；取自 :func:`assemble_context <../chat/session.py>` 的
    ``messages`` 产出）。它是工作上下文的第四样，**不是**把会话上下文当工作上下文
    （[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)；调研报告 §8.4）。"""
    memory: tuple[MemorySlice, ...] = ()
    """**记忆切片**（取自 ``assemble_context`` 的 ``memory.slices``；受预算约束，超限时
    先于历史被舍，见 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。"""
    dropped: tuple[str, ...] = ()
    """**被预算舍弃的项**（``memory:<node_id>`` / ``history:<message_id>``）——如实标注，
    不静默丢；用于空态区分「本来没有」与「有但装不下」。"""
    lazy_tools: bool = False
    """工具目录是否进入**惰性态**（模型可见形态超阈值；见 :func:`assemble_work_context`）。
    为假时目录整份下发，装配输出与未引入检索时逐字节一致。"""
    retrieved_tools: tuple[str, ...] = ()
    """已按检索**展开**的工具名（惰性态下完整描述与参数只给这些条目）。"""
    tool_search_note: str = ""
    """上一次检索的显式说明（命中数 / 未命中 / 参数非法原因；无检索即空）。"""
    subagent_note: str = ""
    """**子 Agent 的结论与引用**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。
    子步骤不进主上下文，只这一段的结论 + ``skill_run_id`` 引用；无派生即空。"""
    plan: tuple[PlanStep, ...] = ()
    """**当前计划**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的计划原语；
    空元组的含义由 ``plan_updated`` 区分「尚未给出」与「显式清空」）。"""
    plan_updated: bool = False
    """模型是否已给过计划更新（含清空）——**区分两态**，不把「清空」渲染成「没给过」。"""
    plan_error: str = ""
    """上一次计划更新**未被采用**的原因（非法形态留给模型下一轮纠正，不终止循环）。"""
    user_inputs: dict[str, Any] = Field(default_factory=dict)
    """**续跑时用户补充的答案**（一问一参的 ``parameter → 值``；无续跑即空）。
    与历史 / 记忆不同，它是本次任务的新输入，故**恒保留**、不参与预算取舍。"""
    budget_chars: int = Field(ge=1)
    """本次装配的规模预算（字符数）。"""
    size_chars: int = Field(ge=0)
    """本次装配的实际规模（:meth:`render_prompt` 带工具的字符数口径）。"""
    over_budget: bool = False
    """是否超限——**如实标注**，不压缩、不丢弃（[D-090](../../../项目管理/决策日志.md) 冻结范围）。"""

    def tool_specs(self) -> tuple[ToolSpec, ...]:
        """工具条目的**模型可见形态**（[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的 ``ToolSpec``）。

        只取三字段——``skill_id`` **不**进模型上下文（它是执行目标，不是模型要看见的东西）。
        惰性态下只给**检索入口 + 已展开条目**；未超阈值时与未引入检索时逐字节一致。
        """
        if not self.lazy_tools:
            return tuple(
                ToolSpec(name=e.name, description=e.description, parameters=e.parameters)
                for e in self.tools
            )
        retrieved = set(self.retrieved_tools)
        return (tool_search_spec(),) + tuple(
            ToolSpec(name=e.name, description=e.description, parameters=e.parameters)
            for e in self.tools
            if e.name in retrieved
        )

    def render_prompt(self, *, include_tools: bool = False) -> str:
        """把上下文渲染成 prompt 文本（确定性；同一输入逐字节一致）。

        :param include_tools: 是否把工具目录并入文本——**走原生工具调用通道的端口不必并**
            （目录经 ``tools`` 入参下发），走 prompt 约定的端口则需要；由端口按自身形态选。
        """
        lines: list[str] = [f"任务：{self.task}"]
        lines.append("")
        lines.extend(self._render_plan_lines())
        lines.append("")
        if self.tool_search_note:
            lines.append(f"上次工具检索：{self.tool_search_note}")
            lines.append("")
        if self.subagent_note:
            lines.append("子 Agent 结果（子上下文不进本窗，仅回传结论与引用）：")
            lines.append(self.subagent_note)
            lines.append("")
        if self.user_inputs:
            lines.append("用户补充（续跑，一问一参）：" + _json(self.user_inputs))
            lines.append("")
        lines.extend(self._render_history_lines())
        if self.lazy_tools:
            lines.append("")
            lines.extend(self._render_lazy_tools(include_tools=include_tools))
        elif include_tools:
            lines.append("")
            lines.extend(self._render_full_tools())
        lines.append("")
        if not self.steps:
            lines.append("已执行动作与结果：无（尚未执行任何动作）")
        else:
            lines.append(f"已执行动作与结果（按发生顺序，共 {len(self.steps)} 步）：")
            for index, step in enumerate(self.steps, start=1):
                lines.append(
                    f"{index}. 调用 {step.tool_name}（执行目标 {step.skill_id}）"
                )
                lines.append(f"   依据：{_json(step.arguments)}")
                lines.append(f"   结果：{_render_result(step)}")
        lines.append("")
        lines.extend(self._render_memory_lines())
        return "\n".join(lines)

    def _render_plan_lines(self) -> list[str]:
        """计划段（三态显式：有 / 已清空 / 尚未给出；另有「上次未采用」的原因）。"""
        out: list[str] = []
        if self.plan_error:
            out.append(f"上次计划更新未采用：{self.plan_error}")
        if self.plan:
            out.append(f"当前计划（{len(self.plan)} 步）：")
            for index, step in enumerate(self.plan, start=1):
                out.append(f"{index}. [{step.status}] {step.title}")
            return out
        if self.plan_updated:
            out.append("当前计划：无（模型已显式清空）")
        else:
            out.append("当前计划：无（尚未给出计划）")
        return out

    def _render_history_lines(self) -> list[str]:
        """会话历史段（**空态显式**，且区分「本来没有」与「被预算舍弃」）。"""
        if self.history:
            out = [f"会话历史（近端 {len(self.history)} 条，按时间序）："]
            for message in self.history:
                label = ROLE_LABELS.get(message.role, message.role)
                out.append(f"- {label}：{message.text}")
                if message.payload is not None:
                    # 助手轮的结果在出站载荷里（text 只是中性摘要）——不带它就答不了
                    # 「上一轮拿到了什么」；超出预算时整条消息一起被舍（不半截丢）。
                    out.append(f"  出站载荷：{_json(message.payload)}")
            return out
        dropped = sum(1 for item in self.dropped if item.startswith("history:"))
        if dropped:
            return [f"会话历史：已按预算舍弃 {dropped} 条最早消息（见运行报告）"]
        return ["会话历史：无（本会话无更早消息）"]

    def _render_memory_lines(self) -> list[str]:
        """记忆切片段（同历史的空态口径：无 / 已舍弃 / 有）。"""
        if self.memory:
            out = [f"相关记忆（{len(self.memory)} 条）："]
            for slice_ in self.memory:
                out.append(f"- {_render_memory(slice_)}")
            return out
        dropped = sum(1 for item in self.dropped if item.startswith("memory:"))
        if dropped:
            return [f"相关记忆：已按预算舍弃 {dropped} 条（原文仍可从记忆图谱读面取回）"]
        return ["相关记忆：无（无相关记忆切片）"]

    def _render_full_tools(self) -> list[str]:
        """整份目录的模型可见形态（未超阈值；与未引检索时的输出逐字节一致）。"""
        out = [f"可用工具（{len(self.tools)} 个）："]
        for entry in self.tools:
            out.append(f"- {entry.name}：{entry.description}")
            out.append("  参数：" + _json(entry.parameters))
        return out

    def _render_lazy_tools(self, *, include_tools: bool) -> list[str]:
        """惰性态：摘要索引 + 检索入口 + 已展开条目（完整参数只给已展开者）。"""
        out = [
            f"可用工具（{len(self.tools)} 个，目录超过阈值：仅给摘要，"
            f"调用 {TOOL_SEARCH_TOOL} 按需展开完整参数）："
        ]
        for entry in self.tools:
            out.append(f"- {entry.name}：{_summary(entry.description)}")
        out.append("")
        out.append("检索入口：")
        out.append(f"- {TOOL_SEARCH_TOOL}：{tool_search_description()}")
        out.append("  参数：" + _json(tool_search_parameters()))
        retrieved_names = set(self.retrieved_tools)
        retrieved = [e for e in self.tools if e.name in retrieved_names]
        out.append("")
        if not retrieved:
            out.append("已展开的工具：无（检索命中后会在此给出完整描述与参数）")
        elif include_tools:
            out.append(f"已展开的工具（{len(retrieved)} 个）：")
            for entry in retrieved:
                out.append(f"- {entry.name}：{entry.description}")
                out.append("  参数：" + _json(entry.parameters))
        else:
            names = "、".join(entry.name for entry in retrieved)
            out.append(f"已展开的工具：{names}（完整参数经工具调用通道下发）")
        return out


def assemble_work_context(
    task: str,
    tools: Sequence[ToolEntry],
    steps: Sequence[WorkStep] = (),
    *,
    history: Sequence[ChatMessage] = (),
    memory: Sequence[MemorySlice] = (),
    plan: Sequence[PlanStep] = (),
    plan_updated: bool = False,
    plan_error: str = "",
    user_inputs: dict[str, Any] | None = None,
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET,
    max_result_chars: int = RESULT_TRUNCATE_CHARS,
    retrieved_tools: Sequence[str] = (),
    tool_search_note: str = "",
    subagent_note: str = "",
    tool_lazy_chars: int | None = None,
) -> ResultEnvelope:
    """装配循环工作上下文（纯函数，GWT-6）。

    :param task: 本次循环的任务（中性转述；不得为空）
    :param tools: 工具目录（``SkillRegistry.tool_catalog()`` 的产出）；**空目录即显式空态**
        （GWT-2）——返回 ``empty`` + 原因，**不**装配出「可调用但无工具」的上下文
    :param steps: 已执行的动作与结果（按发生顺序；逐条过截断，GWT-3 / GWT-4）
    :param history: 近端会话历史（按时间序；取自 ``assemble_context`` 的 ``messages``）
    :param memory: 记忆切片（取自 ``assemble_context`` 的 ``memory.slices``）
    :param plan: 当前计划（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的计划原语）
    :param plan_updated: 模型是否已给过计划更新（含清空）——区分「尚未给出」与「已清空」
    :param plan_error: 上一次计划更新未被采用的原因（非法形态留给模型纠正）
    :param user_inputs: 续跑时用户补充的答案（一问一参）
    :param budget_chars: 规模预算（字符数）
    :param max_result_chars: 单条结果的截断阈值（字符数）
    :param retrieved_tools: 已被工具检索**展开**的工具名（惰性态下完整参数只给这些条目；
        不在目录内的名字被忽略——续跑承接的旧检索结果可能指向已下线能力）
    :param tool_search_note: 上一次检索的显式说明（命中数 / 未命中 / 参数非法原因）
    :param subagent_note: 子 Agent 回传的结论与引用（子步骤不进主上下文；恒保留）
    :param tool_lazy_chars: 惰性态阈值（目录工具段的字符数）；``None`` 即取本次预算
    :returns: ``ok``（载荷为 :class:`WorkContext`）或 ``empty``（目录为空）；入参非法抛
        :class:`~st_agent.l3.errors.AgentRuntimeValidationError`

    **惰性态**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）：目录工具段的
    字符数超过 ``tool_lazy_chars``（缺省＝本次预算）时，只给摘要索引与检索入口，条目按
    ``retrieved_tools`` 展开。未超阈值时输出**逐字节同前**——不向目录加任何条目。

    **超限取舍**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）：先舍记忆切片
    （自末向首），再舍历史消息（自旧向新）；任务、工具目录与已执行动作**不参与取舍**
    （D-090 冻结范围：不压缩、不丢步骤）。被舍项记进 ``WorkContext.dropped``。取舍后仍超限
    则照实标 ``over_budget``——**不假装装得下**。

    结果**不写任何存储**、不读时钟——故同一输入两次装配逐字节一致（GWT-6）。
    """
    if not isinstance(task, str) or not task.strip():
        raise AgentRuntimeValidationError("循环任务不得为空（工作上下文的第一样，05 §10）")
    if not isinstance(budget_chars, int) or isinstance(budget_chars, bool) or budget_chars < 1:
        raise AgentRuntimeValidationError(f"规模预算须为 ≥1 的整数，得到 {budget_chars!r}")
    if not isinstance(max_result_chars, int) or isinstance(max_result_chars, bool) \
            or max_result_chars < 1:
        raise AgentRuntimeValidationError(f"截断阈值须为 ≥1 的整数，得到 {max_result_chars!r}")
    if tool_lazy_chars is not None and (
        not isinstance(tool_lazy_chars, int) or isinstance(tool_lazy_chars, bool)
        or tool_lazy_chars < 1
    ):
        raise AgentRuntimeValidationError(
            f"工具惰性阈值须为 ≥1 的整数，得到 {tool_lazy_chars!r}"
        )

    entries = _checked_entries(tools)
    if not entries:
        return ResultEnvelope.empty(CATALOG_EMPTY_REASON)
    ordered = _checked_steps(steps)
    threshold = budget_chars if tool_lazy_chars is None else tool_lazy_chars
    retrieved_names = set(retrieved_tools)
    retrieved = tuple(
        entry.name for entry in entries if entry.name in retrieved_names
    )
    context, size = _fit_budget(
        task=task,
        tools=entries,
        steps=tuple(_apply_truncation(step, max_result_chars) for step in ordered),
        history=_checked_history(history),
        memory=_checked_memory(memory),
        plan=_checked_plan(plan),
        plan_updated=bool(plan_updated),
        plan_error=str(plan_error or ""),
        user_inputs=dict(user_inputs or {}),
        budget_chars=budget_chars,
        lazy_tools=_tools_section_chars(entries) > threshold,
        retrieved_tools=retrieved,
        tool_search_note=str(tool_search_note or ""),
        subagent_note=str(subagent_note or ""),
    )
    return ResultEnvelope.ok(
        context.model_copy(update={"size_chars": size, "over_budget": size > budget_chars})
    )


def _fit_budget(
    *,
    task: str,
    tools: tuple[ToolEntry, ...],
    steps: tuple[WorkStep, ...],
    history: tuple[ChatMessage, ...],
    memory: tuple[MemorySlice, ...],
    plan: tuple[PlanStep, ...],
    plan_updated: bool,
    plan_error: str,
    user_inputs: dict[str, Any],
    budget_chars: int,
    lazy_tools: bool,
    retrieved_tools: tuple[str, ...],
    tool_search_note: str,
    subagent_note: str,
) -> tuple[WorkContext, int]:
    """按声明顺序取舍直至装下（或无可再舍），返回上下文与最终规模。

    取舍顺序＝**记忆 → 历史（旧→新）**；步骤 / 任务 / 工具目录 / 计划 / 用户补充恒保留
    （[05 §10]）。计划与用户补充是本次任务的结构性输入，不参与取舍。
    """
    kept_history = list(history)
    kept_memory = list(memory)
    dropped: list[str] = []

    def build() -> WorkContext:
        return WorkContext(
            task=task, tools=tools, steps=steps,
            history=tuple(kept_history), memory=tuple(kept_memory),
            plan=plan, plan_updated=plan_updated, plan_error=plan_error,
            user_inputs=dict(user_inputs),
            dropped=tuple(dropped), budget_chars=budget_chars, size_chars=0,
            lazy_tools=lazy_tools, retrieved_tools=retrieved_tools,
            tool_search_note=tool_search_note, subagent_note=subagent_note,
        )

    context = build()
    size = len(context.render_prompt(include_tools=True))
    while size > budget_chars and kept_memory:
        item = kept_memory.pop()
        dropped.append(f"memory:{item.node.memory_node_id}")
        context = build()
        size = len(context.render_prompt(include_tools=True))
    while size > budget_chars and kept_history:
        item = kept_history.pop(0)
        dropped.append(f"history:{item.message_id}")
        context = build()
        size = len(context.render_prompt(include_tools=True))
    return context, size


# ───────────────────────── 内部 ─────────────────────────


def _checked_entries(tools: Iterable[ToolEntry]) -> tuple[ToolEntry, ...]:
    """目录条目的形态自检（不静默跳过坏条目——半份目录比空目录更危险）。"""
    out: list[ToolEntry] = []
    for entry in tools:
        if not isinstance(entry, ToolEntry):
            raise AgentRuntimeValidationError(
                f"工具条目须为 ToolEntry，得到 {type(entry).__name__}"
                "（工具目录的唯一来源是 01 §2 描述体的投影）"
            )
        out.append(entry)
    return tuple(out)


def _checked_steps(steps: Iterable[WorkStep]) -> tuple[WorkStep, ...]:
    """步骤的形态自检（保序；不静默丢弃任何一步）。"""
    out: list[WorkStep] = []
    for step in steps:
        if not isinstance(step, WorkStep):
            raise AgentRuntimeValidationError(
                f"执行步骤须为 WorkStep，得到 {type(step).__name__}"
            )
        out.append(step)
    return tuple(out)


def _checked_history(history: Iterable[ChatMessage]) -> tuple[ChatMessage, ...]:
    """会话历史的形态自检（保序；不静默丢弃坏条目）。"""
    out: list[ChatMessage] = []
    for message in history:
        if not isinstance(message, ChatMessage):
            raise AgentRuntimeValidationError(
                f"会话历史条目须为 ChatMessage，得到 {type(message).__name__}"
                "（历史取自 assemble_context 的 messages 产出）"
            )
        out.append(message)
    return tuple(out)


def _checked_memory(memory: Iterable[MemorySlice]) -> tuple[MemorySlice, ...]:
    """记忆切片的形态自检（保序；同上）。"""
    out: list[MemorySlice] = []
    for item in memory:
        if not isinstance(item, MemorySlice):
            raise AgentRuntimeValidationError(
                f"记忆切片须为 MemorySlice，得到 {type(item).__name__}"
                "（切片取自 assemble_context 的 memory.slices 产出）"
            )
        out.append(item)
    return tuple(out)


def _checked_plan(plan: Iterable[PlanStep]) -> tuple[PlanStep, ...]:
    """计划的形态自检（不静默跳过坏步骤——半份计划比没有计划更误导）。"""
    out: list[PlanStep] = []
    for step in plan:
        if not isinstance(step, PlanStep):
            raise AgentRuntimeValidationError(
                f"计划的步骤须为 PlanStep，得到 {type(step).__name__}"
            )
        out.append(step)
    return tuple(out)


def _render_memory(slice_: MemorySlice) -> str:
    """一条记忆切片的可读形态（节点内容 + 有效置信度 / 来源 / 时间锚点）。

    **不**改写字句——记忆是用户数据（[D-053](../../../项目管理/决策日志.md)）；这里只是
    确定性地序列化，供模型读取。
    """
    node = slice_.node.model_dump(
        mode="json", exclude={"revision_history", "provenance"}
    )
    return (
        f"{_json(node)}（有效置信度 {slice_.confidence:.2f}，"
        f"来源 {slice_.source}，as_of {slice_.as_of.isoformat()}）"
    )


def _apply_truncation(step: WorkStep, max_result_chars: int) -> WorkStep:
    """单条结果的截断（超阈值 → 截断形态 + 指针；未超 → **逐字节原样**，GWT-4）。"""
    envelope = step.envelope
    if envelope.status != "ok":
        # 非 ok 态无载荷（01 §5：载荷只属于 ok）；原因串是中性短文案，不属「结果」体量
        return step
    payload = _json(envelope.data)
    if len(payload) <= max_result_chars:
        return step
    truncated = TruncatedResult(
        log_ref=step.log_ref,
        skill_run_id=step.skill_run_id,
        original_chars=len(payload),
        preview=payload[:TRUNCATED_PREVIEW_CHARS],
    )
    return step.model_copy(
        update={
            "envelope": envelope.model_copy(update={"data": truncated}),
            "truncated": True,
        }
    )


def _render_result(step: WorkStep) -> str:
    """一步结果的可读形态（ok 给载荷；其余给状态 + 原因——失败**照实**呈现）。

    截断件后附**回读提示**：完整载荷仍在 ``execution_log`` 的记录里，模型可经
    ``sk_result_read`` 按 ``offset`` 分片取回（T-AGT-017.2；[01 §5]）。
    """
    envelope = step.envelope
    if envelope.status == "ok":
        rendered = _json(envelope.data)
        if step.truncated:
            return rendered + (
                "\n   （完整结果已外置；回读：sk_result_read"
                f' {{"skill_run_id": "{step.skill_run_id}", "offset": 0, "max_chars": 2000}}'
                "，越界会被拒绝）"
            )
        return rendered
    return f"[{envelope.status}] {envelope.reason or ''}".rstrip()


def _summary(description: str) -> str:
    """摘要索引里的一条描述（折叠空白 + 截断；只用于惰性态）。"""
    text = " ".join(description.split())
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS] + "…"


def _tools_section_chars(entries: tuple[ToolEntry, ...]) -> int:
    """整份目录工具段的字符数（与 :meth:`WorkContext._render_full_tools` 同一形态）。

    这是惰性态的**阈值口径**（超预算即切摘要）；与渲染同形是为了「阈值判断看到的规模」
    与「模型实际看到的规模」不漂移——两处由同一份字段拼出，测试钉住相等。
    """
    lines = [f"可用工具（{len(entries)} 个）："]
    for entry in entries:
        lines.append(f"- {entry.name}：{entry.description}")
        lines.append("  参数：" + _json(entry.parameters))
    return len("\n".join(lines))


def _json(value: Any) -> str:
    """确定性的 JSON 文本（排序键 + 不转义非 ASCII），供 prompt 与规模口径共用。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
