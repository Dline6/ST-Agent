---
id: T-MEM-001
parent: null
title: 记忆机制重整
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P0
milestone: M12
depends_on: [T-AGT-030.1, T-L2-001.3, T-AGT-033.3, T-L2-002.3, T-AGT-033.1, T-L2-001.1, T-L2-002.1, T-L2-002.2, T-L2-004.1]
status: todo
decisions: []
verify:
---

# T-MEM-001 · 记忆机制重整

> **分组节点**：本节点不直接交付，交付由 [`T-MEM-001.1`](T-MEM-001.1-记忆读取工具与按需重查.md) / [`T-MEM-001.2`](T-MEM-001.2-中文主题检索与相关性排序.md) / [`T-MEM-001.3`](T-MEM-001.3-任务类型亲和与主题来源.md) / [`T-MEM-001.4`](T-MEM-001.4-读取时钟与长运行衰减.md) / [`T-MEM-001.5`](T-MEM-001.5-切片计量与截断策略.md) / [`T-MEM-001.6`](T-MEM-001.6-边查询索引与性能.md) / [`T-MEM-001.7`](T-MEM-001.7-记忆写入生产路由与空存储初始化.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标

修复记忆机制当前“只有运行开始时推一批切片、中文主题检索基本失效、读不到写不进、长运行不推进时钟、查询 O(N×E)”的结构性问题。方向是：**记忆读取成为模型可按需调用的只读能力**；主题检索对中文可用且可解释；任务类型/主题来自会话主任务；切片计量与截断按实际渲染体量；边查询走索引；显式偏好写入有生产路由，空存储有初始化路径。

## 验收标准（Given-When-Then）

- GWT-1：由 `.1`–`.7` 合成，父节点不单独验收。

## 接口面

子叶并集（详见各叶）：

- **输入**：[`MemoryReader`](../../src/st_agent/l2/memory/reader.py) · [`MemoryGraph`](../../src/st_agent/l2/memory/graph.py) · [`MemoryWriter`](../../src/st_agent/l2/memory/writer.py) · [`OnboardingProtocol`](../../src/st_agent/l2/memory/onboarding.py) · `sk_memory_op` 承接（[`subprotocols.py`](../../src/st_agent/l3/runtime/subprotocols.py)）· `assemble_context` / `_context_inputs`（[`session.py`](../../src/st_agent/l3/chat/session.py) / [`composition/agent.py`](../../src/st_agent/composition/agent.py)）· 会话主任务（[`T-AGT-033.3`](done/M11/T-AGT-033.3-任务锚点与历史装载.md)）。
- **输出**：按需记忆读取工具 · 中文主题检索与排序 · `investigate` 亲和 · 每查询时钟 · 按渲染体量的切片计量/截断 · 边索引 · 偏好写入生产路由与空存储初始化。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核 L0 / L1 / L2 / L3 / L5 / L6 未闭区）。

## 假设与前提

子叶并集，详见各叶 A 编号。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3 读取面 · §3.2 写入红线 · §4 写入策略 · §7 空状态
- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 循环工作上下文

## 备注

12 条问题与叶的对应：①→`.1`、②→`.2`、③→`.3`、④→`.3`、⑤→`.4`、⑥→`.5`、⑦→`.6`、⑧→`.5`、⑨→**已由 [`T-AGT-033.1`](done/M11/T-AGT-033.1-预算信封与计量口径.md) 的配额回收覆盖，不重复立项**、⑩→`.1`、⑪→`.7`、⑫→`.1`。本组挂 **M12 · 记忆可用**；集成关卡 [`T-INT-013`](T-INT-013-M12集成关卡记忆可用.md) 覆盖 7 叶。真实存储 `D:\Documents\STAgent\memory` 目前只有 `manifest.bin`，无节点/边。
