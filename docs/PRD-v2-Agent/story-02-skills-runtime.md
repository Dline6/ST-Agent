---
id: story-02
title: Skills Runtime 与官方 Skill Pack
priority: P0
layer: L1 能力底座
critical_assumption: true
persona: [老周, 陈姐, 小林]
depends_on: [10-platform-capabilities, story-05]
consumed_by: [story-01, story-04, story-06, story-07, story-08, story-10]
---

# Story 2 · Skills Runtime 与官方 Skill Pack ⭐

> ⭐ 承载关键假设：Skill 化的能力原子能被用户有效调用与组合。
> 本 Story 是产品的**能力底座**：所有对外呈现的功能，本质都是 Skill 的运行。它向上被 [story-01 Chat](story-01-chat-as-os.md) 调用、被 [story-04](story-04-multi-lens.md) 作为视角素材、被 [story-06 Studio](story-06-skill-studio.md) 编排。
> 数据协同复用、执行可追溯、结构化输出契约见 [10-platform-capabilities.md](10-platform-capabilities.md)。

## 功能描述

产品底层是一个 **Skill Runtime**，每个 Skill 是一个原子能力（如"读深交所公告""计算摘帽概率""识别龙虎榜游资席位""回测均值回归"），带标准化描述、输入输出契约、参数、依赖。官方发布一套完整 Skill Pack，覆盖既有全部功能。用户通过 Chat（[story-01](story-01-chat-as-os.md)）或 Skill Studio（[story-06](story-06-skill-studio.md)）调用；Skill 参数化，用户可精调每个参数。

**覆盖的既有需求**：原 4 类被动 Agent（名单维护、板块热点、散户情绪、机构筛选）与 6 类主动 Agent（个股监控、数据聚合、风险预警、机会挖掘、组合压测、策略设计）的全部能力，在此被拆解重组成可组合的原子 Skill。

## In Scope

**官方 Skill Pack · 公共认知 Bundle**（承接原被动 Agent 群）：
- `st-list-sync`：同步交易所最新 ST/\*ST 名单，识别新增/移除
- `delisting-risk-scan`：识别退市高危信号，输出可解释的触发原因
- `unhat-eligibility-check`：动态评估摘帽条件满足情况
- `sector-heatmap`：行业属性标记 + 板块 ST 热力图 + 风险评级
- `sentiment-flow-analysis`：情绪资金流向分析（原"散户模拟"，去拟人化）
- `fundamental-screening`：基本面筛选与安全边际评估（原"机构模拟"，去拟人化）

**官方 Skill Pack · 主动服务 Bundle**（承接原主动 Agent）：
- `stock-watch`：多标的盯盘，触发条件可配（关键词/财报日/异动/换手率）
- `data-aggregate`：数据聚合，输出表格卡/趋势图/简报
- `risk-alert`：风险预警，维度可扩展（退市倒计时/流动性枯竭/…）
- `opportunity-mine`：机会挖掘，风格 + 板块偏好过滤
- `portfolio-stress-test`：组合压力测试，情景规则化
- `strategy-design`：策略设计与回测，四步链路（选股→信号→策略→回测）

**Runtime 机制**：
- **Skill 元数据规范**：每个 Skill 必须有名称、描述、输入 schema、输出 schema、参数、依赖的其他 Skill、版本、来源（官方/用户/第三方 MCP）
- **Skill 参数化配置**：所有参数暴露给用户，通过 Chat（自然语言）或 Studio（表单）修改（双通道契约见 [10-platform-capabilities.md](10-platform-capabilities.md)）
- **Skill 版本化**：官方 Pack 可更新，用户可选择"跟随最新"或"锁定版本"
- **Skill 依赖管理**：Skill 之间依赖自动解析，冲突提示
- **Skill 执行日志**：每次调用留痕（输入、输出、耗时、错误），供 [story-09 反思](story-09-reflection-loop.md) 消费

## Out of Scope

- 中央 Skill 商店服务端（本地优先原则，见 [story-10](story-10-skill-sharing.md)）
- 付费 Skill（全部免费）
- Skill 自动更新（用户手动确认）
- 代码级新原子 Skill 开发（需走 MCP Hub，见 [story-08](story-08-mcp-hub.md)）

## 验收标准（Given / When / Then）

- Given 用户第一次安装产品，When 首次启动，Then 官方 Skill Pack（约 12 个 Skill）自动可用，无需额外下载
- Given 用户在 Chat 中说"帮我做摘帽可能性分析"，When 副驾调度，Then 自动匹配 `unhat-eligibility-check` Skill，展示参数确认卡（用户可调整或直接用默认值）
- Given 用户想修改 `stock-watch` 的推送频率参数，When 在 Studio 中调整，Then 参数立即生效，且下次 Chat 触发时用新参数
- Given 一个 Skill 依赖另一个 Skill 的输出，When 上游 Skill 失败，Then 下游 Skill 明确报错并标注"依赖失败"，不静默返回错误结果
- Given 官方发布 Skill Pack 更新，When 用户查看更新，Then 显示变更日志 + 影响范围，用户可选择更新/跳过/锁定旧版
- Given 一次 Skill 执行，When 用户在 Chat 中点开推理链，Then 可看到该 Skill 的输入快照、输出、耗时、依赖链
- 边缘情况：Skill 参数超出合理范围 → 校验拦截 + 说明理由，不允许保存
- 边缘情况：数据源不可用 → Skill 明确返回"数据不可用"状态，而非编造结果
- 空状态：某 Skill 无输出（如今日无高危信号）→ 返回结构化的"空结果 + 原因说明"

## 设计触点

- **屏**：Skill 库浏览页、Skill 详情页、Skill 参数配置面板、Skill 执行日志页
- **组件**：Skill 卡片、参数表单、依赖关系图、版本选择器、执行日志时间线
- **状态**：已安装 / 待更新 / 执行中 / 成功 / 失败 / 依赖不满足

## 关联文档

- 平台契约：[10-platform-capabilities.md](10-platform-capabilities.md)（契约 1 数据底座、契约 4 复用、契约 5 结构化输出、契约 7 可追溯）
- 依赖：[story-05 本地优先](story-05-local-first.md)（Skill 与数据本地存储）
- 被依赖：[story-01](story-01-chat-as-os.md)（调用）· [story-04](story-04-multi-lens.md)（视角素材）· [story-06](story-06-skill-studio.md)（编排对象）· [story-08](story-08-mcp-hub.md)（MCP tool 注册为 Skill）· [story-10](story-10-skill-sharing.md)（分享对象）
- 关联页面（Sitemap 占位）：待生成后回填
- 设计资产：待生成
