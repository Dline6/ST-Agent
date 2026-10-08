---
id: T-L6-004
parent: null
title: 主动提案落 Studio 草稿接收面
story: ../../docs/PRD-v2-Agent/story-09-reflection-loop.md
arch: ../../docs/技术架构-v2/08-L6-反思演进.md
arch_link: "[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md)"
priority: P1
milestone: M5
depends_on: [T-L6-002.2, T-L1-003.4, T-UI-004.1, T-UI-004.2]
status: done
decisions: [D-100]
verify: 两叶齐备（[`.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md) L6 侧翻译 + Studio 交接受体 / [`.2`](T-L6-004.2-组合根装配与表现层交接线.md) 组合根装配 + 表现层「交 Studio」动作）· 批次验收兑现：[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 的「衔接 Studio」落地口径与 [01 §12](../../docs/技术架构-v2/01-平台共享契约.md) 的 `proposal_card` 动作**改码之前**写实（[铁律 8](../工程宪法.md)）；组合根**首装配** `DraftIntake`（此前无装配点）；「交 Studio」→ 开画布会话 → 接受 / 否决闭环；**真组合根**端到端落 `config/workflow/<flow_id>.json` + `execution_log/workflow-change/<change_id>.json` · 两叶合计新增用例 **20**（`.1` 15 + `.2` 集成 5）+ UI 只增 5 · 范围套件（contracts+layering+tools+integration+l6+ui）**1243 passed（404.59s）** · 未触发联网面 · `verify_docs --strict` 检查 1–9 全过 0 断链 · L6 册 `B1` 销账 + 新登记 `B2` · 见 [执行日志](../执行日志.md) `[T-L6-004]`
---

# T-L6-004 · 主动提案落 Studio 草稿接收面

> 关闭 [L6 册 `B1`](../遗留问题/L6-遗留问题.md)（待人立项）——负责人 2026-10-08 拍定立项。

## 目标
把 [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 的**主动提案**（`SkillProposal` 携 `SkillDraft`，「要不要我为你创建一个专门 Skill？」+ 草稿）接上它的**落地点**——[03 §4](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 的 Skill Studio 草稿接收面 `DraftIntake.receive(WorkflowDraft)`（[story-09](../../docs/PRD-v2-Agent/story-09-reflection-loop.md)「衔接 story-06」）。两端**形态不同**（`SkillDraft` → `WorkflowDraft` 需一次翻译），且翻译属**跨层接线**（L6 → L1），故由 [T-UI-004.2](done/M4/T-UI-004.2-反思中心表现层入口.md) 起提案卡只呈现、不给处置动作。

**本批交付**（三处，见「拆分」）：
1. **翻译与交接受体**（L6 侧适配器）：`SkillDraft` → `WorkflowDraft` 的**唯一**翻译 + 按 `proposal_id` 开画布会话并**持会话**，三态（接受 / 微调 / 拒绝）**委托** L1 `DraftIntake`（不复制其判定）。
2. **组合根装配与表现层动作**：组合根立 `DraftIntake` 并把提案接上；提案卡的 skill 项给出「交 Studio」动作、经**固定回环路由**转交；表现层**不 import 各层类型**（口径同 [T-UI-004.2](done/M4/T-UI-004.2-反思中心表现层入口.md) 的 `ReflectionFacade`）。
3. **端到端**：集成套件以**真组合根**（真 `Store` + 真 `SkillRegistry` + 真 `DraftIntake` + 真 `ProposalEngine`）钉住一次「提案 → 交 Studio → 落画布 → 接受」。

**本批的边界**（不越界）：
- **无 Studio 画布页面**（`ui/web/js/pages/` 只有 `eco` / `evolution` / `reflection`）——故「微调」只到**返回编辑面句柄**（`CanvasEditor`），可视化画布归后续 Studio 页面任务；表现层**显式标注**该限制，不假装有画布。
- 其余三处建议（周报第四段候选 / 训练建议 / 待批准队列）的处置三动作**不受影响**，逐字节不变。
- **组合根对 L3 对话通道的 Studio 落点（[T-L3-003.3](done/M1/T-L3-003.3-工作流草稿交接与Studio落画布.md) 的 `handoff`）同批接上**——它同缺一个装配点，本批一次立好 `DraftIntake`，两处共用同一接收面。

**拆分**（命中[拆分触发](../工作流.md)：涉及 [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) / [01 §12](../../docs/技术架构-v2/01-平台共享契约.md) / [03 §4](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 多个契约小节与 L6 / L1 / UI 三层；单次预计 Agent 会话轮次 > 20）——父转分组节点，交付由两叶承接：

- [`T-L6-004.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md)——**L6 侧**：`SkillDraft` → `WorkflowDraft` 翻译（唯一实现）+ Studio 交接受体（开/持画布会话、三态委托）
- [`T-L6-004.2`](T-L6-004.2-组合根装配与表现层交接线.md)——**组合根装配 + 表现层「交 Studio」动作 + 端到端**

不合并成一叶：两叶失败面不同（「翻译错 / 会话没持住」vs「装配漏 / 动作没接线 / 信封错」），且 `.1` 是纯库面（可被 L6 单测直接覆盖），`.2` 是跨层装配与表现层。

## 验收标准（Given-When-Then）
取自 [story-09](../../docs/PRD-v2-Agent/story-09-reflection-loop.md) 的「衔接 [story-06](../../docs/PRD-v2-Agent/story-06-skill-studio.md)」与 [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md)，逐条落到两叶（父级只汇总，不重复）：

- Given 一条含 Skill 草稿的主动提案，When 交 Studio，Then 草稿经**唯一**翻译落 [03 §4](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 的接收面产出可编辑画布会话；形状非法 → 画布不开、显式失败 → [`.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md) GWT-1 / GWT-2
- Given 已落画布的会话，When 接受 / 否决，Then 接受经 L1 `DraftIntake.accept` 落 v1.0 并产生 `change_id`、否决不落盘且值逐字节不变 → [`.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md) GWT-3
- Given 组合根已装配，When 经门面把一条提案交 Studio，Then 经**组合根适配面**转交 `DraftIntake.receive`，表现层不 import 任何层类型 → [`.2`](T-L6-004.2-组合根装配与表现层交接线.md) GWT-1
- Given 提案卡渲染，When 点「交 Studio」，Then 经固定回环路由提交、按六态回信封；成功后可继以「接受 / 否决」→ [`.2`](T-L6-004.2-组合根装配与表现层交接线.md) GWT-2 / GWT-3
- Given 真组合根，When 跑一次「提案 → 交 Studio → 接受」，Then 真 `WorkflowStore` 里出现落盘的工作流、`execution_log` 出现 `workflow-change/<change_id>.json` → [`.2`](T-L6-004.2-组合根装配与表现层交接线.md) GWT-4

## 接口面
- **输入**（父级汇总；逐叶列于各自 `## 接口面`）：
  - [`ProposalEngine.get(proposal_id)` / `all()`](../../src/st_agent/l6/proposal.py) + [`SkillProposal` / `SkillDraft`](../../src/st_agent/l6/proposal.py)（[T-L6-002.2](done/M4/T-L6-002.2-主动提案与周报候选生成.md)）
  - [`WorkflowDraft` / `DraftIntake.receive` / `.tune` / `.accept` / `.reject` / `DraftSession` / `AcceptResult` / `RejectResult`](../../src/st_agent/l1/studio/draft.py) + [`WorkflowDAG` / `checked_dag`](../../src/st_agent/l1/workflow/models.py) + [`CanvasEditor`](../../src/st_agent/l1/studio/canvas.py)（[T-L1-003.4](done/M0/T-L1-003.4-编辑面与草稿落画布.md)）
  - [`SkillRegistry`](../../src/st_agent/l1/skills/registry.py) 与 `Store`（`DraftIntake` 构造所需）
- **输出**（逐叶列于各自 `## 接口面`；父级汇总）：
  - **新增** [`src/st_agent/l6/studio_adapter.py`](../../src/st_agent/l6/studio_adapter.py)：`to_workflow_draft(...)` + `StudioHandoff`（[`.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md)）
  - **只增** [`src/st_agent/app.py`](../../src/st_agent/app.py)：`DraftIntake` 装配 + `StudioHandoff` 注入 + `M4Runtime.studio`（**表现层适配面**，[`.2`](T-L6-004.2-组合根装配与表现层交接线.md)）；**只增** `ui/server.py` 路由 · `ui/app.py` 的 `api_*` 与卡片槽 · `ui/web/js/plugins/reflection/proposal_card.js`
  - **落盘位置**：接受落 `config` 分区 `workflow/<flow_id>.json` + `execution_log` 分区 `workflow-change/<change_id>.json`（均既有，[L1 已承载](../../src/st_agent/l1/workflow/store.py)）；**本批不新造分区**
  - **文档面**：[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 补「衔接 Studio 的落地口径」+ [01 §12](../../docs/技术架构-v2/01-平台共享契约.md) 的 `proposal_card` 补一条（[铁律 8](../工程宪法.md)，**改码之前**）
- **降级路径**：`DraftIntake` / `ProposalEngine` 任一无 → 该面 fail-closed 并点名（`unavailable` + 原因），**不伪造**「已交 Studio」；表现层子面未接线回 `{"available": false, "reason": …}`（同 `ReflectionFacade` 口径）。

## 可关闭的遗留
- [L6 册](../遗留问题/L6-遗留问题.md) **`B1`**「主动提案的落地点未接」——**本批关闭**：归属细化为 [`.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md)（翻译 + 交接受体）与 [`.2`](T-L6-004.2-组合根装配与表现层交接线.md)（组合根装配 + 提案卡「交 Studio」动作 + 集成端到端），即其「解封条件」的逐条兑现。收工按[册规](../遗留问题/README.md)规则 3 移入册尾「已闭（备查）」+ 写关闭留痕。
- [L6 册](../遗留问题/L6-遗留问题.md) **新登记 `B2`**「Studio 画布页无归属任务」——**本批不改归属、留待人立项**：本批让「交 Studio」真的落画布会话（[`.2`](T-L6-004.2-组合根装配与表现层交接线.md) GWT-2），但画布**页**（可视化微调）是 [story-06](../../docs/PRD-v2-Agent/story-06-skill-studio.md) 的独立表现层任务、本批不建；据[册规](../遗留问题/README.md) 6「闭不了的当场登记归属并写明理由」，登记以免静默留悬。
- 其余各册（2026-10-08 开工逐册读未闭区复核）：L0 册 `C3` / `C5`（皆**人决**）· L1 册全段闭 · L2 册全段闭 · L3 册全段闭 · L5 册空——均「归属 / 解封条件」**非本任务**，无本批可闭项。

## 假设与前提
- **A1 · 按交付面拆两叶**（2026-10-08 ④ 对齐拍定）——前提：翻译与交接受体是**纯库面**（L6 + L1），组合根装配与表现层动作是**跨层装配 + UI**，各有独立失败面。若错（要求更细 / 更粗的粒度）：返工面＝父子任务文件与 [T-INT-006](T-INT-006-M5集成关卡受控自主闭环.md) 的 `depends_on` 指向。**验证方式**：两叶各自范围套件 + `verify_docs --strict` 检查 7。
- **A2 · 「交 Studio」终点＝开画布会话 + 接受 / 否决**（2026-10-08 ④ 对齐拍定）——前提：story-06 要求「画布可视化微调」是**中间步**，一步到底（receive→accept 直接创建）会绕过它；而**开画布会话后给接受 / 否决**在**无画布页面**时仍闭环（微调待画布页）。若错（要求一步到底或只到 receive）：返工面＝[`.2`](T-L6-004.2-组合根装配与表现层交接线.md) 的端点与卡片动作。**验证方式**：GWT-3 断接受经 `DraftIntake.accept` 落 v1.0 + `change_id`。
- **A3 · 翻译与 Studio 装配落 L6 适配器模块**（2026-10-08 ④ 对齐拍定）——前提：[铁律 7](../工程宪法.md)（L6 > L1，L6 可向下 import L1）；先例 [`AgentFamily`](../../src/st_agent/l6/registry_adapter.py)（上层 owner 经组合根注入 L1 门面）。**「翻译塞进组合根 `app.py`」未取**——那会让翻译无法被 L6 单测直接覆盖，且组合根变厚。若错：返工面＝翻译与交接受体的落点。**验证方式**：`tests/test_layering.py` 不改即绿；适配器文件住 `src/st_agent/l6/` 下。
- **A4 · 里程碑取 M5（零工具改动）**（2026-10-08 ④ 对齐拍定）——前提：`MILESTONES` 固定 M0–M5，而 [verify_docs 检查 7](../tools/verify_docs.py) 要求**有活跃任务的里程碑必有活跃 `T-INT-*` 关卡覆盖**；M4 的关卡 [T-INT-005](done/M4/T-INT-005-M4集成关卡反思演进与生态闭环.md) 已归档，故归 M4 会破检查 7（除非改工具）。机械后果：`.1` / `.2` 须补进 [T-INT-006](T-INT-006-M5集成关卡受控自主闭环.md) 的 `depends_on`，M5 收口将等本单。若错（要求语义正确的 M4）：返工面＝里程碑字段 + 检查 7 的历史豁免规则。**验证方式**：`verify_docs --strict` 检查 7 过。
- **A5 · 无 Studio 画布页 ⇒ 微调只到句柄**（2026-10-08 ④ 对齐拍定）——前提：`ui/web/js/pages/` 无 studio / canvas 页（2026-10-08 读码核实），画布渲染是 [story-06](../../docs/PRD-v2-Agent/story-06-skill-studio.md) 的独立表现层任务。本批「微调」只到**返回 `CanvasEditor` 句柄**并显式标注「画布页待后续」。若错（要求本批出画布页）：返工面＝[`.2`](T-L6-004.2-组合根装配与表现层交接线.md) 范围扩张到页面工程，应另立项。**验证方式**：`grep` 断言新增 `pages/` 与画布组件不落本批；卡片标注「画布微调待 Studio 页面」。

## 涉及契约
- [08 §4 主动提案](../../docs/技术架构-v2/08-L6-反思演进.md)（`SkillProposal` 携 `SkillDraft`；本批补「衔接 Studio 的落地口径」）
- [03 §4 Skill Studio 子系统](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（草稿接收契约 / 非法草稿边界 / 接受与变更留痕 / 编辑期不落盘——**沿用，不改**）
- [01 §12 UI 描述](../../docs/技术架构-v2/01-平台共享契约.md)（`proposal_card` 的 skill 项补「交 Studio」动作）
- [铁律 7 层间只向下依赖](../工程宪法.md) · [铁律 8 接口变更有序](../工程宪法.md) · [铁律 10 无留痕不关闭](../工程宪法.md)

## 参考
- Story（What）：[story-09](../../docs/PRD-v2-Agent/story-09-reflection-loop.md)（GWT-4「主动提案」+「衔接 story-06」）· [story-06](../../docs/PRD-v2-Agent/story-06-skill-studio.md)
- 决策：[D-100](../决策日志.md)（本批 ④ 对齐与拆分）· [D-086](../决策日志.md)（T-L6-002 批次拆法）· [D-061](../决策日志.md)（Studio 落画布交接口径）· [D-089](../决策日志.md)（表现层入口接线口径）
- 遗留来源：[L6 册 `B1`](../遗留问题/L6-遗留问题.md)（[执行日志](../执行日志.md) `[T-UI-004.2]` 2026-10-07 的「遗留」行）
- 上游交付方：[T-L6-002.2](done/M4/T-L6-002.2-主动提案与周报候选生成.md) · [T-L1-003.4](done/M0/T-L1-003.4-编辑面与草稿落画布.md) · [T-UI-004.1](done/M4/T-UI-004.1-回环端点骨架、鸭子端口注入与前端页面壳.md) · [T-UI-004.2](done/M4/T-UI-004.2-反思中心表现层入口.md)
- 同批：[`.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md) / [`.2`](T-L6-004.2-组合根装配与表现层交接线.md)
- 下游：[T-INT-006](T-INT-006-M5集成关卡受控自主闭环.md)（`depends_on` 补本批两叶）

## 备注
父任务只承载分组与批次验收口径；交付与逐条 GWT 在两叶。实现细节留给代码 / commit / 执行日志。
