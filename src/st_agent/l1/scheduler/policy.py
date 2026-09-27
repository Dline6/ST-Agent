"""调度策略条目（T-L1-005.3；01 §7 配置元模型）。

离线期间错过的到期点，在网络恢复后**按用户配置**处置——[03 §6](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
的「补跑或跳过」。缺省取**补跑**，其依据是 [02 §7](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
「网络恢复后云端渠道补发（**用户可关闭补发**）」——默认补、用户可关。

落盘（与既有条目同构）：

- 条目 → ``config`` 分区 ``scheduler-policy/offline-catch-up.json``（01 §7 七字段）
- 变更留痕 → ``execution_log`` 分区 ``scheduler-change/<change_id>.json``

.. note::
   条目前缀取 ``scheduler-policy/`` 而非 ``scheduler/``：后者是逐目标**运行状态**
   的目录（``SchedulerState``），两者若同前缀，状态扫描会把策略条目当状态读。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l1.scheduler.errors import SchedulerValidationError

__all__ = [
    "CHANGE_PREFIX",
    "DEFAULT_OFFLINE_CATCH_UP",
    "OFFLINE_CATCH_UP_CONFIG_ID",
    "POLICY_PREFIX",
    "OfflineCatchUp",
    "SchedulerPolicy",
]

POLICY_PREFIX = "scheduler-policy/"
"""``config`` 分区内策略条目的目录前缀。"""

CHANGE_PREFIX = "scheduler-change/"
"""``execution_log`` 分区内策略变更留痕的目录前缀（``change_id`` 回滚单位）。"""

OFFLINE_CATCH_UP_CONFIG_ID = "scheduler-policy/offline-catch-up"
"""离线补跑策略条目的 ``config_id``（01 §7；与文件前缀同源便于双向定位）。"""

OfflineCatchUp = Literal["catch-up", "skip"]
"""``catch-up`` 网络恢复后补跑 / ``skip`` 跳过离线期间错过的到期点。"""

DEFAULT_OFFLINE_CATCH_UP: OfflineCatchUp = "catch-up"
"""缺省补跑（02 §7「产品默认补、用户可关闭」）。"""

_VALUES: tuple[str, ...] = ("catch-up", "skip")


def _new_change_id() -> str:
    """生成一个 ``change_id``（01 §7：每次配置变更产生，作回滚单位）。"""
    return f"chg_{secrets.token_hex(10)}"


def _entry() -> ConfigEntry:
    """按 01 §7 七字段组装离线补跑策略条目。"""
    return ConfigEntry(
        config_id=OFFLINE_CATCH_UP_CONFIG_ID,
        display_name="离线错过的定时任务在恢复后如何处理",
        value_schema={"type": "string", "enum": list(_VALUES)},
        default=DEFAULT_OFFLINE_CATCH_UP,
        description_for_chat=(
            "断网期间到点的定时任务，网络恢复后是补跑一次还是跳过；"
            "按能力声明的离线级别，断网也能跑的照常跑、必须联网的才受本项影响"
        ),
        panel_form_spec=PanelField(
            widget="select",
            label="恢复后处置",
            help_text="补跑＝恢复后补做一次；跳过＝不再补做（该次仍留痕）",
            choices=_VALUES,
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=True),
    )


class SchedulerPolicy:
    """调度侧的 01 §7 策略条目门面（当前只有「离线补跑」一项）。

    :param store: ``Store`` 句柄（条目写 ``config``，留痕写 ``execution_log``）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store, *, now=None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 读 ─────────────────────────

    def value(self) -> OfflineCatchUp:
        """当前取值；条目缺失或损坏 → 缺省值（不因一条配置读不动就停摆）。"""
        try:
            raw = self._store.get("config", self._path())
        except KeyError:
            return DEFAULT_OFFLINE_CATCH_UP
        try:
            default = self._parse(raw).default
        except SchedulerValidationError:
            return DEFAULT_OFFLINE_CATCH_UP
        return default if default in _VALUES else DEFAULT_OFFLINE_CATCH_UP

    def entry(self) -> ConfigEntry:
        """条目的登记形态（01 §7 七字段；供配置注册表面浏览）。"""
        return _entry()

    # ───────────────────────── 写 ─────────────────────────

    def set(self, value: str, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """改一次取值并留痕（**值未变则不留痕**，返回 ``None``）。

        :param value: ``catch-up`` / ``skip``（越界即拒，不静默取最近值）
        :param trace_ref: 关联推理链（如适用）
        :returns: 本次变更记录（含 ``change_id``，作回滚单位）；值未变 → ``None``
        """
        if value not in _VALUES:
            raise SchedulerValidationError(
                f"非法离线处置取值 {value!r}（合法值：{'/'.join(_VALUES)}）"
            )
        old_value = self.value()
        if old_value == value:
            return None
        entry = _entry().model_copy(update={"default": value})
        self._store.put("config", self._path(), entry.model_dump_json().encode("utf-8"))
        change = ChangeRecord(
            change_id=_new_change_id(), config_id=OFFLINE_CATCH_UP_CONFIG_ID,
            old_value=old_value, new_value=value,
            applied_at=self._now().isoformat(), trace_ref=trace_ref,
        )
        self._store.put("execution_log", f"{CHANGE_PREFIX}{change.change_id}.json",
                        change.model_dump_json().encode("utf-8"))
        return change

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（按 ``change_id`` 升序）。"""
        out: list[ChangeRecord] = []
        for name in self._store.list_files("execution_log"):
            if name.startswith(CHANGE_PREFIX) and name.endswith(".json"):
                raw = self._store.get("execution_log", name)
                try:
                    out.append(ChangeRecord(**json.loads(raw.decode("utf-8"))))
                except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                    raise SchedulerValidationError(f"调度策略变更记录损坏：{exc}") from exc
        return tuple(sorted(out, key=lambda c: c.change_id))

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _path() -> str:
        return f"{POLICY_PREFIX}offline-catch-up.json"

    @staticmethod
    def _parse(raw: bytes) -> ConfigEntry:
        try:
            return ConfigEntry(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise SchedulerValidationError(f"调度策略条目损坏无法解析：{exc}") from exc


def _system_now() -> datetime:
    """本机当前时刻（带本地时区）。"""
    return datetime.now().astimezone()
