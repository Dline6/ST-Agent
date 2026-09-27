"""L2 侧 01 §7 配置条目的落盘与变更留痕（共用件）。

[04 §4](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 要求自主写入白名单「注册进
01 §7 配置注册表」，[§5](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 要求置信度
的衰减 / 重估策略「注册为可配置项」——本模块是 L2 落这些条目的**唯一**机制，
`write_policy` 与 `confidence` 两处条目共用，避免各写一套。

落盘（与 L1 既有范式同构，见 `l1/scheduler/policy.py`）：

- 条目 → ``config`` 分区 ``memory-policy/<条目名>.json``（01 §7 七字段）
- 变更留痕 → ``execution_log`` 分区 ``memory-policy-change/<change_id>.json``

条目的**当前取值**写在条目的 ``default`` 槽（与 L1 同款）：一个条目一份文件，
读面一次到位；变更历史另存 ``ChangeRecord``（``change_id`` 作回滚单位，01 §7）。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry
from st_agent.l2.memory.errors import MemoryValidationError

__all__ = [
    "CHANGE_PREFIX",
    "CONFIG_PREFIX",
    "MemoryPolicyStore",
    "new_change_id",
]

CONFIG_PREFIX = "memory-policy/"
"""``config`` 分区内 L2 策略条目的目录前缀。"""

CHANGE_PREFIX = "memory-policy-change/"
"""``execution_log`` 分区内 L2 策略变更留痕的目录前缀（``change_id`` 回滚单位）。"""


def new_change_id() -> str:
    """生成一个 ``change_id``（01 §7：每次配置变更产生，作回滚单位）。"""
    return f"chg_{secrets.token_hex(10)}"


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class MemoryPolicyStore:
    """L2 侧 01 §7 条目的读写与变更留痕（``config`` / ``execution_log`` 两分区）。

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
            raw = self._store.get("config", self.path(config_id))
        except KeyError:
            return None
        try:
            return ConfigEntry(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise MemoryValidationError(f"L2 策略条目损坏无法解析（{config_id}）：{exc}") from exc

    def current(self, config_id: str, default: Any) -> Any:
        """条目当前取值。

        条目**缺失或损坏**时回落 ``default``——一条配置读不动不应让调用方停摆
        （与 L1 调度策略同款取向；损坏仍有 :meth:`entry` 可显式暴露）。
        """
        try:
            stored = self.entry(config_id)
        except MemoryValidationError:
            return default
        return default if stored is None else stored.default

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（按 ``change_id`` 升序；逐条以 ``config_id`` 区分条目）。"""
        out: list[ChangeRecord] = []
        for name in self._store.list_files("execution_log"):
            if not (name.startswith(CHANGE_PREFIX) and name.endswith(".json")):
                continue
            raw = self._store.get("execution_log", name)
            try:
                out.append(ChangeRecord(**json.loads(raw.decode("utf-8"))))
            except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                raise MemoryValidationError(f"L2 策略变更记录损坏（{name}）：{exc}") from exc
        return tuple(sorted(out, key=lambda c: c.change_id))

    # ───────────────────────── 写 ─────────────────────────

    def set(
        self, entry: ConfigEntry, *, previous: Any, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """落盘条目并留痕；**取值未变则不落盘、不留痕**（返回 ``None``）。

        :param entry: 目标条目（其 ``default`` 槽即「本次要设置的取值」）
        :param previous: 本次设置**之前**的取值（由调用方按自己的缺省口径读出）
        :param trace_ref: 关联推理链（如适用）
        """
        if previous == entry.default:
            return None
        self._store.put("config", self.path(entry.config_id),
                        entry.model_dump_json().encode("utf-8"))
        change = ChangeRecord(
            change_id=new_change_id(),
            config_id=entry.config_id,
            old_value=previous,
            new_value=entry.default,
            applied_at=self._now().isoformat(),
            trace_ref=trace_ref,
        )
        self._store.put("execution_log", f"{CHANGE_PREFIX}{change.change_id}.json",
                        change.model_dump_json().encode("utf-8"))
        return change

    # ───────────────────────── 路径 ─────────────────────────

    @staticmethod
    def path(config_id: str) -> str:
        """条目在 ``config`` 分区内的相对路径（``config_id`` 的主干即文件名）。"""
        stem = config_id[len(CONFIG_PREFIX):] if config_id.startswith(CONFIG_PREFIX) else config_id
        if not stem or "/" in stem or "\\" in stem:
            raise MemoryValidationError(f"config_id 非法：{config_id!r}（须为 {CONFIG_PREFIX}<名>）")
        return f"{CONFIG_PREFIX}{stem}.json"
