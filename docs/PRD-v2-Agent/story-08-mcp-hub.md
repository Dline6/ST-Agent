---
id: story-08
title: MCP Hub 数据源与外部工具挂载
priority: P1
layer: L1 能力底座（开放接口）
critical_assumption: false
persona: [小林, 陈姐]
depends_on: [10-platform-capabilities, story-02, story-05]
consumed_by: [story-10]
---

# Story 8 · MCP Hub 数据源与外部工具挂载

> 本 Story 是产品的**开放接口**：让进阶用户通过 MCP 协议挂载自己的数据源或第三方工具，扩展能力边界，落地[设计原则 7（低门槛可成长）](03-goals.md)的进阶侧。挂载的 tool 最终注册为 [story-02](story-02-skills-runtime.md) 的 Skill。

## 功能描述

MCP Hub 是一个本地 MCP Client，允许用户挂载任意 MCP Server（本地进程或远程 URL）：
- **官方 MCP Server**：产品自带一批（交易所公告、行情、龙虎榜、股东数据、舆情）
- **用户自建 MCP Server**：小林可以把自己写的爬虫包装为 MCP Server 挂载
- **第三方 MCP Server**：社区已有的 MCP Server（如 Notion、Google Sheets、私有 API）可直接挂载
- **挂载后自动成为 Skill**：任何 MCP Server 提供的 tool 自动注册为 Skill（[story-02](story-02-skills-runtime.md)），可被 Chat / Studio / Deliberation 调用

## In Scope

- **MCP Client 内置**：产品内置 MCP Client，支持 stdio（本地进程）和 HTTP/SSE（远程）两种传输
- **MCP Server 注册**：用户可添加/删除/启用/禁用 MCP Server
- **权限控制**：每个 MCP Server 明确显示它请求的权限（读文件/网络/执行命令），用户逐个批准
- **Skill 自动映射**：MCP Server 的 tool 自动映射为 Skill，元数据自动填充
- **本地运行优先**：默认只允许本地 stdio MCP Server；远程 HTTP MCP Server 需要用户显式开启（对应[设计原则 1](03-goals.md)）
- **网络活动透明化**：MCP Server 的所有网络请求纳入 [story-05](story-05-local-first.md) 的"网络活动"面板
- **调试工具**：MCP Server 挂载后可"测试连接"+ 查看所有 tool 的输入输出 schema

## Out of Scope

- MCP Server 的中央注册中心（本地优先，用户自己找 Server）
- MCP Server 的付费购买
- MCP Server 的自动发现（用户手动添加）

## 验收标准（Given / When / Then）

- Given 用户想挂载自己的 MCP Server，When 添加，Then 显示配置表单（命令/URL + 权限申请 + 环境变量），保存后自动连接测试
- Given MCP Server 连接成功，When 加载 tool 列表，Then 每个 tool 自动注册为 Skill（元数据从 MCP schema 派生），在 Skill 库可见
- Given MCP Server 请求敏感权限（如执行命令），When 首次调用，Then 弹出权限确认对话框 + 明确说明"这个 Server 想做什么"，用户逐个批准
- Given 用户禁用某个 MCP Server，When 生效，Then 该 Server 的所有 Skill 立即从可用列表移除，进行中的调用中止
- Given MCP Server 是远程 HTTP，When 用户添加，Then 明确警示"这是远程 Server，你的数据会离开本机"+ 需要用户显式确认
- Given 用户查看网络活动，When 打开面板，Then MCP Server 的所有请求都在列表中（Server 名 + 目的 + 数据量）
- 边缘情况：MCP Server 崩溃 → 明确提示 + 自动重连（可配次数）+ 降级到官方数据源
- 边缘情况：MCP Server 提供的 tool 契约变化 → 提示用户重新映射

## 设计触点

- **屏**：MCP Hub 主页、Server 列表、Server 详情页、权限确认对话框、tool schema 浏览器、网络活动面板
- **组件**：Server 卡片、权限徽章、tool 列表、schema 树、连接状态指示器
- **状态**：已连接/断开/重连中/权限待批/禁用

## 关联文档

- 平台契约：[10-platform-capabilities.md](10-platform-capabilities.md)（契约 1 数据底座）
- 依赖：[story-02](story-02-skills-runtime.md)（tool 注册为 Skill）· [story-05](story-05-local-first.md)（本地优先、网络透明化）
- 被依赖：[story-10](story-10-skill-sharing.md)（分享含 MCP 依赖的 Skill 时需追溯 Server 来源）
- 关联页面（Sitemap 占位）：待生成后回填
- 设计资产：待生成
