"""Skill 侧权限的「声明 → 逐项批准」账本（T-L1-009.1；03 §1.1 §1.5；01 §10）。

[01 §10](../../docs/技术架构-v2/01-平台共享契约.md) 要求「能力在安装/挂载时
声明权限，**用户逐项批准**」。MCP 侧早有对应账本（``McpPermissionBook``），
Skill 侧此前只有描述体的声明（``SkillDescriptor.permissions``）、没有批准态——
无人值守的执行（定时调度、L5 投递）取不到「已批准」这一事实（L1 册 `D1`）。
本模块补上那个落点。

**键为 ``base``（能力身份，不含版本）**：批准随能力跨版本存活——同一 base 的
次版本升级不必重批；声明变化由 :meth:`declare` 对账（新声明转 ``pending``、
已移除的丢弃）。这与「待检查标记按 base 归集」（``SkillRegistry``）同一取向。

**与 MCP 侧账本的关系**：簿记逻辑共用
:class:`~st_agent.l1.permission_book.PermissionBook`，批准态形态与措辞住契约层
:mod:`st_agent.contracts.permissions`，**不存在第二份口径**；两者的区别只有
键的形态（``base`` vs ``server_id``）与落盘前缀。

**MCP 派生 Skill（``sk_mcp_<server>_<tool>``）不走本账本**：其声明即所属 Server
的声明，批准事实在 MCP 账本里，由组合根在供给时委托过去（``T-L1-009.2``）——
同一事实不存两份。

布局（只经 ``Store`` 读写）：``config`` 分区 ``skill-permissions/<base>.json``。
"""

from __future__ import annotations

from st_agent.l1.permission_book import PermissionBook
from st_agent.l1.skills.errors import SkillPermissionError, SkillValidationError
from st_agent.l1.skills.ids import check_skill_base

__all__ = [
    "PERMISSION_PREFIX",
    "SkillPermissionBook",
]

PERMISSION_PREFIX = "skill-permissions/"
"""``config`` 分区内 Skill 批准状态记录的目录前缀。"""


class SkillPermissionBook(PermissionBook):
    """Skill 的权限批准账本（落 ``config`` 分区；键为 ``base``）。

    :param store: ``Store`` 句柄
    """

    def __init__(self, store) -> None:
        super().__init__(
            store,
            prefix=PERMISSION_PREFIX,
            check_key=check_skill_base,
            key_label="Skill",
            error_cls=SkillPermissionError,
            validation_cls=SkillValidationError,
        )
