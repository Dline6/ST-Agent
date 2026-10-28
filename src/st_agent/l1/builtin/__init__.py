"""内置原语注册与执行器分派面（T-AGT-008.2）。

对外三个名字：

- :class:`BuiltinPrimitive`——一条原语的声明（种子 + 执行器工厂）
- :func:`install_builtins`——幂等安装进同一个 ``SkillRegistry`` / ``SkillRunner``
- :data:`BUILTIN_PRIMITIVES`——种子表（本叶为空，后续原语叶追加）

口径见 [01 §2](../../../docs/技术架构-v2/01-平台共享契约.md) 与
[03 §1](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)。
"""

from __future__ import annotations

from st_agent.l1.builtin.errors import BuiltinError, BuiltinRegistrationError
from st_agent.l1.builtin.install import BUILTIN_PRIMITIVES, install_builtins
from st_agent.l1.builtin.models import BuiltinExecutorFactory, BuiltinPrimitive

__all__ = [
    "BUILTIN_PRIMITIVES",
    "BuiltinError",
    "BuiltinExecutorFactory",
    "BuiltinPrimitive",
    "BuiltinRegistrationError",
    "install_builtins",
]
