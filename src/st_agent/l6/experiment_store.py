"""A/B 实验记录的落盘（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

- **实验**落 `reflection` 分区 ``experiments/<experiment_id>.json``——路径由 `experiment_id`
  确定性派生（``digest_id("exp", scope, hypothesis)`` 的本地前缀口径），故**同一实验的重复
  启用写同一路径**（幂等覆盖，同 [`.1`](pool_store.py) 的 `feedback_id` 口径）。
- **不新造分区**：`reflection` 已在 [02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
  注册（consumer=L6），且已纳入默认备份范围。
- **损坏即抛**（同 [`.1`](pool_store.py) / [`.3`](weekly_store.py) 的取向）——
  不可解析的实验记录绝不被读成「没跑过实验」。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from st_agent.l6.errors import ExperimentError

__all__ = [
    "EXPERIMENT_PREFIX",
    "ExperimentStore",
]

EXPERIMENT_PREFIX = "experiments/"
"""`reflection` 分区内实验记录的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class ExperimentStore:
    """实验记录的落盘面（`reflection` 分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    def put(self, experiment_id: str, payload: dict[str, Any]) -> None:
        """写一条实验记录（同一标识即**覆盖**——重复结算幂等，见模块 docstring）。"""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._store.put("reflection", self.path_for(experiment_id), body.encode("utf-8"))

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        """取一条实验记录（不存在 → ``None``；存在但损坏 → :class:`ExperimentError`）。"""
        try:
            raw = self._store.get("reflection", self.path_for(experiment_id))
        except KeyError:
            return None
        return _load(raw, experiment_id)

    def all(self) -> tuple[dict[str, Any], ...]:
        """全部实验记录（按落盘路径升序；损坏即抛）。"""
        out: list[dict[str, Any]] = []
        for name in self._store.list_files("reflection"):
            if not (name.startswith(EXPERIMENT_PREFIX) and name.endswith(".json")):
                continue
            raw = self._store.get("reflection", name)
            out.append(_load(raw, name))
        return tuple(out)

    @staticmethod
    def path_for(experiment_id: str) -> str:
        """实验记录的分区内相对路径（由 `experiment_id` 确定性派生）。"""
        return f"{EXPERIMENT_PREFIX}{experiment_id}.json"


def _load(raw: bytes, where: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExperimentError(f"实验记录损坏无法解析（{where}）：{exc}") from exc
    if not isinstance(loaded, dict):
        raise ExperimentError(f"实验记录形态须为映射（{where}）")
    return loaded
