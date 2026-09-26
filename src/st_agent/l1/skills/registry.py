"""Skill 注册表（T-L1-001.1；03 §1.1 注册与发现 + §1.4 版本管理）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 描述体 → ``config`` 分区 ``skill-registry/<skill_id>.json``
  （整文件加密落盘；注册即落盘，GWT-R）
- 待检查标记 → ``config`` 分区 ``skill-update/<base>.json``
  （主版本发布时写入，确认升级/跳过时清除，GWT-5）

注册语义：
- ``register(base, version, ...)``：按 A1 规则拼 ``skill_id``；
  ``name`` 经注入的 ``name_neutrality_check`` 过 §6（A3）；
  ``permissions`` 经 ``validate_permissions`` 过 §10（A3）；
  ``parameters`` 默认值走 ``ParameterSpec`` 构造期校验（GWT-7）；
  同 ``skill_id`` 已存在 → ``SkillExistsError``（更新走 ``publish_version``）。
- ``publish_version``：同 base 新版本发布；主版本变更 → 写入待检查标记
  （引用方 `follow-latest` 的升级需先 ``confirm_update``；``locked`` 引用方
  不受影响，老版本文件保留）。
- ``set_parameters``：Studio 改参入口（GWT-3）——只改参数默认值，
  超范围即 ``SkillValidationError`` 不保存；主版本不变故不写待检查标记。
- ``validate_call_params``：执行前调用传参校验（.2 流水线用；超范围 → 调用方
  包 ``validation_failed``，GWT-7 下半）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.capability_types import ParameterSpec, Provenance, SkillDescriptor
from st_agent.contracts.registry_types import SemVer, validate_permissions
from st_agent.l1.sandbox.models import DISABLED_PREFIX
from st_agent.l1.skills.errors import (
    SkillExistsError,
    SkillNotFoundError,
    SkillValidationError,
)
from st_agent.l1.skills.ids import base_of, check_skill_id, parse_skill_id, skill_id_for

__all__ = [
    "SKILL_PREFIX",
    "UPDATE_PREFIX",
    "SkillRegistry",
    "UpdateInfo",
    "validate_param_values",
]

SKILL_PREFIX = "skill-registry/"
"""``config`` 分区内 Skill 描述体的目录前缀。"""

UPDATE_PREFIX = "skill-update/"
"""``config`` 分区内待检查标记的目录前缀。"""

NeutralityCheck = Callable[[str], bool]
"""命名校验回调形态：``name -> 通过与否``（默认接 ``NeutralityGuard.check_name``）。"""


class UpdateInfo(BaseModel):
    """待检查标记（GWT-5：主版本变更时引用方标「待检查」的数据形态）。"""

    model_config = ConfigDict(frozen=True)

    base: str
    from_version: str
    to_version: str
    changelog: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    published_at: datetime


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def validate_param_values(
    specs: tuple[ParameterSpec, ...], values: dict[str, Any]
) -> dict[str, Any]:
    """校验一组调用/默认值 → 合并后的完整参数（未知参数名即拒）。

    超范围/类型不符 → ``SkillValidationError``（说明理由，不允许保存/调用）。
    未给出的参数取 ``ParameterSpec.default``。
    """
    by_name = {p.name: p for p in specs}
    for key in values:
        if key not in by_name:
            raise SkillValidationError(f"未知参数 {key!r}（该 Skill 未声明）")
    merged: dict[str, Any] = {}
    for name, spec in by_name.items():
        value = values.get(name, spec.default)
        _check_single(spec, value)
        merged[name] = value
    return merged


def _check_single(spec: ParameterSpec, value: Any) -> None:
    """单参数值校验（与 ``ParameterSpec`` 构造期口径同源，GWT-7）。"""
    t = spec.type
    if value is None:
        return
    if t == "string" and not isinstance(value, str):
        raise SkillValidationError(f"参数 {spec.name!r} 须为字符串")
    if t == "boolean" and not isinstance(value, bool):
        raise SkillValidationError(f"参数 {spec.name!r} 须为布尔值")
    if t in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SkillValidationError(f"参数 {spec.name!r} 须为数值")
        if t == "integer" and int(value) != value:
            raise SkillValidationError(f"参数 {spec.name!r} 须为整数")
        if spec.min_value is not None and float(value) < spec.min_value:
            raise SkillValidationError(
                f"参数 {spec.name!r} 值 {value} 低于下限 {spec.min_value}，不允许保存"
            )
        if spec.max_value is not None and float(value) > spec.max_value:
            raise SkillValidationError(
                f"参数 {spec.name!r} 值 {value} 高于上限 {spec.max_value}，不允许保存"
            )
    if t == "enum" and str(value) not in spec.choices:
        raise SkillValidationError(
            f"参数 {spec.name!r} 值 {value!r} 不在可选范围 {list(spec.choices)} 内"
        )


def _checked_descriptor(**fields) -> SkillDescriptor:
    try:
        return SkillDescriptor(**fields)
    except ValidationError as exc:
        raise SkillValidationError(f"Skill 描述体非法：{exc}") from exc


class SkillRegistry:
    """Skill 注册表门面（03 §1.1 + §1.4；持久化只经 ``Store`` 的 ``config`` 分区）。"""

    def __init__(self, store, name_neutrality_check: NeutralityCheck | None = None) -> None:
        self._store = store
        if name_neutrality_check is None:
            from st_agent.contracts.neutrality import NeutralityGuard
            guard = NeutralityGuard()
            name_neutrality_check = lambda n: guard.check_name(n).passed  # noqa: E731
        self._name_ok = name_neutrality_check

    # ───────────────────────── 写入：注册 / 发布版本 / 改参 / 确认更新 ──

    def register(
        self,
        base: str,
        *,
        version: SemVer | str = "1.0",
        name: str,
        description: str,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        parameters=(),
        dependencies=(),
        source: str = "official",
        provenance=None,
        permissions=(),
        offline_level: str = "full",
        version_policy: str = "follow-latest",
    ) -> SkillDescriptor:
        """注册一个 Skill 版本（已存在 → ``SkillExistsError``，更新走 ``publish_version``）。"""
        skill_id = skill_id_for(base, version if isinstance(version, SemVer) else SemVer.parse(version))
        path = self._skill_path(skill_id)
        existing = self._store.list_files("config")
        if path in existing:
            raise SkillExistsError(
                f"Skill {skill_id!r} 已存在；新版本请用 publish_version，不得静默覆盖"
            )
        # 走到此处该 skill_id 必未注册，故残留的禁用旗标只能属于已消失的旧实体：
        # 新注册的 Skill 不继承旧禁用状态（T-L1-008 / GWT-1）。
        flag = f"{DISABLED_PREFIX}{skill_id}.json"
        if flag in existing:
            self._store.delete("config", flag)
        if not self._name_ok(name):
            raise SkillValidationError(f"Skill 命名未过中性化校验: {name!r}（01 §6）")
        try:
            validate_permissions(tuple(permissions))
        except Exception as exc:
            raise SkillValidationError(f"权限声明非法：{exc}") from exc
        param_specs = tuple(
            p if isinstance(p, ParameterSpec) else self._coerce_param(p) for p in parameters
        )
        for dep in tuple(dependencies):
            check_skill_id(dep)
        descriptor = _checked_descriptor(
            skill_id=skill_id, name=name, description=description,
            input_schema=dict(input_schema or {}), output_schema=dict(output_schema or {}),
            parameters=param_specs, dependencies=tuple(dependencies), source=source,
            provenance=provenance if provenance is not None else Provenance(),
            permissions=tuple(permissions), offline_level=offline_level,
            version_policy=version_policy,
        )
        self._store.put("config", path, descriptor.model_dump_json().encode("utf-8"))
        return descriptor

    def publish_version(
        self, base: str, *, version: SemVer | str, changelog: str = "",
        impact: str = "", **overrides,
    ) -> SkillDescriptor:
        """发布同 base 新版本（主版本变更 → 写待检查标记，GWT-5）。

        未给出的字段沿用同 base 最新版本；``changelog``/``impact`` 在主版本
        变更时必填（更新面板展示用）。
        """
        new_ver = version if isinstance(version, SemVer) else SemVer.parse(version)
        latest = self.get_latest(base)
        if latest is None:
            raise SkillNotFoundError(f"base {base!r} 无已注册版本，首版请用 register")
        _, old_ver = parse_skill_id(latest.skill_id)
        if not (new_ver.major > old_ver.major
                or (new_ver.major == old_ver.major and new_ver.minor > old_ver.minor)):
            raise SkillValidationError(
                f"新版本 {new_ver} 须高于当前 {old_ver}（主.次递增）"
            )
        fields: dict[str, Any] = {
            "name": latest.name, "description": latest.description,
            "input_schema": dict(latest.input_schema), "output_schema": dict(latest.output_schema),
            "parameters": latest.parameters, "dependencies": latest.dependencies,
            "source": latest.source, "provenance": latest.provenance,
            "permissions": latest.permissions, "offline_level": latest.offline_level,
            "version_policy": latest.version_policy,
        }
        fields.update(overrides)
        descriptor = self.register(base, version=new_ver, **fields)
        if new_ver.major != old_ver.major:
            if not changelog.strip() or not impact.strip():
                # 版本已落盘，标记缺失则回滚本次发布（主版本变更无日志不可展示）
                self._store.delete("config", self._skill_path(descriptor.skill_id))
                raise SkillValidationError("主版本变更须给出 changelog 与 impact（更新面板展示用）")
            self._store.put(
                "config", self._update_path(base),
                UpdateInfo(base=base, from_version=f"{old_ver.major}.{old_ver.minor}",
                           to_version=f"{new_ver.major}.{new_ver.minor}",
                           changelog=changelog, impact=impact,
                           published_at=_now()).model_dump_json().encode("utf-8"),
            )
        return descriptor

    def set_parameters(self, skill_id: str, values: dict[str, Any]) -> SkillDescriptor:
        """改 Skill 参数默认值（GWT-3 Studio 入口；超范围即拒不保存，GWT-7）。"""
        descriptor = self._load(skill_id)
        merged = validate_param_values(descriptor.parameters, values)
        new_params = tuple(
            p.model_copy(update={"default": merged[p.name]}) for p in descriptor.parameters
        )
        updated = _checked_descriptor(**{**descriptor.model_dump(), "parameters": new_params})
        self._store.put("config", self._skill_path(skill_id), updated.model_dump_json().encode("utf-8"))
        return updated

    def confirm_update(self, base: str) -> UpdateInfo:
        """确认主版本升级（清除待检查标记；返回标记内容作回执，GWT-5）。"""
        try:
            raw = self._store.get("config", self._update_path(base))
        except KeyError as exc:
            raise SkillNotFoundError(f"base {base!r} 无待检查更新") from exc
        info = UpdateInfo.model_validate_json(raw.decode("utf-8"))
        self._store.delete("config", self._update_path(base))
        return info

    def skip_update(self, base: str) -> UpdateInfo:
        """跳过本次更新（锁定旧版：清除标记，老版本文件保留可继续用，GWT-5）。"""
        return self.confirm_update(base)

    def unregister(self, base: str) -> tuple[str, ...]:
        """反注册一个 base 的**全部版本**（T-L1-007 回收面）。

        删除该 base 的全部描述体与待检查标记，返回被删的 ``skill_id``（升序）。

        - 粒度是 base——单版本删除会让 ``get_latest`` / ``list_versions`` 语义碎裂，
          故不提供。
        - 只动 ``config`` 分区；``execution_log`` 是 append-only 审计（02 §6），不注销。
        - base 无任何已注册版本 → ``SkillNotFoundError``（不静默 no-op）。
        """
        marker = self._update_path(base)  # 形态校验（非 sk_ 前缀即拒）
        versions = self.list_versions(base)
        if not versions:
            raise SkillNotFoundError(f"base {base!r} 无已注册版本，无法反注册")
        removed: list[str] = []
        for descriptor in versions:
            self._store.delete("config", self._skill_path(descriptor.skill_id))
            removed.append(descriptor.skill_id)
        if marker in self._store.list_files("config"):
            self._store.delete("config", marker)
        return tuple(removed)

    # ───────────────────────── 读取：单个 / 最新 / 列表 / 待检查 ─────────

    def get(self, skill_id: str) -> SkillDescriptor:
        """读取单个版本描述体（不存在 → ``SkillNotFoundError``）。"""
        return self._load(skill_id)

    def get_latest(self, base: str) -> SkillDescriptor | None:
        """取同 base 下最高版本（无 → ``None``）。"""
        candidates = [s for s in self.list_versions(base)]
        return candidates[-1] if candidates else None

    def list_versions(self, base: str) -> tuple[SkillDescriptor, ...]:
        """列出同 base 全部版本（按 ``(主, 次)`` 升序）。"""
        out: list[tuple[tuple[int, int], SkillDescriptor]] = []
        for name in self._store.list_files("config"):
            if not (name.startswith(SKILL_PREFIX) and name.endswith(".json")):
                continue
            sid = name[len(SKILL_PREFIX):-len(".json")]
            if base_of(sid) != base:
                continue
            _, ver = parse_skill_id(sid)
            out.append(((ver.major, ver.minor), self._load(sid)))
        return tuple(d for _, d in sorted(out, key=lambda t: t[0]))

    def list_all(self) -> tuple[SkillDescriptor, ...]:
        """列出全部已注册 Skill 版本（按 ``(base, 主, 次)`` 排序）。"""
        out: list[tuple[tuple[str, int, int], SkillDescriptor]] = []
        for name in self._store.list_files("config"):
            if not (name.startswith(SKILL_PREFIX) and name.endswith(".json")):
                continue
            sid = name[len(SKILL_PREFIX):-len(".json")]
            base, ver = parse_skill_id(sid)
            out.append(((base, ver.major, ver.minor), self._load(sid)))
        return tuple(d for _, d in sorted(out, key=lambda t: t[0]))

    def pending_updates(self) -> tuple[UpdateInfo, ...]:
        """列出全部待检查标记（更新面板数据源，GWT-5）。"""
        out = []
        for name in self._store.list_files("config"):
            if name.startswith(UPDATE_PREFIX) and name.endswith(".json"):
                raw = self._store.get("config", name)
                out.append(UpdateInfo.model_validate_json(raw.decode("utf-8")))
        return tuple(sorted(out, key=lambda i: i.base))

    def validate_call_params(self, skill_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """校验一次调用的传参 → 完整参数（.2 流水线参数确认步用，GWT-2/GWT-7）。"""
        return validate_param_values(self._load(skill_id).parameters, values)

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _skill_path(skill_id: str) -> str:
        check_skill_id(skill_id)
        return f"{SKILL_PREFIX}{skill_id}.json"

    @staticmethod
    def _update_path(base: str) -> str:
        if not base.startswith("sk_"):
            raise SkillValidationError(f"非法 Skill base {base!r}（须为 sk_ 前缀）")
        return f"{UPDATE_PREFIX}{base}.json"

    @staticmethod
    def _coerce_param(raw: Any) -> ParameterSpec:
        try:
            return raw if isinstance(raw, ParameterSpec) else ParameterSpec(**dict(raw))
        except ValidationError as exc:
            raise SkillValidationError(f"参数声明非法：{exc}") from exc

    def _load(self, skill_id: str) -> SkillDescriptor:
        check_skill_id(skill_id)
        try:
            raw = self._store.get("config", self._skill_path(skill_id))
        except KeyError as exc:
            raise SkillNotFoundError(f"Skill {skill_id!r} 不存在") from exc
        try:
            return _checked_descriptor(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SkillNotFoundError(f"Skill {skill_id!r} 记录损坏无法解析") from exc
