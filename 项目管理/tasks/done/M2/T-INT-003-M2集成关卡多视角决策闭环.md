---
id: T-INT-003
parent: null
title: M2 集成关卡 · 多视角决策闭环（MVP）
story: ../../docs/PRD-v2-Agent/story-04-multi-lens.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M2
depends_on: [T-L4-001, T-L4-002, T-L4-003, T-L4-004, T-L4-004.1, T-L4-004.2, T-L0-010.1, T-L0-010.2, T-L0-010.3, T-L0-010.4, T-L0-010.5, T-L0-012, T-L0-013, T-L0-014, T-L0-015.1, T-L0-015.2, T-L1-010.1, T-L1-010.2, T-L1-012.1, T-L1-012.2, T-L1-012.3, T-L1-013.1, T-L1-013.2, T-L1-013.3, T-L1-013.4, T-L2-005, T-L3-006, T-INT-002]
status: done
decisions: [D-072]
verify: 全量 pytest **2407 passed / 9 deselected**（437s）；`tests/integration/test_m2_deliberation.py` 14 例（GWT-1..8 离线端到端）+ `tests/l4/test_analyze.py` 11 + `tests/l4/test_ports.py` 20 + `tests/l1/test_official_dimensions.py` 4 + `tests/l3/test_intent_protocol.py` +6 · live 子集 **3 passed**（`test_llm_deliberation_live.py`：真端点经 `LlmOpinionSynthesizer` 出方向观点 + `LlmEvidenceReviewer` 真研判，出网审计 2 条）· `verify_docs.py --strict` 检查 1–9 全 0（208 文件 / 6583 链接 / 0 断链）· 红-绿已验（意图级声明短路 → 4 例转红；门面 analyze 分支短路 → GWT-6 转红）· 入库 `cf4f02c` + [PR #81](https://github.com/Dline6/ST-Agent/pull/81) · 见执行日志 [T-INT-003]
---

# T-INT-003 · M2 集成关卡 · 多视角决策闭环（MVP）

## 目标
把 L4 多视角推理装配到可对话的骨架上，端到端跑通一次复杂决策的多视角中性推理闭环。**本关卡 done = MVP 达成**，故其验收同时充当 MVP 的完成定义。

具体地：新增**生产组合根** `build_m2_runtime`（复用 `build_m1_runtime` 的 L2/L3 装配，在其上叠 L4），把 L3 总线的 `analyze` 去向由 `wired=False` 接成真链路（[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的「触发 L4 Deliberation」），实现 L4 三个鸭子端口的真实现（方向合成 / 维度目录 / 证据重研判），并证明 [00 §5 反向流](../../../../docs/技术架构-v2/00-架构总览.md) 的 Deliberation 支在装配态逐段兑现：意图 → 澄清（问模式）→ 确认 → 派发 → 视角并行 → 交叉对照 → 分歧图渲染 → 决策记录写回 L2。

## 验收标准（Given-When-Then）
- **GWT-1 生产装配根可用**：Given 全新空目录 + 口令，When 经 `build_m2_runtime`，Then 官方 Pack 可列出 · L2 图谱可读写 · 7 内置视角已播种于 `config` 分区（`LensRoster.list_enabled()` 非空）· L3 各编排器与 `Deliberation` / `CrossExaminer` / `DivergenceViewer` / `AnalyzeService` **已用真实依赖构造**（`runner`/`reader`/`writer`/`skills`/`llm`/`market_query`），非注入 Fake。
- **GWT-2 analyze 去向来路端到端**：Given 已装配运行时（离线注入**确定性理解器**与**确定性方向合成器**），When 用户输入一句复杂决策 → `understand` → `clarify` 产出**模式问项**（快速/深度）→ `confirm` → 确认卡经用户确认 → `DispatchBus.dispatch`，Then 经组合根注入的编排端口跑通 Deliberation（各视角 `LensOpinion` 齐、证据引用可回溯）→ 交叉对照出 `DisagreementMap` → 视图投影出 `DivergenceView`，信封与载荷经 `DispatchOutcome` 如实透出（**不重包、不改 status**）。
- **GWT-3 视角阵容可增删且命名过中性化**：Given 组合根装配的 `LensRoster`，When `add_custom` 传**违规命名**（如「激进派」/第一人称），Then 被 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 拒；合法自定义视角入库并出现在后续编排的参与视角里；内置视角 `remove` 被拒、`set_enabled(False)` 可停用。
- **GWT-4 证据引用可回溯与追问接口**：Given 一次 Deliberation，When 取某视角的 `trace_id`（矩阵行）经 `describe_trace`，Then 出 `trace_timeline` 描述件、过 `ui` 中性化门；该视角的 `LensOpinion.evidence_refs` 为可解析的 §1 ID 串。
- **GWT-5 只质询不合并（铁律 4 端到端）**：Given 两个视角方向冲突，When 交叉对照 + Mini Debate，Then 出结构化 `ConflictAnalysis`（`sides` 差集 / `shared_refs` / `premise_related`），**全链路任一层对象都无** `verdict`/`recommendation`/`summary`/`advice` 类合并字段（`hasattr` + `model_dump` 双向钉住：`DeliberationResult` / `DisagreementMap` / `DivergenceView` / 描述件槽值）。
- **GWT-6 分歧图呈现在回环服务边界成立**：Given 一次 Deliberation 的视图，When 经 `describe_divergence_map` → `ui` 的 `api_description`，Then 两视图（`matrix` / `network`）槽均非空、无「未识别的槽」降级框，生成文案槽过 `NeutralityGate`；`ui` 侧不改动、`ui` 不 import `app`。
- **GWT-7 特殊情形走显式分支**：Given 空阵容 / 某视角数据不足 / 全员一致三种情形，When 执行，Then 分别走 `empty`（无参与视角）、`insufficient-data` 视角**照常成矩阵行**且不阻塞他者、`unanimity_notice` 提示（**证据面仍完整**）；**不返回残缺卡、不静默略过**。
- **GWT-8 决策记录写回 L2**：Given 一次 Deliberation 后的用户决策（决策 + 理由 + 采纳/忽略视角），When 经组合根的 `Deliberation.record_decision`，Then 落 `memory` 分区的 `history` 节点（`source="user_stated"`）并可经 `MemoryReader` 读回。
- **GWT-9 真实链路（联网面强制项）**：本关卡组合根默认装配真实 `LlmOpinionSynthesizer` 与 `LlmEvidenceReviewer`，**动到真实 LLM 出网面**——离线 GWT-1..8 进默认 `pytest`（注入确定性件，CI 可重复）；另**必跑** `python -m pytest -m live tests/live/test_llm_live.py`（真端点走通发送器），结果写进执行日志**验证**行，无可用端点而 `skip` 要写明原因。
- **回归**：默认 `pytest` **全量**绿（集成关卡强制全量，见[工作流「测试分层」](../../../工作流.md)）；`verify_docs.py --strict` 检查 1–9 全 0；本关卡用例全部离线可跑（真实网络部分标 `live`）。

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API / 落盘位置）
  - 装配入口：`T-INT-002` → `build_m1_runtime` / `open_runtime` / `L1Runtime`（[app.py](../../../../src/st_agent/app.py)）；本关卡抽出 L2/L3 装配助手复用之
  - L4 编排件：`T-L4-001` → `LensRoster(store, skills=, name_check=)`（`config` 分区 `lens-roster/`，[roster.py](../../../../src/st_agent/l4/roster.py)）；`T-L4-002` → `Deliberation(roster=, runner=, reader=, writer=, synthesizer=, executor=, now=)`（`deliberate` / `record_decision` / `FAST_COMBO` / `OpinionSynthesizer`，[deliberation.py](../../../../src/st_agent/l4/deliberation.py)）；`T-L4-003` → `CrossExaminer(roster=, catalog=, reviewer=, now=)`（`cross_examine` / `DimensionCatalog` / `EvidenceReviewer`，[crosscheck.py](../../../../src/st_agent/l4/crosscheck.py) / [divergence.py](../../../../src/st_agent/l4/divergence.py)）；`T-L4-004.1` → `DivergenceViewer(roster=)`（`build(result, map)`，[divergence_view.py](../../../../src/st_agent/l4/divergence_view.py)）
  - L3 渲染面：`T-L4-004.2` → `describe_divergence_map(view)` / `describe_trace(trace)`（[describe.py](../../../../src/st_agent/l3/render/describe.py)）；`T-L3-002.1` → `IntentProtocol(understander=, commands=, matcher=, descriptors=)`（[protocol.py](../../../../src/st_agent/l3/intent/protocol.py)）；`T-L3-002.2` → `DispatchBus(runner=, configs=, adjudications=)`（[bus.py](../../../../src/st_agent/l3/dispatch/bus.py)）
  - L1 面：`T-L0-016`/`T-L1-011` → `runtime.llm`（`LlmClient.invoke`）、`runtime.skills`（`SkillRegistry`）、`runtime.runner`（`SkillRunner`）；`T-L1-001.1` → [pack.py](../../../../src/st_agent/l1/skills/pack.py) 的 `OFFICIAL_PACK`（本关卡在其旁新增 `OFFICIAL_DIMENSIONS`）；取数面 `market_query`（`MarketQuerySource` 鸭子型，[common.py](../../../../src/st_agent/l1/skills/official/common.py)）
  - UI 出站点：`T-UI-001.2/.3` → `build_ui(chat=)` / `UiApp.api_chat`（[ui/app.py](../../../../src/st_agent/ui/app.py)）
- **输出**（本关卡交付的公共 API 与落盘位置）
  - [src/st_agent/app.py](../../../../src/st_agent/app.py)（改）：抽 `_build_l2` / `_build_l3` 助手（`build_m1_runtime` 行为**逐字节不变**），新增 `build_m2_runtime(root, passphrase, *, synthesizer=None, reviewer=None, catalog=None, market_query=None, **kw) -> M2Runtime`；`M2Runtime` 携 M1 全部句柄 + `roster` / `deliberation` / `examiner` / `viewer` / `analyze` / `chat`（对话门面增 analyze 分支）。
  - [src/st_agent/l4/ports.py](../../../../src/st_agent/l4/ports.py)（新）：`LlmOpinionSynthesizer`（真方向研判，经 `LlmClient`）· `MarketDimensionCatalog`（读 MarketDb 表/视图 + `indicator_dictionary`，与声明的产出方 join）· `LlmEvidenceReviewer`（真证据重研判，经 `LlmClient`）。
  - [src/st_agent/l4/analyze.py](../../../../src/st_agent/l4/analyze.py)（新）：`AnalyzeOutcome`（`envelope` + `result` + `map` + `view` + `traces` + `topic`/`mode`）+ `AnalyzeService.analyze(confirmation, *, values=None, now=None)`（deliberate → cross_examine → view 三段的唯一装配点）。
  - [src/st_agent/l1/skills/pack.py](../../../../src/st_agent/l1/skills/pack.py)（改）：新增 `OFFICIAL_DIMENSIONS: dict[str, tuple[str, ...]]`（维度键 → 产出 skill base 名），随官方 Pack 增删同步。
  - [src/st_agent/l3/intent/protocol.py](../../../../src/st_agent/l3/intent/protocol.py)（改）：新增 `INTENT_PARAM_SPECS`（无 target Skill 的意图级参数声明；`analyze` 的 `mode` 问项来源），`clarify`/`confirm`/`_filled` 兼容之。
  - [src/st_agent/l3/dispatch/bus.py](../../../../src/st_agent/l3/dispatch/bus.py)（改）：`DispatchBus(..., deliberations=None)` + `_dispatch_analyze`；`ROUTE_SPECS` 的 `analyze` 转 `wired=True`（订正陈旧 `owner`）；`DispatchOutcome.analysis`。
  - [src/st_agent/l3/render/describe.py](../../../../src/st_agent/l3/render/describe.py)：**零改动**（`describe_divergence_map` 已交付，本关卡只接线）。
  - [tests/integration/rig_m2.py](../../../../tests/integration/rig_m2.py) + `test_m2_deliberation.py`（新）：M2 装配 rig（复用 `rig_m1`）+ GWT-1..8 端到端离线用例。
  - [tests/live/](../../../../tests/live)（新）：`test_llm_deliberation_live.py`——真端点经 `LlmOpinionSynthesizer` 出方向观点（GWT-9）。
  - [tests/test_layering.py](../../../../tests/test_layering.py)（改）：`APP_ALLOWED_IMPORTS` 加 `l4`（[`T-L4-002` A2](T-L4-002-多视角执行编排.md) 已预告此改动）。
- **不交付**（显式标注，非欠账）：[06 §6](../../../../docs/技术架构-v2/06-L4-多视角推理.md) 的**触达联动**（Deliberation → L5 Signal）属 M3/L5；前端视图挂载属 `T-UI-*`；`memory_op`/`configure`/`query` 各去向已在 `T-INT-002` 交付、本关卡只随回归覆盖。

## 可关闭的遗留
- **无册内归属本任务项**（开工逐册核 [L0](../../../遗留问题/L0-遗留问题.md) / [L1](../../../遗留问题/L1-遗留问题.md) / [L2](../../../遗留问题/L2-遗留问题.md) / [L3](../../../遗留问题/L3-遗留问题.md) 未闭区：L1/L2 全闭；L0 的 `A1b`/`A4`/`A5`/`C1`/`C2` 与 L3 的 `A2` 归属均非本任务；[阻塞与未决](../../../阻塞与未决.md) 0 open）。
- **任务层遗留清偿**：[`T-L4-004`](T-L4-004-分歧图可视化边界与特殊情形.md) 收工遗留③点名「`analyze` 去向的 `owner="T-L4-002"` 是陈旧指针，随 `T-INT-003` 接线时一并订正」——由本关卡的 `ROUTE_SPECS` 改动清偿。

## 假设与前提
- **A1** 组合根落在 [`app.py`](../../../../src/st_agent/app.py)：抽 L2/L3 装配助手以复用、新增 `build_m2_runtime`，并令 [`test_layering.py`](../../../../tests/test_layering.py) 的 `APP_ALLOWED_IMPORTS` 加 `l4`（[`T-L4-002` A2](T-L4-002-多视角执行编排.md) 已预告「装配 l4 即越界，归 T-INT-003」）。**若错**（要求另立组合根模块）：`app.py` 与层扫描用例返工，M2 装配面重做。**验证**：`test_app_is_composition_root_only` 全绿且新增断言「`app` 可装配 `l0–l4`」。
- **A2** `analyze` 的 `topic` 经 `IntentDraft.understood` → `IntentConfirmation.values` 流入；`mode`（`quick`/`deep`）的**问项来源**由本关卡新增的**意图级参数声明**（`INTENT_PARAM_SPECS["analyze"]`）提供——[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 原口径「一条问项 ＝ 目标 Skill 的 `SkillDescriptor.parameters` 中未给出值的一项」对**无 target Skill 的意图**（`analyze` 无被调用的 Skill）不成立，是 [06 §2.1](../../../../docs/技术架构-v2/06-L4-多视角推理.md)「询问模式」的空悬。**若错**（负责人改判该问项归别的机制）：澄清层返工、Story 的触发询问 GWT 不成立。**验证**：GWT-2 断言 `clarify` 对 `analyze` 产出 `mode` 问项、`confirm` 按答案或默认 `deep` 落 `values`。
- **A3** 三个 L4 端口的真实现均在**组合根默认装配**、离线关卡注入确定性件（同 [`T-INT-002` A4](../../T-INT-002-M1集成关卡首次可对话.md) 的「生产装真件、关卡注入脚本」口径）：`LlmOpinionSynthesizer` / `LlmEvidenceReviewer` 经 `runtime.llm` 出网（**触发 live 子集强制项**），`MarketDimensionCatalog` 读本地 MarketDb（不出网）。**若错**（要求真件也离线可跑）：须改走本地推理端点，`LlmClient` 的端点选择面返工。**验证**：离线用例注入脚本件且断言不触网；GWT-9 的 live 用例用真端点。
- **A4** 盲点「维度 → 产出 Skill」映射来自 **L1 官方 Pack 旁的 `OFFICIAL_DIMENSIONS` 声明**（与 `OFFICIAL_PACK` 同文件、随增删同步），目录读 MarketDb 取维度**全集**后与声明 join；**未登记的维度** `skill_ids` 为空 → 对照件按既有口径记「覆盖不可判」并如实标注，**不臆断为盲点**。**若错**（要求扩 `SkillDescriptor` 契约承载）：触 [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md)（[铁律 8](../../../工程宪法.md) 序），改动面显著扩大。**验证**：GWT-7 注入含未登记维度的目录 → 盲点不入其列且 `notes` 记条数。
- **A5** `DispatchOutcome.analysis` 是**鸭子载荷**（L3 **不 import L4**，`LAYER_ORDER` 为 `l3 < l4`）：总线只断言其含 `envelope: ResultEnvelope` 且类型合法，其余字段（`view` / `result` / `traces`）由组合根的对话门面按属性取值。**若错**（要求 L3 显式认知 L4 类型）：须增跨层契约或反向依赖，违 [铁律 7](../../../工程宪法.md)。**验证**：`test_layering.py` 全绿 + 新增断言「`bus.py` 无 `st_agent.l4` import」。
- **A6** 分歧图描述件经**组合根对话门面**的 `analyze` 分支出（鸭子取 `analysis.view`），`describe_divergence_map` 自身的**零改动**复用；追问接口复用 `describe_trace`（矩阵行 `trace_id` → `analysis.traces`），**不自造**链渲染。**若错**：渲染面返工。**验证**：GWT-4 / GWT-6 端到端用例。
- **A7** 本关卡**不改** L3 渲染件、不改前端资产（`T-L4-004.2` 已交付并升真）；出站点仍走 `ui/api_description` 的同一条必填槽 + 中性化门。**若错**：出站点重复实现。**验证**：GWT-6 断言经 `build_ui` + `api_chat` 的真实往返。

## 涉及契约
- [00-架构总览 §5](../../../../docs/技术架构-v2/00-架构总览.md) 端到端数据流（**反向流的 Deliberation 支**——本关卡 GWT 的锚点）
- [01-平台共享契约 §3](../../../../docs/技术架构-v2/01-平台共享契约.md) `LensOpinion` · [§4](../../../../docs/技术架构-v2/01-平台共享契约.md) `Trace` / `ConclusionRef` · [§5](../../../../docs/技术架构-v2/01-平台共享契约.md) `ResultEnvelope` · [§6](../../../../docs/技术架构-v2/01-平台共享契约.md) 中性化校验 · [§12](../../../../docs/技术架构-v2/01-平台共享契约.md) UI 描述（`divergence_map` 已登记）
- [05-L3 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 意图分类（`analyze` 去向）· [§3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 澄清协议（**问项来源扩展**）· [§4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 派发总线接入态 · [§6–§7](../../../../docs/技术架构-v2/05-L3-对话主入口.md) Generative UI / 推理链
- [06-L4 §1–§6](../../../../docs/技术架构-v2/06-L4-多视角推理.md)（视角模型 / 编排 / 交叉对照 / Mini Debate / 分歧图 / 边界）
- [04-L2 §2.4 消费面](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)（`record_decision` 落 `history`，供 L6 消费）

## 参考
- Story（What）：[story-04](../../../../docs/PRD-v2-Agent/story-04-multi-lens.md)
- 前置关卡：[T-INT-002](../../T-INT-002-M1集成关卡首次可对话.md)
- 装配范式：[tests/integration/rig_m1.py](../../../../tests/integration/rig_m1.py)（生产组合根 + 确定性注入）

## 备注
不重复单任务单测；只测装配关系与跨层数据流。离线用例进默认 `pytest`、真实网络标 `live`。实现细节留给代码 / commit / 执行日志。
