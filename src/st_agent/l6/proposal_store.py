"""主动提案的落盘与条目留痕（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

- **提案**落 `reflection` 分区 ``proposals/<proposal_id>.json``——键＝同类键的确定性摘要，
  故**同一类观察的重复检出写同一路径**（幂等覆盖，不产生重复提案；同 [`.1`](pool_store.py)
  的 `feedback_id` 口径）。
- **条目**落 `config` 分区（两条，见下）；**变更留痕**落
  `execution_log/proposal-change/<change_id>.json`。
- **不新造分区**：三个分区都在 [02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
  注册在案。

与 [`weekly_store`](weekly_store.py)（周报）/ [L5 的 `daily_report_store`](../l5/daily_report_store.py)
同构：一个条目一份文件、当前取值写在条目的 ``default`` 槽、**取值未变不落盘不留痕**。
"""

from __future__ import annotations

import json
from collections.abc import Callable
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
from st_agent.l6.errors import ProposalError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "PROPOSAL_PREFIX",
    "RATE_LIMIT_CONFIG_ID",
    "REPEAT_CONFIG_ID",
    "ProposalStore",
]

CONFIG_PREFIX = "proposal."
"""本层条目的 ``config_id`` 前缀（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 点分语义）。"""

REPEAT_CONFIG_ID = f"{CONFIG_PREFIX}repeat-threshold"
"""同类问题重复出现次数的阈值（可写标量：正整数；story-09「超过 5 次」的缺省）。"""

RATE_LIMIT_CONFIG_ID = f"{CONFIG_PREFIX}rate-limit"
"""每个 ISO 周最多放行的提案条数（可写标量：非负整数——`0` 合法＝只排队不放行）。"""

PROPOSAL_PREFIX = "proposals/"
"""`reflection` 分区内提案的目录前缀。"""

CHANGE_PREFIX = "proposal-change/"
"""`execution_log` 分区内变更留痕的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class ProposalStore:
    """提案的读写与条目留痕（`reflection` / `config` / `execution_log` 三分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 提案 ─────────────────────────

    def put(self, proposal_id: str, payload: dict[str, Any]) -> None:
        """按提案标识写一条（同一标识即**覆盖**——重复检出幂等，见模块 docstring）。"""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._store.put("reflection", self.path_for(proposal_id), body.encode("utf-8"))

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        """取一条提案（不存在 → ``None``；存在但损坏 → :class:`ProposalError`）。"""
        try:
            raw = self._store.get("reflection", self.path_for(proposal_id))
        except KeyError:
            return None
        return _load(raw, proposal_id)

    def all(self) -> tuple[dict[str, Any], ...]:
        """全部提案（按落盘路径升序；损坏即抛）。"""
        out: list[dict[str, Any]] = []
        for name in self._store.list_files("reflection"):
            if not (name.startswith(PROPOSAL_PREFIX) and name.endswith(".json")):
                continue
            raw = self._store.get("reflection", name)
            out.append(_load(raw, name))
        return tuple(out)

    @staticmethod
    def path_for(proposal_id: str) -> str:
        """提案的分区内相对路径（由 `proposal_id` 确定性派生）。"""
        return f"{PROPOSAL_PREFIX}{proposal_id}.json"

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
            raise ProposalError(f"提案条目损坏无法解析（{config_id}）：{exc}") from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值（缺失或损坏 → ``default``；损坏仍有 :meth:`entry` 可显式暴露）。"""
        try:
            stored = self.entry(config_id)
        except ProposalError:
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
                raise ProposalError(f"提案变更记录损坏（{name}）：{exc}") from exc
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


def _load(raw: bytes, where: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProposalError(f"提案损坏无法解析（{where}）：{exc}") from exc
    if not isinstance(loaded, dict):
        raise ProposalError(f"提案形态须为映射（{where}）")
    return loaded
