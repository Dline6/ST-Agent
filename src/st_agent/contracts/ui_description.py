"""01-平台共享契约 §12 UI 描述（Generative UI）。

契约要点（§12）：
- 描述由**产出方**（L3）生成、由**渲染方**（表现层）解析渲染；本节只登记**结构**
- 描述恒经 §5 的 :class:`~st_agent.contracts.result_envelope.ResultEnvelope` 包裹——
  **仅 ``status=ok`` 时 ``data`` 为描述**，非 ok 态不带描述
- ``component_type`` 是**枚举键**，不是代码：渲染器只解析描述、**不解析任意代码**
- ``slots`` 只承载**已解析的 JSON 值**（或 §1 的 ID 引用字符串）——禁表达式 / 模板 /
  可执行片段；本模块以「递归 JSON 值检查」把这条钉在构造期
- ``text_kinds`` **必须逐槽显式标注**（槽名集合须与 ``slots`` 完全一致）：``generated``
  的文本过 §6 执行点 2，``data``（用户原话 / 记忆本体）原样呈现、**不整串复检**
  （[D-053]），留白即误伤用户数据

登记来源：2026-09-29 由 ``T-UI-001.3`` 按 [铁律 8]（先 01 → 05 → 代码）落地，
选型见决策日志 [D-063]。``divergence_map`` 于 2026-10-04 由 ``T-L4-004.2`` 从
:data:`RESERVED_COMPONENT_TYPES` 移入 :data:`IMPLEMENTED_COMPONENT_TYPES`——§12 早已
把它列入「已登记、本期不实现」，其理由即「使后续补这些形态属于**实现**而非改契约」，
故本次不触 §12 的字段表与槽形状（登记与实现的界线见 [D-063] / [D-071]）。

``heatmap`` / ``trend_chart`` 于 2026-10-10 由 ``T-UI-010.1`` 同样从
:data:`RESERVED_COMPONENT_TYPES` 升为**实现**（story-01 的看板明列热力图与图表，属**实现**
而非改契约）；同批 ``graph_view`` / ``timeline_view`` 由 ``T-UI-010.1`` **新增登记并实现**
——前者是记忆区「图谱视图」的节点-边图（枚举面原无对应型），后者是**通用时序面**（既有三型
皆不可复用：``change_timeline`` 的字段硬编码对位「演进变更」、``trace_timeline`` 对位
「推理步骤」），两型均按 [铁律 8] 先改 [01 §12] 再改代码（[D-108]）。
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import DescriptionId

__all__ = [
    "COMPONENT_TYPES",
    "DESCRIPTION_ID_PREFIX",
    "IMPLEMENTED_COMPONENT_TYPES",
    "RESERVED_COMPONENT_TYPES",
    "ComponentType",
    "TextKind",
    "UiDescription",
    "checked_description",
    "new_description_id",
]

DESCRIPTION_ID_PREFIX = "desc"
"""01 §1：``description_id`` 形态为 ``desc_`` ＋ 20 位十六进制（见 :class:`DescriptionId`）。"""

ComponentType = Literal[
    # 本期登记并实现（01 §12 与 T-UI-001.3 / T-L3-004 / T-L4-004.2 的交付面对齐）
    "table",
    "report_card",
    "trace_timeline",
    "context_card",
    "config_draft_card",
    "conflict_adjudication_card",
    "permission_approval_card",
    # 表现层入口（T-UI-004.2）：反思中心的反馈按钮组与提案卡——story-09 设计触点明列的
    # 两件，此前无载体；2026-10-07 按 [铁律 8] 先登记 01 §12 再实现
    "feedback_capture",
    "proposal_card",
    # 演进面（T-UI-004.3）：变更历史时间线（带回滚动作）与逐条设置面板（带应用动作）
    "change_timeline",
    "setting_panel",
    # 生态面（T-UI-004.4）：越界行为警示（story-10 的「异常行为警示对话框」）
    "violation_alert",
    # 由「已登记未实现」升为**本期实现**：01 §12 提前登记 `divergence_map` 的用途即此
    # ——补该形态属**实现**而非改契约（06 §5 分歧图；交付见 T-L4-004.2）
    "divergence_map",
    # Studio 画布（T-UI-005.1）：story-06 主画布的载体——会话 + 节点 / 连线 / 分组 +
    # 校验违规 + 接受 / 否决；可视化微调经固定回环路由，不进描述（01 §12 动作不进描述）
    "studio_canvas",
    # 由「已登记未实现」升为**本期实现**（T-UI-010.1）：01 §12 提前登记 `heatmap` /
    # `trend_chart` 的用途即此——story-01 的看板明列热力图与图表，补该形态属**实现**而非改契约
    "heatmap",
    "trend_chart",
    # **新登记并实现**（T-UI-010.1）：记忆区「图谱视图」的节点-边图在枚举面原无对应型
    # （`divergence_map` 的 `network` 槽同形态但槽语义对位「分歧」且与 `matrix` 同为必填，
    # 复用会混淆语义），故按铁律 8 先改 01 §12 再实现
    "graph_view",
    # **新登记并实现**（T-UI-010.1）：通用**时序视图**——既有三型皆不可复用（`change_timeline`
    # 的字段硬编码对位「演进变更」、`trace_timeline` 对位「推理步骤」），故另立；承载记忆区
    # 时间线视图 / 修正历史页与触达区推送历史时间线（跨两区，非某一页专属）
    "timeline_view",
    # 已登记、本期不实现——提前登记使后续补这些形态属于「实现」而非「改契约」
    "risk_badge",
    "pinned_board",
]
"""组件类型键（§12 的枚举面）。**必须是键，不是代码**——渲染器按它查注册表。"""

COMPONENT_TYPES: tuple[str, ...] = get_args(ComponentType)
"""§12 登记的**全部**组件类型的机器可读副本（与 :data:`ComponentType` 同源，不会漂移）。"""

IMPLEMENTED_COMPONENT_TYPES: tuple[str, ...] = (
    "table",
    "report_card",
    "trace_timeline",
    "context_card",
    "config_draft_card",
    "conflict_adjudication_card",
    "permission_approval_card",
    "feedback_capture",
    "proposal_card",
    "change_timeline",
    "setting_panel",
    "violation_alert",
    "divergence_map",
    "studio_canvas",
    "heatmap",
    "trend_chart",
    "graph_view",
    "timeline_view",
)
"""**本期实现**的类型（渲染方有对应渲染件）；其余登记型一律走**显式降级占位**。"""

RESERVED_COMPONENT_TYPES: tuple[str, ...] = tuple(
    t for t in COMPONENT_TYPES if t not in IMPLEMENTED_COMPONENT_TYPES
)
"""已登记、本期不实现的类型（§12「留给后续里程碑」）。"""

TextKind = Literal["generated", "data"]
"""槽文本的来源分栏（§12）：``generated`` 过 §6、``data`` 原样呈现。"""

assert set(IMPLEMENTED_COMPONENT_TYPES) <= set(COMPONENT_TYPES), (
    "已实现的组件类型必须是已登记类型的子集（01 §12）"
)

_SLOT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
"""槽名形态：稳定的标识符风格——渲染件按名取值，靠名而不是位置绑定。"""


def new_description_id() -> str:
    """生成一个 ``description_id``（本机生成、不外发，见 §1）。"""
    return DescriptionId.generate().value


def _ensure_json_value(value: Any, *, path: str) -> None:
    """递归断言值是可序列化的 JSON 值（§12：禁表达式 / 模板 / 可执行片段）。"""
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ContractViolation(f"{path} 不是合法 JSON 值：{value!r} 非有限数")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractViolation(f"{path} 的键必须为字符串，收到 {key!r}")
            _ensure_json_value(item, path=f"{path}.{key}")
        return
    raise ContractViolation(
        f"{path} 不是合法 JSON 值（{type(value).__name__}）——slots 只承载已解析的 JSON 值"
        "或 §1 的 ID 引用，禁表达式 / 模板 / 可执行片段（01 §12）"
    )


class UiDescription(BaseModel):
    """一份 UI 描述（§12）。frozen；不变量在构造期固化。"""

    model_config = ConfigDict(frozen=True)

    description_id: Annotated[str, Field(min_length=1, max_length=128)]
    """描述标识（§1；形态见 :data:`DESCRIPTION_ID_PREFIX`）。"""

    component_type: ComponentType
    """组件类型键；未登记的值在构造期即被拒（**不是**跑到渲染期才降级）。"""

    title: str | None = None
    """生成文案——过 §6 执行点 2（由渲染前的校验门负责，见 §12）。"""

    slots: dict[str, Any] = Field(default_factory=dict)
    """数据绑定：槽名 → 已解析的 JSON 值（或 §1 的 ID 引用字符串）。"""

    layout: dict[str, Any] | None = None
    """声明式布局提示；渲染器**可忽略**（M1 不依赖它）。"""

    as_of: datetime | None = None
    """数据快照时间（§8）；带时区语义。"""

    text_kinds: dict[str, TextKind] = Field(default_factory=dict)
    """槽名 → 文本来源分栏；**逐槽标注、不留白**。"""

    @field_validator("description_id")
    @classmethod
    def _check_description_id(cls, value: str) -> str:
        """形态校验复用 §1 的单一真相源（`DescriptionId`），不另造副本。"""
        try:
            DescriptionId.of(value)
        except ValidationError as exc:
            raise ContractViolation(
                f"description_id 格式非法：{value!r}（期望 {DESCRIPTION_ID_PREFIX}_ + 20 位十六进制，01 §1）"
            ) from exc
        return value

    @field_validator("as_of")
    @classmethod
    def _tz_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ContractViolation("as_of 必须带时区语义（01 §8：内部传输带时区）")
        return value

    @model_validator(mode="after")
    def _enforce_contract_invariants(self) -> "UiDescription":
        for name, value in self.slots.items():
            if not _SLOT_NAME_RE.match(name):
                raise ContractViolation(f"槽名非法：{name!r}（须为稳定标识符，01 §12）")
            _ensure_json_value(value, path=f"slots.{name}")

        missing = sorted(set(self.slots) - set(self.text_kinds))
        extra = sorted(set(self.text_kinds) - set(self.slots))
        if missing or extra:
            raise ContractViolation(
                "text_kinds 必须逐槽显式标注且不得多标（01 §12）："
                f"缺标注 {missing}，多标注 {extra}"
            )
        return self


def checked_description(**fields: Any) -> UiDescription:
    """构造一份描述；形状非法时抛 :class:`ContractViolation`（本层错误，不是 pydantic 裸错）。"""
    try:
        return UiDescription(**fields)
    except ValidationError as exc:
        raise ContractViolation(f"UI 描述不合 01 §12：{exc.errors()[0].get('msg', exc)}") from exc
