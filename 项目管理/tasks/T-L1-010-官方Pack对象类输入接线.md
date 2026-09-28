---
id: T-L1-010
title: 官方 Pack 对象类输入接线（消费 L2 记忆注入面）
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P1
milestone: M2
depends_on: [T-L1-001, T-L1-004]
status: done
decisions: [D-056]
---

# T-L1-010 · 官方 Pack 对象类输入接线（消费 L2 记忆注入面）

> 2026-09-27 由 `T-L2-001` 收工查册立项（[L1 册 `C2`](../遗留问题/L1-遗留问题.md)）；验收段与接口面在开工 ④ 对齐时落定（2026-09-28）。

## 目标

把官方 Pack 里三处**对象类输入**从「可解释默认口径」改为**从 L2 记忆图谱取真实用户数据**，兑现 [L1 册 `C2`](../遗留问题/L1-遗留问题.md)：

| 执行器 | 对象类输入 | 当前默认口径 | 记忆侧取值面 |
|---|---|---|---|
| `portfolio-stress-test` | 用户组合 | `portfolio_source` 标注来源 | `attention.holdings` |
| `opportunity-mine` | 风格 / 板块偏好 | `preference` 标注来源 | `attention.sector_preferences` / `.theme_interests` + `identity.risk_preference` |
| `strategy-design` | 回测标的池（`universe`） | 全市场等权 | `attention.holdings` / `.watchlist` |

**通道口径**（[D-056](../决策日志.md) ④ 对齐选定）：对象经**描述体早已声明却从未被读取的 `input_schema`** 进执行器（`runner.run(..., inputs=…)` → `ctx.inputs`）——L1 **不**读 L2（[铁律 7](../工程宪法.md)），「读 `MemoryReader` → 汇成 inputs」是**调用方 / 集成关卡**的事（循 [D-055](../决策日志.md) 与 [`T-L0-015.1`](T-L0-015.1-关注面注入通道与coverage合约.md) 口径）。取不到相关记忆 → **回落当前默认口径**并显式标注来源，不冒充用户数据。

## 验收标准（Given-When-Then）

行为验收由两个叶子承担（[`.1`](T-L1-010.1-input_schema运行期输入通道.md) 通道与契约 / [`.2`](T-L1-010.2-三执行器对象类输入接线.md) 三执行器接线）；父任务 `status` 与 `depends_on` 由 children 派生。父级收口语义＝两叶 GWT 全绿 + [L1 册 `C2`](../遗留问题/L1-遗留问题.md) 销账。

## 拆分记录（③ 已执行）

命中拆分触发（**验收 GWT 条数 > 5** 且**涉及 2 个及以上契约小节 / 架构层**——[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) · [03 §1.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) · [03 §2.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)），2026-09-28 ④ 对齐后拆为：

- `T-L1-010.1` **`input_schema` 运行期输入通道**——[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 方言澄清把 `input_schema` 定为运行期输入判定面 + [03 §1.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 流水线补「输入核验」步 + `SkillRunner.run(inputs=…)` / `SkillContext.inputs`
- `T-L1-010.2` **三执行器对象类输入接线**——三处输入改从 `ctx.inputs` 取、来源标注改写、降级语义

`.2` 依赖 `.1`（需先有 inputs 通道）。**父 `depends_on` 随之变化**：原立项时挂的 `T-L1-002` / `T-L2-*` 三项**全部脱落**——本交付不含 L1 读 L2 的代码（[铁律 7](../工程宪法.md)），`T-L2-001` 等只是「注入数据的最终来源」而非本任务的代码前置（同 [`T-L3-001`](T-L3-001-会话模型上下文卡片快捷指令.md) 收口时 `T-L1-001` 脱落的情形）。父任务 `status` 与 `depends_on` 由 children 派生，不手填。

## 接口面

- 见两叶（[`.1`](T-L1-010.1-input_schema运行期输入通道.md) · [`.2`](T-L1-010.2-三执行器对象类输入接线.md)）的 `## 接口面`——父任务本身不产出代码接口，只聚合子任务。
  - **输入**：调用方读 [L2 `MemoryReader`](../../src/st_agent/l2/memory/reader.py) 的上下文切片（`attention` / `identity` 节点）后**汇成的 `inputs` 对象**；执行器侧另有既有单一取数入口 `query_rows`
  - **输出**：`SkillRunner.run(inputs=…)` / `SkillContext.inputs`；三个执行器的输入来源改点（`portfolio_source` / `preference` / `signal_check` 语义从「默认口径」改为「来源标注」）
  - **前置（跨层，铁律 8）**：按 **01 §2 → 03 §1.2 / §2.2 → 代码** 顺序改（`input_schema` 从「工作流连线判定面」扩为「运行期输入判定面」，属契约澄清）

## 可关闭的遗留

- [L1 册 `C2`](../遗留问题/L1-遗留问题.md)——归属即本任务（2026-09-27 立项）。其「**注入面**」一半已由 `T-L2-001.1`（`attention` / `identity` 节点模型与亲和表）与 `T-L2-001.3`（`MemoryReader` 上下文切片）交付，本任务只做**接线**（父任务收口时销）。

## 假设与前提

- 假设与验证面见两叶（[`.1`](T-L1-010.1-input_schema运行期输入通道.md) 的 `A1`–`A3` · [`.2`](T-L1-010.2-三执行器对象类输入接线.md) 的 `A1`–`A3`）。父任务本身无独立假设（对齐在批次层做了一次，四个岔口见 [D-056](../决策日志.md)）。

## 涉及契约

- [01 §2 SkillDescriptor](../../docs/技术架构-v2/01-平台共享契约.md)（`input_schema` 的运行期语义）
- [03 §1.2 执行流水线](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（输入核验步）· [03 §2.2 主动服务 Bundle](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（三处对象类输入的取值面）
- [04 §3.1 MemoryReader](../../docs/技术架构-v2/04-L2-记忆图谱.md)（注入数据的最终来源；不由 L1 直读）

## 参考

- Story（What）：[skills-runtime](../../docs/PRD-v2-Agent/story-02-skills-runtime.md)
- 上游：[`T-L1-004`](done/M0/T-L1-004-官方SkillPack公共认知主动服务Bundl.md)（三处执行器与其默认口径、`input_schema` 的声明）· [`T-L2-001`](T-L2-001-节点边模型读写接口MemoryReaderWri.md)（注入面）· [`T-L2-004`](T-L2-004-Onboarding协议导出导入继承.md)（画像生成的来源）
- 口径先例：[`T-L0-015.1`](T-L0-015.1-关注面注入通道与coverage合约.md)（「调用方注入、被注入方不向上读」）· [D-055](../决策日志.md)（同一取向的决议）· [D-028](../决策日志.md)（本缺口的登记与默认口径的由来）

## 备注

实现细节不写此处，留给代码 / commit / 执行日志。
