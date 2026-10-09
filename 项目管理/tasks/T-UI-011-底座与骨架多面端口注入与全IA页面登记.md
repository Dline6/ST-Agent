---
id: T-UI-011
parent: null
title: 底座与骨架：多面端口注入与全 IA 页面登记
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §12](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M6
depends_on: [T-UI-010]
status: todo
decisions: [D-103]
verify:
---

# T-UI-011 · 底座与骨架：多面端口注入与全 IA 页面登记

> **M6 的底座与骨架叶**（[D-103](../决策日志.md)）。按「先铺底座 + 骨架，再逐区填内容」的起手式（[D-103](../决策日志.md) ②），把**全部 IA 页面的可达性**与**取数面**先立起来，使七面功能叶只剩「往已登记的页面里填内容」。

## 目标

三件交付，都落在**表现层与组合根的骨架面**：

1. **多面鸭子端口注入**——[`UiApp`](../../src/st_agent/ui/app.py) 现只有四个端口（`chat` / `reflection` / `eco` / `memory`）。本叶按 [11-sitemap §2](../../docs/PRD-v2-Agent/11-sitemap.md) 七面的取数需求，在**组合根** [`st_agent/app.py`](../../src/st_agent/app.py) 造齐各面门面（L1 Skill/MCP 面 · L2 图谱 / 画像面 · L4 推理面 · L5 触达面 · L0 存储 / 配置面），经**同形鸭子端口**注入 `UiApp` / `build_ui` / `serve` / [`__main__.py`](../../src/st_agent/__main__.py)。**未接线的面 fail-closed**（`unavailable` + 点名装配归属，不 500、不装空表）。
2. **全 IA 页面登记**——[11-sitemap §2](../../docs/PRD-v2-Agent/11-sitemap.md) 声明的每条屏的**宿主页**进 [`PAGE_PATHS`](../../src/st_agent/ui/app.py) 与 [`pages.js`](../../src/st_agent/ui/web/js/pages.js)；未填 `render` 的页走 [`main.js`](../../src/st_agent/ui/web/js/main.js) 的**六态可用性探针**（只证「路由通、端口在不在」，不冒充业务内容）。
3. **回环端点骨架**——各面的 `GET` 探针端点（`/api/<面>/status` 同形）+ 写面经**固定回环路由**（[01 §12 动作不进描述](../../docs/技术架构-v2/01-平台共享契约.md)）。

**本批的边界**（不越界）：

- **不填业务内容**——各页的具体读面 / 交互 / 渲染件归七面功能叶；本叶只让页面**可达**、探针**如实**。
- **不动既有四端口语义**——`chat` / `reflection` / `eco` / `memory` 保持原形，新面只增。
- **不引构建链**（[D-063](../../项目管理/决策日志.md)）。
- **不越层取数**——组合根是唯一装配点，表现层只经鸭子端口（[铁律 7](../工程宪法.md)）。

## 验收标准（Given-When-Then）

- GWT-1：Given [11-sitemap §2](../../docs/PRD-v2-Agent/11-sitemap.md) 声明的每条屏，When 对位 [`PAGE_PATHS`](../../src/st_agent/ui/app.py) 与 [`pages.js`](../../src/st_agent/ui/web/js/pages.js)，Then 每条屏有宿主路径、每个路径两端同源登记，**无屏无家可归**
- GWT-2：Given 每个新页面路径，When 首导航，Then 回静态壳（200，令牌只压 `/api/*`），且**未填 `render` 者**出六态可用性探针（`ok` / `empty` / `unavailable` / `delayed` / `error` 如实），**不**出错误红、**不**静默留白
- GWT-3：Given 组合根未注入某面端口，When 取该面探针，Then `unavailable` + 点名装配归属（**fail-closed**），不 500、不回空表冒充
- GWT-4：Given 全部新端点，When 检查写面，Then 写动作走**固定回环路由**（描述里**无** URL / 方法），只读呈现与带动作面分型清楚
- GWT-5：Given 七面各一条取数路径，When 在真组合根 + 真回环服务上取数，Then 每面回该面**已定形态**的 `ResultEnvelope`（不越层、不旁路），中性化门在出站点生效
- GWT-6：Given 全项目相对链接，When 跑 `python tools/verify_docs.py --strict`，Then 检查 1–9 全过、0 断链

## 接口面

<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留

<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提

<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约

- [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md) 六态 · [§6 中性化校验](../../docs/技术架构-v2/01-平台共享契约.md) · [§7 配置注册表](../../docs/技术架构-v2/01-平台共享契约.md) · [§12 UI 描述](../../docs/技术架构-v2/01-平台共享契约.md)
- [00 §1.1 进程形态](../../docs/技术架构-v2/00-架构总览.md)——后端口常驻 + UI 可分离
- [05 §4 六态](../../docs/技术架构-v2/05-L3-对话主入口.md) · [§6–§7 Generative UI](../../docs/技术架构-v2/05-L3-对话主入口.md)
- [11-sitemap §2–§3](../../docs/PRD-v2-Agent/11-sitemap.md)——页面集与 Flow（覆盖面的判据）
- [铁律 7 层间只向下依赖](../工程宪法.md) · [铁律 8 接口变更有序](../工程宪法.md)

## 参考

- 决策：[D-103](../决策日志.md)（M6 起手式「先铺底座 + 骨架」）· [D-060](../../项目管理/决策日志.md)（表现层承载形态）· [D-063](../../项目管理/决策日志.md)（零构建前端）
- 上游：[`T-UI-010`](T-UI-010-契约先行组件型补齐与页面集口径.md)（组件型与页面集口径）
- 下游：七面功能叶 [`T-UI-012`](T-UI-012-Chat主界面对话流与生成式UI宿主.md) ~ [`T-UI-018`](T-UI-018-设置区与全局要素.md)
- 先例：[`T-UI-004.1`](done/M4/T-UI-004.1-回环端点骨架、鸭子端口注入与前端页面壳.md)（回环端点骨架 + 鸭子端口注入 + 前端页面壳）
- 关卡：[`T-INT-007`](T-INT-007-M6集成关卡界面完备.md)

## 备注

范围套件 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/ui`（本叶改 `src/st_agent/ui/**`、`src/st_agent/app.py`、`src/st_agent/__main__.py`）+ `verify_docs.py --strict`。**拆分触发**：端子面（多面端口注入）与页子面（全 IA 页面登记）跨不同契约小节、各自 GWT>5 时，本叶转分组节点拆 `.1`（端口与门面）/ `.2`（页面登记与探针）。
