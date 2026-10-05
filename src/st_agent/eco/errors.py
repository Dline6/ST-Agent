"""生态与分享（ECO 层）的失败形态（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）。

三类失败各有其因，调用方据此分流——**不合并成一个泛化错误**：

- :class:`ShareFormatError`——容器**结构**层面不成立：非 JSON / 段缺失 / 格式标识不符
  （含把备份归档喂进来）/ 载荷结构非法 / **校验和不符**。对应 [09 §3](../../../docs/技术架构-v2/09-生态与分享.md)
  「损坏文件明确报错不安装」。
- :class:`ShareVersionError`——格式版本**主版本**不兼容（[01 §9](../../../docs/技术架构-v2/01-平台共享契约.md)）。
  与「结构坏了」分开：文件是好的，只是本机读不懂。
- :class:`ShareExportError`——**导出侧**失败：命名 / 描述未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
  中性化校验（[09 §2](../../../docs/技术架构-v2/09-生态与分享.md)「导出前拦截」）· 该类导出所需的取材面
  未注入 · 写出目标不可用。**文件尚未生成**（或未写出）。
"""

from __future__ import annotations

__all__ = [
    "EcoError",
    "ShareExportError",
    "ShareFormatError",
    "ShareVersionError",
]


class EcoError(Exception):
    """生态与分享面的基类（本层自有的失败形态，不外泄 pydantic 的内部结构）。"""


class ShareFormatError(EcoError):
    """分享物容器结构不成立（含校验和不符）。"""


class ShareVersionError(EcoError):
    """格式版本主版本不兼容（01 §9：主版本变更 = 契约不兼容）。"""


class ShareExportError(EcoError):
    """导出侧失败（拦截 / 取材面缺失 / 写出目标不可用；文件未生成或未写出）。"""
