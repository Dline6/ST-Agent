"""官方 Skill Pack 错误类型（T-L1-004.1；失败显式化，01 §5）。

装载与取数基件属配置/接线面，非法入参 fail-fast；执行面的业务失败一律由
``SkillRunner`` 包 ``ResultEnvelope``，本模块不抛运行时业务异常。
"""

__all__ = [
    "OfficialPackError",
    "OfficialPackLoadError",
]

from st_agent.l1.skills.errors import SkillError


class OfficialPackError(SkillError):
    """官方 Skill Pack 子系统错误基类。"""


class OfficialPackLoadError(OfficialPackError):
    """装载期缺陷：取数源不合口径 / Bundle 模块未提供执行器表 / 执行器工厂非法。"""
