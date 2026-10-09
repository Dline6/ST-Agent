---
id: T-UI-012
parent: null
title: Chat 主界面：对话流与生成式 UI 宿主
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §6](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M6
depends_on: [T-UI-011]
status: todo
decisions: [D-103]
verify:
---

# T-UI-012 · Chat 主界面：对话流与生成式 UI 宿主

> **M6 七面功能叶之一**（[D-103](../决策日志.md)）。补上 IA 的**唯一主入口**——[D-103](../决策日志.md) ① 已核实：`POST /api/chat` 后端早已通、集成用例在用，但**前端无一文件消费它**，首页 `/` 在 [`pages.js`](../../src/st_agent/ui/web/js/pages.js) 里**没有 `render`**；且生成式 UI 的已实现渲染件（`context_card` / `trace_timeline` / `config_draft_card` / `permission_approval_card` / `divergence_map`）经对话面下发，**在 UI 里没有宿主**。本叶立起这个宿主。

## 目标

把 [11-sitemap §2 Chat 主界面](../../docs/PRD-v2-Agent/11-sitemap.md) 声明的屏做成可用界面：

- **Chat 主界面（首页）**：对话流 + 输入框 + 首开引导语，**不显示传统菜单**（[story-01](../../docs/PRD-v2-Agent/story-01-chat-as-os.md)）
- **上下文卡片**（常驻首页）：六段——持仓摘要 / 关注池 / 未读告警 / 近期热点 / 当前 thesis / 风险偏好；非 ok 段以「不可用 + 原因」呈现，**不静默留空**；无记忆时空态引导 Onboarding
- **澄清追问对话框**：关键参数追问，**不超过 3 个**，每问可跳过（用默认值继续）
- **意图确认卡**：澄清收敛后列出理解条目供确认
- **快捷指令菜单**：`/` 触发（/盯盘 /回测 /压测 /今日看板 /反思）
- **Generative UI 容器**：对话内按需生成看板 / 图表 / 卡片 / 回测报告，含「钉」入口；**只解析描述不解析代码**，未登记型走显式降级

**本批的边界**（不越界）：

- **工作区归 [`T-UI-013`](T-UI-013-工作区钉住的持久组件.md)**——本叶只出「钉」入口，钉住后的持久组件面归该叶。
- **推理链展开面板归 [`T-UI-016`](T-UI-016-推理区触发进度分歧图与视角详情.md)**——§3.3 的 `trace_timeline` 渲染件跨页复用，本叶只留入口。
- **不新建渲染路径**——生成式 UI 走 [05 §6](../../docs/技术架构-v2/05-L3-对话主入口.md) 既有注册表；新组件型归 [`T-UI-010`](T-UI-010-契约先行组件型补齐与页面集口径.md)。

## 验收标准（Given-When-Then）

- GWT-1：Given 首页 `/`，When 打开，Then 出对话流 + 输入框 + 首开引导语（**无**传统菜单），输入框提交走 `POST /api/chat`（[D-103](../决策日志.md) ① 的缺口关闭）
- GWT-2：Given 一条对话响应，When 渲染，Then 其中的 `UiDescription` 交**生成式 UI 容器宿主**按注册表渲染（五型及以上），未登记 / 未实现型走**显式降级占位**（虚线边框 + 中性说明 + 可折叠原始描述），**不用**错误红
- GWT-3：Given 首页上下文卡片，When 任一段非 `ok`，Then 以 `--delayed` 呈现 + 中性原因（**不静默留空**）；无记忆时出 Onboarding 空态引导
- GWT-4：Given 一句含糊意图，When 触发澄清，Then 追问**不超过 3 问**、每问可跳过；收敛后出意图确认卡列出理解条目
- GWT-5：Given 文案（引导语 / 空状态 / 确认卡 / 按钮），When 逐条核对，Then 按[契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md)撰写，**无拟人化**（人名 / 性格 / 第一人称 / 情感 / 对话体）
- GWT-6：Given 真组合根 + 真回环服务，When 在 Edge（与目标 WebView2 同引擎）走查，Then 对话流 → 生成式 UI → 确认卡 → 六态呈现全程可用，控制台无新增报错

## 接口面

<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留

<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提

<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约

- [05 §6–§7 Generative UI](../../docs/技术架构-v2/05-L3-对话主入口.md)——渲染器维护类型注册表、只解析描述不解析代码
- [05 §4 六态](../../docs/技术架构-v2/05-L3-对话主入口.md) · [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md) · [§12 UI 描述](../../docs/技术架构-v2/01-平台共享契约.md)
- [11-sitemap §2 Chat 主界面](../../docs/PRD-v2-Agent/11-sitemap.md) · [§3.1 首次启动](../../docs/PRD-v2-Agent/11-sitemap.md) · [§3.2 用户发起](../../docs/PRD-v2-Agent/11-sitemap.md) · [§3.4 对话即配置](../../docs/PRD-v2-Agent/11-sitemap.md)
- [契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md) · [铁律 2 中性视角](../工程宪法.md)

## 参考

- Story（What）：[story-01 chat-as-os](../../docs/PRD-v2-Agent/story-01-chat-as-os.md)（主入口 = Chat）
- 决策：[D-103](../决策日志.md)（界面盘点：对话面缺口）· [D-063](../../项目管理/决策日志.md)（零构建前端）
- 上游：[`T-UI-011`](T-UI-011-底座与骨架多面端口注入与全IA页面登记.md)（端口与页面登记）
- 同批：[`T-UI-013`](T-UI-013-工作区钉住的持久组件.md)（工作区）· [`T-UI-016`](T-UI-016-推理区触发进度分歧图与视角详情.md)（推理链面板）
- 关卡：[`T-INT-007`](T-INT-007-M6集成关卡界面完备.md)

## 备注

范围套件 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/ui`（含前端纯净度静态扫描 [`test_frontend_pure.py`](../../tests/ui/test_frontend_pure.py)）+ `verify_docs.py --strict`。**拆分触发**：本叶覆盖 [11-sitemap §2–§3](../../docs/PRD-v2-Agent/11-sitemap.md) 的 7 条屏与四条 Flow，GWT 远超 5——④ 对齐时按「对话骨架 / 上下文卡片与澄清确认 / 生成式 UI 宿主」转分组节点拆三叶。
