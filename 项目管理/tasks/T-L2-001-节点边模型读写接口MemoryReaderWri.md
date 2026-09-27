---
id: T-L2-001
parent: null
title: 节点/边模型 + 读写接口（MemoryReader/Writer）
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §1–§3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P0
milestone: M1
depends_on: [T-SC-001, T-L0-001]
status: done
decisions: []
verify:
---

# T-L2-001 · 节点/边模型 + 读写接口（MemoryReader/Writer）

## 目标

把 [04 §1–§3](../../docs/技术架构-v2/04-L2-记忆图谱.md) 的 L2 本体与访问面实现为代码里的单一真相源：六类节点 + 四类边的模型与其 `memory` 分区落盘（本体），以及读写两个接口（`MemoryReader` / `MemoryWriter`）。它是 L2 后续任务（写入策略 / 删除审计 / Onboarding / 导出导入）与 L3–L6 一切记忆读写的共同底座。

## 验收标准（Given-When-Then，概要）

- 六类节点与四类边可按 §1/§2 建模并有 `memory` 分区落盘往返（细见 `.1`）
- 用户显式与系统推断两条写入路径可用，系统不得自主改既有节点内容（细见 `.2`）
- 上下文切片查询可用，切片带 `as_of` 与置信度，三视图共享同一查询层（细见 `.3`）

## 涉及契约

- [04 §1 节点模型 / §2 边模型 / §3 读写接口](../../docs/技术架构-v2/04-L2-记忆图谱.md)
- [01 §1 标识](../../docs/技术架构-v2/01-平台共享契约.md)（`memory_node_id`）· [§5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md) · [§8 时间口径](../../docs/技术架构-v2/01-平台共享契约.md)
- 物理存储：L0 `memory` 分区（[02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md)）

## 参考

- Story（What）：[memory-graph](../../docs/PRD-v2-Agent/story-03-memory-graph.md)

## 拆分记录（③ 已执行）

命中拆分触发（涉及 §1 / §2 / §3 多个契约小节，且跨越「建模 → 落盘 → 读写」三类一次性改动），已拆为叶子子任务：

- `T-L2-001.1` 记忆图谱本体（节点/边模型 + `memory` 分区落盘）（§1 / §2）
- `T-L2-001.2` MemoryWriter 写入接口（§3.2；依赖 `.1`）
- `T-L2-001.3` MemoryReader 上下文切片查询（§3.1；依赖 `.1`）

父任务 `T-L2-001` 的 `status` 由 children 派生，不再手填。

## 备注

实现细节不写此处，留给代码 / commit / 执行日志。
