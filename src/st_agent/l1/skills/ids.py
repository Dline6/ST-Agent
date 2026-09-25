"""``skill_id`` 版本后缀拼接规则（T-L1-001.1；兑现 T-SC-001 遗留①）。

规则：``sk_<注册名>_v<主>.<次>``

- ``<注册名>``：小写 kebab/下划线形态（``[a-z0-9][a-z0-9_.\\-]{0,60}``），
  由注册方在 ``register`` 时声明，注册后不可改名（改名即新 base）。
- ``_v<主>.<次>``：两位 SemVer（01 §9 主.次语义；补丁位不进契约）。
  主版本变更 = 契约不兼容（消费方需人工确认）；次版本 = 兼容增强。
- 同 base 多版本共存：老版本文件保留，供 ``version_policy=locked``
  的引用方继续使用；``get_latest`` 取同 base 下最高版本。

示例：``sk_unhat_eligibility_check_v1.0`` → base
``sk_unhat_eligibility_check`` + ``SemVer(1, 0)``。
"""

from __future__ import annotations

import re

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.skills.errors import SkillValidationError

__all__ = [
    "SKILL_ID_PATTERN",
    "base_of",
    "check_skill_id",
    "parse_skill_id",
    "skill_id_for",
]

SKILL_ID_PATTERN = re.compile(r"^(sk_[a-z0-9][a-z0-9_.\-]{0,60})_v(\d+)\.(\d+)$")
"""注册名拼接规则的机器可读副本（A1 验证口径）。"""


def check_skill_id(value: str) -> str:
    """校验 skill_id 拼接形态（非法 → ``SkillValidationError``，不抛裸 ValueError）。"""
    if not isinstance(value, str) or not SKILL_ID_PATTERN.match(value):
        raise SkillValidationError(
            f"非法 skill_id {value!r}（须为 sk_<注册名>_v<主>.<次>，如 "
            "sk_unhat_eligibility_check_v1.0）"
        )
    if len(value) > 128:
        raise SkillValidationError(f"skill_id 超长（≤128）：{value!r}")
    return value


def parse_skill_id(skill_id: str) -> tuple[str, SemVer]:
    """拆 ``skill_id`` → ``(base, SemVer)``（形态非法即拒）。"""
    check_skill_id(skill_id)
    m = SKILL_ID_PATTERN.match(skill_id)
    assert m is not None
    return m.group(1), SemVer(major=int(m.group(2)), minor=int(m.group(3)))


def base_of(skill_id: str) -> str:
    """取 skill_id 的 base 部分（版本号剥离；待检查标记按 base 归集）。"""
    base, _ = parse_skill_id(skill_id)
    return base


def skill_id_for(base: str, version: SemVer | str) -> str:
    """由 base + 版本拼出 skill_id（base 须已为 ``sk_`` 前缀形态）。"""
    if isinstance(version, str):
        version = SemVer.parse(version)
    candidate = f"{base}_v{version.major}.{version.minor}"
    return check_skill_id(candidate)
