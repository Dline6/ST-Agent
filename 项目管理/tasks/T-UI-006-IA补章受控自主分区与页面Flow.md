---
id: T-UI-006
parent: null
title: IA 补章：受控自主分区与页面 Flow
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M6
depends_on: []
status: todo
decisions: [D-103]
verify:
---

# T-UI-006 · IA 补章：受控自主分区与页面 Flow

> **M6 批次一的首叶**（[D-103](../决策日志.md)）。本任务是纯 PRD 侧文档增量：把 M5 交付的**受控自主**功能面补进 IA，使页面集重新等于「10 个 story 的设计触点 · 屏」的归并结果。

## 目标

M5 的受控自主运行时（[05 §10 的 `investigate` 循环](../../docs/技术架构-v2/05-L3-对话主入口.md)）交付于 2026-10-09，而 [11-sitemap](../../docs/PRD-v2-Agent/11-sitemap.md) 最后一次实质修订在 M1（骨架提交 #69）——**该册全文 0 处**提自主查证 / 工具调用 / 授权闸门，故 §2 页面清单里没有它的分区、§3 九个 Flow 里没有它的走向。本任务补上这一段。

**交付**：

1. [11-sitemap](../../docs/PRD-v2-Agent/11-sitemap.md) 新增「受控自主」分区（§2）——循环的用户可见面在页面集里有归宿；新增对应 Flow（§3）——从识别 `investigate` 到循环执行、闸门交还、终止与上界标注。
2. [story-01](../../docs/PRD-v2-Agent/story-01-chat-as-os.md) 的「设计触点 · 屏」「关联页面（Sitemap 占位）」「设计资产」字段同批回填。
3. §4「与 Story 的对应」表补入该分区。

**本批的边界**（不越界）：

- **不新造渲染路径**——[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 已定循环产出交**既有**渲染路径（§6 的 UI 描述与组件注册表）呈现，故本任务只做「它在哪个屏、怎么走」，**不**登记新组件型（那归 M6 的契约先行叶）。
- **不写 `12-edge-cases.md`**——异常态系统穷举是[另一处欠账](../../docs/PRD-v2-Agent/README.md)（离线 / LLM 不可用 / MCP 崩溃 / 存储损坏 / 演进漂移），与功能面缺章不同源，另立任务。
- **不重排既有分区**——记忆 / 能力 / 推理 / 触达 / 设置五区的页面**已有归属**，只是尚无实现任务（归 M6 后续批次）；本任务不改它们的编排。

## 验收标准（Given-When-Then）

- GWT-1：Given [11-sitemap §2 页面清单](../../docs/PRD-v2-Agent/11-sitemap.md) 与 [05 §10 自主查证循环](../../docs/技术架构-v2/05-L3-对话主入口.md)，When 逐条对位，Then 循环的**每一处用户可见面**——多步证据包与完整 Trace（每步一条 `skill_run`）/ **闸门交还**（「下一步需确认」的显式告知）/ **上界与终止原因的如实标注** / **失败步照实呈现**——在页面集里各有归宿，无未归并的屏
- GWT-2：Given 新增分区的 Flow，When 通读，Then 覆盖「识别为 `investigate` → 循环执行 → 遇非 `autonomous-ok` 动作交还用户 → 确认或终止」的走向，且与 §3.3 推理链展开、§3.2 用户发起两条**既有** Flow 接得上（复用其渲染件，不重复定义）
- GWT-3：Given [story-01](../../docs/PRD-v2-Agent/story-01-chat-as-os.md) 的三处字段与 §4 对应表，When 查看，Then 同批回填、与 11-sitemap 双向可追溯，无「待生成」残留
- GWT-4：Given 本册新增的文案（分区名 / 引导语 / 空状态 / 确认卡），When 逐条核对，Then 按[契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md)撰写，**无拟人化**（人名 / 性格 / 第一人称 / 情感 / 对话体）
- GWT-5：Given 全项目相对链接，When 在 `项目管理/` 目录下跑 `python tools/verify_docs.py --strict`，Then 检查 1–9 全过、0 断链

## 接口面

<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留

<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提

<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约

- [11-sitemap](../../docs/PRD-v2-Agent/11-sitemap.md) §2 页面清单 / §3 页面 Flow / §4 与 Story 的对应 / §5 未纳入本 IA——**本册即交付面**
- [05 §10 自主查证循环](../../docs/技术架构-v2/05-L3-对话主入口.md)——该分区的契约来源（产出形态与中性边界、闸门与交还、上界与终止）
- [05 §6 Generative UI](../../docs/技术架构-v2/05-L3-对话主入口.md) · [01 §12 UI 描述](../../docs/技术架构-v2/01-平台共享契约.md)——该分区的呈现形态（**沿用既有渲染路径，不另造第二条**）
- [PRD README「待后续补充的上游 / 下游产出」](../../docs/PRD-v2-Agent/README.md)——「页面 Flow 设计资产」占位段
- [契约 9 中性表达](../../docs/PRD-v2-Agent/10-platform-capabilities.md) · [铁律 2 中性视角](../工程宪法.md) · [铁律 9 文档一致性](../工程宪法.md)

## 参考

- Story（What）：[story-01 chat-as-os](../../docs/PRD-v2-Agent/story-01-chat-as-os.md)（主入口 = Chat，无传统菜单式入口层级）
- 决策：[D-103](../决策日志.md)（M6 / M7 里程碑登记与 M6 批次一）
- 上游：[11-sitemap](../../docs/PRD-v2-Agent/11-sitemap.md)（本任务在其上增补，不重排既有分区）
- 同批：M6 批次一的后续叶（契约先行 / 底座与骨架，**尚未立项**，由 ④ 对齐逐叶点名后建文件）
- 下游关卡：[`T-INT-007`](T-INT-007-M6集成关卡界面完备.md)（M6 集成关卡）

## 备注

纯 docs 任务，收工只跑 `verify_docs.py --strict`（[测试分层](../工作流.md)的「文档-only 变更」档）。产出全在 `docs/`，受[铁律 9](../工程宪法.md) 约束：PRD 新增须同批回填受影响 Story。
