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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolSpec
from st_agent.l1.runner import RUN_PREFIX
from st_agent.l1.skills.tool_catalog import ToolEntry
from st_agent.l3.errors import AgentRuntimeValidationError

__all__ = [
    "CATALOG_EMPTY_REASON",
    "DEFAULT_WORK_CONTEXT_BUDGET",
    "RESULT_TRUNCATE_CHARS",
    "TRUNCATED_PREVIEW_CHARS",
    "TruncatedResult",
    "WorkContext",
    "WorkStep",
    "assemble_work_context",
    "log_ref_of",
    "new_work_step",
]

DEFAULT_WORK_CONTEXT_BUDGET = 4000
"""工作上下文的默认规模预算（**字符数**近似）。

取与 [`:data:`DEFAULT_CONTEXT_BUDGET <../chat/session.py>`] 同一口径（§1 未定默认值，
4000 是既有装配件的缺省）——两处**共用口径**而**不共用装配逻辑**
（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。取值属实现口径，可经参数覆盖。
"""

RESULT_TRUNCATE_CHARS = 1200
"""单条结果的截断阈值（字符数；超者截断 + 留指针）。

取值属实现口径：在 4000 字符的预算下，单条结果占四分之一以上即无谓挤占后续步骤的位置。
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
    budget_chars: int = Field(ge=1)
    """本次装配的规模预算（字符数）。"""
    size_chars: int = Field(ge=0)
    """本次装配的实际规模（:meth:`render_prompt` 带工具的字符数口径）。"""
    over_budget: bool = False
    """是否超限——**如实标注**，不压缩、不丢弃（[D-090](../../../项目管理/决策日志.md) 冻结范围）。"""

    def tool_specs(self) -> tuple[ToolSpec, ...]:
        """工具条目的**模型可见形态**（[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的 ``ToolSpec``）。

        只取三字段——``skill_id`` **不**进模型上下文（它是执行目标，不是模型要看见的东西）。
        """
        return tuple(
            ToolSpec(name=e.name, description=e.description, parameters=e.parameters)
            for e in self.tools
        )

    def render_prompt(self, *, include_tools: bool = False) -> str:
        """把上下文渲染成 prompt 文本（确定性；同一输入逐字节一致）。

        :param include_tools: 是否把工具目录并入文本——**走原生工具调用通道的端口不必并**
            （目录经 ``tools`` 入参下发），走 prompt 约定的端口则需要；由端口按自身形态选。
        """
        lines: list[str] = [f"任务：{self.task}"]
        if include_tools:
            lines.append("")
            lines.append(f"可用工具（{len(self.tools)} 个）：")
            for entry in self.tools:
                lines.append(f"- {entry.name}：{entry.description}")
                lines.append(
                    "  参数：" + _json(entry.parameters)
                )
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
        return "\n".join(lines)


def assemble_work_context(
    task: str,
    tools: Sequence[ToolEntry],
    steps: Sequence[WorkStep] = (),
    *,
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET,
    max_result_chars: int = RESULT_TRUNCATE_CHARS,
) -> ResultEnvelope:
    """装配循环工作上下文（纯函数，GWT-6）。

    :param task: 本次循环的任务（中性转述；不得为空）
    :param tools: 工具目录（``SkillRegistry.tool_catalog()`` 的产出）；**空目录即显式空态**
        （GWT-2）——返回 ``empty`` + 原因，**不**装配出「可调用但无工具」的上下文
    :param steps: 已执行的动作与结果（按发生顺序；逐条过截断，GWT-3 / GWT-4）
    :param budget_chars: 规模预算（字符数）
    :param max_result_chars: 单条结果的截断阈值（字符数）
    :returns: ``ok``（载荷为 :class:`WorkContext`）或 ``empty``（目录为空）；入参非法抛
        :class:`~st_agent.l3.errors.AgentRuntimeValidationError`

    结果**不写任何存储**、不读时钟——故同一输入两次装配逐字节一致（GWT-6）。
    """
    if not isinstance(task, str) or not task.strip():
        raise AgentRuntimeValidationError("循环任务不得为空（工作上下文的第一样，05 §10）")
    if not isinstance(budget_chars, int) or isinstance(budget_chars, bool) or budget_chars < 1:
        raise AgentRuntimeValidationError(f"规模预算须为 ≥1 的整数，得到 {budget_chars!r}")
    if not isinstance(max_result_chars, int) or isinstance(max_result_chars, bool) \
            or max_result_chars < 1:
        raise AgentRuntimeValidationError(f"截断阈值须为 ≥1 的整数，得到 {max_result_chars!r}")

    entries = _checked_entries(tools)
    if not entries:
        return ResultEnvelope.empty(CATALOG_EMPTY_REASON)
    ordered = _checked_steps(steps)
    context = WorkContext(
        task=task,
        tools=entries,
        steps=tuple(_apply_truncation(step, max_result_chars) for step in ordered),
        budget_chars=budget_chars,
        size_chars=0,
    )
    size = len(context.render_prompt(include_tools=True))
    return ResultEnvelope.ok(
        context.model_copy(update={"size_chars": size, "over_budget": size > budget_chars})
    )


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
    """一步结果的可读形态（ok 给载荷；其余给状态 + 原因——失败**照实**呈现）。"""
    envelope = step.envelope
    if envelope.status == "ok":
        return _json(envelope.data)
    return f"[{envelope.status}] {envelope.reason or ''}".rstrip()


def _json(value: Any) -> str:
    """确定性的 JSON 文本（排序键 + 不转义非 ASCII），供 prompt 与规模口径共用。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
