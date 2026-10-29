"""内置原语注册与执行器分派面（T-AGT-008.2；M9 增补 T-AGT-013 批）。

对外名字：

- :class:`BuiltinPrimitive`——一条原语的声明（种子 + 执行器工厂）
- :func:`install_builtins`——幂等安装进同一个 ``SkillRegistry`` / ``SkillRunner``
- :data:`BUILTIN_PRIMITIVES`——种子表（本地数据、取数 / 同步、文档取数、循环控制、文件面与开放网络）
- :class:`SyncPort`——同步执行面的**后置绑定**容器（组合根造好 runner 后注入）
- :class:`WebAccess`——开放网络的配置 / 凭据 / HTTP 入口（注入给两条网络原语）

口径见 [01 §2](../../../docs/技术架构-v2/01-平台共享契约.md) 与
[03 §1](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)。
"""

from __future__ import annotations

from st_agent.l1.builtin.errors import BuiltinError, BuiltinRegistrationError
from st_agent.l1.builtin.install import BUILTIN_PRIMITIVES, install_builtins
from st_agent.l1.builtin.models import BuiltinExecutorFactory, BuiltinPrimitive
from st_agent.l1.builtin.ports import SyncPort
from st_agent.l1.builtin.web import WebAccess

__all__ = [
    "BUILTIN_PRIMITIVES",
    "BuiltinError",
    "BuiltinExecutorFactory",
    "BuiltinPrimitive",
    "BuiltinRegistrationError",
    "SyncPort",
    "WebAccess",
    "install_builtins",
]

