"""01-平台共享契约 §7 配置元模型（Configurability Registry）+ §9 版本化 + §10 权限。

§7 契约要点：**所有可配置项必须注册到统一配置注册表**，注册后自动获得：
对话可改（description_for_chat）、面板可改（panel_form_spec）、效果一致；
配置可整体导出/导入；每次变更产生 ``change_id`` 可回滚（L6 复用同一机制）。
本模块交付注册条目类型与变更记录类型；注册表的持久化由 L0/L1 各自任务承载。

§9 契约要点：版本「主.次」语义——主版本变更 = 契约不兼容（消费方需人工确认）；
次版本变更 = 兼容性增强。升级导致契约变化时下游自动标「待检查」，
用户确认后才升级——禁止自动破坏下游。

§10 契约要点：三项权限（local_read:<path-scope> / net_access:<host-pattern> /
exec_command），能力安装时声明、用户逐项批准；运行时越界即拦截。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.errors import ContractViolation

__all__ = [
    "ChangeRecord",
    "ConfigEntry",
    "PanelField",
    "PermissionAction",
    "SemVer",
    "VersionBump",
    "parse_permission",
    "validate_permissions",
]

# ───────────────────────── §7 配置元模型 ─────────────────────────

ConfigScope = Literal["global", "skill", "workflow", "channel"]
"""生效范围：全局 / 单 Skill / 单工作流 / 单渠道（§7 scope）。"""


class PanelField(BaseModel):
    """面板通道表单渲染规格的单字段（§7 panel_form_spec 组成单元）。"""

    model_config = ConfigDict(frozen=True)

    widget: Literal["text", "number", "toggle", "select", "slider", "matrix"]
    label: Annotated[str, Field(min_length=1)]
    help_text: Annotated[str, Field(min_length=1)]
    """面板上的参数说明（与 description_for_chat 同义不同通道）。"""
    choices: tuple[str, ...] = ()
    """widget=select 时必填。"""

    @model_validator(mode="after")
    def _choices_for_select(self) -> "PanelField":
        if self.widget == "select" and not self.choices:
            raise ContractViolation("widget=select 时 choices 不得为空")
        return self


class ConfigEntry(BaseModel):
    """配置注册表条目（§7 七字段）。"""

    model_config = ConfigDict(frozen=True)

    config_id: Annotated[str, Field(min_length=3, max_length=128)]
    display_name: Annotated[str, Field(min_length=1)]
    value_schema: dict[str, object]
    """取值契约（JSON Schema 方言或等价结构；不锁定方言，契约文档明确）。"""
    default: object
    description_for_chat: Annotated[str, Field(min_length=1)]
    """面向对话通道的自然语言描述（供 L3 意图理解与澄清）。"""
    panel_form_spec: PanelField
    """面板通道的表单渲染规格（双通道之一）。"""
    scope: ConfigScope
    change_policy: "ChangePolicy"
    """变更是否需确认、是否记入变更历史、是否可回滚。"""

    @model_validator(mode="after")
    def _default_matches_schema(self) -> "ConfigEntry":
        # value_schema 不锁定方言，这里只做最小自洽：声明了 type 时 default 类型须匹配
        t = self.value_schema.get("type")
        if t == "number" and not isinstance(self.default, (int, float)):
            raise ContractViolation("value_schema.type=number 时 default 须为数值")
        if t == "boolean" and not isinstance(self.default, bool):
            raise ContractViolation("value_schema.type=boolean 时 default 须为布尔")
        if t == "string" and not isinstance(self.default, str):
            raise ContractViolation("value_schema.type=string 时 default 须为字符串")
        return self


class ChangePolicy(BaseModel):
    """§7 change_policy：变更是否需确认、是否记入变更历史、是否可回滚。"""

    model_config = ConfigDict(frozen=True)

    requires_confirmation: bool
    """变更是否需用户确认（L6 演进授权档位映射于此）。"""
    record_history: bool = True
    """是否记入变更历史（默认记——共同演化透明，宪法铁律 6）。"""
    rollback_enabled: bool = True
    """是否可回滚（默认可——架构总览 §6「一切可回滚」）。"""


class ChangeRecord(BaseModel):
    """一次配置变更的留痕（§7 变更产生 change_id；L6 演进复用同一机制）。"""

    model_config = ConfigDict(frozen=True)

    change_id: Annotated[str, Field(min_length=3, max_length=128)]
    """§1 change_id 字符串形态（回滚单位）。"""
    config_id: Annotated[str, Field(min_length=3, max_length=128)]
    old_value: object
    new_value: object
    applied_at: Annotated[str, Field(min_length=10)]
    """ISO 8601 带时区时间（§8 时间口径）。"""
    trace_ref: str | None = None
    """关联 trace_id（如适用；01 §11 事件须携带关联 ID）。"""


# ───────────────────────── §9 版本化规范 ─────────────────────────

VersionBump = Literal["major", "minor"]
"""major = 契约不兼容（消费方需人工确认）；minor = 兼容性增强（§9）。"""


class SemVer(BaseModel):
    """「主版本.次版本」语义化版本（§9）。

    契约只定义两位语义；补丁位不进契约（实现自由域，默认 0）。
    """

    model_config = ConfigDict(frozen=True)

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(default=0, ge=0)
    """实现自由域，契约不约束（默认 0）。"""

    @classmethod
    def parse(cls, raw: str) -> "SemVer":
        m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?$", raw.strip())
        if not m:
            raise ContractViolation(f"版本号须为 主.次[.补丁] 格式: {raw!r}")
        return cls(major=int(m.group(1)), minor=int(m.group(2)),
                   patch=int(m.group(3) or 0))

    def bump(self, kind: VersionBump) -> "SemVer":
        if kind == "major":
            return SemVer(major=self.major + 1, minor=0, patch=0)
        return SemVer(major=self.major, minor=self.minor + 1, patch=0)

    def is_compatible_upgrade_from(self, older: "SemVer") -> bool:
        """older → self 是否兼容升级（§9：次版本 = 兼容；主版本 = 不兼容）。"""
        return self.major == older.major

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


# ───────────────────────── §10 权限声明模型 ─────────────────────────

PermissionAction = Literal["local_read", "net_access", "exec_command"]
"""§10 三项权限。"""

_PERM_RE = re.compile(r"^(local_read):(<.+>)$|^(net_access):(<.+>)$|^(exec_command)$")
"""权限串语法：local_read:<path-scope> / net_access:<host-pattern> / exec_command。"""


def parse_permission(decl: str) -> tuple[PermissionAction, str]:
    """解析一条权限声明 → (action, scope)。语法非法即拒（§10）。"""
    m = _PERM_RE.match(decl.strip())
    if not m:
        raise ContractViolation(
            f"权限声明语法非法: {decl!r}（合法形式 local_read:<path-scope> / "
            "net_access:<host-pattern> / exec_command）"
        )
    groups = m.groups()
    if groups[4] == "exec_command":          # 无 scope 权限
        return "exec_command", "*"
    action = groups[0] or groups[2]
    scope = groups[1] or groups[3]
    return action, scope                     # type: ignore[return-value]


def validate_permissions(decls: tuple[str, ...] | list[str]) -> None:
    """校验权限声明列表（SkillDescriptor.permissions / MCP Server 声明共用）。

    §10 规则：能力安装时声明，用户逐项批准；本函数只做语法层校验，
    「用户批准」与「运行时越界拦截」由 L1 沙箱任务承载。
    """
    seen: set[tuple[str, str]] = set()
    for d in decls:
        key = parse_permission(d)
        if key in seen:
            raise ContractViolation(f"权限声明重复: {d!r}")
        seen.add(key)
