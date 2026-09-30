"""配置登记面的消费端口与双通道视图（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) + [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)）。

[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 要求草稿生成与编辑**共用**
[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 配置注册表，保证对话通道与面板通道
效果一致。本模块交付该约定的**消费侧**：

- :class:`ConfigRegistryPort`——登记面的**鸭子类型端口**（读取 / 落值两语义）。
  仓库现**无**统一门面（各子系统自落 `config/<前缀>*.json`），且 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
  明说注册表的持久化不归 L3；故本层只依赖**语义**、不依赖存储布局，门面本体
  待人立项（已登记 [L1 册](../../../项目管理/遗留问题/L1-遗留问题.md)）。缺省不注入
  → 落值 fail-closed（``unavailable`` + 点名），**不假装生效**。
- :func:`dual_channel_view`——把目标声明体的每个参数解析为**一份**双通道视图
  （对话描述 + 面板字段 + 默认值 + **解析来源**）：该参数已登记时取同一条
  ``ConfigEntry``；未登记时**两个通道同样**回落到声明面 ``ParameterSpec``
  （[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 明说它是双通道的参数承载单元）。
  **不出现**「一个通道取登记项、另一个取声明面」——「没走登记表」这件事在视图上
  ``origin`` 字段可见，不静默。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.registry_types import ConfigEntry, ConfigScope, PanelField
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.registry.panel import panel_field_for as _panel_field_for

__all__ = [
    "DESCRIPTORS_ABSENT_REASON",
    "REGISTRY_ABSENT_REASON",
    "ChannelParam",
    "ConfigRegistryPort",
    "DualChannelView",
    "dual_channel_view",
    "panel_field_for",
]

REGISTRY_ABSENT_REASON = (
    "未注入配置登记面，无法取登记项 / 落值（01 §7；统一门面本体待人立项）"
)

DESCRIPTORS_ABSENT_REASON = (
    "未注入描述体取数口，无法枚举目标声明参数（05 §5 / 01 §2）"
)

ParamOrigin = Literal["registry", "declared"]
"""一个参数的解析来源：``registry`` 取自 01 §7 登记项 / ``declared`` 回落到声明参数。"""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class ConfigRegistryPort(Protocol):
    """[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 登记面的消费端口（鸭子类型）。

    消费方只依赖下列**语义**、不依赖存储布局（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
    「登记项的消费面」）：

    - ``entry_for(target, param)``——按目标（Skill / 工作流标识）与参数名取登记项；
      该参数未登记即 ``None``（不是错误）。寻址规则（`config_id` 命名等）属门面实现。
    - ``apply(target, values, *, trace_id=None)``——一次调用同时完成「值生效」与
      「产生 ``change_id``」的变更留痕，返回逐条 :class:`ChangeRecord`。
    """

    def entry_for(self, target: str, param: str) -> ConfigEntry | None: ...

    def apply(
        self,
        target: str,
        values: Mapping[str, Any],
        *,
        trace_id: str | None = None,
    ) -> tuple[Any, ...]: ...


def panel_field_for(spec: ParameterSpec) -> PanelField:
    """声明参数 → 面板字段（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) ``panel_form_spec``）。

    实现为**唯一一份**、住在 L1（``st_agent.l1.registry.panel``）——登记项的物化
    与双通道视图必须同一口径；层间只向下依赖（[铁律 7](../../../../项目管理/工程宪法.md)），
    故 L3 由此再导出，不各造一套。
    """
    return _panel_field_for(spec)


class ChannelParam(BaseModel):
    """一个参数的**双通道**视图（对话描述 + 面板字段 + 默认值 + 解析来源）。

    ``origin`` 让「该参数走没走 01 §7 登记表」在数据上可见（§5：不静默）。
    """

    model_config = ConfigDict(frozen=True)

    name: str
    origin: ParamOrigin
    chat_text: str
    """对话通道：登记的 ``description_for_chat`` 或声明的 ``ParameterSpec.description``。"""
    panel_field: PanelField
    """面板通道：登记的 ``panel_form_spec`` 或由 ``type`` 映射的表单字段。"""
    default: Any = None
    scope: ConfigScope | None = None
    """登记项的生效范围；``origin="declared"`` 时为 ``None``。"""
    config_id: str | None = None
    """登记项标识；``origin="declared"`` 时为 ``None``。"""


class DualChannelView(BaseModel):
    """一个目标下全部参数的双通道视图（§5 的同源落点）。"""

    model_config = ConfigDict(frozen=True)

    target: str
    params: tuple[ChannelParam, ...] = ()

    def chat_channel(self) -> tuple[tuple[str, str], ...]:
        """对话通道视图（参数名 → 描述）。"""
        return tuple((p.name, p.chat_text) for p in self.params)

    def panel_channel(self) -> tuple[tuple[str, PanelField], ...]:
        """面板通道视图（参数名 → 表单字段）。"""
        return tuple((p.name, p.panel_field) for p in self.params)


def dual_channel_view(
    target: str,
    *,
    descriptors: Any = None,
    registry: Any = None,
) -> ResultEnvelope:
    """目标声明体 → 双通道视图（载荷即 :class:`DualChannelView`；§5）。

    :param descriptors: 描述体取数口（鸭子类型 ``get(skill_id) -> SkillDescriptor``）；
        缺省 ``None`` → ``unavailable`` + 点名（参数枚举无从进行）
    :param registry: 登记面端口（:class:`ConfigRegistryPort`）；缺省 ``None`` 时
        **两个通道一致地**只走声明面（``origin="declared"``），不视为失败
    """
    if descriptors is None:
        return ResultEnvelope.unavailable(
            DESCRIPTORS_ABSENT_REASON, last_updated_at=_now()
        )
    try:
        descriptor = descriptors.get(target)
    except Exception as exc:  # 注入端口的实现缺陷 → 显式失败，不静默降级
        return ResultEnvelope.dependency_failed(f"描述体取数口异常：{exc}")
    specs = tuple(getattr(descriptor, "parameters", ()) or ())
    params: list[ChannelParam] = []
    for spec in specs:
        params.append(_channel_param(target, spec, registry))
    return ResultEnvelope.ok(
        DualChannelView(target=target, params=tuple(params)), as_of=_now()
    )


def _channel_param(target: str, spec: ParameterSpec, registry: Any) -> ChannelParam:
    """**同一处解析**一个参数的两个通道（§5：不出现两通道各取一处）。"""
    entry = None
    if registry is not None:
        try:
            entry = registry.entry_for(target, spec.name)
        except Exception:
            entry = None
    if entry is not None:
        return ChannelParam(
            name=spec.name, origin="registry",
            chat_text=entry.description_for_chat,
            panel_field=entry.panel_form_spec,
            default=entry.default, scope=entry.scope, config_id=entry.config_id,
        )
    return ChannelParam(
        name=spec.name, origin="declared",
        chat_text=spec.description,
        panel_field=panel_field_for(spec),
        default=spec.default,
    )
