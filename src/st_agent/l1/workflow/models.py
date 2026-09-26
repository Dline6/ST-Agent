"""WorkflowDAG 模型（T-L1-003.1；03 §3.1）。

字段逐条对应 §3.1 的 schema 表：``flow_id`` / ``name`` / ``description``、
``nodes[]``（引用 skill_id + 参数绑定）、``edges[]``（数据流连线）、``groups[]``
（子流程分组）、``schedule``（触发方式）、``version``（版本）。

本模块只承载**结构与形状**约束（如「literal 绑定必须带 value」）。跨节点、
跨 Skill 的判定——成环、连线契约匹配、命名中性化、绑定可解析——是
``st_agent.l1.workflow.validate`` 的职责（校验结论是数据，不是异常）。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.workflow.errors import WorkflowValidationError
from st_agent.l1.workflow.ids import parse_flow_id

__all__ = [
    "ID_PATTERN",
    "ParamBinding",
    "WorkflowDAG",
    "WorkflowEdge",
    "WorkflowGroup",
    "WorkflowNode",
    "WorkflowSchedule",
    "checked_dag",
]

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
"""节点 / 边 / 分组标识形态（小写字母数字起头，可含 ``_`` / ``-``，≤64）。"""

_Id = Annotated[str, Field(min_length=1, max_length=64)]


def _check_id(kind: str, value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise WorkflowValidationError(
            f"非法{kind} {value!r}（须为小写字母数字起头、可含 _ 与 -、≤64 字符）"
        )
    return value


class ParamBinding(BaseModel):
    """节点参数绑定：字面量或上游输出引用（03 §3.1 nodes[].参数绑定）。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["literal", "ref"]
    value: Any = None
    """``kind=literal`` 的常量值（本身可为 ``None``，故按「是否显式给出」判定）。"""
    from_node: str | None = None
    """``kind=ref`` 引用的上游节点 ``node_id``。"""
    path: str | None = None
    """``kind=ref`` 的取值路径（``a`` / ``a.b``，不含 ``$``；缺省即整个输出对象）。"""

    @model_validator(mode="after")
    def _shape(self) -> "ParamBinding":
        if self.kind == "literal":
            if "value" not in self.model_fields_set:
                raise WorkflowValidationError("kind=literal 的绑定须显式给出 value")
            if self.from_node is not None or self.path is not None:
                raise WorkflowValidationError(
                    "kind=literal 的绑定不得带 from_node / path")
            return self
        if not self.from_node:
            raise WorkflowValidationError("kind=ref 的绑定须给出 from_node")
        if self.path is not None and not self.path.strip():
            raise WorkflowValidationError("kind=ref 的 path 不得为空白串")
        return self


class WorkflowSchedule(BaseModel):
    """触发方式（03 §3.1 ``schedule``）。

    本任务只定义**数据形态**；触发实现（调度器）归 ``T-L1-005``（03 §6）。
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["manual", "interval", "cron", "event"] = "manual"
    interval_minutes: int | None = Field(default=None, ge=1)
    cron: str | None = None
    event: str | None = None

    @model_validator(mode="after")
    def _payload_present(self) -> "WorkflowSchedule":
        if self.mode == "interval" and self.interval_minutes is None:
            raise WorkflowValidationError("mode=interval 须给出 interval_minutes")
        if self.mode == "cron" and not self.cron:
            raise WorkflowValidationError("mode=cron 须给出 cron 表达式")
        if self.mode == "event" and not self.event:
            raise WorkflowValidationError("mode=event 须给出 event 名")
        return self


class WorkflowNode(BaseModel):
    """工作流节点：引用一个 Skill + 该次调用的参数绑定。"""

    model_config = ConfigDict(frozen=True)

    node_id: _Id
    skill_id: str
    params: dict[str, ParamBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _node_id_form(self) -> "WorkflowNode":
        _check_id("节点标识", self.node_id)
        return self


class WorkflowEdge(BaseModel):
    """数据流连线（03 §3.1 ``edges[]``）。

    粒度取**节点到节点**：连线是否可接由两侧 Skill 的 ``output_schema`` →
    ``input_schema`` 判定（``contracts.schema_check.check_link``，01 §2 唯一口径）；
    值级取用由下游节点的 ``ParamBinding``（``kind=ref``）声明。
    """

    model_config = ConfigDict(frozen=True)

    edge_id: _Id
    from_node: str
    to_node: str

    @model_validator(mode="after")
    def _edge_form(self) -> "WorkflowEdge":
        _check_id("边标识", self.edge_id)
        for label, ref in (("起点", self.from_node), ("终点", self.to_node)):
            if not ref:
                raise WorkflowValidationError(f"连线{label}不得为空")
        return self


class WorkflowGroup(BaseModel):
    """子流程分组（03 §3.1 ``groups[]``；可整体作为复合 Skill 导出）。"""

    model_config = ConfigDict(frozen=True)

    group_id: _Id
    name: Annotated[str, Field(min_length=1)]
    node_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _group_form(self) -> "WorkflowGroup":
        _check_id("分组标识", self.group_id)
        return self


class WorkflowDAG(BaseModel):
    """工作流定义（03 §3.1 全字段；不可变——变更即新版本）。"""

    model_config = ConfigDict(frozen=True)

    flow_id: str
    """``wf_<注册名>_v<主>.<次>``（01 §1；与 ``version`` 必须一致）。"""
    name: Annotated[str, Field(min_length=1)]
    """过 01 §6 中性化校验（校验在 ``validate`` 步，构造期只保证非空）。"""
    description: Annotated[str, Field(min_length=1)]
    version: SemVer
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...] = ()
    groups: tuple[WorkflowGroup, ...] = ()
    schedule: WorkflowSchedule = Field(default_factory=WorkflowSchedule)

    @model_validator(mode="after")
    def _version_matches_flow_id(self) -> "WorkflowDAG":
        _, ver = parse_flow_id(self.flow_id)
        if (ver.major, ver.minor) != (self.version.major, self.version.minor):
            raise WorkflowValidationError(
                f"flow_id 版本后缀 {ver.major}.{ver.minor} 与 version 字段 "
                f"{self.version.major}.{self.version.minor} 不一致"
            )
        return self

    def node(self, node_id: str) -> WorkflowNode | None:
        """按 ``node_id`` 取节点（无 → ``None``）。"""
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None


def checked_dag(**fields: Any) -> WorkflowDAG:
    """构造 WorkflowDAG（非法 → ``WorkflowValidationError``，不抛裸异常）。"""
    try:
        return WorkflowDAG(**fields)
    except ValidationError as exc:
        raise WorkflowValidationError(f"WorkflowDAG 非法：{exc}") from exc
