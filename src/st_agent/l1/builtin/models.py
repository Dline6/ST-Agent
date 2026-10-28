"""内置原语的声明体（T-AGT-008.2；[01 §2](../../../docs/技术架构-v2/01-平台共享契约.md) + [03 §1](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。

内置原语与 Skill 共用**同一条**描述体与注册面（[01 §2]），差别只在三条写面语义
（不可编辑 / 不可版本化 / 不可导出分享，见
:data:`st_agent.contracts.capability_types.SkillSource`）。本模块只管**声明**：
:class:`BuiltinPrimitive` 是「种子 + 执行器工厂」，安装时被投影成
``SkillDescriptor`` 落进同一个 ``SkillRegistry``——**不是**第二份能力登记。

**``permissions`` 必填且无默认值**：官方 Pack 的 ``permissions=()`` 是「只经注入
取数面、自身不发文件 / 网络 / 命令」的**结论**，不是可照抄的默认值。原语若碰三类
出口，必须在此逐条声明，运行时由 ``SkillRunner`` / ``SkillSandbox`` 按 01 §10
逐项核对（未声明或未批准一律拦截且不执行原动作）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from st_agent.contracts.capability_types import OfflineLevel, ParameterSpec
from st_agent.contracts.registry_types import SemVer
from st_agent.l1.runner.runner import SkillContext, SkillExecutor
from st_agent.l1.skills.ids import check_skill_base

__all__ = ["BuiltinExecutorFactory", "BuiltinPrimitive"]

BuiltinExecutorFactory = Callable[[Mapping[str, Any]], SkillExecutor]
"""执行器工厂：注入上下文（``requires`` 所列键的只读视图）→ ``SkillExecutor``。

工厂在**安装期**调用一次，上下文缺失即安装失败（不在执行期才发现 KeyError）。
"""


class BuiltinPrimitive(BaseModel):
    """一条内置原语的种子（形态对齐 ``SkillRegistry.register`` 的参数面）。

    ``base`` / ``version`` 与 Skill 同规则拼出 ``skill_id``（``sk_<base>_v<主>.<次>``），
    故工具目录的版本折叠、执行目标解析与其余来源**完全一致**。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    base: str
    """能力身份（``sk_`` 前缀的小写注册名；不含版本）。"""
    version: SemVer = Field(default_factory=lambda: SemVer(major=1, minor=0))
    """注册版本；内置原语**不可版本化**（发布新版本被拒），但 id 仍带版本。"""
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    parameters: tuple[ParameterSpec, ...] = ()
    permissions: tuple[str, ...]
    """权限声明（01 §10）——**显式给出**；仅经注入取数面者可空。"""
    offline_level: OfflineLevel = "full"
    requires: tuple[str, ...] = ()
    """执行器工厂所需的上文键（安装期核对，缺失即拒）。"""
    executor: BuiltinExecutorFactory
    """执行器工厂（接注入上下文，产出 ``SkillExecutor``）。"""

    @field_validator("base")
    @classmethod
    def _check_base(cls, value: str) -> str:
        return check_skill_base(value)

    @field_validator("version", mode="before")
    @classmethod
    def _parse_version(cls, value: Any) -> Any:
        return SemVer.parse(value) if isinstance(value, str) else value


# ``SkillExecutor`` 的签名里带 ``SkillContext`` 前向引用（定义在 runner 模块），
# 在本模块名空间补上解析所需的类型名。
BuiltinPrimitive.model_rebuild(_types_namespace={"SkillContext": SkillContext})
