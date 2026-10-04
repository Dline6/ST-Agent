"""L4 视角模型错误类型（06-L4；失败显式化，分级与 L1/L2 同构）。

- ``LensValidationError``——Lens 字段 / 命名 / skill_bundle 非法（含中性化拒绝），fail-fast
- ``LensNotFoundError``——按 ``lens_id`` 取不到视角（寻址失败）
- ``BuiltinLensError``——对内置视角执行不允许的操作（删除；内置只能停用不可删，见 06 §1 与任务 A2）
- ``PortUnavailableError``——组合根注入的鸭子端口（观点合成 / 维度目录 / 证据重研判）运行期不可用
  （端点或取数面失败）——显式抛出，由调用方按各自口径处置（编排降级为数据不足 / 对照标注未判定）

写入路径的失败一律显式抛出，不做静默覆盖（与 [04 §4](../../../docs/技术架构-v2/04-L2-记忆图谱.md)
「不存在任何静默覆盖」同口径）。
"""

__all__ = [
    "BuiltinLensError",
    "L4Error",
    "LensNotFoundError",
    "LensValidationError",
    "PortUnavailableError",
]


class L4Error(Exception):
    """L4 多视角推理子系统错误基类。"""


class PortUnavailableError(L4Error):
    """注入的鸭子端口运行期不可用（端点 / 取数面失败）——显式抛出，不静默降级。"""


class LensValidationError(L4Error, ValueError):
    """Lens 标识 / 命名 / skill_bundle / 评判准则等配置项非法（含 01 §6 中性化拒绝）。"""


class LensNotFoundError(L4Error, KeyError):
    """按 ``lens_id`` 取不到视角（寻址失败）。"""


class BuiltinLensError(L4Error):
    """对内置（``kind=builtin``）视角执行删除——内置只能停用不可删（06 §1 常设阵容）。"""
