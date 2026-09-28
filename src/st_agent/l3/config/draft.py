"""配置草稿的契约与生成（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

把一张**已确认**的 `configure` 意图确认卡（[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）
收敛为 :class:`ConfigDraft`——四字段齐备，且 **`understanding_summary` 与确认卡条目
同源**（§3.2 定案：卡的条目列表即该字段的同一份内容源，**不另造第二份摘要**）。

**四字段的来源只有一处**（§5 落地口径，2026-09-28 定案）：

| 字段 | 来源 |
| --- | --- |
| `target` | 确认卡的 `target`（目标 Skill / 工作流） |
| `parameter_draft` | 确认卡已收敛的 `values`（用户给出 / 指令预填 / 默认值） |
| `understanding_summary` | 确认卡的**条目列表**（`ConfirmationItem.line()` 逐条拼装） |
| `open_questions` | 目标声明参数中**未进** `values` 者 + 已按默认值填充者（逐条结构化，见下） |

**`open_questions` 逐条结构化**（§5）：元素为 :class:`OpenQuestion`（参数名 /
问句 / 默认值 / 来源），``source`` 区分 ``defaulted``（已按默认值填充——跳过或超
澄清预算者）与 ``missing``（待追问）两类；两类**不混同**，不静默替用户决定。

**分支判定只有一处**（§5 落地口径）：:func:`has_workflow_structure`——调用方随
派发提供了工作流结构载荷即为工作流分支，其草稿交
[`l3.config.workflow`](workflow.py)（经**注入的鸭子类型端口**，缺省 fail-closed）。
新草稿尚无 `flow_id`，无从按 id 形态识别目标是否为工作流，故判据取结构载荷。

**中性口径（铁律 2 / [D-053](../../../项目管理/决策日志.md)）**：本模块**自有**的生成
文案（未澄清项的问句、来源标签）过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)
执行点 2 ``check_output``；而 `understanding_summary` 由确认卡条目（其 `text` 已在
构造期过校验、`value` 属数据展示）**派生**而成，故**不整串复检**——复检会把用户数据
误判为违规。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.errors import ConfigValidationError
from st_agent.l3.intent.protocol import IntentConfirmation

__all__ = [
    "CONFIG_DRAFT_ABSENT_REASON",
    "OPEN_QUESTION_LABELS",
    "WORKFLOW_BRANCH_ABSENT_REASON",
    "ConfigDraft",
    "ConfigDraftProtocol",
    "OpenQuestion",
    "OpenQuestionSource",
    "has_workflow_structure",
    "open_questions",
    "summarise",
]

CONFIG_DRAFT_ABSENT_REASON = "未注入配置草稿生成面，无法把配置意图收敛为草稿（05 §5）"

WORKFLOW_BRANCH_ABSENT_REASON = (
    "未注入工作流草稿生成面（归属 T-L3-003.3），无法产出工作流草稿（05 §5）"
)

OpenQuestionSource = Literal["defaulted", "missing"]
"""未澄清项的来源：``defaulted`` 已按默认值填充 / ``missing`` 待追问（§5）。"""

OPEN_QUESTION_LABELS: dict[str, str] = {
    "defaulted": "已按默认值填充",
    "missing": "待追问",
}
"""来源的展示标签（本模块生成文案，逐条过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def has_workflow_structure(structure: Mapping[str, Any] | None) -> bool:
    """**唯一**的「是否工作流分支」判据（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    判据取「调用方随派发提供了工作流结构载荷」——新草稿尚无 `flow_id`，无从按
    id 形态识别目标是否为工作流（见模块文档）。:mod:`st_agent.l3.config.workflow`
    的 ``is_workflow_draft`` 复用本函数，**不另立第二处判据**。
    """
    return structure is not None


def summarise(confirmation: IntentConfirmation) -> str:
    """确认卡 → 结构化理解摘要（§5：**同一份内容源**，不另造第二份）。

    逐条取 :meth:`ConfirmationItem.line`（生成描述 + 原样值）拼装；摘要因此由
    「构造期已过校验的描述」与「原样承载的数据」派生，本函数**不再整串复检**。
    """
    return "\n".join(item.line() for item in confirmation.items)


def open_questions(
    confirmation: IntentConfirmation,
    specs: tuple[ParameterSpec, ...],
    *,
    guard: NeutralityGuard | None = None,
) -> tuple["OpenQuestion", ...]:
    """未澄清项逐条结构化（§5）：``defaulted`` 与 ``missing`` 两类**不混同**。

    - 已进 ``confirmation.values`` 且其确认卡条目来源为 ``default`` → ``defaulted``
      （已按默认值填充：跳过或超澄清预算者）
    - 未进 ``values`` → ``missing``（待追问；``default`` 一并带上，追问可作可跳过的默认）
    - 已由用户给出 / 指令预填者**不进**本清单

    ``guard`` 缺省用默认规则库；问句命中 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)
    执行点 2 即抛 :class:`ConfigValidationError`（**阻断**）。
    """
    rule = guard if guard is not None else NeutralityGuard()
    given = confirmation.values
    filled = {
        item.param for item in confirmation.items
        if item.param and item.source == "default"
    }
    out: list[OpenQuestion] = []
    for spec in specs:
        if spec.name in given:
            if spec.name in filled:
                out.append(_question(rule, spec, "defaulted"))
            continue
        out.append(_question(rule, spec, "missing"))
    return tuple(out)


def _question(guard: NeutralityGuard, spec: ParameterSpec,
              source: "OpenQuestionSource") -> "OpenQuestion":
    text = f"参数 {spec.name}：{spec.description}"
    verdict = guard.check_output(text)
    if not verdict.passed:
        hits = "、".join(f"{f.kind}:{f.matched}" for f in verdict.findings)
        raise ConfigValidationError(
            f"草稿生成文案未过中性校验（{hits}）：{text!r}",
            findings=tuple(hits.split("、")),
        )
    return OpenQuestion(
        param=spec.name, prompt=text, default=spec.default, source=source
    )


class OpenQuestion(BaseModel):
    """一条未澄清项（§5 ``open_questions`` 的元素形态）。

    ``source="defaulted"`` 者已按 `default` 填充并**同批进** `parameter_draft`；
    ``source="missing"`` 者待追问。``prompt`` 是**生成文案**（过 §6）。
    """

    model_config = ConfigDict(frozen=True)

    param: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1)
    default: Any = None
    source: OpenQuestionSource

    def line(self) -> str:
        """渲染为一行（生成描述 + 来源标签）。"""
        return f"{self.prompt}（{OPEN_QUESTION_LABELS[self.source]}）"


class ConfigDraft(BaseModel):
    """[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的配置草稿（四字段）。

    本模型承载**参数分支**（`target` 为 Skill）；`target` 为工作流时草稿由
    :mod:`st_agent.l3.config.workflow` 以 [03-L1 §4](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
    的接收载体（四字段 + `workflow_draft` 子对象）承载，**不在此另立平行模型**。
    """

    model_config = ConfigDict(frozen=True)

    target: str = Field(min_length=1)
    """目标 Skill / 工作流（新配置或修改既有配置）。"""
    parameter_draft: dict[str, Any] = Field(default_factory=dict)
    """从对话提取的参数值（对齐 ``SkillDescriptor.parameters``）。"""
    understanding_summary: str = Field(min_length=1)
    """结构化理解摘要（确认卡条目同源；见 :func:`summarise`）。"""
    open_questions: tuple[OpenQuestion, ...] = ()
    """未澄清项（逐条结构化，见 :class:`OpenQuestion`）。"""


class ConfigDraftProtocol:
    """配置草稿的生成面（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param descriptors: 描述体取数口（鸭子类型 ``get(skill_id) -> SkillDescriptor``，
        如 L1 的 ``SkillRegistry``）——``open_questions`` 的候选源与默认值来源；
        缺省 ``None`` 时不产 `open_questions`（不臆测）
    :param workflow: 工作流分支的生成口（鸭子类型 ``build(confirmation, structure)
        -> ResultEnvelope``，如 :class:`st_agent.l3.config.workflow.WorkflowDraftBuilder`）；
        缺省 ``None`` → 工作流分支 fail-closed（``unavailable`` + 点名）
    :param guard: 中性规则库守卫（缺省用默认规则库；**无**关闭开关）
    """

    def __init__(
        self,
        *,
        descriptors: Any = None,
        workflow: Any = None,
        guard: NeutralityGuard | None = None,
    ) -> None:
        self._descriptors = descriptors
        self._workflow = workflow
        self._guard = guard if guard is not None else NeutralityGuard()

    # ───────────────────────── §5 生成 ─────────────────────────

    def generate(
        self,
        confirmation: IntentConfirmation,
        *,
        structure: Mapping[str, Any] | None = None,
    ) -> ResultEnvelope:
        """已确认的确认卡 → 草稿（载荷即 :class:`ConfigDraft`，或工作流分支的载体）。

        :param structure: 工作流结构载荷（`name` / `description` / `nodes` /
            `edges` / `groups` / `schedule`，可含 `flow_name`）——**非空即工作流分支**
            （[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的判定只有这一处）
        """
        if not confirmation.confirmed:
            return ResultEnvelope.validation_failed(
                "意图确认卡未经用户确认，不得生成配置草稿（05 §5）"
            )
        target = (confirmation.target or "").strip()
        if not target:
            return ResultEnvelope.validation_failed(
                "配置草稿缺少目标 Skill / 工作流，无法生成（05 §5）"
            )
        if has_workflow_structure(structure):
            if self._workflow is None:
                return ResultEnvelope.unavailable(
                    WORKFLOW_BRANCH_ABSENT_REASON, last_updated_at=_now()
                )
            return self._workflow.build(confirmation, structure)
        specs = self._specs(target)
        values = dict(confirmation.values)
        unknown = tuple(sorted(k for k in values if k not in {s.name for s in specs})) if specs else ()
        if unknown:
            return ResultEnvelope.validation_failed(
                f"参数 {'、'.join(unknown)} 未在目标 {target} 的声明参数中（05 §5）"
            )
        try:
            questions = open_questions(confirmation, specs, guard=self._guard)
        except ConfigValidationError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        return ResultEnvelope.ok(
            ConfigDraft(
                target=target,
                parameter_draft=values,
                understanding_summary=summarise(confirmation),
                open_questions=questions,
            ),
            as_of=_now(),
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _specs(self, target: str) -> tuple[ParameterSpec, ...]:
        if self._descriptors is None:
            return ()
        try:
            descriptor = self._descriptors.get(target)
        except Exception:
            return ()
        return tuple(getattr(descriptor, "parameters", ()) or ())


def checked_draft(**fields: Any) -> ConfigDraft:
    """构造一份草稿（非法 → :class:`ConfigValidationError`）。"""
    try:
        return ConfigDraft(**fields)
    except ValidationError as exc:
        raise ConfigValidationError(f"配置草稿非法：{exc}") from exc
