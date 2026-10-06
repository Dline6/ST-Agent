"""L5 每日报告的落盘与条目留痕（[07 §5](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

- **报告**落 ``execution_log`` 分区 ``daily_report/<YYYY-MM-DD>.json``——路径由**日期**
  确定性派生（**不新造分区**），故同一日的重跑**写同一路径**、不产生重复报告。
- **条目**落 ``config`` 分区：``daily-report.time``（到点时刻，**可写标量** HH:MM）与
  ``daily-report.template``（四段开关 + 订阅渠道链，**取值为对象** ⇒ 写面归
  `DailyReportBuilder` 自有 API，01 §7 的裁定同
  [`budget`](budget.py) 的格位 / [`channel_policy`](channel_policy.py) 的链）。
- **变更留痕**落 ``execution_log/daily-report-change/<change_id>.json``。

与 [`budget_store`](budget_store.py) / [`channel_store`](channel_store.py) 同构：一个条目
一份文件、当前取值写在条目的 ``default`` 槽、取值未变不落盘不留痕。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry
from st_agent.l1.registry.naming import (
    CHANGE_PARTITION,
    CONFIG_PARTITION,
    config_path,
    new_change_id,
)
from st_agent.l5.errors import DailyReportValidationError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "REPORT_PREFIX",
    "TEMPLATE_CONFIG_ID",
    "TIME_CONFIG_ID",
    "DailyReportStore",
]

CONFIG_PREFIX = "daily-report."
"""本层条目的 ``config_id`` 前缀（01 §7 点分语义）。"""

TIME_CONFIG_ID = f"{CONFIG_PREFIX}time"
"""到点时刻的条目 id（可写标量 ``HH:MM``）。"""

TEMPLATE_CONFIG_ID = f"{CONFIG_PREFIX}template"
"""结构模板的条目 id（对象：四段开关 + 订阅渠道链 ⇒ 写面归自有 API）。"""

CHANGE_PREFIX = "daily-report-change/"
"""``execution_log`` 分区内变更留痕的目录前缀。"""

REPORT_PREFIX = "daily_report/"
"""``execution_log`` 分区内日报的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class DailyReportStore:
    """日报的读写与条目留痕（``execution_log`` / ``config`` 两分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 报告 ─────────────────────────

    def put(self, day: date, payload: dict[str, Any]) -> None:
        """按日期写一份报告（同一天即**覆盖**——重跑幂等，见模块 docstring）。"""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._store.put("execution_log", self.path_for(day), body.encode("utf-8"))

    def get(self, day: date) -> dict[str, Any] | None:
        """取某日的报告（不存在 → ``None``；存在但损坏 → 校验错）。"""
        try:
            raw = self._store.get("execution_log", self.path_for(day))
        except KeyError:
            return None
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise DailyReportValidationError(f"日报损坏无法解析（{day}）：{exc}") from exc
        if not isinstance(loaded, dict):
            raise DailyReportValidationError(f"日报形态须为映射（{day}）")
        return loaded

    @staticmethod
    def path_for(day: date) -> str:
        """报告的分区内相对路径（由日期确定性派生）。"""
        return f"{REPORT_PREFIX}{day.isoformat()}.json"

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
            raise DailyReportValidationError(
                f"日报条目损坏无法解析（{config_id}）：{exc}"
            ) from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值（缺失或损坏 → ``default``；损坏仍有 :meth:`entry` 可显式暴露）。"""
        try:
            stored = self.entry(config_id)
        except DailyReportValidationError:
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
                raise DailyReportValidationError(f"日报变更记录损坏（{name}）：{exc}") from exc
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
