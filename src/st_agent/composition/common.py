"""组合根各部件共用的小工具与常量。"""

from __future__ import annotations

from datetime import datetime


_LLM_ENDPOINT_ID = "cloud-main"
"""引导装载端点的固定标识（[l1/runtime.py](../l1/runtime.py) 的 `LLM_ENDPOINT_ID`，T-L1-011）。"""


def _now() -> datetime:
    return datetime.now().astimezone()


EXPORTS_DIR_NAME = "exports"
"""导出目录名（存储根下；表现层文件面的**固定常量**，非 [01 §7] 条目——见 [09 §6]）。"""
