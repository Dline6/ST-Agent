"""存储格式标记（02 §2.2「格式自辨」）。

存储根落一枚**明文**标记文件，声明本根的落盘格式与模式；:meth:`Store.open`
先读它，再决定要不要口令、走哪条读取路径。标记**不含任何密钥材料**、
人可读——故它可以在解密任何东西**之前**被读，这正是「调用方不指定格式」
能成立的前提（[T-L0-018.1](../../../项目管理/tasks/T-L0-018.1-存储加密可选与明密双向转换.md) A1）。

两处兼容口径：

- **标记缺失** ⇒ 历史存储（本机制落地前建的根），按**加密**口径读——与此前
  行为逐字节一致（A2）。既有加密根不因本机制的引入而需要迁移。
- **标记存在但内容非法**（格式名不符 / 模式未知）⇒ 显式拒开，不猜。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from st_agent.l0.storage.errors import StorageOpenError

__all__ = [
    "MODE_ENCRYPTED",
    "MODE_PLAIN",
    "STORE_FORMAT",
    "STORE_MARKER",
    "StoreMode",
    "read_mode",
    "write_marker",
]

STORE_MARKER = "store.json"
"""存储根下的格式标记文件名（明文）。"""

STORE_FORMAT = "st-agent-store/v1"
"""格式名 + 版本（主版本变更 = 契约不兼容，与 01 §9 的版本语义同族）。"""

MODE_PLAIN = "plain"
"""明文模式：除 ``secrets`` 外分区免口令落盘（默认）。"""

MODE_ENCRYPTED = "encrypted"
"""加密模式：全部分区经用户主密码派生的密钥加密（口令必填）。"""

StoreMode = Literal["plain", "encrypted"]


def read_mode(root: Path | str) -> StoreMode | None:
    """读存储根的格式模式；``None`` ＝ 无标记（历史根，按加密口径读）。

    标记损坏 / 格式名或模式无法识别 → :class:`StorageOpenError`（不静默回退到
    某个模式——猜错模式会让上层读到乱码而误报损坏）。
    """
    marker = Path(root) / STORE_MARKER
    if not marker.is_file():
        return None
    try:
        meta = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageOpenError(f"存储格式标记损坏（{STORE_MARKER}）：{exc}") from exc
    fmt = meta.get("format")
    mode = meta.get("mode")
    if fmt != STORE_FORMAT:
        raise StorageOpenError(
            f"存储格式标记的格式名不支持：{fmt!r}（本实现认 {STORE_FORMAT!r}）"
        )
    if mode not in (MODE_PLAIN, MODE_ENCRYPTED):
        raise StorageOpenError(f"存储格式标记的模式非法：{mode!r}")
    return mode


def write_marker(root: Path | str, mode: StoreMode) -> None:
    """落格式标记（临时文件 + ``os.replace``，与 02 §2.4 的落盘原子性同款）。"""
    marker = Path(root) / STORE_MARKER
    tmp = marker.with_name(f"{STORE_MARKER}.tmp-{os.urandom(6).hex()}")
    payload = json.dumps({"format": STORE_FORMAT, "mode": mode}, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, marker)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
