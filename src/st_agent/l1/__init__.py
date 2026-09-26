"""L1 Skill Runtime 门面（03-L1 §1；注册/执行/沙箱/复用）。

各子模块按 T-L1-001 拆分落地：
- ``.1`` 注册发现 + 参数校验 + 版本管理 → :mod:`st_agent.l1.skills`
- ``.2`` 执行流水线 → :mod:`st_agent.l1.runner`
- ``.3`` 执行沙箱越界拦截 → :mod:`st_agent.l1.sandbox`
- ``.4`` 输出复用 + 新鲜度查询 → :mod:`st_agent.l1.reuse`
"""

from __future__ import annotations

__all__: list[str] = []
