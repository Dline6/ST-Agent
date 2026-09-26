---
id: T-L1-004
parent: null
title: 官方 Skill Pack（公共认知 + 主动服务 Bundle）
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P0
milestone: M0
depends_on: [T-L1-001, T-L0-005]
status: done
decisions: []
verify:
---

# T-L1-004 · 官方 Skill Pack（公共认知 + 主动服务 Bundle）

## 目标

落地 03 §2 的**官方 Skill Pack 可执行面**：两 Bundle 共 12 个 Skill（清单为产品承诺的最小集，可增不可缺）不只是描述体，而是**装上就能跑**的能力——首次启动即全部可调用，输入经 L0 数据源缓存（`MarketDb.query`）进入，输出过各自 `output_schema`，数据不可用 / 无结果时走显式信封分支。

描述体种子（`skills/pack.py::OFFICIAL_PACK`）与幂等播种（`ensure_official_pack`）已由 [T-L1-001.1](T-L1-001.1-注册发现参数校验版本管理.md) 交付；[T-L1-003.5](T-L1-003.5-模板库与空状态.md) 的对齐定案已把边界划死：**模板库的机制与内容归 `T-L1-003`，本任务保持纯 Skill Pack 面**（该任务假设 A2 点名的「官方 Skill 的执行器」即在本文内兑现）。本任务交付的是**执行器 + 装载接线**，不新增运行时机制。

## 验收标准（Given-When-Then，从 Story 抄）

- 待 ④ 对齐时从对应 Story 验收段逐条补全（父任务状态与叶子行为由 children 聚合，见下）

## 拆分记录（③ 已执行）

命中拆分触发（Story-02 验收段中尚未被上游任务覆盖的条目 + §2 两 Bundle 12 个 Skill 的实现面，远超 5 条 GWT；横跨 [03 §1.2 执行与留痕 / §1.3 输出复用与新鲜度](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 与 §2 Pack 两个以上契约小节，并接 [02 §5 数据源缓存](../../docs/技术架构-v2/02-L0-本地优先基座.md)；预计 Agent 会话轮次 > 20），已拆为叶子子任务：

- `T-L1-004.1` **官方 Pack 装载接线与数据访问基件**——`install_official_pack(registry, runner, *, market_query)` 幂等装载（播种描述体 + 为 12 个 `skill_id` 注册执行器）；执行器的取数统一经 `MarketDb.query` 的**单一入口**，非 `ok` 信封（`unavailable` / `empty` / `failed`）原样上抛不编造；交付公共认知 / 主动服务两 Bundle 共用的基件
- `T-L1-004.2` **公共认知 Bundle 执行器**（`st-list-sync` / `delisting-risk-scan` / `unhat-eligibility-check` / `sector-heatmap` / `sentiment-flow-analysis` / `fundamental-screening`）——逐个履行 §2.1 职责与关键输出
- `T-L1-004.3` **主动服务 Bundle 执行器**（`stock-watch` / `data-aggregate` / `risk-alert` / `opportunity-mine` / `portfolio-stress-test` / `strategy-design`）——逐个履行 §2.2 职责；含 `strategy-design` 的**未来函数检测**特殊契约；收口 [L0 册 `A3`](../遗留问题/L0-遗留问题.md)

`.2` / `.3` 均依赖 `.1`（需先有装载入口与取数基件）。父任务 `T-L1-004` 的 `status` 由 children 派生，不手填。

## 涉及契约

[03 §2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)

## 参考

- Story（What）：[runtime](../../docs/PRD-v2-Agent/story-02-skills-runtime.md) / [studio](../../docs/PRD-v2-Agent/story-06-skill-studio.md) / [mcp](../../docs/PRD-v2-Agent/story-08-mcp-hub.md)
- 上游：[`T-L1-001.1`](T-L1-001.1-注册发现参数校验版本管理.md)（描述体种子 + 播种）· [`T-L0-005`](T-L0-005-数据源缓存BaoStock首个源落地.md)（`MarketDb.query` 取数面）· [`T-L0-005.1`](T-L0-005.1-BaoStock真实抓取器接入.md)
- 相关：[`T-L1-003.5`](T-L1-003.5-模板库与空状态.md)（官方模板引用官方 Skill；权重边界按该任务 ④ 定案）· [`T-L1-006`](T-L1-006-L1运行时装配组合根.md)（组合根把装载接进启动序列）· [`T-INT-001`](T-INT-001-M0集成关卡骨架打通冒烟.md)（GWT-2 的「官方 Pack Skill 执行器」即本任务交付）

## 备注

本阶段交付后端执行面（仓库无前端）。实现细节不写此处，留给代码 / commit / 执行日志。
