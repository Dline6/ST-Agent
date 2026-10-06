"""L5 ``channel-delivery.*`` 条目的落盘与变更留痕（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。

与 [`budget_store`](budget_store.py) / L2 `memory-policy` / L1 `scheduler-policy` 同构，
是本层落这两件事的**唯一**机制：

- 条目 → ``config`` 分区 ``channel-delivery/<条目名>.json``（01 §7 七字段；路径由
  [`config_path`](../../l1/registry/naming.py) 按点分 `config_id` 确定性派生，**不另设路径规则**）
- 变更留痕 → ``execution_log`` 分区 ``channel-delivery-change/<change_id>.json``
  （``change_id`` 作回滚单位；[铁律 6](../../../项目管理/工程宪法.md) 共同演化透明）

条目的**当前取值**写在条目的 ``default`` 槽（与 L1 / L2 / L5 预算同款）：一entry一文件，读面一次到位。
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
from st_agent.l5.errors import ChannelValidationError

__all__ = ["CHANGE_PREFIX", "CONFIG_PREFIX", "ChannelPolicyStore"]

CONFIG_PREFIX = "channel-delivery."
"""本层条目的 ``config_id`` 前缀（01 §7 点分语义：``<族>.<名>``）。"""

CHANGE_PREFIX = "channel-delivery-change/"
"""``execution_log`` 分区内变更留痕的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class ChannelPolicyStore:
    """渠道偏好条目的读写与变更留痕（``config`` / ``execution_log`` 两分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 读 ─────────────────────────

    def entry(self, config_id: str) -> ConfigEntry | None:
        """取条目的登记形态（不存在 → ``None``；存在但损坏 → 校验错）。"""
        try:
            raw = self._store.get(CONFIG_PARTITION, config_path(config_id))
        except KeyError:
            return None
        try:
            return ConfigEntry(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise ChannelValidationError(
                f"渠道偏好条目损坏无法解析（{config_id}）：{exc}"
            ) from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值。

        条目**缺失或损坏**时回落 ``default``——一条配置读不动不应让投递停摆
        （与 L1 / L2 / L5 预算同款取向；损坏仍有 :meth:`entry` 可显式暴露）。
        """
        try:
            stored = self.entry(config_id)
        except ChannelValidationError:
            return default
        return default if stored is None else stored.default

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（按 ``change_id`` 升序；逐条以 ``config_id`` 区分条目）。"""
        out: list[ChangeRecord] = []
        for name in self._store.list_files(CHANGE_PARTITION):
            if not (name.startswith(CHANGE_PREFIX) and name.endswith(".json")):
                continue
            raw = self._store.get(CHANGE_PARTITION, name)
            try:
                out.append(ChangeRecord(**json.loads(raw.decode("utf-8"))))
            except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                raise ChannelValidationError(
                    f"渠道偏好变更记录损坏（{name}）：{exc}"
                ) from exc
        return tuple(sorted(out, key=lambda c: c.change_id))

    # ───────────────────────── 写 ─────────────────────────

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
