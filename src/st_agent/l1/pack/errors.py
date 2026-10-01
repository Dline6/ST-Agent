"""官方资源包（Official Resource Pack）错误类型（01 §13）。

三类失败显式化（[01 §5](../../../docs/技术架构-v2/01-平台共享契约.md) 失败显式化）：
条目形状非法（构造期 fail-fast）、kind 无 loader（装载期显式拒）、未知 kind 操作。
"""

from __future__ import annotations

__all__ = [
    "PackError",
    "PackLoaderMissingError",
    "PackResourceError",
]


class PackError(Exception):
    """官方资源包子系统错误基类。"""


class PackResourceError(PackError, ValueError):
    """资源条目形状非法（``kind`` 空 / ``version`` 不合 [01 §9] 语义等）。"""


class PackLoaderMissingError(PackError, KeyError):
    """某条目的 ``kind`` 没有任何 loader——显式拒，不静默忽略、不猜测（01 §13）。"""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind

    def __str__(self) -> str:  # KeyError.__str__ 会加引号，这里给可读文案
        return f"kind {self.kind!r} 没有注册 loader，无法装载（01 §13：未注册即显式拒）"
