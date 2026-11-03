---
id: T-MEM-001.6
parent: T-MEM-001
title: 边查询索引与性能
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P2
milestone: M12
depends_on: [T-L2-001.1, T-L2-001.3]
status: todo
decisions: []
verify:
---

# T-MEM-001.6 · 边查询索引与性能

## 目标

消除 `MemoryReader.query` 对每个候选节点调用 `graph.edges_of()`、而 `edges_of` 每次重新读取全部边文件的 O(N×E) 盘读：一次查询内构建一次边索引（或 `edges()` 单次扫描），按节点取相接边；写操作后索引可失效/重建，不返回陈旧边。

## 验收标准（Given-When-Then）

- GWT-1：Given N 个候选节点、E 条边，Then 一次查询对每条边文件最多读取一次（或使用一次构建的邻接索引），不出现 N 次全量 `edges()`。
- GWT-2：Given 查询后新增/删除一条边，Then 下一次查询看到最新边，不返回陈旧索引。
- GWT-3：Given `graph` 视图，Then 每条切片的边与 `edges_of(node_id)` 结果一致（正确性不因索引改变）。
- GWT-4：Given 空图/无边，Then 查询正常返回，不额外报错。
- GWT-5：Given 规模基准（例如 N=1000、E=5000），Then 查询盘读次数/耗时为线性量级，并有测试或基准留痕。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`MemoryGraph.edges` / `edges_of`（[`graph.py`](../../src/st_agent/l2/memory/graph.py)）· `MemoryReader._to_slice` / `query`（[`reader.py`](../../src/st_agent/l2/memory/reader.py)）。
- 输出：单次查询边索引/邻接表 · 写后失效 · 正确性与规模用例。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：索引只在单次查询内有效即可满足性能目标；若做跨查询缓存，必须按写操作失效。
- **A2**：`Store` 已有分区锁；索引不引入新的并发共享状态。
- **A3**：不改变 `MemoryEdge` 的存储形态或路径口径。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §1 边模型 · §3.1 图谱视图
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §2 分区与锁

## 备注

证据：`MemoryGraph.edges_of` 调用 `self.edges()`，后者对每个边文件调用 `_read_edge`；`MemoryReader._to_slice` 对每个候选节点调用 `edges_of`，即 N 次全量边读。
