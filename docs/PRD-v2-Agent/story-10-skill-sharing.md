---
id: story-10
title: Local Skill Sharing 本地 Skill 分享
priority: P2
layer: L1 能力底座（生态降级方案）
critical_assumption: false
persona: [小林, 老周]
depends_on: [10-platform-capabilities, story-02, story-03, story-05, story-06, story-08]
consumed_by: []
---

# Story 10 · Local Skill Sharing 本地 Skill 分享（生态降级方案）

> 本 Story 是"共享式 Agent 市场"这一原始差异化在**本地优先约束下的重新实现**：由于不做中央商店服务端，分享改走本地文件。它把 [story-02/03/06/08](story-02-skills-runtime.md) 产出的 Skill / Memory 片段 / 视角 / MCP 依赖打包成可携带文件。

## 功能描述

由于本地优先原则（见 [story-05](story-05-local-first.md)），产品**不做中央 Skill 商店服务端**。取而代之的是"本地文件分享"模式：
- **导出**：任何自建 Skill / 工作流 / 自定义视角 / Memory 公开片段都可导出为本地文件（`.stskill` / `.stflow` / `.stlens` / `.stmem` 格式）
- **导入**：用户可从任何来源（朋友发的文件、社区论坛、GitHub、网盘）导入这些文件
- **官方 Skill 索引**：产品方维护一份**只读的官方 Skill 索引**（含官方 Pack 更新 + 认证的社区 Skill 推荐），用户可浏览但下载走本地文件（不通过产品方服务器中转数据）
- **社区生态外部化**：社区分享走外部渠道（GitHub、论坛、微信群），产品只提供导入导出能力

## In Scope

- **导出格式规范**：Skill / Flow / Lens / Memory 片段各自的标准格式（含元数据、依赖声明、版本）
- **导入校验**：导入时校验格式、依赖、权限、恶意行为（如 Skill 试图读取超出声明范围的本地文件）
- **隐私分级**：导出 Memory 片段时强制过滤私有/敏感节点（分级规则见 [story-03](story-03-memory-graph.md)），只导出用户显式勾选的公开部分
- **依赖解析**：导入的 Skill 依赖其他 Skill 时，自动检查本地是否已有，缺失时提示
- **官方 Skill 索引**：只读浏览（不下载），指向外部下载地址
- **分享链接生成**：可选生成"分享卡片"（含 Skill 描述 + 校验和 + 下载指引），用户自行发布到任何渠道
- **导入来源追溯**：每个导入的 Skill 都记录来源（谁分享的、什么时候、校验和）

## Out of Scope

- 中央 Skill 商店（不做产品方托管的上传/下载/评分/评论服务）
- 付费 Skill 交易
- Skill 的自动更新订阅（用户手动重新导入）
- Skill 的社区评分（评分走外部渠道）

## 验收标准（Given / When / Then）

- Given 用户在 Studio 中创建了自建 Skill，When 点击导出，Then 生成 `.stskill` 文件（含完整定义 + 元数据 + 依赖声明 + 校验和），可复制到任何位置
- Given 用户导入他人分享的 `.stskill` 文件，When 校验，Then 显示"这个 Skill 想做什么"（权限申请 + 依赖列表 + 来源追溯）+ 用户批准后安装
- Given 导入的 Skill 依赖用户没有的其他 Skill，When 检测，Then 明确提示缺失依赖 + 提供获取途径（官方 Pack / 外部下载）
- Given 用户导出 Memory 片段，When 生成文件，Then 强制过滤私有/敏感节点 + 显示"这次导出包含以下公开信息"清单 + 用户确认后才生成
- Given 用户浏览官方 Skill 索引，When 查看，Then 显示官方 Pack 更新 + 认证社区 Skill 推荐（含描述 + 外部下载地址 + 校验和）
- Given 导入的 Skill 试图读取超出声明范围的本地文件，When 运行时，Then 拦截并明确警示"这个 Skill 行为异常"+ 提供禁用选项
- Given 用户查看某个导入 Skill 的来源，When 打开详情，Then 显示完整追溯（分享者、时间、校验和、导入历史）
- 边缘情况：导入文件格式损坏 → 明确报错 + 不安装
- 空状态：新用户未导入任何第三方 Skill → 显示"你的 Skill 库目前只有官方 Pack"+ 引导浏览索引

## 设计触点

- **屏**：Skill 库-导入导出面板、导出对话框（隐私过滤）、导入校验页、官方 Skill 索引浏览页、来源追溯页、异常行为警示对话框
- **组件**：文件格式徽章、权限申请列表、依赖树、校验和显示、追溯时间线
- **状态**：导出中/导入中/校验通过/校验失败/权限待批/已安装/已禁用

## 关联文档

- 平台契约：[10-platform-capabilities.md](10-platform-capabilities.md)（契约 2 本地持久化、契约 9 中性表达——命名校验）
- 依赖：[story-02](story-02-skills-runtime.md)（分享对象）· [story-03](story-03-memory-graph.md)（隐私分级）· [story-05](story-05-local-first.md)（本地文件与主权）· [story-06](story-06-skill-studio.md)（自建 Skill 来源）· [story-08](story-08-mcp-hub.md)（含 MCP 依赖的追溯）
- 关联页面（Sitemap 占位）：待生成后回填
- 设计资产：待生成
