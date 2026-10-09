---
id: T-UI-015
parent: null
title: 能力区：Skill 库、MCP Hub 与 Studio 附加面
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 L1 能力底座](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P1
milestone: M6
depends_on: [T-UI-011]
status: todo
decisions: [D-103]
verify:
---

# T-UI-015 · 能力区：Skill 库、MCP Hub 与 Studio 附加面

> **M6 七面功能叶之一**（[D-103](../决策日志.md)）。补上 [11-sitemap §2 能力](../../docs/PRD-v2-Agent/11-sitemap.md)——四 story 归并的能力区是 IA 里**最大的一区**（22 条屏），现前端只覆盖 Studio 主画布（[`T-UI-005`](done/M5/T-UI-005-Studio画布页.md)）与 ECO 四页（导入校验 / 官方索引 / 来源追溯 / 越界警示）。

## 目标

把 [11-sitemap §2 能力](../../docs/PRD-v2-Agent/11-sitemap.md) 声明的屏做成可用界面，**只补未实现的**：

- **story-02**：Skill 库浏览页 / Skill 详情页 / Skill 参数配置面板 / Skill 执行日志页
- **story-06**：Studio 的 Skill 参数面板 / 工作流属性面板 / 调试控制台 / 版本历史页 / 模板库 / 导入导出对话框（**主画布已交付**）
- **story-08**：MCP Hub 主页 / Server 列表 / Server 详情页 / 权限确认对话框 / tool schema 浏览器
- **story-10**：**导入校验页 / 官方索引 / 来源追溯 / 越界警示已交付**（[`T-UI-004.4`](done/M4/T-UI-004.4-生态面表现层入口.md)）；本叶不重造

**本批的边界**（不越界）：

- **不重造已交付页**——Studio 主画布（[`T-UI-005`](done/M5/T-UI-005-Studio画布页.md)）与 ECO 四页**已在**，本叶只补其余屏。
- **权限确认对话框 / 审批面复用既有型**——`permission_approval_card` / `setting_panel` 已是已实现型，本叶只做宿主页与取数，**不新造型**（确需新形态归 [`T-UI-010`](T-UI-010-契约先行组件型补齐与页面集口径.md)）。
- **能力安装 / 导入审批面归 L3 对话面**（`T-L3-006` 已交付），本叶只做库浏览 / 详情 / 配置 / 日志 / Hub 这些**独立页**。

## 验收标准（Given-When-Then）

- GWT-1：Given [11-sitemap §2 能力](../../docs/PRD-v2-Agent/11-sitemap.md) 的屏清单，When 对位现状，Then 除**已交付**（Studio 主画布 / ECO 四页 / L3 审批面）外，每条屏有可见载体
- GWT-2：Given Skill 库浏览与详情，When 打开，Then 出 Skill 清单、单 Skill 详情、参数配置面板、执行日志页；参数改值经 `setting_panel`（**带动作**）
- GWT-3：Given MCP Hub，When 打开，Then 出 Hub 主页 / Server 列表 / Server 详情 / 权限确认对话框 / tool schema 浏览器；权限逐条批准、tool 映射为 Skill 可见（[§3.7](../../docs/PRD-v2-Agent/11-sitemap.md)）
- GWT-4：Given 数据面非 `ok`，When 呈现，Then 按六态如实（能力池空 → `empty` + 原因），**不静默留空**
- GWT-5：Given 文案，When 核对，Then 按[契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md)撰写，**无拟人化**（Skill 无人名 / 无第一人称）
- GWT-6：Given 真组合根 + 真回环服务，When 在 Edge 走查，Then 各屏可用、控制台无新增报错

## 接口面

<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留

<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提

<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约

- [03 L1 能力底座 Skills/MCP](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)——Skill 目录 / 执行面 / MCP Hub 与 tool 映射
- [01 §12 UI 描述](../../docs/技术架构-v2/01-平台共享契约.md) · [§5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md) 六态 · [§7 配置注册表](../../docs/技术架构-v2/01-平台共享契约.md)
- [11-sitemap §2 能力](../../docs/PRD-v2-Agent/11-sitemap.md) · [§3.4 对话即配置](../../docs/PRD-v2-Agent/11-sitemap.md) · [§3.7 能力挂载与导入](../../docs/PRD-v2-Agent/11-sitemap.md)
- [契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md)

## 参考

- Story（What）：[story-02 skills-runtime](../../docs/PRD-v2-Agent/story-02-skills-runtime.md) · [story-06 studio](../../docs/PRD-v2-Agent/story-06-skill-studio.md) · [story-08 mcp-hub](../../docs/PRD-v2-Agent/story-08-mcp-hub.md) · [story-10 skill-sharing](../../docs/PRD-v2-Agent/story-10-skill-sharing.md)
- 决策：[D-103](../决策日志.md)
- 上游：[`T-UI-011`](T-UI-011-底座与骨架多面端口注入与全IA页面登记.md)（L1 Skill / MCP 面端口）
- 已交付：[`T-UI-005`](done/M5/T-UI-005-Studio画布页.md)（主画布）· [`T-UI-004.4`](done/M4/T-UI-004.4-生态面表现层入口.md)（ECO 四页）· `T-L3-006`（安装审批面）
- 关卡：[`T-INT-007`](T-INT-007-M6集成关卡界面完备.md)

## 备注

范围套件同 [`T-UI-012`](T-UI-012-Chat主界面对话流与生成式UI宿主.md)。**拆分触发**：本叶跨 story-02 / 06 / 08 三个契约面、屏数约 15 条，**必然触发**——④ 对齐时按「Skill 库与详情 / MCP Hub / Studio 附加面」转分组节点拆三叶。
