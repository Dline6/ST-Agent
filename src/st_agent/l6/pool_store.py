"""L6 反思数据池的落盘（[08 §1](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

- **反馈**落 `reflection` 分区 ``feedback/<feedback_id>.json``——**键＝ `feedback_id`**
  （[01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 的一条用户反馈标识，由交互层铸造），
  故同一 ID 重复到达**写同一路径、幂等覆盖**（不追加流水）。
- **不新造分区**：`reflection` 已在 [02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
  注册（consumer=L6），且已被 [T-L0-006](../l0/backup/backup.py) 系纳入默认备份四分区。
- **损坏即抛**（同 [L5 各 store](../../src/st_agent/l5/daily_report_store.py) 的取向）——
  不可解析的池内文件绝不被读成「没有反馈」。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from st_agent.l6.errors import FeedbackPoolError

__all__ = [
    "FEEDBACK_PREFIX",
    "FeedbackPoolStore",
]

FEEDBACK_PREFIX = "feedback/"
"""`reflection` 分区内反馈记录的目录前缀。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class FeedbackPoolStore:
    """反思数据池的落盘面（`reflection` 分区）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    def put(self, feedback_id: str, payload: dict[str, Any]) -> None:
        """写一条反馈（同一 `feedback_id` 即**覆盖**——重放幂等，见模块 docstring）。"""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._store.put("reflection", self.path_for(feedback_id), body.encode("utf-8"))

    def get(self, feedback_id: str) -> dict[str, Any] | None:
        """取一条反馈（不存在 → ``None``；存在但损坏 → :class:`FeedbackPoolError`）。"""
        try:
            raw = self._store.get("reflection", self.path_for(feedback_id))
        except KeyError:
            return None
        return _load(raw, feedback_id)

    def all(self) -> tuple[dict[str, Any], ...]:
        """池内全部反馈（按落盘路径升序；损坏即抛）。"""
        out: list[dict[str, Any]] = []
        for name in self._store.list_files("reflection"):
            if not (name.startswith(FEEDBACK_PREFIX) and name.endswith(".json")):
                continue
            raw = self._store.get("reflection", name)
            out.append(_load(raw, name))
        return tuple(out)

    @staticmethod
    def path_for(feedback_id: str) -> str:
        """反馈记录的分区内相对路径（由 `feedback_id` 确定性派生）。"""
        return f"{FEEDBACK_PREFIX}{feedback_id}.json"


def _load(raw: bytes, where: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeedbackPoolError(f"反馈记录损坏无法解析（{where}）：{exc}") from exc
    if not isinstance(loaded, dict):
        raise FeedbackPoolError(f"反馈记录形态须为映射（{where}）")
    return loaded
