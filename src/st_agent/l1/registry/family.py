"""01 §7 登记项的「族」协议与**作用域参数族**实现。

门面（:mod:`st_agent.l1.registry.facade`）按 ``config_id`` 前缀把请求分派到族；
一个族负责一族条目的**列举 / 单取 / 落值**。本模块交付：

- :class:`ConfigFamily`——族协议（`.2` 的存量族适配器与后续新族都实现它）；
- :class:`ParamFamily`——**作用域参数族**（``skill.<base>.<param>`` /
  ``workflow.<base>.<param>``）：条目在**读时**按描述体
  [`ParameterSpec`](../../contracts/capability_types.py) **物化**，只有「被显式改过、
  偏离默认」的值才落盘为 override（决策 [D-067](../../../项目管理/决策日志.md) ④）。

物化的两个理由：① 登记表不因播种而变脏——官方 Pack 12 条一律播种，若注册即落条目
会把「有配置」与「无配置」在盘上扫成不可分辨（[D-066](../../../项目管理/决策日志.md)
实测否决过的同型风险）；② 条目落盘时是**自述**的完整 ``ConfigEntry``，故
:meth:`ParamFamily.entry` 在无法反解描述体时仍能回存盘形态，不编造。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    ConfigScope,
)
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.naming import (
    CHANGE_PARTITION,
    CONFIG_PARTITION,
    check_config_id,
    config_id_for_path,
    config_path,
    new_change_id,
    param_config_id,
    parse_param_config_id,
    target_parts,
)
from st_agent.l1.registry.panel import panel_field_for

__all__ = ["ConfigFamily", "ParamFamily", "change_prefix_for", "declaration_for"]

_JSON_TYPE: dict[str, str] = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
}


@runtime_checkable
class ConfigFamily(Protocol):
    """01 §7 登记项的一个族（门面按 ``config_prefix`` 唯一分派）。"""

    config_prefix: str
    """本族 ``config_id`` 的前缀（如 ``"skill."`` / ``"scheduler-policy/"``）。"""

    scope: ConfigScope
    """本族条目的生效范围（01 §7 ``scope``）。"""

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本族全部条目（按 ``config_id`` 升序）。"""
        ...

    def entry(self, config_id: str) -> ConfigEntry | None:
        """取一条登记项；不属于本族或取不到即 ``None``（不是错误）。"""
        ...

    def apply(
        self, config_id: str, value: Any, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次取值并留痕；**值未变 → 不落盘、不留痕**（返回 ``None``）。

        不属于本族、或取值不合约即抛 :class:`RegistryValidationError`。
        """
        ...


def change_prefix_for(scope: str) -> str:
    """作用域族的留痕前缀（``config`` 条目 → ``execution_log`` 留痕）。"""
    return f"{scope}-config-change/"


def declaration_for(scope: str, base: str, spec: ParameterSpec) -> ConfigEntry:
    """声明参数 → 规范 ``ConfigEntry``（01 §7 七字段；未落值时的条目形态）。

    ``value_schema`` 只放**能自洽**的键：``spec.default`` 为 ``None`` 时不写
    ``type``（``ConfigEntry`` 会校验「声明了 type 则 default 类型须匹配」，
    而 ``None`` 与任何具体类型都不匹配）。
    """
    schema: dict[str, object] = {}
    if spec.type == "enum":
        schema["enum"] = list(spec.choices)
        if spec.default is not None:
            schema["type"] = "string"
    else:
        if spec.default is not None:
            schema["type"] = _JSON_TYPE[spec.type]
    if spec.min_value is not None:
        schema["minimum"] = spec.min_value
    if spec.max_value is not None:
        schema["maximum"] = spec.max_value
    return ConfigEntry(
        config_id=param_config_id(scope, base, spec.name),
        display_name=f"{base} · {spec.name}",
        value_schema=schema,
        default=spec.default,
        description_for_chat=spec.description,
        panel_form_spec=panel_field_for(spec),
        scope=scope,  # type: ignore[arg-type]
        change_policy=ChangePolicy(requires_confirmation=False),
    )


def _find_param(descriptor: Any, param: str) -> ParameterSpec | None:
    for spec in getattr(descriptor, "parameters", ()) or ():
        if isinstance(spec, ParameterSpec) and spec.name == param:
            return spec
    return None


def _validate_value(spec: ParameterSpec, value: Any) -> Any:
    """取值须落在参数声明类型 / 取值域内（越界即拒，不静默取最近值）。"""
    if spec.type == "enum":
        if str(value) not in spec.choices:
            raise RegistryValidationError(
                f"参数 {spec.name!r} 取值 {value!r} 不在 choices 内：{spec.choices}"
            )
        return value
    ok = {
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }[spec.type]
    if not ok:
        raise RegistryValidationError(
            f"参数 {spec.name!r} 取值 {value!r} 与声明类型 {spec.type} 不符"
        )
    if spec.type in ("number", "integer"):
        if spec.min_value is not None and float(value) < spec.min_value:
            raise RegistryValidationError(
                f"参数 {spec.name!r} 取值 {value!r} 低于下限 {spec.min_value}"
            )
        if spec.max_value is not None and float(value) > spec.max_value:
            raise RegistryValidationError(
                f"参数 {spec.name!r} 取值 {value!r} 高于上限 {spec.max_value}"
            )
    return value


class ParamFamily:
    """作用域参数族：``<scope>.<base>.<param>``（物化 + 只存 override）。

    :param store: ``Store`` 句柄（条目写 ``config``、留痕写 ``execution_log``）
    :param scope: ``"skill"`` / ``"workflow"``
    :param resolve: ``base → 描述体 | None``（缺省 ``None`` 时只回存盘形态、
        不物化——即「未接入描述体」的 fail-closed 形态）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        store,
        *,
        scope: str,
        resolve: Callable[[str], Any] | None = None,
        now: Callable[[], Any] | None = None,
    ) -> None:
        if scope not in ("skill", "workflow"):
            raise RegistryValidationError(f"未知作用域族 {scope!r}")
        self._store = store
        self._scope = scope
        self._resolve = resolve
        self._now = now
        self.scope: ConfigScope = scope  # type: ignore[assignment]
        self.config_prefix = f"{scope}."
        self.change_prefix = change_prefix_for(scope)

    # ───────────────────────── 读 ─────────────────────────

    def entries(self) -> tuple[ConfigEntry, ...]:
        """全部**已落盘**的 override 条目（读时按声明刷新描述，不落盘即不列）。"""
        prefix = f"{self._scope}/"
        out: list[ConfigEntry] = []
        for name in self._store.list_files(CONFIG_PARTITION):
            if not (name.startswith(prefix) and name.endswith(".json")):
                continue
            entry = self.entry(config_id_for_path(name))
            if entry is not None:
                out.append(entry)
        return tuple(sorted(out, key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        """取一条条目：声明的规范形态叠加已落盘的 override 取值。

        - 取不到声明（未知 base / 未知参数）→ 只回**存盘形态**（条目是自述的，
          不编造声明）；存盘也没有 → ``None``。
        - 不属于本族 → ``None``（不是错误）。
        """
        parts = parse_param_config_id(config_id)
        stored = self._stored(config_id)
        if parts is None or parts[0] != self._scope:
            return stored
        _, base, param = parts
        spec = self._spec(base, param)
        if spec is None:
            return stored
        declared = declaration_for(self._scope, base, spec)
        if stored is None:
            return declared
        return declared.model_copy(update={"default": stored.default})

    # ───────────────────────── 写 ─────────────────────────

    def apply(
        self, config_id: str, value: Any, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次取值并留痕；值未变 → ``None``（不落盘、不留痕）。"""
        parts = parse_param_config_id(config_id)
        if parts is None or parts[0] != self._scope:
            raise RegistryValidationError(f"{config_id!r} 不属本族（{self.config_prefix}*）")
        _, base, param = parts
        spec = self._spec(base, param)
        if spec is None:
            raise RegistryValidationError(
                f"未知配置目标：{base!r} 无参数 {param!r}（描述体未接入或名字不符）"
            )
        _validate_value(spec, value)
        old_value = spec.default
        stored = self._stored(config_id)
        if stored is not None:
            old_value = stored.default
        if old_value == value:
            return None
        entry = declaration_for(self._scope, base, spec).model_copy(update={"default": value})
        self._store.put(
            CONFIG_PARTITION, config_path(config_id),
            entry.model_dump_json().encode("utf-8"),
        )
        change = ChangeRecord(
            change_id=new_change_id(),
            config_id=config_id,
            old_value=old_value,
            new_value=value,
            applied_at=self._now_iso(),
            trace_ref=trace_id,
        )
        self._store.put(
            CHANGE_PARTITION, f"{self.change_prefix}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"),
        )
        return change

    def apply_targeted(
        self, target: str, values: Mapping[str, Any], *, trace_id: str | None = None
    ) -> tuple[ChangeRecord, ...]:
        """按目标 id + 参数名批量落值（门面 :meth:`apply` 的便捷入口）。"""
        parts = target_parts(target)
        if parts is None or parts[0] != self._scope:
            raise RegistryValidationError(f"目标 {target!r} 不属本族（{self.config_prefix}*）")
        scope, base = parts
        records: list[ChangeRecord] = []
        for param, value in values.items():
            change = self.apply(param_config_id(scope, base, param), value, trace_id=trace_id)
            if change is not None:
                records.append(change)
        return tuple(records)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _spec(self, base: str, param: str) -> ParameterSpec | None:
        if self._resolve is None:
            return None
        try:
            descriptor = self._resolve(base)
        except Exception:  # 取数口实现缺陷 / 无此 base → 不物化（不臆测）
            return None
        return _find_param(descriptor, param)

    def _stored(self, config_id: str) -> ConfigEntry | None:
        try:
            check_config_id(config_id)
            raw = self._store.get(CONFIG_PARTITION, config_path(config_id))
        except KeyError:
            return None
        return ConfigEntry.model_validate_json(raw.decode("utf-8"))

    def _now_iso(self) -> str:
        now = self._now() if self._now is not None else datetime.now().astimezone()
        return now.isoformat()
