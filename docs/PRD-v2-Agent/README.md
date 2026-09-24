# 面向个性化投资决策的可配置主动服务 Agent — PRD（结构化版）

本目录是产品需求文档（PRD）的**多文件结构化版本**，为 Agent 消费优化：每个文档聚焦单一主题，可独立加载，文档之间用本地相对链接互相引用。

**内容边界**：本 PRD 只描述产品要做什么（功能、原则、验收标准、约束），**不含**技术选型、实现架构、量化数据指标（KPI/回测目标/指标口径）、发布计划与项目管理信息。产品成效度量另立《指标与度量方案》。

## 产品一句话

一个**本地优先、对话式、可自演进的 ST 股票投资决策副驾**：底层是本地 Skills + MCP Hub 能力池，中层是个人投资记忆图谱（Memory Graph），交互层是 Chat-as-OS 主入口，复杂决策走多视角中性推理（Multi-Lens Deliberation），触达层是 Ambient Delivery 主动服务，最上层是 Reflection Loop 自我演进。

## 架构总览（六层）

```
┌─────────────────────────────────────────────────────┐
│  L6 · Reflection Loop（反思演进层）  → story-09      │
├─────────────────────────────────────────────────────┤
│  L5 · Ambient Delivery（主动触达层） → story-07      │
├─────────────────────────────────────────────────────┤
│  L4 · Multi-Lens Deliberation（多视角推理）→ story-04│
├─────────────────────────────────────────────────────┤
│  L3 · Chat-as-OS（对话主入口）        → story-01     │
├─────────────────────────────────────────────────────┤
│  L2 · Memory Graph（个人投资记忆图谱）→ story-03     │
├─────────────────────────────────────────────────────┤
│  L1 · Skills Runtime + MCP Hub（能力底座）→ 02/06/08│
├─────────────────────────────────────────────────────┤
│  L0 · Local-first Foundation（本地优先基座）→ 05     │
└─────────────────────────────────────────────────────┘
```

## 文档索引

### 总览层（按顺序读一遍即建立全局认知）

| 文档 | 内容 |
|---|---|
| [01-overview.md](01-overview.md) | 产品概要、问题域、竞品空白、范围边界（不解决什么） |
| [02-personas.md](02-personas.md) | 三类用户画像（老周/小林/陈姐）、JTBD、不为谁做 |
| [03-goals.md](03-goals.md) | 产品目标、7 条设计原则、反原则（anti-metrics）、关键假设 |
| [04-value-proposition.md](04-value-proposition.md) | 对各类用户的价值主张、竞争差异化、押注 |
| [05-constraints.md](05-constraints.md) | 范围约束、不可妥协的设计约束、数据隐私合规、关键风险表 |

### 平台契约（所有 Story 共享，写任何功能前必读）

| 文档 | 内容 |
|---|---|
| [10-platform-capabilities.md](10-platform-capabilities.md) | 本地数据底座、记忆持久化、LLM 调用抽象、双通道配置、推理可追溯、中性表达约束等 9 条共享契约 |

### 功能 Story（按优先级分组，每个可独立消费）

**P0 — AI Native 骨架**

| 文档 | Story | 承载架构层 |
|---|---|---|
| [story-01-chat-as-os.md](story-01-chat-as-os.md) | Chat 对话式主入口 ⭐ | L3 |
| [story-02-skills-runtime.md](story-02-skills-runtime.md) | Skills Runtime 与官方 Skill Pack ⭐ | L1 |
| [story-03-memory-graph.md](story-03-memory-graph.md) | 个人投资记忆图谱 ⭐ | L2 |
| [story-04-multi-lens.md](story-04-multi-lens.md) | 多视角推理（中性化）⭐ | L4 |
| [story-05-local-first.md](story-05-local-first.md) | 本地优先与数据主权基座 | L0 |

**P1 — 扩展与主动**

| 文档 | Story | 承载架构层 |
|---|---|---|
| [story-06-skill-studio.md](story-06-skill-studio.md) | Skill Studio 可视化编排与自建 | L1 |
| [story-07-ambient-delivery.md](story-07-ambient-delivery.md) | Ambient 主动触达与注意力预算 | L5 |
| [story-08-mcp-hub.md](story-08-mcp-hub.md) | MCP Hub 数据源与外部工具挂载 | L1 |

**P2 — 演进与分享**

| 文档 | Story | 承载架构层 |
|---|---|---|
| [story-09-reflection-loop.md](story-09-reflection-loop.md) | Reflection Loop 反思演进闭环 ⭐ | L6 |
| [story-10-skill-sharing.md](story-10-skill-sharing.md) | 本地 Skill 分享（生态降级方案） | L1 |

> ⭐ = 承载关键假设（见 [03-goals.md](03-goals.md)），若假设证伪则产品价值主张需重新校准。

## 引用关系图

```
                  03-goals.md（设计原则，全局权威）
                  ▲   ▲   ▲
        ┌─────────┘   │   └─────────┐
   05-constraints.md  10-platform-  01/02/04（背景类）
        ▲             capabilities  ▲
        │                 ▲         │
  （所有 story 受约束与合规条款约束）（所有 story 依赖平台契约）
                              │
   story-05(L0) ◄── 被所有 story 依赖（本地优先基座）
   story-02(L1) ◄── story-01（调用）· story-06（编排）· story-08（MCP 注册）· story-10（分享）
   story-03(L2) ◄── story-01（上下文卡片）· story-04（个性化推理）· story-07（文案个性化）· story-09（训练修正）
   story-04(L4) ◄── story-01（触发入口）· story-07（结果触达）
   story-01(L3) ◄── story-07（晚间交互式对话时段）
   story-09(L6) ◄── story-02/04/07（消费执行日志、决策记录、推送反馈）
```

## Agent 加载指南

- **了解产品全貌**：按序读 01 → 05 五个总览文档
- **实现某个功能**：读对应 story 文件 → 按其中链接加载 [10-platform-capabilities.md](10-platform-capabilities.md)（共享契约）与 [05-constraints.md](05-constraints.md)（硬约束）→ 按需加载被依赖的 story
- **判断"某能力放哪里"**：先查 [10-platform-capabilities.md](10-platform-capabilities.md) 的共享契约，已有则复用，没有再归属到对应 story
- **冲突仲裁**：设计原则（[03-goals.md](03-goals.md)）> 约束（[05-constraints.md](05-constraints.md)）> story 内描述；任何 story 不得违背三条硬约束（本地优先、中性视角、可配置性优先）

## 待后续补充的上游/下游产出（占位）

- **量化指标与度量方案**：另立文档，本 PRD 不含
- **站点地图（IA 骨架）**：待生成，生成后在此目录新增 `11-sitemap.md` 并回填各 story 的"关联页面"字段
- **页面 Flow 设计资产**：待生成，生成后回填各 story 的"已生成的设计资产"字段
- **异常态系统穷举**：待跑，生成后新增 `12-edge-cases.md`，重点覆盖离线、LLM 不可用、MCP Server 崩溃、本地存储损坏、演进漂移等场景
- **Persona 用研校准**：三类 Persona 均为场景推断锚点，待用户研究验证（尤其陈姐这一隐私敏感型用户的真实存在性）
