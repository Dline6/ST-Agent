---
id: T-MEM-001.5
parent: T-MEM-001
title: 切片计量与截断策略
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P1
milestone: M12
depends_on: [T-L2-001.3, T-AGT-033.1]
status: todo
decisions: []
verify:
---

# T-MEM-001.5 · 切片计量与截断策略

## 目标

让记忆切片的预算计量等于**实际渲染体量**：`_render_memory` 只输出节点本体，不输出边，计量不应把 `MemorySlice.model_dump_json()` 里的 `edges` 算进去（图视图需要边时另行计量）；截断遇到超大切片时**跳过**而不是 `break`，让后面更小的相关切片仍有机会进入。记忆配额在历史/步骤之间的回收由 [`T-AGT-033.1`](done/M11/T-AGT-033.1-预算信封与计量口径.md) 承接，本叶不重复实现。

## 验收标准（Given-When-Then）

- GWT-1：Given 一条带多条边的切片，Then list/timeline 视图的预算计量只按节点本体 + 元数据（与 `_render_memory` 的输出同口径），不因 `edges` 被高估而提前截掉。
- GWT-2：Given graph 视图，Then 边参与计量（或按独立边预算），且不改变“三视图共享候选集、只改排序”的既有语义。
- GWT-3：Given 第一条切片超大、后面还有更小的相关切片，Then 跳过超大者继续收后面的，不因 `break` 让整批记忆为空；被跳过项显式标注（或至少有可解释原因）。
- GWT-4：Given 同一输入与预算，Then 截断结果确定；保留项的排序仍是原相关性序。
- GWT-5：Given 记忆为空/配额回收，Then 由 [`T-AGT-033.1`](done/M11/T-AGT-033.1-预算信封与计量口径.md) 处理，本叶不另造一套分配逻辑。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`MemorySlice` / `_truncate` / `_order`（[`reader.py`](../../src/st_agent/l2/memory/reader.py)）· `_render_memory`（[`context.py`](../../src/st_agent/l3/runtime/context.py)）· 记忆配额（[`T-AGT-033.1`](done/M11/T-AGT-033.1-预算信封与计量口径.md)）。
- 输出：按渲染体量的计量函数 · 跳过超大切片 · 图视图边计量 · 截断留痕/说明。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：L2 不能 import L3 的 `_render_memory`；计量口径由 L2 按节点字段复算，并由测试与 L3 渲染体量钉住（同 `results.py` 与 `truncation.py` 的做法）。
- **A2**：跳过超大切片可能改变“前 N 条”集合，但保留原相关性序；是否把被跳过项记入 `dropped` 在 ④ 定。
- **A3**：图视图的边计量可以独立预算，也可以只在图视图包含边；不影响 list/timeline 的节点计量。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3.1 切片与预算
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 记忆切片装载

## 备注

证据：`_truncate` 用 `len(s.model_dump_json())`，包含 `edges`；`_render_memory` 只输出节点（含 confidence/source/as_of），不输出边；`_truncate` 第一条超预算即 `break`，大节点会挡住后面所有更小的相关节点。配额 50% 固定未回收归 `T-AGT-033.1`。
