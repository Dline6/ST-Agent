"""数据同步的「启动应用时自动同步」配置（T-UI-037.3；[01 §7]）。

逐「标的 × 数据集」登记布尔条目 ``data-sync/auto.<target>.<dataset>``：勾选表示应用
启动时自动发起一次同步。本模块只做**读写与键校验**；变更留痕由 L1 注册表族
（``DataSyncAutoFamily``）补落，与 ``web-egress/*`` 同款。

落盘：``config`` 分区 ``data-sync/auto.json``，内容为 ``{config_id: true}``（只保留
勾选项；取消即移除该键）。
"""

from __future__ import annotations

import json
from typing import Any

from st_agent.contracts.registry_types import ChangePolicy, ConfigEntry, PanelField
from st_agent.l0.market.datasets import get_dataset
from st_agent.l0.market.targets import TARGETS, TARGET_IDS

__all__ = [
    "AUTO_SYNC_CONFIG_PREFIX",
    "AUTO_SYNC_PATH",
    "AutoSyncConfigError",
    "auto_sync_config_id",
    "auto_sync_entry",
    "get_auto_sync_flags",
    "get_auto_sync_keys",
    "get_auto_sync_pairs",
    "parse_auto_sync_config_id",
    "set_auto_sync",
]

AUTO_SYNC_CONFIG_PREFIX = "data-sync/auto."
"""本族 ``config_id`` 前缀（[01 §7]）。"""

AUTO_SYNC_PATH = "data-sync/auto.json"
"""勾选集合的落盘路径（``config`` 分区）。"""

_TARGET_LABELS = {spec.target_id: spec.label for spec in TARGETS}
_TARGET_LABELS.setdefault("other", "其他")


class AutoSyncConfigError(ValueError):
    """自动同步配置的键 / 值不合法。"""


def auto_sync_config_id(target: str, dataset: str) -> str:
    """标的 + 数据集 → ``config_id``。"""
    return f"{AUTO_SYNC_CONFIG_PREFIX}{target}.{dataset}"


def parse_auto_sync_config_id(config_id: str) -> tuple[str, str] | None:
    """``config_id`` → ``(target, dataset)``；不属本族或组合非法 → ``None``。"""
    text = str(config_id or "")
    if not text.startswith(AUTO_SYNC_CONFIG_PREFIX):
        return None
    rest = text[len(AUTO_SYNC_CONFIG_PREFIX):]
    target, separator, dataset = rest.partition(".")
    if not separator or target not in TARGET_IDS:
        return None
    try:
        spec = get_dataset(dataset)
    except Exception:  # noqa: BLE001 —— 未知数据集统一视为非法键
        return None
    # 页面只在「其他」下展示全局数据集、在逐证券标的下列逐证券数据集。
    if spec.target_kind == "global" and target != "other":
        return None
    if spec.target_kind == "target" and target == "other":
        return None
    return target, dataset


def _read(store: Any) -> dict[str, bool]:
    try:
        raw = store.get("config", AUTO_SYNC_PATH)
    except KeyError:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AutoSyncConfigError(f"自动同步配置损坏（{AUTO_SYNC_PATH}）：{exc}") from exc
    if not isinstance(payload, dict):
        raise AutoSyncConfigError(f"自动同步配置须为对象（{AUTO_SYNC_PATH}）")
    out: dict[str, bool] = {}
    for config_id, value in payload.items():
        if parse_auto_sync_config_id(str(config_id)) is None:
            raise AutoSyncConfigError(f"自动同步配置含非法键：{config_id!r}")
        if not isinstance(value, bool):
            raise AutoSyncConfigError(f"自动同步配置 {config_id} 须为布尔：{value!r}")
        out[str(config_id)] = value
    return out


def _write(store: Any, mapping: dict[str, bool]) -> None:
    body = json.dumps(mapping, ensure_ascii=False, sort_keys=True).encode("utf-8")
    store.put("config", AUTO_SYNC_PATH, body)


def get_auto_sync_flags(store: Any) -> dict[str, bool]:
    """全部已落值的勾选（``config_id`` → 布尔；缺省空集）。"""
    return dict(_read(store))


def get_auto_sync_keys(store: Any) -> tuple[str, ...]:
    """已勾选的 ``config_id`` 元组（按字典序）。"""
    return tuple(sorted(cid for cid, enabled in _read(store).items() if enabled))


def get_auto_sync_pairs(store: Any) -> dict[str, bool]:
    """页面用映射：``"<target>:<dataset>"`` → 是否勾选（只出勾选项）。"""
    out: dict[str, bool] = {}
    for config_id in get_auto_sync_keys(store):
        parsed = parse_auto_sync_config_id(config_id)
        if parsed is not None:
            target, dataset = parsed
            out[f"{target}:{dataset}"] = True
    return out


def set_auto_sync(store: Any, config_id: str, enabled: bool) -> bool:
    """落一次勾选（只保留勾选项；非法键 / 非布尔即抛）。"""
    parsed = parse_auto_sync_config_id(config_id)
    if parsed is None:
        raise AutoSyncConfigError(f"非法自动同步条目：{config_id!r}")
    if not isinstance(enabled, bool):
        raise AutoSyncConfigError(f"{config_id} 的取值须为布尔：{enabled!r}")
    mapping = _read(store)
    if enabled:
        mapping[config_id] = True
    else:
        mapping.pop(config_id, None)
    _write(store, mapping)
    return enabled


def auto_sync_entry(config_id: str, *, current: bool) -> ConfigEntry | None:
    """一条勾选的规范形态（当前值由族适配器叠加）；非法键 → ``None``。"""
    parsed = parse_auto_sync_config_id(config_id)
    if parsed is None:
        return None
    target, dataset = parsed
    spec = get_dataset(dataset)
    target_label = _TARGET_LABELS.get(target, target)
    return ConfigEntry(
        config_id=config_id,
        display_name=f"启动自动同步 · {target_label} · {spec.label}",
        value_schema={"type": "boolean"},
        default=current,
        description_for_chat=(
            "应用启动时自动同步该标的与数据集：已有数据按水位补齐到当天，"
            "无数据只取最新一期；与手动同步共用单飞锁"
        ),
        panel_form_spec=PanelField(
            widget="toggle",
            label="启动应用时自动同步",
            help_text=(
                "勾选后应用启动时后台发起同步；已有数据按水位补齐，"
                "无数据只取最新一期"
            ),
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=False),
    )
