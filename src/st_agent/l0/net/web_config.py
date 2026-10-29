"""开放网络配置（01 §7；T-AGT-015 批）。

四条全局条目，owner 住 L0：

- ``search.provider``——搜索提供方（``""`` / ``brave`` / ``tavily``），缺省未配置。
- ``search.key``——搜索服务密钥。**密钥本体只落加密凭据库**
  （``secrets`` 分区，标识 :data:`SEARCH_CREDENTIAL_ID`）；本条目只承载
  注册与「已配置 / 未配置」状态，读取明文一律经 ``CredentialVault.use``。
- ``web-egress/enabled``——开放网络总开关，缺省 **关**（D-137 D-3）。
- ``web-egress/allowed-hosts``——网页抓取域名名单（逗号 / 换行分隔的 glob），
  缺省空＝全拒。

本模块只做**读写与值域校验**；配置变更的 ``ChangeRecord`` 留痕由 L1 注册表族
（``SearchFamily`` / ``WebEgressFamily``）补落，与 ``net-audit`` 同款。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from st_agent.contracts.registry_types import ChangePolicy, ConfigEntry, PanelField
from st_agent.l0.net.errors import NetWebConfigError

__all__ = [
    "ALLOWED_HOSTS_CONFIG_ID",
    "ALLOWED_HOSTS_PATH",
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_EGRESS_ENABLED",
    "DEFAULT_SEARCH_PROVIDER",
    "EGRESS_ENABLED_CONFIG_ID",
    "EGRESS_ENABLED_PATH",
    "KEY_CONFIG_ID",
    "PROVIDER_CONFIG_ID",
    "PROVIDER_PATH",
    "SEARCH_CREDENTIAL_ID",
    "SEARCH_PROVIDERS",
    "WebSettings",
    "allowed_host_patterns",
    "get_allowed_hosts",
    "get_egress_enabled",
    "get_search_provider",
    "load_web_settings",
    "set_allowed_hosts",
    "set_egress_enabled",
    "set_search_provider",
    "validate_host_patterns",
    "web_config_entries",
]

PROVIDER_CONFIG_ID = "search.provider"
KEY_CONFIG_ID = "search.key"
EGRESS_ENABLED_CONFIG_ID = "web-egress/enabled"
ALLOWED_HOSTS_CONFIG_ID = "web-egress/allowed-hosts"

PROVIDER_PATH = "search/provider.json"

EGRESS_ENABLED_PATH = "web-egress/enabled.json"
ALLOWED_HOSTS_PATH = "web-egress/allowed-hosts.json"

SEARCH_CREDENTIAL_ID = "search-api-key"
"""搜索密钥在 ``secrets`` 分区的凭据标识（恒加密、惰性解锁）。"""

SEARCH_PROVIDERS: tuple[str, ...] = ("brave", "tavily")

DEFAULT_SEARCH_PROVIDER = ""
DEFAULT_EGRESS_ENABLED = False
DEFAULT_ALLOWED_HOSTS = ""

_HOST_PATTERN_RE = re.compile(r"^[A-Za-z0-9*._-]+$")
"""域名名单的一条模式（允许 glob 通配；不允许 scheme / 端口 / 路径）。"""


@dataclass(frozen=True)
class WebSettings:
    """一次读取的开放网络运行态（**不含密钥明文**）。"""

    provider: str
    egress_enabled: bool
    allowed_hosts: tuple[str, ...]
    allowed_hosts_raw: str = ""


def _read_json(store, path: str, key: str) -> object | None:
    """读一个配置文件里的单键值；文件不存在 → ``None``（取缺省）。"""
    try:
        raw = store.get("config", path)
    except KeyError:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NetWebConfigError(f"开放网络配置 {path} 损坏：{exc}") from exc
    if not isinstance(payload, dict) or key not in payload:
        raise NetWebConfigError(f"开放网络配置 {path} 缺少字段 {key!r}")
    return payload[key]


def _write_json(store, path: str, key: str, value: object) -> None:
    body = json.dumps({key: value}, ensure_ascii=False).encode("utf-8")
    store.put("config", path, body)


# ───────────────────────── 搜索提供方 ─────────────────────────


def get_search_provider(store) -> str:
    """读搜索提供方（缺省 ``""``；非法取值显式抛，不静默回退）。"""
    value = _read_json(store, PROVIDER_PATH, "provider")
    if value is None:
        return DEFAULT_SEARCH_PROVIDER
    if not isinstance(value, str):
        raise NetWebConfigError(f"search.provider 须为字符串：{value!r}")
    if value not in ("", *SEARCH_PROVIDERS):
        raise NetWebConfigError(
            f"search.provider 取值 {value!r} 不在 {('', *SEARCH_PROVIDERS)} 内"
        )
    return value


def set_search_provider(store, provider: str) -> str:
    """写搜索提供方（取值须在枚举内；空串表示未配置）。"""
    if not isinstance(provider, str) or provider not in ("", *SEARCH_PROVIDERS):
        raise NetWebConfigError(
            f"search.provider 取值须为 {('', *SEARCH_PROVIDERS)} 之一：{provider!r}"
        )
    _write_json(store, PROVIDER_PATH, "provider", provider)
    return provider


# ───────────────────────── 出网开关与域名名单 ─────────────────────────


def get_egress_enabled(store) -> bool:
    """读开放网络开关（缺省关）。"""
    value = _read_json(store, EGRESS_ENABLED_PATH, "enabled")
    if value is None:
        return DEFAULT_EGRESS_ENABLED
    if not isinstance(value, bool):
        raise NetWebConfigError(f"web-egress/enabled 须为布尔：{value!r}")
    return value


def set_egress_enabled(store, enabled: bool) -> bool:
    """写开放网络开关（取值须为布尔）。"""
    if not isinstance(enabled, bool):
        raise NetWebConfigError(f"web-egress/enabled 须为布尔：{enabled!r}")
    _write_json(store, EGRESS_ENABLED_PATH, "enabled", enabled)
    return enabled


def validate_host_patterns(raw: str) -> tuple[str, ...]:
    """把逗号 / 换行 / 分号分隔的名单拆成模式并校验。

    空串合法（表示全拒）；每条模式不得含 scheme、端口、路径或空白，
    允许 ``*`` 通配。非法模式**显式拒**，不静默丢弃。
    """
    if not isinstance(raw, str):
        raise NetWebConfigError(f"web-egress/allowed-hosts 须为字符串：{raw!r}")
    patterns: list[str] = []
    for piece in re.split(r"[,;，；\r\n]+", raw):
        pattern = piece.strip().lower()
        if not pattern:
            continue
        if len(pattern) > 253 or not _HOST_PATTERN_RE.fullmatch(pattern):
            raise NetWebConfigError(
                f"非法域名模式 {piece.strip()!r}：只允许字母 / 数字 / 点 / 连字符 / 下划线 / *"
            )
        patterns.append(pattern)
    return tuple(patterns)


def allowed_host_patterns(raw: str) -> tuple[str, ...]:
    """名单原文 → 模式元组（不校验；供读取既有配置时容忍历史值）。"""
    if not isinstance(raw, str):
        return ()
    return tuple(
        piece.strip().lower()
        for piece in re.split(r"[,;，；\r\n]+", raw)
        if piece.strip()
    )


def get_allowed_hosts(store) -> str:
    """读域名名单原文（缺省空）。"""
    value = _read_json(store, ALLOWED_HOSTS_PATH, "hosts")
    if value is None:
        return DEFAULT_ALLOWED_HOSTS
    if not isinstance(value, str):
        raise NetWebConfigError(f"web-egress/allowed-hosts 须为字符串：{value!r}")
    return value


def set_allowed_hosts(store, raw: str) -> str:
    """写域名名单（写入前校验每条模式）。"""
    validate_host_patterns(raw)
    _write_json(store, ALLOWED_HOSTS_PATH, "hosts", raw)
    return raw


def load_web_settings(store) -> WebSettings:
    """一次取齐开放网络运行态（开关 / 名单 / 提供方；不含密钥）。"""
    raw = get_allowed_hosts(store)
    return WebSettings(
        provider=get_search_provider(store),
        egress_enabled=get_egress_enabled(store),
        allowed_hosts=allowed_host_patterns(raw),
        allowed_hosts_raw=raw,
    )


# ───────────────────────── 01 §7 条目形态 ─────────────────────────


def web_config_entries() -> tuple[ConfigEntry, ...]:
    """四条开放网络条目的规范形态（当前值由 L1 注册表族叠加）。"""
    return (
        ConfigEntry(
            config_id=PROVIDER_CONFIG_ID,
            display_name="搜索提供方",
            value_schema={"type": "string", "enum": ["", *SEARCH_PROVIDERS]},
            default=DEFAULT_SEARCH_PROVIDER,
            description_for_chat=(
                "联网搜索使用的第三方提供方（brave / tavily）；留空表示未配置。"
                "未配置时搜索原语不可用并给出配置指引"
            ),
            panel_form_spec=PanelField(
                widget="select", label="搜索提供方",
                help_text="选择第三方搜索服务；密钥在凭据区按 search-api-key 配置",
                choices=("", *SEARCH_PROVIDERS),
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=False),
        ),
        ConfigEntry(
            config_id=KEY_CONFIG_ID,
            display_name="搜索服务密钥",
            value_schema={"type": "string"},
            default="",
            description_for_chat=(
                "搜索服务的 API 密钥。密钥本体只存加密凭据库（凭据标识 search-api-key），"
                "本条目只表示是否已配置，不保存也不回显明文"
            ),
            panel_form_spec=PanelField(
                widget="text", label="搜索服务密钥",
                help_text=(
                    "密钥保存在加密凭据库；本页只显示「已配置 / 未配置」。"
                    "新增或重设请用下方凭据动作（标识 search-api-key）"
                ),
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        ),
        ConfigEntry(
            config_id=EGRESS_ENABLED_CONFIG_ID,
            display_name="开放网络",
            value_schema={"type": "boolean"},
            default=DEFAULT_EGRESS_ENABLED,
            description_for_chat=(
                "是否允许 Agent 联网搜索与抓取网页。默认关闭——开启后搜索查询词与"
                "你指定抓取的网页地址会发送给对应第三方服务；本机只记录域名、目的、"
                "时间与数据量，不记录查询正文与网页内容"
            ),
            panel_form_spec=PanelField(
                widget="toggle", label="开放网络",
                help_text=(
                    "开启后，搜索查询词与目标网页地址会发送给第三方；默认关闭。"
                    "关闭时两条原语均不可用且不发起任何请求"
                ),
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        ),
        ConfigEntry(
            config_id=ALLOWED_HOSTS_CONFIG_ID,
            display_name="允许抓取的域名",
            value_schema={"type": "string"},
            default=DEFAULT_ALLOWED_HOSTS,
            description_for_chat=(
                "网页抓取允许访问的主机名单（逗号或换行分隔的 glob，如 "
                "*.example.com）；留空表示全部拒绝"
            ),
            panel_form_spec=PanelField(
                widget="text", label="允许抓取的域名",
                help_text=(
                    "逗号或换行分隔的主机模式，如 *.example.com；留空＝全部拒绝。"
                    "名单外主机在发包前拦截"
                ),
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=False),
        ),
    )
