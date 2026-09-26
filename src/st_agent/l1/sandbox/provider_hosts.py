"""``provider → host`` 映射（T-L1-001.5；未决 Q-001 → 决议 D-005）。

LLM 出网要按沙箱声明的 ``net_access`` 范围核对「端点 provider 的目标主机」，
而「provider → host」映射在 01 契约中未定义归属。2026-09-26 经未决 Q-001
决议（决策日志 D-005）：**暂由 L1 沙箱持有**，按 01 §7 ``ConfigEntry`` 七字段
形态持久化；最终归属由 `T-L1-006`（运行时组合根）复核是否迁回 L0——迁移须
新增 ``D-NNN`` 并以 ``supersedes: D-005`` 指回。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 映射条目 → ``config`` 分区 ``llm-provider-host/<provider>.json``
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ConfigEntry,
    PanelField,
)
from st_agent.l1.sandbox.errors import (
    ProviderHostExistsError,
    ProviderHostNotFoundError,
    SandboxValidationError,
)

__all__ = [
    "PROVIDER_HOST_PREFIX",
    "PROVIDER_PATTERN",
    "ProviderHostRegistry",
    "check_host",
    "check_provider",
]

PROVIDER_HOST_PREFIX = "llm-provider-host/"
"""``config`` 分区内映射条目的目录前缀。"""

PROVIDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
"""提供方标识形状（同时是 ``config`` 分区内的安全相对路径段）。"""

CONFIG_ID_PREFIX = "llm-provider-host/"
"""配置标识前缀（01 §7 ``config_id``；与文件前缀同源便于双向定位）。"""


def check_provider(value: str) -> str:
    """校验提供方标识（非法 → ``SandboxValidationError``）。"""
    if not isinstance(value, str) or not PROVIDER_PATTERN.match(value):
        raise SandboxValidationError(
            f"非法提供方标识 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，"
            "≤64 字符——该标识同时用作配置文件名）"
        )
    return value


def check_host(value: str) -> str:
    """校验目标主机（非空、无空白与 ``/``；非法 → ``SandboxValidationError``）。"""
    if not isinstance(value, str) or not value.strip():
        raise SandboxValidationError("目标主机不得为空")
    host = value.strip()
    if any(ch.isspace() for ch in host) or "/" in host:
        raise SandboxValidationError(f"非法目标主机 {value!r}（不得含空白或 /）")
    return host


def _entry(provider: str, host: str) -> ConfigEntry:
    """按 01 §7 七字段组装一条映射条目（取值即目标主机）。"""
    return ConfigEntry(
        config_id=f"{CONFIG_ID_PREFIX}{provider}",
        display_name=f"LLM 提供方 {provider} 的目标主机",
        value_schema={"type": "string"},
        default=host,
        description_for_chat=(
            f"提供方 {provider} 的 LLM 调用目标主机；沙箱按该主机核对 Skill "
            "声明的 net_access 范围（调用本身另经 L0 出网网关审计）"
        ),
        panel_form_spec=PanelField(
            widget="text",
            label="目标主机",
            help_text="LLM 调用经此主机发出；须落在 Skill 声明的 net_access 内",
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=True),
    )


class ProviderHostRegistry:
    """``provider → host`` 映射注册表（01 §7 条目形态，落 ``config`` 分区）。

    :param store: ``Store`` 句柄（映射只读/写 ``config`` 分区）
    """

    def __init__(self, store) -> None:
        self._store = store

    # ───────────────────────── 写入 ─────────────────────────

    def register(self, provider: str, host: str) -> ConfigEntry:
        """登记一条映射（同一 provider 已存在 → ``ProviderHostExistsError``）。"""
        entry = _entry(check_provider(provider), check_host(host))
        path = self._path(provider)
        if path in self._store.list_files("config"):
            raise ProviderHostExistsError(
                f"提供方 {provider!r} 已登记；更新请用 update"
            )
        self._store.put("config", path, entry.model_dump_json().encode("utf-8"))
        return entry

    def update(self, provider: str, host: str) -> ConfigEntry:
        """更新一条映射（不存在 → ``ProviderHostNotFoundError``）。"""
        entry = _entry(check_provider(provider), check_host(host))
        path = self._path(provider)
        if path not in self._store.list_files("config"):
            raise ProviderHostNotFoundError(f"提供方 {provider!r} 未登记")
        self._store.put("config", path, entry.model_dump_json().encode("utf-8"))
        return entry

    # ───────────────────────── 读取 ─────────────────────────

    def resolve(self, provider: str) -> str | None:
        """查提供方的目标主机（未登记或记录损坏 → ``None``；沙箱据此 fail-closed）。"""
        try:
            raw = self._store.get("config", self._path(provider))
        except KeyError:
            return None
        default = self._parse(raw).default
        return default if isinstance(default, str) else None

    def get(self, provider: str) -> ConfigEntry:
        """读整条映射条目（未登记 → ``ProviderHostNotFoundError``）。"""
        try:
            raw = self._store.get("config", self._path(provider))
        except KeyError as exc:
            raise ProviderHostNotFoundError(f"提供方 {provider!r} 未登记") from exc
        return self._parse(raw)

    def list(self) -> tuple[ConfigEntry, ...]:
        """全部映射条目（按 ``config_id`` 升序）。"""
        entries = [
            self._parse(self._store.get("config", name))
            for name in self._store.list_files("config")
            if name.startswith(PROVIDER_HOST_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(entries, key=lambda e: e.config_id))

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _path(provider: str) -> str:
        return f"{PROVIDER_HOST_PREFIX}{check_provider(provider)}.json"

    @staticmethod
    def _parse(raw: bytes) -> ConfigEntry:
        try:
            return ConfigEntry(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise SandboxValidationError(f"提供方主机映射记录损坏无法解析：{exc}") from exc
