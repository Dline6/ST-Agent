"""L1 Skill Runtime 门面（03-L1 §1；注册/执行/沙箱/复用）。

各子模块按 T-L1-001 拆分落地：
- ``.1`` 注册发现 + 参数校验 + 版本管理 → :mod:`st_agent.l1.skills`
- ``.2`` 执行流水线 → 后续任务
- ``.3`` 沙箱 → 后续任务
- ``.4`` 输出复用 → 后续任务
"""

from __future__ import annotations

__all__: list[str] = []
