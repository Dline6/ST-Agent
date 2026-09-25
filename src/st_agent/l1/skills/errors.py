"""Skill 注册表错误类型（03 §1；失败显式化，01 §5）。

- 配置面（fail-fast 异常）：``SkillNotFoundError`` / ``SkillExistsError`` /
  ``SkillValidationError``
- 执行面（运行时失败一律走 ``ResultEnvelope``，由 T-L1-001.2 承载，不抛异常）
"""

__all__ = [
    "SkillError",
    "SkillExistsError",
    "SkillNotFoundError",
    "SkillValidationError",
]


class SkillError(Exception):
    """Skill Runtime 子系统错误基类。"""


class SkillNotFoundError(SkillError, KeyError):
    """Skill 不存在（调用方传了未注册的 skill_id，属调用方缺陷）。"""


class SkillExistsError(SkillError):
    """同标识 Skill 已存在——更新走版本发布流程，不得静默覆盖。"""


class SkillValidationError(SkillError, ValueError):
    """Skill 标识 / 参数 / 版本 / 命名 / 权限等配置项非法。"""
