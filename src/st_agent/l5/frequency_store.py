"""L5 去重与频控的状态落盘与条目留痕（[07 §6](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

两件事，各落**既有分区**（**不新造分区**）：

- **计数状态**落 ``execution_log`` 分区 ``frequency/``——去重窗口的逐 `dedup_key`
  计数（``dedup.json``）与格位节奏的逐格位计数（``slot.json``）。**一份文件一份状态**
  被刻意否掉：`dedup_key` 含 `:`（如 ``sh.600000:announcement_density``）等
  **Windows 文件名非法字符**，逐键建文件就得另造一套转义规则——而状态是一张表，
  整表读写即免去该规则（键仍在 JSON 里逐条保留、可读可查）。
- **条目**落 ``config`` 分区（去重窗口 ``frequency.dedup-window-minutes``），
  变更留痕落 ``execution_log/frequency-change/``——与
  [`budget_store`](budget_store.py) / [`channel_store`](channel_store.py) 同构。

状态**损坏即显式暴露**（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）：
读不动时抛 :class:`FrequencyValidationError`，**不返回空表**——空表会被读成
「没有超限」，正是「静默失败」的形态。
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
from st_agent.l5.errors import FrequencyValidationError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "DEDUP_STATE_PATH",
    "STATE_PREFIX",
    "SLOT_STATE_PATH",
    "WINDOW_CONFIG_ID",
    "FrequencyStore",
]

CONFIG_PREFIX = "frequency."
"""本层条目的 ``config_id`` 前缀（01 §7 点分语义）。"""

WINDOW_CONFIG_ID = f"{CONFIG_PREFIX}dedup-window-minutes"
"""去重窗口条目 id（可写标量：整数分钟）。"""

CHANGE_PREFIX = "frequency-change/"
"""``execution_log`` 分区内变更留痕的目录前缀。"""

STATE_PREFIX = "frequency/"
"""``execution_log`` 分区内计数状态的目录前缀。"""

DEDUP_STATE_PATH = f"{STATE_PREFIX}dedup.json"
"""逐 `dedup_key` 的去重窗口计数（``{dedup_key: {window_start, count}}``）。"""

SLOT_STATE_PATH = f"{STATE_PREFIX}slot.json"
"""逐格位的节奏计数（``{slot_key: {period_start, count}}``）。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class FrequencyStore:
    """去重与频控的状态读写与条目留痕（``execution_log`` / ``config`` 两分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 计数状态 ─────────────────────────

    def load_dedup(self) -> dict[str, dict[str, Any]]:
        """去重计数整表（**缺失 → 空表；损坏 → 显式抛错**）。"""
        return self._load(DEDUP_STATE_PATH)

    def save_dedup(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        self._save(DEDUP_STATE_PATH, state)

    def load_slots(self) -> dict[str, dict[str, Any]]:
        """格位节奏计数整表（同上：缺失 → 空表；损坏 → 抛错）。"""
        return self._load(SLOT_STATE_PATH)

    def save_slots(self, state: Mapping[str, Mapping[str, Any]]) -> None:
        self._save(SLOT_STATE_PATH, state)

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
            raise FrequencyValidationError(
                f"频控条目损坏无法解析（{config_id}）：{exc}"
            ) from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值（缺失或损坏 → ``default``；损坏仍有 :meth:`entry` 可显式暴露）。"""
        try:
            stored = self.entry(config_id)
        except FrequencyValidationError:
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
                raise FrequencyValidationError(f"频控变更记录损坏（{name}）：{exc}") from exc
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
            raise FrequencyValidationError(f"频控计数状态损坏无法解析（{path}）：{exc}") from exc
        if not isinstance(loaded, dict):
            raise FrequencyValidationError(
                f"频控计数状态须为映射（{path}），收到 {type(loaded).__name__}"
            )
        return {str(k): dict(v) for k, v in loaded.items() if isinstance(v, Mapping)}

    def _save(self, path: str, state: Mapping[str, Mapping[str, Any]]) -> None:
        payload = json.dumps(
            {k: dict(v) for k, v in state.items()}, ensure_ascii=False, sort_keys=True,
        )
        self._store.put("execution_log", path, payload.encode("utf-8"))
