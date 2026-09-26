"""官方 Skill Pack 装载入口（T-L1-004.1；03 §2）。

一次调用做完两件事，使官方 Pack 「装上就能跑」：

1. **播种描述体**——委托 :func:`st_agent.l1.skills.pack.ensure_official_pack`
   （幂等：已注册版本即跳过，不静默覆盖）
2. **注册执行器**——按 Bundle 模块的 ``EXECUTORS`` 表（``base -> 工厂``）为对应
   ``skill_id`` 注册执行器（工厂接取数源，产出 ``SkillExecutor``）

两 Bundle 的执行模块**延迟导入**（:data:`BUNDLE_MODULES`）：装载入口不因
某个 Bundle 尚未落地而不可导入；:func:`uncovered_bases` 给出「尚无执行器的
base」这一装载完整性口径，供组合根与用例断言。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import Any

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.runner.runner import SkillExecutor
from st_agent.l1.skills.ids import skill_id_for
from st_agent.l1.skills.official.common import MarketQuerySource
from st_agent.l1.skills.official.errors import OfficialPackLoadError
from st_agent.l1.skills.pack import OFFICIAL_PACK, ensure_official_pack

__all__ = [
    "BUNDLE_MODULES",
    "ExecutorFactory",
    "executor_table",
    "install_official_pack",
    "uncovered_bases",
]

ExecutorFactory = Callable[[MarketQuerySource], SkillExecutor]
"""执行器工厂：取数源 → ``(SkillContext, params) -> ResultEnvelope``。"""

BUNDLE_MODULES: tuple[str, ...] = (
    "st_agent.l1.skills.official.cognition",
    "st_agent.l1.skills.official.ambient",
)
"""两 Bundle 的执行器模块（按此顺序合并各自的 ``EXECUTORS`` 表）。"""


def _pack_bases() -> tuple[tuple[str, SemVer], ...]:
    """官方 Pack 的 ``(base, 版本)`` 清单（种子即真相源，不另抄一份）。"""
    return tuple(
        (seed["base"], SemVer.parse(seed.get("version", "1.0")))
        for seed in OFFICIAL_PACK
    )


def executor_table() -> dict[str, ExecutorFactory]:
    """合并两 Bundle 模块的 ``EXECUTORS`` 表 → ``base -> 执行器工厂``。"""
    table: dict[str, ExecutorFactory] = {}
    for module_name in BUNDLE_MODULES:
        module = importlib.import_module(module_name)
        part = getattr(module, "EXECUTORS", None)
        if not isinstance(part, dict):
            raise OfficialPackLoadError(
                f"{module_name} 未提供 EXECUTORS 表（base -> 执行器工厂）"
            )
        table.update(part)
    return table


def uncovered_bases(
    executors: Mapping[str, ExecutorFactory] | None = None,
) -> tuple[str, ...]:
    """官方 Pack 中尚无执行器工厂的 base（装载完整性口径；无缺口 → 空元组）。"""
    table = executor_table() if executors is None else dict(executors)
    return tuple(base for base, _version in _pack_bases() if base not in table)


def install_official_pack(
    registry: Any,
    runner: Any,
    *,
    market_query: MarketQuerySource,
    executors: Mapping[str, ExecutorFactory] | None = None,
) -> tuple[str, ...]:
    """装载官方 Pack → 本次**新增**的 ``skill_id`` 元组（已存在的不重复注册）。

    :param registry: ``SkillRegistry``（播种描述体）
    :param runner: ``SkillRunner``（注册执行器）
    :param market_query: 取数源（须提供 ``query(sql, params=()) -> ResultEnvelope``）
    :param executors: 执行器工厂表；``None`` 即用两 Bundle 模块的默认表
    """
    if not isinstance(market_query, MarketQuerySource):
        raise OfficialPackLoadError(
            "取数源不合口径（须提供 query(sql, params=()) -> ResultEnvelope）；"
            f"收到 {type(market_query).__name__}"
        )
    table = executor_table() if executors is None else dict(executors)
    for base, factory in table.items():
        if not callable(factory):
            raise OfficialPackLoadError(f"base {base!r} 的执行器工厂须为 callable")
    added = ensure_official_pack(registry)
    for base, version in _pack_bases():
        factory = table.get(base)
        if factory is None:
            continue
        runner.register_executor(skill_id_for(base, version), factory(market_query))
    return added
