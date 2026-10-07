"""L6 每周反思报告的落盘与条目留痕（[08 §2](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

- **报告**落 `reflection` 分区 ``weekly/<ISO 年>-W<ISO 周>.json``——路径由**周键**确定性
  派生，故同一周的重跑**写同一路径**、不产生重复报告（08 §2「留档于 `reflection` 分区」）。
- **条目**落 `config` 分区（四项，见下）；**变更留痕**落
  `execution_log/weekly-report-change/<change_id>.json`。
- **不新造分区**：三个分区都在 [02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
  注册在案。

与 [L5 的 `daily_report_store`](../l5/daily_report_store.py) 同构：一个条目一份文件、
当前取值写在条目的 ``default`` 槽、**取值未变不落盘不留痕**。
"""

from __future__ import annotations

import json
import re
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
from st_agent.l6.errors import WeeklyReportError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "DAY_CONFIG_ID",
    "MIN_FEEDBACK_CONFIG_ID",
    "REPORT_PREFIX",
    "TEMPLATE_CONFIG_ID",
    "TIME_CONFIG_ID",
    "WEEK_KEY_RE",
    "WeeklyReportStore",
]

WEEK_KEY_RE = re.compile(r"^(\d{4})-W(\d{2})$")
"""周键形态（``<ISO 年>-W<ISO 周>``）——落盘键与窗口标识的**单一**出处。

住本模块而非 :mod:`st_agent.l6.weekly`：落盘侧的列举读面（:meth:`WeeklyReportStore.weeks`）
也要用它筛键，而 `weekly` 已依赖 `weekly_store`（反向 import 会成环）。
"""

CONFIG_PREFIX = "weekly-report."
"""本层条目的 ``config_id`` 前缀（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 点分语义）。"""

DAY_CONFIG_ID = f"{CONFIG_PREFIX}day-of-week"
"""到点「周几」的条目 id（可写标量：0–6，周一＝0，同 `datetime.weekday()`）。"""

TIME_CONFIG_ID = f"{CONFIG_PREFIX}time"
"""到点时刻的条目 id（可写标量 ``HH:MM``）。"""

TEMPLATE_CONFIG_ID = f"{CONFIG_PREFIX}template"
"""结构模板的条目 id（对象：四段开关 + 订阅渠道链 ⇒ 写面归自有 API）。"""

MIN_FEEDBACK_CONFIG_ID = f"{CONFIG_PREFIX}min-feedback"
"""数据不足阈值的条目 id（可写标量：正整数）。"""

CHANGE_PREFIX = "weekly-report-change/"
"""`execution_log` 分区内变更留痕的目录前缀。"""

REPORT_PREFIX = "weekly/"
"""`reflection` 分区内周报的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class WeeklyReportStore:
    """周报的读写与条目留痕（`reflection` / `config` / `execution_log` 三分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 报告 ─────────────────────────

    def put(self, week: str, payload: dict[str, Any]) -> None:
        """按周键写一份报告（同一周即**覆盖**——重跑幂等，见模块 docstring）。"""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._store.put("reflection", self.path_for(week), body.encode("utf-8"))

    def get(self, week: str) -> dict[str, Any] | None:
        """取某周的报告（不存在 → ``None``；存在但损坏 → 校验错）。"""
        try:
            raw = self._store.get("reflection", self.path_for(week))
        except KeyError:
            return None
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise WeeklyReportError(f"周报损坏无法解析（{week}）：{exc}") from exc
        if not isinstance(loaded, dict):
            raise WeeklyReportError(f"周报形态须为映射（{week}）")
        return loaded

    @staticmethod
    def path_for(week: str) -> str:
        """报告的分区内相对路径（由周键确定性派生）。"""
        return f"{REPORT_PREFIX}{week}.json"

    def weeks(self) -> tuple[str, ...]:
        """已落盘的**合法**周键（升序）——表现层列历史报告 / 取最近一期的取材面。

        形态不合的杂项文件**不冒充**一期报告（`reflection/weekly/` 下只该有周报，
        但读面不把「叫得像」当成「是」）；单份内容损坏仍由 :meth:`get` 读取时显式抛。
        """
        out: list[str] = []
        for name in self._store.list_files("reflection"):
            if not (name.startswith(REPORT_PREFIX) and name.endswith(".json")):
                continue
            key = name[len(REPORT_PREFIX):-len(".json")]
            if WEEK_KEY_RE.match(key):
                out.append(key)
        return tuple(sorted(out))

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
            raise WeeklyReportError(f"周报条目损坏无法解析（{config_id}）：{exc}") from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值（缺失或损坏 → ``default``；损坏仍有 :meth:`entry` 可显式暴露）。"""
        try:
            stored = self.entry(config_id)
        except WeeklyReportError:
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
                raise WeeklyReportError(f"周报变更记录损坏（{name}）：{exc}") from exc
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
