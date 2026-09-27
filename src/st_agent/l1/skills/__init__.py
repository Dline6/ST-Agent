"""Skill 注册表子包（T-L1-001.1；03 §1.1 + §1.4）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- Skill 描述体 → ``config`` 分区 ``skill-registry/<skill_id>.json``
- 待检查标记 → ``config`` 分区 ``skill-update/<base>.json``
- 权限批准态 → ``config`` 分区 ``skill-permissions/<base>.json``（T-L1-009.1）
"""

from st_agent.l1.skills.errors import (
    SkillError,
    SkillExistsError,
    SkillNotFoundError,
    SkillPermissionError,
    SkillValidationError,
)
from st_agent.l1.skills.ids import (
    SKILL_BASE_PATTERN,
    SKILL_ID_PATTERN,
    base_of,
    check_skill_base,
    check_skill_id,
    parse_skill_id,
    skill_id_for,
)
from st_agent.l1.skills.pack import OFFICIAL_PACK, ensure_official_pack
from st_agent.l1.skills.permissions import PERMISSION_PREFIX, SkillPermissionBook
from st_agent.l1.skills.registry import (
    SKILL_PREFIX,
    UPDATE_PREFIX,
    SkillRegistry,
    UpdateInfo,
    validate_param_values,
)

__all__ = [
    "OFFICIAL_PACK",
    "PERMISSION_PREFIX",
    "SKILL_BASE_PATTERN",
    "SKILL_ID_PATTERN",
    "SKILL_PREFIX",
    "UPDATE_PREFIX",
    "SkillError",
    "SkillExistsError",
    "SkillNotFoundError",
    "SkillPermissionBook",
    "SkillPermissionError",
    "SkillRegistry",
    "SkillValidationError",
    "UpdateInfo",
    "base_of",
    "check_skill_base",
    "check_skill_id",
    "ensure_official_pack",
    "parse_skill_id",
    "skill_id_for",
    "validate_param_values",
]
