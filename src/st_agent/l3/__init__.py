"""L3 · 对话主入口（Chat-as-OS，[05](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

产品的**唯一用户交互主入口**：会话管理、意图理解与澄清、任务派发、Generative UI、
推理链可视化、对话即配置。本包是 L3 的代码根，跨层协作一律经 01-平台共享契约
（依赖注入 / 事件），只向下依赖（铁律 7）。

已落地的三段（[`T-L3-001`](../../../项目管理/tasks/T-L3-001-会话模型上下文卡片快捷指令.md) 三叶）：

- [`chat.session`](chat/session.py)——会话模型（[05 §1](../../../docs/技术架构-v2/05-L3-对话主入口.md)）
- [`home.card`](home/card.py)——上下文卡片（[05 §2](../../../docs/技术架构-v2/05-L3-对话主入口.md)）
- [`commands.registry`](commands/registry.py)——快捷指令（[05 §8](../../../docs/技术架构-v2/05-L3-对话主入口.md)）

**中性视角（铁律 2）**：本层产出的**生成性文案**须过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
``check_output``；**记忆本体内容**属数据展示、不在 §6 三类对象内（口径见
[D-053](../../../项目管理/决策日志.md)）。
"""

from __future__ import annotations

from st_agent.l3.errors import (
    CommandValidationError,
    L3Error,
    SessionNotFoundError,
    SessionValidationError,
)

__all__ = [
    "CommandValidationError",
    "L3Error",
    "SessionNotFoundError",
    "SessionValidationError",
]
