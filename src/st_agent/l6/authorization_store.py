"""L6 演进授权档位与风险分级清单的落盘与条目留痕（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

- **条目**落 `config` 分区（两条，见下）；**变更留痕**落
  `execution_log/evolution-change/<change_id>.json`。
- **不新造分区**：两个分区都在 [02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
  注册在案。

与 [`weekly_store.py`](weekly_store.py) 同构：一个条目一份文件、当前取值写在条目的
``default`` 槽、**取值未变不落盘不留痕**。
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
from st_agent.l6.errors import AuthorizationError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "GRADING_CONFIG_ID",
    "TIER_CONFIG_ID",
    "EvolutionStore",
]

CONFIG_PREFIX = "evolution."
"""本层条目的 ``config_id`` 前缀（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 点分语义）。"""

TIER_CONFIG_ID = f"{CONFIG_PREFIX}authorization"
"""演进授权档位的条目 id（可写标量：`manual` / `collaborative` / `autonomous`）。"""

GRADING_CONFIG_ID = f"{CONFIG_PREFIX}risk-grading"
"""风险分级清单的条目 id（对象：逐前缀的风险类 ⇒ 写面归自有 API）。"""

CHANGE_PREFIX = "evolution-change/"
"""`execution_log` 分区内变更留痕的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class EvolutionStore:
    """演进授权条目的读写与变更留痕（`config` / `execution_log` 两分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    :param change_prefix: 变更留痕的目录前缀（**缺省** :data:`CHANGE_PREFIX`＝演进族的
        `evolution-change/`）。运行期授权档位（[`runtime_authorization`](runtime_authorization.py)）
        借用同一读写面而落**自己的**目录——[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)
        明写变更流只管演进动作，故运行期档位不该混进 [08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md)
        出厂重置的「授权档复位」回放面。
    """

    def __init__(
        self, store: Any, *, now: Callable[[], datetime] | None = None,
        change_prefix: str = CHANGE_PREFIX,
    ) -> None:
        self._store = store
        self._now = _system_now if now is None else now
        self._change_prefix = change_prefix

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
            raise AuthorizationError(
                f"演进授权条目损坏无法解析（{config_id}）：{exc}"
            ) from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值（缺失 → ``default``；损坏 → 校验错，**不静默回落**）。"""
        stored = self.entry(config_id)
        return default if stored is None else stored.default

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（按 ``change_id`` 升序）。"""
        out: list[ChangeRecord] = []
        for name in self._store.list_files(CHANGE_PARTITION):
            if not (name.startswith(self._change_prefix) and name.endswith(".json")):
                continue
            raw = self._store.get(CHANGE_PARTITION, name)
            try:
                out.append(ChangeRecord(**json.loads(raw.decode("utf-8"))))
            except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                raise AuthorizationError(f"演进授权变更记录损坏（{name}）：{exc}") from exc
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
            CHANGE_PARTITION, f"{self._change_prefix}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"),
        )
        return change
