"""L6 变更流与出厂重置的落盘（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) / [§6](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

- **演进变更留痕**落 `reflection` 分区 ``changes/<change_id>.json``——路径由 `change_id`
  确定性派生，故同一变更的重放写同一路径（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)
  「产生 `change_id`，记入变更历史」）。该 `change_id` 由 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
  的**门面**铸造（变更流不另造一套标识）。
- **待批准 / 排队留痕**落 `reflection/pending/<pending_id>.json`（键＝提案的确定性摘要
  ⇒ 同一提案重复提交是同一件事，**幂等覆盖**、不追加流水）。
- **出厂重置留痕**落 `reflection/reset/<reset_id>.json`。
- **不新造分区**：`reflection` 已在 [02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 注册在案。

与 [`weekly_store.py`](weekly_store.py) 同构：路径由标识确定性派生、损坏即抛、缺省纯内存态。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from st_agent.l6.errors import ChangeFlowError, FactoryResetError

__all__ = [
    "CHANGE_PREFIX",
    "PENDING_PREFIX",
    "RESET_PREFIX",
    "EvolutionChangeStore",
]

CHANGE_PREFIX = "changes/"
"""`reflection` 分区内演进变更留痕的目录前缀。"""

PENDING_PREFIX = "pending/"
"""`reflection` 分区内待批准 / 排队提案的目录前缀。"""

RESET_PREFIX = "reset/"
"""`reflection` 分区内出厂重置留痕的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class EvolutionChangeStore:
    """演进变更 / 待批准项 / 重置留痕的读写（`reflection` 分区）。

    :param store: ``Store`` 句柄；``None`` ⇒ **纯内存态**（可读不可持久）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any = None, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now
        self._memory: dict[str, dict[str, Any]] = {}          # 纯内存态（无 Store 时）

    # ───────────────────────── 演进变更 ─────────────────────────

    def put_change(self, change_id: str, payload: dict[str, Any]) -> None:
        """按 `change_id` 写一条演进变更留痕（同一 ID 即覆盖——重放幂等）。"""
        self._write(CHANGE_PREFIX, change_id, payload)

    def get_change(self, change_id: str) -> dict[str, Any] | None:
        """取一条演进变更留痕（不存在 → ``None``；损坏 → 抛）。"""
        return self._read(CHANGE_PREFIX, change_id, "演进变更留痕")

    def all_changes(self) -> tuple[dict[str, Any], ...]:
        """全部演进变更留痕（按（路径）升序；无 → 空集，不报错）。"""
        return self._read_all(CHANGE_PREFIX, "演进变更留痕")

    # ───────────────────────── 待批准 / 排队 ─────────────────────────

    def put_pending(self, pending_id: str, payload: dict[str, Any]) -> None:
        """按 `pending_id` 写一条待批准项（同一 ID 即覆盖）。"""
        self._write(PENDING_PREFIX, pending_id, payload)

    def get_pending(self, pending_id: str) -> dict[str, Any] | None:
        """取一条待批准项（不存在 → ``None``；损坏 → 抛）。"""
        return self._read(PENDING_PREFIX, pending_id, "待批准提案")

    def all_pending(self) -> tuple[dict[str, Any], ...]:
        """全部待批准项（含已否决 / 已接受——**处置过的不抹除**）。"""
        return self._read_all(PENDING_PREFIX, "待批准提案")

    # ───────────────────────── 出厂重置 ─────────────────────────

    def put_reset(self, reset_id: str, payload: dict[str, Any]) -> None:
        """按 `reset_id` 写一条出厂重置留痕。"""
        self._write(RESET_PREFIX, reset_id, payload)

    def get_reset(self, reset_id: str) -> dict[str, Any] | None:
        """取一条出厂重置留痕（不存在 → ``None``；损坏 → 抛）。"""
        try:
            raw = self._raw(RESET_PREFIX, reset_id)
        except ChangeFlowError:
            raise
        if raw is None:
            return None
        return _parse(raw, f"出厂重置留痕 {reset_id}", FactoryResetError)

    def all_resets(self) -> tuple[dict[str, Any], ...]:
        """全部出厂重置留痕（按路径升序）。"""
        return self._read_all(RESET_PREFIX, "出厂重置留痕", error=FactoryResetError)

    # ───────────────────────── 内部 ─────────────────────────

    def _write(self, prefix: str, key: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if self._store is None:
            self._memory[f"{prefix}{key}"] = payload
            return
        self._store.put("reflection", f"{prefix}{key}.json", body.encode("utf-8"))

    def _raw(self, prefix: str, key: str) -> bytes | None:
        if self._store is None:
            return None
        try:
            return self._store.get("reflection", f"{prefix}{key}.json")
        except KeyError:
            return None

    def _read(self, prefix: str, key: str, what: str) -> dict[str, Any] | None:
        if self._store is None:
            return self._memory.get(f"{prefix}{key}")
        raw = self._raw(prefix, key)
        return None if raw is None else _parse(raw, f"{what} {key}", ChangeFlowError)

    def _read_all(
        self, prefix: str, what: str, *, error: type[Exception] = ChangeFlowError
    ) -> tuple[dict[str, Any], ...]:
        if self._store is None:
            items = [
                (k, v) for k, v in self._memory.items() if k.startswith(prefix)
            ]
            return tuple(v for _, v in sorted(items, key=lambda kv: kv[0]))
        out: list[dict[str, Any]] = []
        for name in sorted(self._store.list_files("reflection")):
            if not (name.startswith(prefix) and name.endswith(".json")):
                continue
            raw = self._store.get("reflection", name)
            out.append(_parse(raw, f"{what} {name}", error))
        return tuple(out)


def _parse(raw: bytes, what: str, error: type[Exception]) -> dict[str, Any]:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise error(f"{what} 损坏无法解析：{exc}") from exc
    if not isinstance(loaded, dict):
        raise error(f"{what} 形态须为映射")
    return loaded
