---
id: T-MEM-001.3
parent: T-MEM-001
title: 任务类型亲和与主题来源
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P1
milestone: M12
depends_on: [T-L2-001.3, T-AGT-033.3]
status: todo
decisions: []
verify:
---

# T-MEM-001.3 · 任务类型亲和与主题来源

## 目标

给 Agent 循环一个更窄的 `task_type`（如 `investigate` / `agent`）亲和，而不是固定 `chat` 命中全部六类节点；主题来源改为**会话主任务**（由 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md) 提供），不再把用户原话「继续」当作 topic。

## 验收标准（Given-When-Then）

- GWT-1：Given `task_type="investigate"`，Then 候选节点类型收窄（至少排除 `pattern` / `evolution` 这类反思演进面），不是全部六类；Given `task_type="chat"`，Then 原“全部类型”行为不变。
- GWT-2：Given 用户输入「继续」且会话有主任务，Then 记忆查询的 `topic` 取主任务（或主任务 + 当前跟进），不是「继续」。
- GWT-3：Given 无主任务可用，Then 回退到当前文本并显式标注“主题未锚定”，不静默使用「继续」。
- GWT-4：Given 未登记的 `task_type`，Then 保持现有“取全部类型”的宽容口径，不因新增枚举破坏旧调用方。
- GWT-5：Given 亲和表变更，Then 04 §3.1 的口径与测试同步，三视图仍共享同一候选集（只改类型面，不改视图语义）。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`TASK_TYPE_AFFINITY` / `SliceQuery.task_type` / `SliceQuery.topic`（[`reader.py`](../../src/st_agent/l2/memory/reader.py)）· `assemble_context` 的 `task_type="chat"`（[`session.py`](../../src/st_agent/l3/chat/session.py)）· `_context_inputs` 的 `topic=task`（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）· 会话主任务（[`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md)）。
- 输出：`investigate` / `agent` 亲和表 · 主任务 topic 来源 · 未锚定显式标注 · 04 §3.1 同步。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：`investigate` 的候选类型集合在 ④ 定（初版建议 `thesis` / `attention` / `identity` / `history`）；`pattern` / `evolution` 归反思演进。
- **A2**：主任务字段由 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md) 提供；本叶只消费，不重复实现锚定。
- **A3**：`chat` 的全类型亲和保留，避免普通对话行为回退。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3.1 任务类型亲和
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 记忆切片装载

## 备注

证据：`assemble_context` 固定 `task_type="chat"`，而 `TASK_TYPE_AFFINITY["chat"] = NODE_TYPES`；`_context_inputs(session, task)` 直接把 `task` 当 `topic`；真实运行中 `task` 为「继续」。
