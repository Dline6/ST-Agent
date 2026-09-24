---
id: story-06
title: Skill Studio 可视化编排与用户自建 Skill
priority: P1
layer: L1 能力底座（创作侧）
critical_assumption: false
persona: [小林, 老周]
depends_on: [10-platform-capabilities, story-02, story-04, story-05]
consumed_by: [story-10]
---

# Story 6 · Skill Studio 可视化编排与用户自建 Skill

> 本 Story 是 [story-02 Skills Runtime](story-02-skills-runtime.md) 的**创作侧**：story-02 定义能力如何运行，story-06 定义用户如何组合与新建能力。服务进阶配置者小林，落地[设计原则 7（低门槛可成长）](03-goals.md)。
> 双通道配置（对话 + 可视化）契约见 [10-platform-capabilities.md](10-platform-capabilities.md)。

## 功能描述

Skill Studio 是一个可视化编排环境。用户可以：
- **参数微调**：修改任何 Skill 的参数（对应 [story-02](story-02-skills-runtime.md) 的参数体系）
- **工作流编排**：把多个 Skill 串成 DAG（如"筛选 → 回测 → 推送"）
- **对话式生成**：说一句话"我想要一个每天早晨给我 ST 板块简报的流程"→ 副驾自动生成工作流草稿 → 用户在 Studio 中可视化微调
- **自建 Skill**：通过组合已有 Skill + 定义输入输出契约，创建新的复合 Skill（本质是"命名的工作流"）
- **调试与测试**：任何工作流都可"试跑"，看每一步的输入输出
- **版本化**：自建 Skill 可保存多个版本，可回滚

**覆盖的既有需求**：原"策略设计四步链路（历史分析→成因选股→信号设计→形成策略）"的自主编排、原"Agent 工作室/工作流编排"能力。

## In Scope

- **可视化画布**：节点（Skill）+ 连线（数据流）+ 分组（子流程）
- **对话式生成 + 可视化编辑双通道**（对应设计原则 3）
- **自建 Skill 保存**：命名 + 描述 + 参数暴露 + 图标；命名遵循中性约束（见 [story-04 核心约束](story-04-multi-lens.md)，不额外重复定义）
- **调试模式**：单步执行 + 输入注入 + 输出快照
- **试跑历史**：每次试跑留存记录，可对比
- **版本管理**：多版本共存、diff 对比、一键回滚
- **导入导出**：自建 Skill 可导出为本地文件（供 [story-10](story-10-skill-sharing.md) 分享）
- **模板库**：官方预置一批工作流模板（如"每日 ST 简报""退市风险扫描""策略回测流水线"），用户可 fork 后修改

## Out of Scope

- 代码级 Skill 开发（写新的原子能力需走 MCP Hub，见 [story-08](story-08-mcp-hub.md)）
- 协同编辑（本地优先，不做）
- 工作流的自动优化（由 [story-09 反思闭环](story-09-reflection-loop.md) 承接）

## 验收标准（Given / When / Then）

- Given 用户想微调 `stock-watch` Skill 参数，When 在 Studio 中打开，Then 显示所有参数的表单 + 说明 + 默认值 + 修改历史
- Given 用户在 Chat 中说"我想要一个每天早晨给我 ST 板块简报的流程"，When 副驾生成，Then Studio 中自动出现工作流草稿（含节点、连线、参数），用户可接受/微调/拒绝
- Given 用户在画布上拖拽 Skill 组合，When 连线，Then 自动校验输入输出契约匹配，不匹配时提示"这里需要一个 XX 类型的输入"
- Given 用户保存自建 Skill，When 命名，Then 校验是否与已有 Skill 重名（命名中性校验复用 [story-04](story-04-multi-lens.md) 规则）
- Given 用户点击"试跑"，When 执行，Then 单步显示每个节点的输入输出，可暂停/继续/中止
- Given 用户导出自建 Skill，When 生成文件，Then 文件含完整定义（不含 Memory 私有数据）+ 可在另一台设备导入使用
- Given 工作流形成循环依赖，When 保存时，Then 检测并阻止成环
- 边缘情况：Skill 版本升级导致契约变化 → 使用该 Skill 的工作流自动标"待检查"，用户手动确认后升级
- 空状态：新用户未创建任何工作流 → 显示模板库 + "用一句话描述你想要的工作流"引导入口

## 设计触点

- **屏**：Studio 主画布、Skill 参数面板、工作流属性面板、调试控制台、版本历史页、模板库、导入导出对话框
- **组件**：节点、连线、分组框、参数表单、调试步进器、版本 diff 视图
- **状态**：编辑中/试跑中/保存成功/校验失败/循环依赖

## 关联文档

- 平台契约：[10-platform-capabilities.md](10-platform-capabilities.md)（契约 4 数据协同复用、契约 6 双通道配置）
- 依赖：[story-02](story-02-skills-runtime.md)（被编排的 Skill）· [story-04](story-04-multi-lens.md)（命名中性校验规则、策略评判）· [story-05](story-05-local-first.md)（本地存储）
- 被依赖：[story-01](story-01-chat-as-os.md)（对话生成草稿的执行环境）· [story-10](story-10-skill-sharing.md)（自建 Skill 的分享来源）
- 关联页面（Sitemap 占位）：待生成后回填
- 设计资产：待生成
