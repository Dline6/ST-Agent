---
id: story-01
title: Chat-as-OS 对话式主入口
priority: P0
layer: L3 交互层
critical_assumption: true
persona: [老周, 小林, 陈姐]
depends_on: [10-platform-capabilities, story-02, story-03, story-05]
consumed_by: [story-04, story-06, story-07]
---

# Story 1 · Chat-as-OS 对话式主入口 ⭐

> ⭐ 承载关键假设（见 [03-goals.md](03-goals.md)）：用户愿意用对话表达偏好并参与共同演化。
> 本 Story 是产品的**唯一主入口**——所有其他 Story 的能力原则上都应可从对话中触达。
> 共享契约（双通道配置、推理链、中性表达）见 [10-platform-capabilities.md](10-platform-capabilities.md)，此处不重复定义。

## 功能描述

产品首页 = 一个 Chat 输入框 + 一张"当前上下文卡片"（持仓摘要、关注池、未读告警数、近期热点摘要）。用户以自然语言表达任何意图，副驾理解后：或立即回答、或澄清追问、或调用 Skill 执行任务、或按需生成一块 Generative UI（看板/图表/回测报告）。产品的全部功能（监控、聚合、预警、挖掘、压测、策略）都在这个对话中被触发，**没有传统菜单式入口层级**。

**覆盖的既有需求**：原"个股监控（对话创建）、数据聚合（对话按需出报表）、策略设计（对话式设计链路）"等交互入口。

## In Scope

- **主对话窗**：多轮、有状态、可回溯、可分支
- **意图理解与澄清协议**：模糊意图 → 副驾主动追问（不超过 3 个关键问题）→ 收敛到明确任务；澄清问题必须允许用户"跳过"（用默认值继续）
- **Generative UI**：对话中按需生成看板、图表、卡片、回测报告；生成物可"钉"到工作区变成持久组件
- **推理链可视化**：每条回答可点开"我是怎么想的"，展示：调用了哪些 Skill → 读取了 Memory 的哪些片段 → 汇总了哪些视角 → 结论
- **对话即配置**：用户一句话（如"帮我盯 XX，只要有 YY 就告诉我"）→ 副驾自动生成 Skill 配置草稿 → 用户可接受/微调/拒绝（对接 [story-02](story-02-skills-runtime.md) 的 Skill 参数体系）
- **上下文卡片**：显示 [story-03 Memory Graph](story-03-memory-graph.md) 的关键切片（当前 thesis、风险偏好、近期决策），可点击进入完整图谱
- **对话历史本地存储**：完全离线可查、可搜索、可导出为 Markdown（存储契约见 [story-05](story-05-local-first.md)）
- **快捷指令**：`/` 触发的常用命令（/盯盘 /回测 /压测 /今日看板 /反思），降低学习成本

## Out of Scope

- 语音输入（后续候选）
- 多用户共享对话（本地优先原则，不做）
- 对话中直接下单（合规红线，永不做）

## 验收标准（Given / When / Then）

- Given 用户第一次打开产品，When 首页加载，Then 显示 Chat 输入框 + 上下文卡片 + 引导语（"告诉我你在关注哪些 ST，我来帮你..."），不显示传统菜单
- Given 用户输入"帮我盯 \*ST XX，只要有退市风险公告立刻告诉我"，When 副驾处理，Then 追问关键参数（"退市风险"具体包含哪些情形？推送渠道？推送时段？），追问不超过 3 个问题
- Given 用户回答澄清问题，When 意图收敛，Then 自动生成对应 Skill 配置草稿并显示"我理解到 3 件事：①... ②... ③...，对吗？"，用户确认后 Skill 上线
- Given 用户问"今天全市场 ST 状况"，When 副驾响应，Then 即时生成一块可交互看板（Generative UI），含关键指标 + 热力图 + 高危名单，可"钉"到工作区
- Given 副驾给出任何结论，When 用户点击"推理链"按钮，Then 展开完整推理过程（数据来源、Skill 调用序列、视角汇总、耗时），每一环可点开细看
- Given 用户对话历史，When 断网重启产品，Then 历史完整可读、可搜索
- Given 用户意图过于模糊，When 副驾无法确定方向，Then 提供"我不太确定你想要什么，这里有 3 个可能的方向：..."而非强行猜测
- 边缘情况：LLM 调用失败 → 明确告知"云端 LLM 暂不可用，切换到本地推理模式（能力受限）"或"请稍后重试"，不静默失败
- 空状态：新用户无 Memory 时 → 上下文卡片显示"你还没告诉我任何偏好，我们先聊两句？"引导 Onboarding

## 设计触点

- **屏**：Chat 主界面、上下文卡片、Generative UI 看板、推理链展开面板、快捷指令菜单、澄清追问对话框
- **组件**：对话气泡、意图确认卡、Generative UI 容器、"钉"按钮、推理链时间线、Skill 配置草稿卡
- **状态**：正常 / 思考中 / 追问中 / 执行中 / 失败 / 离线降级

## 关联文档

- 平台契约：[10-platform-capabilities.md](10-platform-capabilities.md)（契约 6 双通道、契约 7 可追溯）
- 依赖：[story-02 Skills Runtime](story-02-skills-runtime.md)（被调度的能力）· [story-03 Memory Graph](story-03-memory-graph.md)（上下文卡片数据源）· [story-05 本地优先](story-05-local-first.md)（历史存储）
- 被依赖：[story-04](story-04-multi-lens.md)（Deliberation 触发入口）· [story-06](story-06-skill-studio.md)（对话生成工作流草稿）· [story-07](story-07-ambient-delivery.md)（晚间交互式对话时段）
- 关联页面（Sitemap 占位）：待生成后回填
- 设计资产：待生成
