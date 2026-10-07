"""统一配置注册表门面（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的消费面）。

[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 规定登记条目的**形态**与三项能力
（双通道可改 / 变更留痕 / 可回滚），并明说「**不规定**由谁提供统一的读取与落值门面
……门面的实现归属由立项决定」。本模块即该门面（归属 L1，决策
[D-067](../../../项目管理/决策日志.md)）。

消费方只依赖以下**语义**（与 L3 的
[`ConfigRegistryPort`](../../l3/config/registry.py) 同构、不依赖存储布局）：

- **读取**——按目标与参数名取条目（:meth:`entry_for`）、按 ``scope`` 列举（:meth:`list`）；
- **落值**——一次调用同时完成「值生效」与「产生 ``change_id``」的变更留痕
  （:meth:`apply`）。

族（:class:`~st_agent.l1.registry.family.ConfigFamily`）按 ``config_id`` 前缀唯一分派：
本模块自带**作用域参数族**（skill / workflow），存量族（``scheduler-policy/`` /
``mcp-hub/`` / ``llm-provider-host/`` / ``memory-policy/`` / ``retention``）由
:meth:`register_family` 接入（见 ``families.py`` 与组合根装配）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l1.registry.errors import RegistryFamilyConflict, RegistryValidationError
from st_agent.l1.registry.family import ConfigFamily, ParamFamily
from st_agent.l1.registry.naming import (
    CHANGE_PARTITION,
    WITHIN_SCOPE_FAMILIES,
    param_config_id,
    target_parts,
)

__all__ = ["ConfigRegistryFacade"]


class ConfigRegistryFacade:
    """01 §7 登记面的统一读取与落值门面。

    :param store: ``Store`` 句柄（条目写 ``config``、留痕写 ``execution_log``）
    :param resolve: ``base → 描述体 | None``（作用域参数族的物化来源；
        缺省 ``None`` 时作用域族只回存盘形态、不物化——消费方随之回落声明面）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store, *, resolve=None, now=None) -> None:
        self._store = store
        self._families: dict[str, ConfigFamily] = {}
        for scope in WITHIN_SCOPE_FAMILIES:
            self.register_family(
                ParamFamily(store, scope=scope, resolve=resolve, now=now)
            )

    # ───────────────────────── 族注册 ─────────────────────────

    def register_family(self, family: ConfigFamily) -> None:
        """接入一族条目；同一 ``config_id`` 前缀已被声明即拒（不静默后者覆盖前者）。"""
        prefix = family.config_prefix
        if not prefix:
            raise RegistryValidationError("族的 config_prefix 不得为空")
        if prefix in self._families:
            raise RegistryFamilyConflict(
                f"config_id 前缀 {prefix!r} 已被 "
                f"{type(self._families[prefix]).__name__} 声明，不重复接入"
            )
        self._families[prefix] = family

    def families(self) -> tuple[ConfigFamily, ...]:
        """已接入的族（按前缀升序）。"""
        return tuple(self._families[k] for k in sorted(self._families))

    def _family_for(self, config_id: str) -> ConfigFamily | None:
        """按**最长前缀**分派（未接入任何族 → ``None``，不臆测）。"""
        best: tuple[str, ConfigFamily] | None = None
        for prefix, family in self._families.items():
            if not config_id.startswith(prefix):
                continue
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, family)
        return None if best is None else best[1]

    # ───────────────────────── 读 ─────────────────────────

    def entry(self, config_id: str) -> ConfigEntry | None:
        """按 ``config_id`` 取登记项（01 §7 读取语义之一）；不属于任何已接入族 → ``None``。"""
        family = self._family_for(config_id)
        return None if family is None else family.entry(config_id)

    def entry_for(self, target: str, param: str) -> ConfigEntry | None:
        """按目标（``skill_id`` / ``flow_id``）与参数名取登记项。

        该参数**未登记**或**目标形态不认得**即 ``None``——消费方据此回落声明面
        （与 L3 [05 §5](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的
        「两通道一致回落并标注来源」同一语义）。
        """
        parts = target_parts(target)
        if parts is None:
            return None
        scope, base = parts
        try:
            config_id = param_config_id(scope, base, param)
        except RegistryValidationError:
            return None
        family = self._family_for(config_id)
        if family is None:
            return None
        return family.entry(config_id)

    def list(self, scope: ConfigScope | None = None) -> tuple[ConfigEntry, ...]:
        """按 ``scope`` 列举已接入族的条目（``scope=None`` 时全列举，按 ``config_id`` 升序）。"""
        out: list[ConfigEntry] = []
        for family in self.families():
            if scope is not None and family.scope != scope:
                continue
            out.extend(family.entries())
        return tuple(sorted(out, key=lambda e: e.config_id))

    # ───────────────────────── 写 ─────────────────────────

    def apply(
        self,
        target: str,
        values: Mapping[str, Any],
        *,
        trace_id: str | None = None,
    ) -> tuple[ChangeRecord, ...]:
        """按目标 + 逐参数取值落值并留痕（**值未变的参数不留痕、不落盘**）。

        返回**本次真实产生的**变更记录（`change_id` 即回滚单位）；目标形态不认得、
        或参数不属任何已接入族即抛 :class:`RegistryValidationError`（不静默丢）。
        """
        parts = target_parts(target)
        if parts is None:
            raise RegistryValidationError(
                f"无法识别的配置目标 {target!r}（须为 sk_ / wf_ 形态的 id）"
            )
        scope, base = parts
        records: list[ChangeRecord] = []
        for param, value in values.items():
            config_id = param_config_id(scope, base, param)
            family = self._family_for(config_id)
            if family is None:
                raise RegistryValidationError(
                    f"{config_id!r} 不属任何已接入的族（门面未注册该族）"
                )
            change = family.apply(config_id, value, trace_id=trace_id)
            if change is not None:
                records.append(change)
        return tuple(records)

    def set(  # noqa: A003 - 与 01 §7「落值」一词对齐
        self, config_id: str, value: Any, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """**按 ``config_id`` 落一次值**并留痕（01 §7 落值语义；值未变 → ``None``）。

        不属于任何已接入族即抛 :class:`RegistryValidationError`。
        """
        family = self._family_for(config_id)
        if family is None:
            raise RegistryValidationError(f"{config_id!r} 不属任何已接入的族（门面未注册该族）")
        return family.apply(config_id, value, trace_id=trace_id)

    # ───────────────────────── 跨族变更读面 ─────────────────────────

    def changes(self) -> tuple[ChangeRecord, ...]:
        """**跨族**列举变更留痕（01 §7「每次变更产生 `change_id`，可回滚」的读面）。

        [08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md) 的「变更历史时间线」按此取数——
        消费方**不依赖**各族各自的落盘前缀（那是存储布局，[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
        「存储布局不构成契约」）。覆盖范围＝本门面**已接入族**（含由组合根注入的 L2 / L5 / L6 各族）
        在 `execution_log` 分区里的变更留痕目录（目录名以 `-change` 结尾，文件名形如
        `<change_id>.json`，如 `weekly-report-change/` / `mcp-hub-change/` / `skill-config-change/`）。

        :return: 按（生效时刻, `change_id`）升序的变更记录；无 → **空集**（不报错）
        :raises RegistryValidationError: 某条留痕形态损坏（**不静默跳过**）
        """
        out: list[ChangeRecord] = []
        for name in self._store.list_files(CHANGE_PARTITION):
            directory, _, filename = name.rpartition("/")
            if not directory.endswith("-change") or not filename.endswith(".json"):
                continue
            raw = self._store.get(CHANGE_PARTITION, name)
            try:
                out.append(ChangeRecord(**json.loads(raw.decode("utf-8"))))
            except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                raise RegistryValidationError(f"变更留痕损坏（{name}）：{exc}") from exc
        return tuple(sorted(out, key=lambda c: (c.applied_at, c.change_id)))
