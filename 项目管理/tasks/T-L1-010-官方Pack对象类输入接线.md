---
id: T-L1-010
title: 官方 Pack 对象类输入接线（消费 L2 记忆注入面）
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P1
milestone: M2
depends_on: [T-L1-004, T-L2-001, T-L2-004]
status: todo
decisions: []
---

# T-L1-010 · 官方 Pack 对象类输入接线（消费 L2 记忆注入面）

> 2026-09-27 由 `T-L2-001` 收工查册立项（[L1 册 `C2`](../遗留问题/L1-遗留问题.md)）；验收段与接口面在开工 ④ 对齐时落定。

## 目标

把官方 Pack 里三处**对象类输入**从「可解释默认口径」改为**从 L2 记忆图谱取真实用户数据**，兑现 [L1 册 `C2`](../遗留问题/L1-遗留问题.md)：

| 执行器 | 对象类输入 | 当前默认口径 |
|---|---|---|
| `portfolio-stress-test` | 用户组合 | `portfolio_source` 标注来源 |
| `opportunity-mine` | 风格 / 板块偏好 | `preference` 标注来源 |
| `strategy-design` | 信号定义 | `signal_check.reason` 标注来源 |

既有两条输入通道（声明参数的 `ParameterSpec.type` 仅 string / number / integer / boolean / enum，以及上游输出）**承载不了对象**——本任务要接的是 L2 侧的**注入面**（`T-L2-001` 已交付），接线工作落 L1。

## 验收标准（Given-When-Then，从 Story 抄）

- 待 ④ 对齐时补全（须覆盖：三处输入各自的取数路径与降级语义、无相关记忆时的空态、默认口径与用户数据的**显式区分**）。

## 接口面

- 暂无（④ 对齐时补）
  - **输入**：L2 [`MemoryReader`](../../src/st_agent/l2/memory/reader.py) 的上下文切片（`attention` / `identity` 节点）+ 既有单一取数入口 `query_rows`
  - **输出**：三个执行器的输入来源改点；`portfolio_source` / `preference` / `signal_check.reason` 的语义从「默认口径」改为「来源标注」
  - **前置（跨层，铁律 8）**：若确认对象类输入须走**新的输入通道**，须**先改** [03 §1.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 与 [01 §2](../../docs/技术架构-v2/01-平台共享契约.md)，再返工全部执行器（[L1 册 `C2`](../遗留问题/L1-遗留问题.md) 明列的返工面）

## 可关闭的遗留

- [L1 册 `C2`](../遗留问题/L1-遗留问题.md)——归属即本任务（2026-09-27 立项）。其「**注入面**」一半已由 `T-L2-001.1`（`attention` / `identity` 节点模型与亲和表）与 `T-L2-001.3`（`MemoryReader` 上下文切片）交付，本任务只做**接线**。

## 假设与前提

- 暂无（④ 对齐时补：A<n> 编号，每条含前提内容 / 若错的影响 / 验证方式）

## 涉及契约

- [01 §2 SkillDescriptor](../../docs/技术架构-v2/01-平台共享契约.md)（输入输出契约）· [§7 配置元模型](../../docs/技术架构-v2/01-平台共享契约.md)
- [03 §1.2 执行流水线](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（既有两条输入路径）
- [04 §3.1 MemoryReader](../../docs/技术架构-v2/04-L2-记忆图谱.md)（注入面）

## 参考

- Story（What）：[skills-runtime](../../docs/PRD-v2-Agent/story-02-skills-runtime.md)
- 上游：[`T-L1-004`](done/M0/T-L1-004-官方SkillPack公共认知主动服务Bundl.md)（三处执行器与其默认口径）· [`T-L2-001`](T-L2-001-节点边模型读写接口MemoryReaderWri.md)（注入面）· [`T-L2-004`](T-L2-004-Onboarding协议导出导入继承.md)（画像生成的来源）

## 备注

实现细节不写此处，留给代码 / commit / 执行日志。
