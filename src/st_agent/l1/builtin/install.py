"""内置原语的幂等安装（T-AGT-008.2；[01 §2](../../../docs/技术架构-v2/01-平台共享契约.md) + [03 §1](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）。

安装做两件事，与官方 Pack 的同形：

1. **播种描述体**——把每条 :class:`~st_agent.l1.builtin.models.BuiltinPrimitive`
   以 ``source="builtin"`` 写进**同一个** ``SkillRegistry``（已注册的 ``skill_id``
   即跳过，不静默覆盖；与既有**非内置**能力同 id 则显式拒）。
2. **注册执行器**——按种子工厂为 ``skill_id`` 注册执行器。执行器不持久化，故**每次
   安装都重绑**；同一 ``skill_id`` 重复注册即覆盖（同 ``SkillRunner.register_executor``）。

**原子性口径**：先校验全部种子（形态 / 重复 / 权限语法 / ``requires`` 依赖），
再落任何描述体——任一种子不成立即整体拒，不留半个原语。

**本叶不接组合根**：安装入口由 :func:`st_agent.l1.runtime.build_l1_runtime` 与官方
Pack 同点调用；种子表 :data:`BUILTIN_PRIMITIVES` 目前含本地数据面的两条原语
（``local_sql`` / ``data_status``，T-AGT-010.*）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from st_agent.contracts.capability_types import Provenance
from st_agent.contracts.registry_types import validate_permissions
from st_agent.l1.builtin.ask_user import ASK_USER
from st_agent.l1.builtin.data_fetch import DATA_FETCH
from st_agent.l1.builtin.data_status import DATA_STATUS
from st_agent.l1.builtin.data_sync import DATA_SYNC
from st_agent.l1.builtin.docs import DOCS
from st_agent.l1.builtin.errors import BuiltinRegistrationError
from st_agent.l1.builtin.local_sql import LOCAL_SQL
from st_agent.l1.builtin.models import BuiltinPrimitive
from st_agent.l1.builtin.plan_update import PLAN_UPDATE
from st_agent.l1.skills.errors import SkillExistsError, SkillValidationError
from st_agent.l1.skills.ids import skill_id_for

__all__ = ["BUILTIN_PRIMITIVES", "install_builtins"]

BUILTIN_PRIMITIVES: tuple[BuiltinPrimitive, ...] = (
    LOCAL_SQL, DATA_STATUS, DATA_SYNC, DATA_FETCH, DOCS, PLAN_UPDATE, ASK_USER,
)
"""内置原语种子表（本地数据面四条 + 循环控制面两条 + 文档取数一条）。

一行一条能力。权限必须逐条显式给出（见 ``models.BuiltinPrimitive.permissions``）。
循环控制面两条（``sk_plan_update`` / ``sk_ask_user``）在循环内由 L3 拦截执行，
登记的目的是让它们进入**同一个**工具目录（注册面＝可见面），不是第二条执行路径。
``sk_data_sync`` / ``sk_data_fetch`` 的执行面（``DataSyncRunner``）由 M5 组合根后置
注入（``L1Runtime.bind_data_sync``），未注入时原语显式 ``unavailable``。
"""

_SOURCE = "builtin"


def install_builtins(
    registry: Any,
    runner: Any,
    *,
    context: Mapping[str, Any] | None = None,
    primitives: Sequence[BuiltinPrimitive] | None = None,
) -> tuple[str, ...]:
    """安装内置原语 → 本次**新增**的 ``skill_id`` 元组（已存在的描述体不重复注册）。

    :param registry: ``SkillRegistry``（播种描述体）
    :param runner: ``SkillRunner``（注册执行器）
    :param context: 注入给执行器工厂的依赖（键须覆盖各条种子的 ``requires``）
    :param primitives: 种子序列；``None`` 即用 :data:`BUILTIN_PRIMITIVES`

    :raises BuiltinRegistrationError: 种子重复 / 同 id 与既有非内置能力冲突 /
        ``requires`` 依赖缺失 / 权限声明或 id 形态非法
    """
    seeds = tuple(BUILTIN_PRIMITIVES if primitives is None else primitives)
    ctx = dict(context or {})
    _validate_seeds(seeds, ctx)

    view = MappingProxyType(ctx)
    added: list[str] = []
    for seed in seeds:
        skill_id = _skill_id_of(seed)
        try:
            descriptor = registry.register(
                seed.base,
                version=seed.version,
                name=seed.name,
                description=seed.description,
                input_schema=dict(seed.input_schema),
                output_schema=dict(seed.output_schema),
                parameters=seed.parameters,
                dependencies=(),
                source=_SOURCE,
                provenance=Provenance(),
                permissions=seed.permissions,
                offline_level=seed.offline_level,
                version_policy="locked",
            )
        except SkillExistsError:
            descriptor = _existing_builtin(registry, skill_id)
        else:
            added.append(descriptor.skill_id)
        runner.register_executor(skill_id, seed.executor(view))
    return tuple(added)


def _validate_seeds(
    seeds: Sequence[BuiltinPrimitive], context: Mapping[str, Any]
) -> None:
    """安装前整体校验（不写任何存储）。"""
    seen: set[str] = set()
    for seed in seeds:
        skill_id = _skill_id_of(seed)
        if skill_id in seen:
            raise BuiltinRegistrationError(
                f"内置原语种子重复：{skill_id!r} 出现多次（不静默取其一）"
            )
        seen.add(skill_id)
        if not callable(seed.executor):
            raise BuiltinRegistrationError(
                f"内置原语 {skill_id!r} 的执行器工厂须为 callable"
            )
        try:
            validate_permissions(seed.permissions)
        except Exception as exc:  # noqa: BLE001 —— 统一为本层失败形态
            raise BuiltinRegistrationError(
                f"内置原语 {skill_id!r} 的权限声明非法：{exc}（01 §10）"
            ) from exc
        missing = [key for key in seed.requires if key not in context]
        if missing:
            raise BuiltinRegistrationError(
                f"内置原语 {skill_id!r} 缺少注入依赖 {missing}——"
                "在安装期显式失败，不拖到执行期才 KeyError"
            )


def _skill_id_of(seed: BuiltinPrimitive) -> str:
    try:
        return skill_id_for(seed.base, seed.version)
    except SkillValidationError as exc:
        raise BuiltinRegistrationError(
            f"内置原语 {seed.base!r} 的 skill_id 不合法：{exc}"
        ) from exc


def _existing_builtin(registry: Any, skill_id: str) -> Any:
    """已存在同 id 时取回描述体；**非内置**来源即冲突（01 §2）。"""
    descriptor = registry.get(skill_id)
    if descriptor.source != _SOURCE:
        raise BuiltinRegistrationError(
            f"内置原语 {skill_id!r} 与既有非内置能力同 id（source={descriptor.source!r}）——"
            "拒绝静默覆盖（01 §2）"
        )
    return descriptor
