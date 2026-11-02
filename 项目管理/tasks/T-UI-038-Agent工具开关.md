---
id: T-UI-038
parent: null
title: Agent 工具开关
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-003, T-UI-018.1, T-UI-036]
status: done
decisions: [D-162]
verify: 新增 `agent-tools/<base>` 族（缺省启用、关闭集合落 disabled.json、留痕 agent-tool-change/）与 `AgentToolFamily`；设置-运行期授权页按 8 个功能域折叠列出 35 条工具，逐条开关；关闭即从循环可见面移除并在 `SkillRunner` 统一入口拒绝（Skill 库 / 工作流 / 试跑同口径），损坏 fail-closed。定向 361 passed；范围 2699 passed / 2 例本机环境性 test_shell ERROR；verify_docs --strict PASS；界面走查关闭→2/3→刷新保持→恢复。见执行日志 [T-UI-038]
---

# T-UI-038 · Agent 工具开关

## 目标

在设置-运行期授权页按功能分组、可折叠列出全部已注册工具，逐条勾选「允许 Agent 使用」。
取消勾选的工具从自主查证循环的模型可见目录中移除，并在 L1 统一执行入口拒绝其执行——
故 Skill 库手动执行与工作流节点同样受影响。开关落 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md)
的布尔条目 `agent-tools/<base>`（缺省启用），走统一落值面并留痕、可回滚。

## 验收标准（Given-When-Then）

- **GWT-1 · 列表与分组**：Given 打开设置-运行期授权页，When 取数，Then 页面按功能域折叠列出
  全部已注册工具（内置 / 官方 / 自建 / MCP 映射），每条显示工具标识、说明与当前开关；未落值一律显示为启用。
- **GWT-2 · 落值与留痕**：Given 取消勾选某工具，When 应用，Then `agent-tools/<base>` 为 `false`
  并产生 `agent-tool-change/` 留痕；重新勾选即恢复；刷新页面状态保持。
- **GWT-3 · 循环可见面**：Given 某工具已关闭，When 发起一次 Agent 运行或续跑，Then 交给模型的
  工具目录不含该工具；重新启用后下一次运行即恢复。
- **GWT-4 · 手动执行拦截**：Given 某工具已关闭，When 经 Skill 库直接执行、工作流试跑或工作流正式执行
  调用它，Then L1 执行入口回 `unavailable` 并点名该工具，不调用执行器。
- **GWT-5 · 损坏 fail-closed**：Given `agent-tools/disabled.json` 损坏，When 取数或执行，
  Then 设置页显示错误，Agent 运行拿到显式原因且不静默放行，L1 执行入口拒绝执行。

## 接口面

**输入**

- `SkillRegistry.tool_catalog()`（[`l1/skills/registry.py`](../../src/st_agent/l1/skills/registry.py)）——
  工具目录的唯一来源（[01 §2](../../docs/技术架构-v2/01-平台共享契约.md)）。
- `Store` 的 `config` 分区与 `execution_log` 分区（既有句柄）。
- `SkillRunner._execute`（[`l1/runner/runner.py`](../../src/st_agent/l1/runner/runner.py)）——
  Skill 库、工作流、试跑与 Agent 循环共用的执行入口。
- `_AgentInvestigator`（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）——
  组装循环工具目录的两处调用点。
- `SettingsFacade`（[`composition/facades/settings.py`](../../src/st_agent/composition/facades/settings.py)）与
  设置读面（[`ui/surfaces/settings/`](../../src/st_agent/ui/surfaces/settings/)）。

**输出**

- 新增 L1 模块 [`l1/skills/tool_selection.py`](../../src/st_agent/l1/skills/tool_selection.py)：
  `agent-tools/<base>` 的读写、校验与 `get_disabled_tools(store)`；落盘 `config/agent-tools/disabled.json`
  （只保留被关闭的 base）。
- 新增 L1 配置族 `AgentToolFamily`（`config_prefix = "agent-tools/"`，留痕 `agent-tool-change/`），
  在 `_build_config_registry` 注册。
- `SkillRunner._execute` 在描述体取到后、执行器调用前增加关闭检查（`unavailable` + 点名）。
- `_AgentInvestigator` 两处 `tools=` 改为经关闭集合过滤后的目录；读配置失败时显式回 `unavailable`。
- 设置读面新增 `tools` 段（`table` 部件，`values.groups` 供页面分组渲染），并入现有
  `GET /api/settings/agent-authorization`；写面复用 `POST /api/settings/apply`。
- 前端 `renderAgentAuthorization` 在预算段之后渲染可折叠分组（复用 `.settings-category`）。

## 可关闭的遗留

无。

## 假设与前提

- **A1 · 全部工具可关，含循环控制类**（2026-11-02 负责人 ④ 选定）：计划更新 / 提问 / 子 Agent 也在列表内
  且允许关闭。**若错的影响**：需把三条标为只读展示、不提供开关。**验证方式**：GWT-1 / GWT-2。
- **A2 · 缺省全开**：未落值的工具视为启用，出厂行为不变。**验证方式**：GWT-1 / GWT-3。
- **A3 · 影响面覆盖手动执行**（2026-11-02 负责人 ④ 选定）：在 L1 统一执行入口拦截，故 Skill 库、
  工作流与试跑一并向。**若错的影响**：若只想约束 Agent，改为仅过滤循环工具目录并撤掉执行入口检查。
  **验证方式**：GWT-4。
- **A4 · 配置损坏 fail-closed**：读不出关闭集合时不得静默当作「全部启用」。**验证方式**：GWT-5。
- **A5 · 分组是展示口径**：按功能域分组、未收录的落「其他能力」，不进入能力登记，也不改
  [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 的「工具目录＝注册面投影」这一事实。
  **验证方式**：GWT-1。

## 涉及契约

- [01 §2 工具暴露面](../../docs/技术架构-v2/01-平台共享契约.md)（投影 + 用户允许集）
- [01 §7 配置条目与统一落值面](../../docs/技术架构-v2/01-平台共享契约.md)
- [05 §10 自主查证循环](../../docs/技术架构-v2/05-L3-对话主入口.md)
- [11-sitemap §2 设置-运行期授权页](../../docs/PRD-v2-Agent/11-sitemap.md)
