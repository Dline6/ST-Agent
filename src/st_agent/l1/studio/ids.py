"""工作流注册名（base）派生（T-L1-003.4；03 §4「接受」口径）。

``flow_id`` 形如 ``wf_<注册名>_v<主>.<次>``（01 §1）。草稿只携**展示名**
（可为中文），注册名由本模块派生——否则「每日 ST 简报」这类名字拼不出 flow_id：

- 优先取草稿显式给出的 ASCII ``flow_name``
- 缺省时由展示名 slug 化：小写、非 ``[a-z0-9]`` 转 ``_``、去首尾 ``_``——
  slug 为空（如纯中文名）即**显式拒**，提示调用方补 ``flow_name``

派生结果一律以 ``wf_`` 冠首，供 ``workflow.ids.flow_id_for`` 拼完整 flow_id。
"""

from __future__ import annotations

import re

from st_agent.l1.studio.errors import DraftAcceptError

__all__ = ["SLUG_PATTERN", "slugify", "workflow_base"]

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,60}$")
"""注册名形态（``contracts.identifiers.FLOW_ID_PATTERN`` 注册名捕获组的**子集**——
只以 ``_`` 作分隔，不引入 ``.`` / ``-``，避免与版本后缀 ``_v`` 的解析产生歧义）。"""

_SLUG_MAX = 61
"""注册名长度上限（``FLOW_ID_PATTERN`` 的注册名捕获组为 1 + 60）。"""


def slugify(name: str) -> str:
    """展示名 → ASCII 注册名候选（无可用字符 → 空串）。"""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug[:_SLUG_MAX]


def workflow_base(name: str, flow_name: str | None = None) -> str:
    """派生工作流 base ``wf_<注册名>``（无法派生 → ``DraftAcceptError``）。"""
    candidate = (flow_name or "").strip()
    if candidate:
        if not SLUG_PATTERN.match(candidate):
            raise DraftAcceptError(
                f"flow_name {candidate!r} 形态非法"
                "（须为小写字母数字起头、可含 _、≤61 字符）")
        return f"wf_{candidate}"
    candidate = slugify(name)
    if not candidate:
        raise DraftAcceptError(
            f"展示名 {name!r} 无法派生注册名（不含 ASCII 字母数字）——"
            "请由调用方显式提供 ASCII flow_name")
    if not SLUG_PATTERN.match(candidate):
        raise DraftAcceptError(f"由展示名派生的注册名 {candidate!r} 形态非法")
    return f"wf_{candidate}"
