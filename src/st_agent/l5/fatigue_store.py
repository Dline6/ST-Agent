"""L5 推送疲劳监控的状态落盘与条目留痕（[07 §7](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

- **状态**落 ``execution_log`` 分区 ``fatigue/``——逐类的连续忽略计数与最近答复
  （``state.json``）、静音清单（``mutes.json``）。**整表读写、不逐键建文件**：类名是
  `dedup_key`（如 ``sh.600000:announcement_density``），含 `:` 等 **Windows 文件名非法
  字符**，逐键建文件就得另造一套转义规则（同 [`frequency_store`](frequency_store.py) 的裁法）。
- **条目**落 ``config`` 分区（阈值 ``fatigue.ignore-threshold``），变更留痕落
  ``execution_log/fatigue-change/``——与 [`budget_store`](budget_store.py) 同构。

状态**损坏即显式暴露**（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）：
读不动时抛 :class:`~st_agent.l5.errors.FatigueValidationError`，**不返回空表**——
空表会被读成「没有疲劳」，正是静默失败。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry
from st_agent.l1.registry.naming import (
    CHANGE_PARTITION,
    CONFIG_PARTITION,
    config_path,
    new_change_id,
)
from st_agent.l5.errors import FatigueValidationError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "MUTES_PATH",
    "STATE_PATH",
    "THRESHOLD_CONFIG_ID",
    "FatigueStore",
]

CONFIG_PREFIX = "fatigue."
"""本层条目的 ``config_id`` 前缀（01 §7 点分语义）。"""

THRESHOLD_CONFIG_ID = f"{CONFIG_PREFIX}ignore-threshold"
"""连续忽略阈值的条目 id（可写标量：正整数）。"""

CHANGE_PREFIX = "fatigue-change/"
"""``execution_log`` 分区内变更留痕的目录前缀。"""

STATE_PATH = "fatigue/state.json"
"""逐类的连续忽略计数与最近答复。"""

MUTES_PATH = "fatigue/mutes.json"
"""静音清单（用户答复「关闭」的类）。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class FatigueStore:
    """疲劳监控的状态读写与条目留痕（``execution_log`` / ``config`` 两分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 状态 ─────────────────────────

    def load_state(self) -> dict[str, dict[str, Any]]:
        """逐类状态整表（**缺失 → 空表；损坏 → 显式抛错**）。"""
        return self._load(STATE_PATH)

    def save_state(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        self._save(STATE_PATH, state)

    def load_mutes(self) -> dict[str, dict[str, Any]]:
        """静音清单整表（同上）。"""
        return self._load(MUTES_PATH)

    def save_mutes(self, mutes: Mapping[str, Mapping[str, Any]]) -> None:
        self._save(MUTES_PATH, mutes)

    # ───────────────────────── 条目（01 §7） ─────────────────────────

    def entry(self, config_id: str) -> ConfigEntry | None:
        """取条目登记形态（不存在 → ``None``；损坏 → 校验错）。"""
        try:
            raw = self._store.get(CONFIG_PARTITION, config_path(config_id))
        except KeyError:
            return None
        try:
            return ConfigEntry(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise FatigueValidationError(f"疲劳条目损坏无法解析（{config_id}）：{exc}") from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值（缺失或损坏 → ``default``；损坏仍有 :meth:`entry` 可显式暴露）。"""
        try:
            stored = self.entry(config_id)
        except FatigueValidationError:
            return default
        return default if stored is None else stored.default

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（按 ``change_id`` 升序）。"""
        out: list[ChangeRecord] = []
        for name in self._store.list_files(CHANGE_PARTITION):
            if not (name.startswith(CHANGE_PREFIX) and name.endswith(".json")):
                continue
            raw = self._store.get(CHANGE_PARTITION, name)
            try:
                out.append(ChangeRecord(**json.loads(raw.decode("utf-8"))))
            except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                raise FatigueValidationError(f"疲劳变更记录损坏（{name}）：{exc}") from exc
        return tuple(sorted(out, key=lambda c: c.change_id))

    def set(
        self, entry: ConfigEntry, *, previous: Any, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """落盘条目并留痕；**取值未变则不落盘、不留痕**（返回 ``None``，01 §7）。"""
        if previous == entry.default:
            return None
        self._store.put(
            CONFIG_PARTITION, config_path(entry.config_id),
            entry.model_dump_json().encode("utf-8"),
        )
        change = ChangeRecord(
            change_id=new_change_id(),
            config_id=entry.config_id,
            old_value=previous,
            new_value=entry.default,
            applied_at=self._now().isoformat(),
            trace_ref=trace_ref,
        )
        self._store.put(
            CHANGE_PARTITION, f"{CHANGE_PREFIX}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"),
        )
        return change

    # ───────────────────────── 内部 ─────────────────────────

    def _load(self, path: str) -> dict[str, dict[str, Any]]:
        try:
            raw = self._store.get("execution_log", path)
        except KeyError:
            return {}
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise FatigueValidationError(f"疲劳状态损坏无法解析（{path}）：{exc}") from exc
        if not isinstance(loaded, dict):
            raise FatigueValidationError(
                f"疲劳状态须为映射（{path}），收到 {type(loaded).__name__}"
            )
        return {str(k): dict(v) for k, v in loaded.items() if isinstance(v, Mapping)}

    def _save(self, path: str, state: Mapping[str, Mapping[str, Any]]) -> None:
        payload = json.dumps(
            {k: dict(v) for k, v in state.items()}, ensure_ascii=False, sort_keys=True,
        )
        self._store.put("execution_log", path, payload.encode("utf-8"))
