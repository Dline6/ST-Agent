"""内置原语安装面的失败形态（[01 §2](../../../docs/技术架构-v2/01-平台共享契约.md)）。

- :class:`BuiltinRegistrationError`——**安装期**失败：种子形态非法 / 同 `skill_id`
  重复 / 与既有非内置能力冲突 / `requires` 的依赖未注入。一律显式失败，不留半个
  原语（同官方 Pack 装载的「已存在即跳过、冲突不静默覆盖」口径）。
"""

from __future__ import annotations

__all__ = ["BuiltinError", "BuiltinRegistrationError"]


class BuiltinError(Exception):
    """内置原语面的基类错误。"""


class BuiltinRegistrationError(BuiltinError):
    """内置原语种子 / 安装不成立（形态、冲突、依赖缺失）。"""
