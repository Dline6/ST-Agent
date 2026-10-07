---
id: T-ECO-001.2
parent: T-ECO-001
title: 四类导出流程与分享卡片
story: ../../docs/PRD-v2-Agent/story-10-skill-sharing.md
arch: ../../docs/技术架构-v2/09-生态与分享.md
arch_link: "[09 §2·§5](../../../../docs/技术架构-v2/09-生态与分享.md)"
priority: P1
milestone: M4
depends_on: [T-ECO-001.1, T-L1-001, T-L1-003, T-L2-001, T-L4-001]
status: done
decisions: [D-079]
verify: 四类导出共用一套容器（`.stskill` / `.stflow` / `.stlens` / `.stmem`）· 依赖声明逐类从本体推导 · `.stmem` **强制三步**（清单与载荷同源 · 未确认即由 L2 确认门拒）· 命名 / 描述**中性化拦截**且记忆节点原文**不施校验**（D-053）· 出处链追加（导入物二次导出链长 +1、历史不覆盖）· 分享卡片纯文本且 `card` / `export` 无任何出网件（AST 断言）· 导出 + `save_to` 全程**不写 `Store`** · 32 例 · 范围 **561** / 全量 **2697** 全绿；留痕见 执行日志 `[T-ECO-001.2]`
---

# T-ECO-001.2 · 四类导出流程与分享卡片

## 目标
交付 [09 §2](../../../../docs/技术架构-v2/09-生态与分享.md) 的**导出流程**与 [09 §5](../../../../docs/技术架构-v2/09-生态与分享.md) 的**出处链追加**：从四个取材面取本体，经**命名与描述的中性化拦截**（[01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)）与 `.stmem` 的**强制三步**，产出 [`T-ECO-001.1`](T-ECO-001.1-四类分享物统一容器与格式.md) 的容器字节；可选生成**分享卡片**；导出物经 `save_to(path)` 写到用户显式指定的位置。

| 分享物 | 取材面 | 确认门 |
| --- | --- | --- |
| `.stskill` | [`SkillRegistry.get`](../../../../src/st_agent/l1/skills/registry.py) | 无（[09 §2](../../../../docs/技术架构-v2/09-生态与分享.md)：任何自建物可导出） |
| `.stflow` | [`WorkflowStore.get`](../../../../src/st_agent/l1/workflow/store.py) | 无 |
| `.stlens` | [`LensRoster.get`](../../../../src/st_agent/l4/roster.py) | 无 |
| `.stmem` | [`MemoryShare.plan` / `.export`](../../../../src/st_agent/l2/memory/sharing.py) | **强制三步**：过滤 → 清单 → 用户确认 |

**不做**：不改容器形态（归 `.1`）；不做导入校验（归 [T-ECO-002](T-ECO-002-导入校验流水线官方Skill索引生态边界.md)）；不做导出对话框 / 面板（表现层，仓库无前端）。

## 验收标准（Given-When-Then）
- **GWT-1**：Given Studio 中一个自建 Skill，When 导出，Then 生成 `.stskill` 容器（完整定义 + 元数据 + 依赖声明 + 校验和）；Given 自建工作流 / 自定义视角，When 导出，Then 同构产出 `.stflow` / `.stlens`——**四类共用同一套容器代码**，调用面形状一致
- **GWT-2**：Given `.stmem` 导出请求，When 处理，Then 走**强制三步**：先按隐私分级过滤 `private` / `sensitive` → 展示「本次导出包含的公开信息」清单（**不含节点原文**）→ **用户确认后**才生成；未确认即拒（`confirmed_by` 门），且过滤结果与清单**同一次**算出一致
- **GWT-3**：Given Skill / 工作流 / 视角的命名或描述含拟人化措辞，When 导出，Then **导出前拦截**并点名违规（[01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)），**不产文件**；Given `.stmem`，When 导出，Then 记忆节点原文**原样**导出（数据展示不过输出校验，[D-053](../../../决策日志.md)）
- **GWT-4**：Given 一个**从他人处导入**的分享物再次被导出，When 生成 `manifest`，Then 出处链**追加**一环（本次分享者 + 上一环持有的出处链），**历史不覆盖**；Given 请求分享卡片，When 生成，Then 产出描述 + 校验和 + 下载指引，措辞**中性**（无第一人称 / 无对话体）
- **GWT-5**：Given 任一导出，When 交付，Then 返回容器字节；`save_to(path)` 把**同一份**字节写到用户给的路径——全程**不写** `Store` 任何分区，也不自建目录或索引（导出物是用户自持的明文副本）

## 接口面
- **输入**：
  - [`T-ECO-001.1`](T-ECO-001.1-四类分享物统一容器与格式.md) 的容器面：`ShareKind` / `ShareManifest` / `ShareContainer`（打包与字节化）· `container_checksum`
  - 四个取材门面：[`SkillRegistry.get` / `get_latest`](../../../../src/st_agent/l1/skills/registry.py)（[T-L1-001](../M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md)）· [`WorkflowStore.get` / `get_latest`](../../../../src/st_agent/l1/workflow/store.py)（[T-L1-003](../M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md)）· [`LensRoster.get`](../../../../src/st_agent/l4/roster.py)（[T-L4-001](../M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)）· [`MemoryShare.plan` / `export`](../../../../src/st_agent/l2/memory/sharing.py)（[T-L2-001](../M1/T-L2-001-节点边模型读写接口MemoryReaderWri.md)，其 `plan()` 已把「过滤 + 清单」算在同一次读图上）
  - 契约面：[`NeutralityGuard.check_name`](../../../../src/st_agent/contracts/neutrality.py)（名称 + 描述；01 §6 执行点 1）· [`Provenance`](../../../../src/st_agent/contracts/capability_types.py)（`sharer` / `origin_chain`）
- **输出**（本叶交付的公共面与落点）：
  - [`src/st_agent/eco/export.py`](../../../../src/st_agent/eco/export.py)：`ShareExporter`——四类导出入口（各自一个方法）· 出处链追加（`appended_chain`）
  - [`src/st_agent/eco/container.py`](../../../../src/st_agent/eco/container.py)：`ShareContainer.save_to(path)`（写出**同一份**字节；落在容器上，导出面不另造一份）
  - [`src/st_agent/eco/card.py`](../../../../src/st_agent/eco/card.py)：`ShareCard`（描述 + 校验和 + 下载指引，中性措辞）
  - 用例 [`tests/eco/test_export.py`](../../../../tests/eco/test_export.py)
  - **落盘位置**：**无本机数据落点**——只有 `save_to(path)` 写用户显式给出的那一个路径；**不写** `Store` 任何分区（用例断言分区写计数为 0）
- **对外形状**：四类各自 `export_*(..., sharer=…)` → 容器字节（+ 可选卡片）；`.stmem` 另给 `plan_memory()`（返回待展示的公开信息清单）与 `export_memory(confirmed_by=…)`（确认门）

## 可关闭的遗留
- 无（L0 册 `A4` 由**父任务**统一销账；逐册读 [L0](../../../遗留问题/L0-遗留问题.md) / [L1](../../../遗留问题/L1-遗留问题.md) / [L2](../../../遗留问题/L2-遗留问题.md) / [L3](../../../遗留问题/L3-遗留问题.md) 未闭区，无「归属＝本叶」或「解封条件＝本叶 `done`」者）

## 假设与前提
- **A1 · `.stmem` 的三步已由 L2 落点承担，本叶只接线**——前提：[`MemoryShare.plan()`](../../../../src/st_agent/l2/memory/sharing.py) 已把「节点级过滤 + 相接边回收 + 清单」算在**同一次**读图上，`export(confirmed_by=…)` 已实现确认门；本叶不重算过滤。若错（需在本层另做过滤）：返工面＝过滤的单一真相源分裂成两处。验证方式：GWT-2 用例断言本叶产出的清单与载荷经 `MemoryShare` 直取时**逐项一致**，且未确认即拒。
- **A2 · 中性化拦截施于三类对象，不施于记忆节点**——前提（父 `A5`）：[09 §2](../../../../docs/技术架构-v2/09-生态与分享.md) 的措辞是「命名与描述」，`.stmem` 的节点内容属数据展示（[D-053](../../../决策日志.md)）。若错（要求记忆文本也过校验）：返工面＝`.stmem` 导出路径。验证方式：三类拦截用例 + `.stmem` 的「节点原文原样导出」用例。
- **A3 · 出处链来源＝被导出物自身的 `provenance`**——前提：导入物的 `Provenance` 已携 `sharer` / `origin_chain`（[`ImportOrigin`](../../../../src/st_agent/l2/memory/models.py) 与 [`Provenance`](../../../../src/st_agent/contracts/capability_types.py) 四字段同形），故「追加一环」＝在既有链尾接上本次分享者，不新造存储。若错（导入面没把出处写进本体）：返工面＝导入侧（[T-ECO-002](T-ECO-002-导入校验流水线官方Skill索引生态边界.md)）与 L2 的 `imported` 分支。验证方式：GWT-4 的「二次导出 ⇒ 链长 +1 且首环不变」用例。
- **A4 · 分享卡片是纯文本 / 结构化的产出，不出网**——前提：卡片只是「描述 + 校验和 + 下载指引」的字符串，用户自行发布到外部渠道（[09 §2](../../../../docs/技术架构-v2/09-生态与分享.md)），产品不经手任何数据。若错（要求内置发布通道）：返工面＝越出本地优先边界，须先改 [09 §6](../../../../docs/技术架构-v2/09-生态与分享.md) 的生态边界。验证方式：卡片生成用例断言其不触发任何网络调用（网关调用计数为 0）。

## 涉及契约
- [09 §2 导出流程 / §5 来源追溯链](../../../../docs/技术架构-v2/09-生态与分享.md)——本叶的 What/How 来源
- [01 §6 中性化](../../../../docs/技术架构-v2/01-平台共享契约.md)（导出前拦截）· [01 §2 `provenance`](../../../../docs/技术架构-v2/01-平台共享契约.md)（出处链的形状）
- [04 §8 记忆片段分享](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)（`.stmem` 的三步与确认门）
- [09 §6 生态边界](../../../../docs/技术架构-v2/09-生态与分享.md)（不内置发布通道；社区互动走外部渠道）

## 参考
- 决策：[D-053](../../../决策日志.md)（记忆本体作数据展示不过输出校验）· [D-079](../../../决策日志.md)（本批拆分与导出形态）
- 上游：[`T-ECO-001.1`](T-ECO-001.1-四类分享物统一容器与格式.md)（容器）· [T-L1-001](../M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) · [T-L1-003](../M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md) · [T-L2-001](../M1/T-L2-001-节点边模型读写接口MemoryReaderWri.md) · [T-L4-001](../M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)
- 下游：[T-ECO-002](T-ECO-002-导入校验流水线官方Skill索引生态边界.md)（导入侧消费本叶产出）· [T-INT-005](T-INT-005-M4集成关卡反思演进与生态闭环.md)（导出 → 导入闭环）

## 备注
本叶**不落** `Store`、不建导出目录、不内置发布通道。实现细节留给代码 / commit / 执行日志。
