---
id: T-MEM-001.1
parent: T-MEM-001
title: 记忆读取工具与按需重查
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M12
depends_on: [T-AGT-030.1, T-L2-001.3]
status: todo
decisions: []
verify:
---

# T-MEM-001.1 · 记忆读取工具与按需重查

## 目标

给模型一个**只读、可按需调用**的记忆读取工具（暂名 `sk_memory_read`）：按主题 / 节点 id / 节点类型查询记忆，返回切片（节点、`as_of`、有效置信度、来源、边）或显式空态原因；不再只有“运行开始时推一批切片”这一条路径。同步修正预算舍弃时的提示——不再指向模型不具备的“记忆图谱读面”，而是指向该工具；并为运行中的插话/工具结果变化提供重新查询路径。

## 验收标准（Given-When-Then）

- GWT-1：Given 模型调用 `sk_memory_read(topic=...)`，Then 返回按相关性排序的切片（含 `memory_node_id` / `as_of` / `confidence` / `source`），空结果带显式原因，不返回裸空。
- GWT-2：Given 模型按 `node_id` 或 `type` 查询，Then 返回该节点/该类型切片；未知 id、非法 type 显式 `validation_failed` / `unavailable`，不猜。
- GWT-3：Given 记忆因预算被舍弃，Then prompt 的提示改为“用 `sk_memory_read` 按需读取”，且该工具确实在模型可见目录中；不再出现指向不存在能力的提示。
- GWT-4：Given 运行中插话或工具结果改变了主题，Then 模型可再次调用 `sk_memory_read` 取新主题的记忆；运行时也可在步骤边界触发一次重查（二选一，需在 ④ 定），不把运行开始时的切片当作永远最新。
- GWT-5：Given 该工具只读，Then 出厂风险类为 `autonomous-ok`，不产写副作用、不产 `skill_run`（若实现为控制动作）；读取本身进运行留痕/事件，可审计。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`MemoryReader.query` / `MemoryGraph.get_node`（[`reader.py`](../../src/st_agent/l2/memory/reader.py) / [`graph.py`](../../src/st_agent/l2/memory/graph.py)）· `sk_memory_op` 的 L3 承接形态（[`subprotocols.py`](../../src/st_agent/l3/runtime/subprotocols.py)）· 工具目录（[`tool_catalog.py`](../../src/st_agent/l1/skills/tool_catalog.py)）· 工作上下文记忆段（[`context.py`](../../src/st_agent/l3/runtime/context.py)）。
- 输出：`sk_memory_read` 工具（新）或 `sk_memory_op` 的 `operation=query` 支 · 工具描述/参数 schema · 预算舍弃提示改写 ·（可选）步骤边界重查触发。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：优先做 **L3 控制动作**（与 `sk_memory_op` 同型，不产 `skill_run`），避免 L1 向上依赖 L2；若改 L1 内置原语，须经组合根注入 reader 端口。
- **A2**：读取工具只读，不触发冲突检测/写入策略；写仍走 `T-MEM-001.7`。
- **A3**：运行中重查的触发点（插话/工具结果/每步）在 ④ 定；缺省只在模型显式调用时查，避免每步自动查询放大成本。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3.1 读取面
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 工作上下文 · 工具调用
- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §7 运行期授权

## 备注

证据：`MemoryReader.query` 只在 `_context_inputs` 被调用一次（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）；`sk_memory_op` 只产 `adjudicator.cards()`（[`subprotocols.py`](../../src/st_agent/l3/runtime/subprotocols.py)）；`context._render_memory_lines` 在被舍时写“原文仍可从记忆图谱读面取回”，但模型没有该工具。
