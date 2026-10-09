---
id: T-UI-007
parent: null
title: 视觉设计规范：研判页方向与 token 层
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §12](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M6
depends_on: []
status: done
decisions: [D-104]
verify: 三处产出齐备（[13-visual-design.md](../../docs/PRD-v2-Agent/13-visual-design.md) 九块 + [`tokens.css`](../../src/st_agent/ui/web/css/tokens.css) 28 令牌 + [`app.css`](../../src/st_agent/ui/web/css/app.css) 改消费）· **实机渲染已验**（Edge / WebView2 引擎同源）：`--bg`=rgb(247,248,249) / `--fg`=rgb(27,27,29) / 行高 27.2px=16×1.7、暗色 `--bg`=rgb(20,22,26)，六态 chrome（`delay`=rgb(138,90,0) / `input-error`=rgb(165,33,33)）与分歧图**三视角并列**均正常 · 守卫 `tests/ui/test_ui_design_tokens.py` 4 例 + **红-绿已验**（改规范里一个令牌名 → 断言失败，复原即绿）· 范围套件 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/ui` 全绿 · `verify_docs --strict` 检查 1–9 全 0 · 见 [执行日志](../执行日志.md) `[T-UI-007]`
---

# T-UI-007 · 视觉设计规范：研判页方向与 token 层

> **M6 批次一的第二叶**（[D-104](../决策日志.md)）。这是项目**首份**视觉设计规范——现有 [`ui/web/css/app.css`](../../src/st_agent/ui/web/css/app.css) 首行即自述「骨架样式……**不做视觉定稿**」，故当前**不存在视觉设计**（是「尚未设计」而非「有设计待改」）。

## 目标

给出统一整套的视觉设计规范，并落成可消费的 token 层。它是后续各面页面「风格一致、交互连贯」的**唯一抓手**：规范定在页面前面，才不至于七面写完再回头统一视觉（那等于七面重做）。

**交付**：

1. **规范文档** `docs/PRD-v2-Agent/13-visual-design.md`——**13 而非 12**（12 由 [PRD README](../../docs/PRD-v2-Agent/README.md) 预留给 `12-edge-cases.md`）。九块：设计原则（从铁律派生）/ 色彩 / 字体 / 版式与密度 / **14 个已实现组件型的视觉基线** / 六态表达 / 动效 / 暗色 / 可达性，附文案细则（[契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md)的落地）。
2. **token 层** `src/st_agent/ui/web/css/tokens.css`（浅色 + 暗色两套命名变量）+ [`app.css`](../../src/st_agent/ui/web/css/app.css) 改为**消费 token**（值从 token 取；结构类名与六态 chrome 不动）。

**已定的三处选型**（[D-104](../决策日志.md)，2026-10-09 经负责人拍定）：

- **视觉方向**取 **A「研判页（对照栏）」为主干**，吸收 **B 台账的批注追溯**（trace 挂行/栏侧）与 **C 工作台的密度**（高信息密度、键盘优先）。
- **涨跌色**取 **A 股惯例红涨绿跌**，并**强制方向符（▲▼）与数值符号冗余编码**；色值避开纯饱和（绛红 / 松绿），以降低色盲下的辨错率。
- **实现能力维持零构建**（[D-063](../决策日志.md) ① 不动）——规范落成 CSS token 层，**不引** npm / 打包器 / 框架。

**本批的边界**（不越界）：

- **只定视觉，不定实现**——交互逻辑、页面结构与路由归后续各叶；本任务不新建页面。
- **不嵌中文字体**——中文走系统栈（`PingFang SC` / `Microsoft YaHei` / `Noto Sans SC`）；数字自带等宽子集（财务对齐是功能）。嵌入中文字体动辄 5–10 MB 且涉商用许可，与本地优先的体积取向相抵。
- **不改 [01 §12](../../docs/技术架构-v2/01-平台共享契约.md) 的组件型枚举**——新增组件型（如记忆图谱的节点-边图）归 M6 的契约先行叶，本任务只给**已登记**的 14 型定视觉基线。

## 验收标准（Given-When-Then）

- GWT-1：Given [01 §12](../../docs/技术架构-v2/01-平台共享契约.md) 与 [`ui/registry.py`](../../src/st_agent/ui/registry.py) 的**全部 14 个已实现组件型**与 [§5 六态](../../docs/技术架构-v2/01-平台共享契约.md)，When 逐个对照规范，Then 每一型有视觉基线（比例 / 间距 / 层级 / 数字处理），且六态各有**可辨识、不依赖单一颜色**的表达
- GWT-2：Given [06 §5](../../docs/技术架构-v2/06-L4-多视角推理.md)（分歧图两视图**均**实现）与方向 A 的对照栏，When 通读规范，Then 并列呈现是版式的第一公民，全篇**不出现**任何「合并后」的版式（呼应[铁律 4](../工程宪法.md)）
- GWT-3：Given 涨跌信息，When 查看色彩规范，Then 红涨绿跌 + ▲▼ 与数值符号**冗余编码**三件齐备，色值避开纯饱和，并给出色盲（红绿色觉异常）下的可辨性判据
- GWT-4：Given 中文为主的界面与财务数字，When 查看字体与版式规范，Then 中文走系统栈、数字走自带等宽子集；行长 < 80 字、中文行高比拉丁宽一档；**浅色与暗色两套 token 齐备**且对比度达标
- GWT-5：Given [铁律 2](../工程宪法.md) 与[契约 9](../../docs/PRD-v2-Agent/10-platform-capabilities.md)，When 查看视觉语汇与文案细则，Then 无拟人化（人名 / 性格 / 第一人称 / 情感 / 对话体），视觉语汇**不借「助手 / 伙伴」隐喻**（界面读作工具与仪表，不读作人）

## 接口面

- **输入**（消费的前置接口）：
  - [`src/st_agent/contracts/ui_description.py`](../../src/st_agent/contracts/ui_description.py) 的 `ComponentType` 枚举与 [`src/st_agent/ui/registry.py`](../../src/st_agent/ui/registry.py) 的 14 个已实现型必填槽——决定规范 §5「组件视觉基线」覆盖哪些型、每型有哪些槽要定视觉。
  - [`src/st_agent/ui/web/js/render.js`](../../src/st_agent/ui/web/js/render.js) 的 `PRESENTATION_CLASS` 映射与 `state--*` 类名——决定 §6「六态」覆盖哪五档（`normal` / `empty_state` / `delayed` / `error` / `input_error`）。
  - [`src/st_agent/ui/web/css/app.css`](../../src/st_agent/ui/web/css/app.css) 现有的语义色与结构类名（本批改为**消费**令牌，类名不变）。
  - [05 §6](../../docs/技术架构-v2/05-L3-对话主入口.md)（对话之外页面**同源同一渲染路径**——故规范只需一套）· [06 §5](../../docs/技术架构-v2/06-L4-多视角推理.md)（分歧图两视图均实现）· [11-sitemap §1](../../docs/PRD-v2-Agent/11-sitemap.md) + [契约 9](../../docs/PRD-v2-Agent/10-platform-capabilities.md)（文案与方向的约束来源）。
- **输出**（本任务交付的公共 API / 落盘位置）：
  - `docs/PRD-v2-Agent/13-visual-design.md`——视觉值口径的**唯一真相源**（§2.1 令牌总表）。
  - `src/st_agent/ui/web/css/tokens.css`——**变量命名面**：后续各页面叶的公共依赖，名字一旦定错则各页一起漂；浅色 + 暗色两套值。
  - `src/st_agent/ui/web/index.html` 增 `tokens.css` 的 `<link>`（**排在 `app.css` 之前**——后者消费前者）。
  - `src/st_agent/ui/web/css/app.css` 改为消费令牌（**结构类名与六态 chrome 不变**）。
  - `tests/ui/test_ui_design_tokens.py`——令牌名 ↔ 规范表一致性守卫（4 例）。
- **降级路径**：令牌未定义 / `tokens.css` 未加载 ⇒ 值回落到浏览器默认（不会报错）——故由守卫用例钉住「文件在盘」+「`index.html` 真的引了它」，不靠自觉。

## 可关闭的遗留

- **无**（2026-10-09 开工逐册读 [遗留问题](../遗留问题/README.md) 未闭区复核）：L0 册 `C3` / `C5` 与 `D1`–`D3`（皆**人决**）· L5 册 `A1`（归属＝**人**，须真实端点凭据）· L1 / L2 / L3 / L6 册未闭区为空——均无「归属＝本任务」或「解封条件＝本任务 `done`」者。

## 假设与前提

- **A1 · 零构建 + 纯 CSS 能承载方向 A 的全部视觉**（2026-10-09 ④ 对齐拍定）：前提是本批只定配色 / 字体 / 版式 / 组件视觉基线，不含图谱、图表这类需要 SVG 或画布的渲染。若错（视觉要求超出 CSS 能表达的范围）→ 返工面＝后续图谱 / 图表类页面需要 JS 侧生成矢量，规范要补一节「矢量图元规范」。**验证方式**：本批 14 型基线全部由 CSS 类与令牌表达（`tokens.css` + `app.css`），无 JS 参与视觉。
- **A2 · 不嵌中文字体不损观感**（2026-10-09 ④ 对齐拍定）：前提是三平台系统栈（`PingFang SC` / `Microsoft YaHei` / `Noto Sans SC`）都覆盖所需字重与字形。若错（某平台字面明显劣化）→ 返工面＝补一份中文子集字体（体积 5–10 MB + 商用许可），并改 `--font-ui` 与打包资产清单。**验证方式**：本机 Windows 实测字面正常（`Microsoft YaHei` 生效）；另两平台待各自主机复核，风险已写进规范 §3。
- **A3 · `app.css` 只改值 + 就近取阶梯 ⇒ `tests/ui` 零回归**（2026-10-09 ④ 对齐拍定）：前提是**结构类名与六态 chrome 一字不改**，字号按就近阶梯合入。若错（类名被改 / 有断言钉住旧值）→ 返工面＝`tests/ui` 里依赖具体数值的用例。**验证方式**：范围套件运行时 `tests/ui` 不改即绿；本批新增守卫也不断言任何具体色值。
- **A4 · 规范只需一套**（2026-10-09 ④ 对齐拍定）：前提是 [05 §6](../../docs/技术架构-v2/05-L3-对话主入口.md) 已定「对话之外的表现层页面同源同一渲染路径」——即对话页与各辅助页共用同一注册表与校验门。若错（某类页面走独立渲染路径）→ 返工面＝规范分裂成两套，组件基线要按路径分栏。**验证方式**：`ui/registry.js` 是唯一注册表（`tests/ui/test_ui_registry.py` 断言两侧不漂移）。
- **A5 · 机器守卫取最轻一条**（2026-10-09 ④ 对齐拍定，负责人确认「按建议加」）：只加**令牌名 ↔ 规范表一致性** + 两条链路存在性（文件在盘 / `index.html` 引了它），**不加**裸色值扫描那类脆用例（易被合法写法误伤）。若错（漂移仍发生）→ 返工面＝补更细的守卫（如逐型基线清单比对）。**验证方式**：红-绿已验——改规范里一个令牌名 → 断言失败、复原即绿。

## 涉及契约

- [01 §5 ResultEnvelope 六态](../../docs/技术架构-v2/01-平台共享契约.md) / [§6 中性化校验](../../docs/技术架构-v2/01-平台共享契约.md) / [§12 UI 描述](../../docs/技术架构-v2/01-平台共享契约.md)（组件型枚举与「动作不进描述」）
- [05 §6 Generative UI](../../docs/技术架构-v2/05-L3-对话主入口.md)（对话之外的表现层页面**同源同一渲染路径**——故视觉规范只需一套）
- [06 §5 分歧图两视图](../../docs/技术架构-v2/06-L4-多视角推理.md)
- [11-sitemap §1 文案口径](../../docs/PRD-v2-Agent/11-sitemap.md) · [契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md)
- [铁律 2 中性视角](../工程宪法.md) · [铁律 4 多视角分歧并列](../工程宪法.md) · [铁律 9 文档一致性](../工程宪法.md)

## 参考

- 决策：[D-104](../决策日志.md)（三处选型与立项）· [D-103](../决策日志.md)（M6 / M7 登记）· [D-063](../决策日志.md)（零构建前端 / `01 §12` / M1 最小面）· [D-060](../决策日志.md)（承载形态：系统 WebView 三引擎兼容底线）
- 现状： [`ui/web/css/app.css`](../../src/st_agent/ui/web/css/app.css)（骨架样式，六态语义色已有一套）· [`ui/registry.py`](../../src/st_agent/ui/registry.py)（14 个已实现型的必填槽）
- 同批：[`T-UI-006`](T-UI-006-IA补章受控自主分区与页面Flow.md)（IA 补章）
- 下游：M6 的契约先行叶与底座/骨架叶（**尚未立项**）——骨架叶的 `depends_on` 须含本叶（规范定在页面前面）
- 关卡：[`T-INT-007`](T-INT-007-M6集成关卡界面完备.md)

## 备注

收工按[测试分层](../工作流.md)跑 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/ui`（本批改 `src/st_agent/ui/**` 静态资产）。**同批加了机器守卫**（④ 对齐时点名并入本叶，见 A5）：`tests/ui/test_ui_design_tokens.py` 断言「`tokens.css` 令牌名集合 == 规范 §2.1 令牌总表」——它是 GWT-1 与 GWT-4「令牌表齐备」那条的**验证机制**（不另立 GWT，GWT 仍为 5 条）；另附两条链路存在性断言（文件在盘、`index.html` 引了 `tokens.css` 且排在 `app.css` 之前）。

落笔时的两处**据实偏差**已记入规范 §10：① 正文由 `15px/1.6` 改为 `--text-md`/`--leading`（`1rem`/`1.7`，中文行高放宽一档）；② 其余字号按就近阶梯合入（如 `.9rem → --text-sm`、`.85rem → --text-xs`），最大偏移 `.05rem`。
