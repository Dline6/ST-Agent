"""内置原语的后置端口容器（T-AGT-013 批；[D-145](../../../项目管理/决策日志.md)）。

有些执行面在 **L1 装配完成之后**才由组合根造出——``DataSyncRunner`` 依赖
M5 的关注池回调，而 L1 先于它装配。直接以 ``None`` 占位会让「未接线」与
「接了但不可用」混为一谈；故用一个小容器承载**后置绑定**：

- 安装期端口对象**恒在**（``requires`` 校验通过，原语照常进工具目录）；
- 组合根造好真实执行面后调 :meth:`SyncPort.bind` 绑定；
- 未绑定时原语读到 ``None``，显式回 ``unavailable`` 并点名未接线——
  **不臆造运行态、不做假取数**。

容器只做存取，不持业务语义；线程安全的读取是为了后台同步批次与循环线程并存。
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = ["SyncPort"]


class SyncPort:
    """同步执行面（``DataSyncRunner``）的后置绑定容器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runner: Any = None

    def bind(self, runner: Any) -> None:
        """绑定真实执行面；重复绑定以最后一次为准（幂等装配口径）。"""
        with self._lock:
            self._runner = runner

    def get(self) -> Any:
        """取已绑定的执行面；未绑定 → ``None``。"""
        with self._lock:
            return self._runner

    @property
    def bound(self) -> bool:
        with self._lock:
            return self._runner is not None
