---
id: T-L4-003
title: 交叉对照 + Mini Debate 冲突质询
story: ../../docs/PRD-v2-Agent/story-04-multi-lens.md
arch_link: "[06 §3–§4](../../../../docs/技术架构-v2/06-L4-多视角推理.md)"
priority: P0
milestone: M2
depends_on: [T-L4-002]
status: done
decisions: []
verify: 按范围 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/l4` → 506 passed（含 tests/l4 新增 30 条）；收口全量 2299 passed / 8 deselected；无 LLM 出网面（不 import l0.llm），live 子集不适用；`verify_docs.py --strict` 检查 1–9 全过；红-绿两处已验（摘 §6 门→违规说明漏出 / 摘 Mini Debate 触发条件→不该触发者也产出冲突分析）；执行日志 [T-L4-003] 2026-10-04
---

# T-L4-003 · 交叉对照 + Mini Debate 冲突质询

## 目标

交付 [06 §3 交叉对照](../../../../docs/技术架构-v2/06-L4-多视角推理.md) 与 [§4 Mini Debate](../../../../docs/技术架构-v2/06-L4-多视角推理.md) 两节的 L4 侧件：接 [`T-L4-002`](T-L4-002-多视角执行编排.md) 的 `DeliberationResult`，对其各视角 `LensOpinion` 做**算法性**比对（证据引用交集 / 差集 + 结论方向比对），产出 `DisagreementMap`（一致点 / 分歧点 / 盲点 / 证据网络）；当两视角结论方向冲突时触发 **Mini Debate**，产出结构化的**冲突原因分析**（哪条证据被 A 引用被 B 忽略 / 前提是否相关 + 一轮证据重评估）。

**范围边界（关键）**：本任务只到 **§3 数据产物 + §4 冲突质询**。分歧图**可视化**（矩阵视图 / 证据网络图渲染）与 §5–§6 边界情形归 [`T-L4-004`](T-L4-004-分歧图可视化边界与特殊情形.md)；接进 L3 总线 / 组合根属 [`T-INT-003`](T-INT-003-M2集成关卡多视角决策闭环.md)（铁律 7 禁 L3 import L4，`app.py` 层扫描用例锁 `APP_ALLOWED_IMPORTS={contracts,l0,l1,l2,l3}`）。

**架构级红线（06 §3）**：对照是**算法性比对**，**不引入额外 LLM「裁判」**——任何带裁判的实现在架构上违规。L4 **不存在「汇总结论」组件**：本任务只产出并列的对照记录，绝不合并分歧、不给统一建议（铁律 4）。

## 验收标准（Given-When-Then，从 Story 4 + 06 §3–§4 抄，限本任务范围）

- **GWT-1**（一致点 + 证据网络）Given 一次 Deliberation 结果（各视角 `LensOpinion` 齐备），When `cross_examine(result)`，Then 产出 `DisagreementMap`：`agreements` = 被 **≥2 视角引用**且这些视角**立场同向**的同一证据（逐项含证据引用 + 引用它的 `lens_id` 集）；`evidence_network` = 全部被引证据节点（按 §1 ID 类型标注 `kind`）+ `lens_id → evidence_ref` 引用边；全程**算法性集合比对**，不调用任何 LLM / 裁判。
- **GWT-2**（分歧点）Given 各视角立场不全同向，When 对照，Then `disagreements` 逐项含 `topic`（争议对象）+ 各视角立场映射（`lens_id → stance`）+ **冲突证据对**（两方各自引用而对方未引用的证据差集）；Given 全部视角立场同向，Then `disagreements` 为空（不臆造分歧）。
- **GWT-3**（盲点）Given 注入维度目录端口，When 对照，Then `blind_spots` = 目录给出的该主题相关维度中、**未被任何参与视角覆盖**者（逐项含维度键 + 中性标签）；Given **未注入**目录，Then `blind_spots` 为空且记降级步 / 显式注明（组合根按 MarketDb 表结构 + 指标字典接，归 `T-INT-003`，**不静默丢弃**）。
- **GWT-4**（Mini Debate 触发与冲突原因分析）Given 两视角 `stance` 方向冲突（`positive` vs `negative`），When 检测，Then 产出 `ConflictAnalysis`：冲突双方、**被 A 引用被 B 忽略**的证据（差集，算法性）、前提相关性判定（共享 `skill_bundle` 元素 → 相关）；Given 方向冲突但**既无证据交集也无共享 skill**，Then 不触发并记录未触发原因；Given 无方向冲突，Then 不触发。
- **GWT-5**（证据重评估端口 + 形态约束）Given Mini Debate 触发，When 对冲突证据做一轮重评估（成立 / 时效 / 权重），Then 经**注入的** `EvidenceReviewer` 端口完成、缺省用确定性件（时效按 `as_of` / 时间戳、成立 `unknown`、权重按被引用次数，**不触网**）；重评估产出与冲突原因分析**仍为结构化对照记录**（非对话体、无拟人化），逐条过 `check_output`，命中即降级。

## 接口面

**输入（逐条点名上游任务 id + 具体 API/落盘位置）**：
- [`T-L4-002`]：[`l4/deliberation.py`](../../../../src/st_agent/l4/deliberation.py) 的 `DeliberationResult`（`.opinions` / `.traces` / `.lens_ids` / `.opinions_by_lens`）与其 `LensOpinion`（[01 §3](../../../../docs/技术架构-v2/01-平台共享契约.md)）——本任务的主取数面
- [`T-L4-001`]：[`l4/roster.py`](../../../../src/st_agent/l4/roster.py) 的 `LensRoster.get(lens_id) -> Lens`（取 `.skill_bundle` / `.name`，供盲点与前提相关性判定）；鸭子类型注入
- [`T-SC-001`]：[`contracts/trace.py`](../../../../src/st_agent/contracts/trace.py) 的 `Trace` / `TraceStep` / `ConclusionRef` / `digest_of`（对照产物的链锚点）· [`contracts/result_envelope.py`](../../../../src/st_agent/contracts/result_envelope.py) 的 `ResultEnvelope` / `EvidenceRef`（01 §5）· [`contracts/neutrality.py`](../../../../src/st_agent/contracts/neutrality.py) 的 `NeutralityGuard.check_output`（01 §6 执行点 2）
- [`T-L0-001`]/[`T-L0-005`]（**间接，经端口**）：本地市场数据库表结构与 `indicator_dictionary`（[数据库设计 §05](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)）——**本任务只定端口形态**，真实读取归 `T-INT-003` 组合根（见 A2）

**输出（本任务交付的公共 API 与落盘位置）**：
- 新建 [`l4/divergence.py`](../../../../src/st_agent/l4/divergence.py)——`DisagreementMap` 模型（[06 §3](../../../../docs/技术架构-v2/06-L4-多视角推理.md) 四字段：`agreements` / `disagreements` / `blind_spots` / `evidence_network`）+ 子模型（`Agreement` / `Disagreement` / `BlindSpot` / `EvidenceNode` / `EvidenceEdge`）+ `ConflictAnalysis`（[06 §4](../../../../docs/技术架构-v2/06-L4-多视角推理.md)）；`DimensionCatalog` / `EvidenceReviewer` 两个鸭子端口 Protocol
- 新建 [`l4/crosscheck.py`](../../../../src/st_agent/l4/crosscheck.py)——`CrossExaminer` 门面：`cross_examine(result) -> DisagreementMap`（含 Mini Debate 段）
- [`l4/__init__.py`](../../../../src/st_agent/l4/__init__.py) 导出面更新
- 落盘：**不落盘**（对照产物为计算产物；`ConclusionRef(kind="divergence_map")` 仅作 Trace 锚点，落盘 / 渲染归上层）

## 可关闭的遗留

无（逐册读 L0 / L1 / L2 / L3 四册未闭区 + [阻塞与未决.md](../../../阻塞与未决.md)，无任何条目的「归属」或「解封条件」指向 `T-L4-003`；`阻塞与未决.md` 现 0 open）。

## 假设与前提

- **A1**：分歧项的 `topic` 取**本轮 Deliberation 主题**——06 §3 表内 `topic` 指争议对象，而算法性对照无法生成子话题标签（生成标签即引入 LLM 裁判，06 §3 红线），故以编排主题为值；子话题粒度留后续。**若错**（要求逐条子话题）：须引入受控的话题标注通道。**验证**：GWT-2 断言分歧项的 `topic` 等于 `result.topic`。
- **A2**：盲点的「相关维度」可枚举集经**注入的 `DimensionCatalog` 端口**给出（组合根按 MarketDb 表结构 + `indicator_dictionary` 接，归 [`T-INT-003`](T-INT-003-M2集成关卡多视角决策闭环.md)）；未注入 → `blind_spots` 为空 + 降级标注。**若错**（要求本任务即直连 `MarketDb`）：需在 L4 直接 import `l0.market`（向下依赖允许，但把 DB 连接耦合进对照件），触发返工。**验证**：GWT-3 两用例（注入目录出盲点 / 未注入降级标注）。
- **A3**：Mini Debate 的**证据重评估**经**注入的 `EvidenceReviewer` 端口**完成（真 LLM 端点在 `T-INT-003` 组合根接）；缺省用**确定性件**（时效按 `as_of` / 时间戳、成立 `unknown`、权重按被引用次数），**不触网**——同 [`T-L4-002`](T-L4-002-多视角执行编排.md) A1 的合成端口范式，**不触发 live 子集强制项**。**若错**（要求本任务即接真实 LLM 出网重评估）：须补 `-m live tests/live/test_llm_live.py` 并在执行日志验证行写明。**验证**：`l4/crosscheck.py` 不 import `st_agent.l0.llm`、不含出网调用；测试全用注入替身。
- **A4**：Mini Debate 触发条件中的「前提相关」以**两视角 `skill_bundle` 有交集**为算法代理（共享数据来源 ⇒ 前提相关）。**若错**（要求更细的前提比对口径）：须扩 `Lens.judging_criteria` 的可比结构。**验证**：GWT-4 三用例（冲突且相关→触发 / 冲突但既无证据交集也无共享 skill→不触发 / 无冲突→不触发）。
- **A5**：对照件**不接 L3 总线 / 组合根**——同 [`T-L4-002`](T-L4-002-多视角执行编排.md) A2（铁律 7 禁 L3 import L4；`test_layering.py` 锁层间依赖方向）。本任务交付自足 L4 件 + 可注入端口（catalog / reviewer）。**若错**（若判定接线归本任务）：需同步改 `app.py` 与 `test_layering.py` 的允许集。**验证**：`test_layering.py` 全绿（`l4/**` 只向下 import contracts/l0/l1/l2）。

## 涉及契约（链接到具体小节）

- [06-L4 §3 交叉对照](../../../../docs/技术架构-v2/06-L4-多视角推理.md)（`DisagreementMap` 四字段 + 算法性比对红线）· [§4 Mini Debate](../../../../docs/技术架构-v2/06-L4-多视角推理.md)（触发 / 流程 / 形态约束）
- [01-平台共享契约 §3](../../../../docs/技术架构-v2/01-平台共享契约.md)（`LensOpinion` / `Stance`）· [§4](../../../../docs/技术架构-v2/01-平台共享契约.md)（`Trace` / `ConclusionRef.kind="divergence_map"`）· [§5](../../../../docs/技术架构-v2/01-平台共享契约.md)（`ResultEnvelope` / `EvidenceRef`）· [§6](../../../../docs/技术架构-v2/01-平台共享契约.md)（中性化输出校验）
- [数据库设计 §05 指标字典](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)（盲点维度可枚举集的来源口径）
- Story：[multi-lens](../../../../docs/PRD-v2-Agent/story-04-multi-lens.md)

## 参考
- Story（What）：[multi-lens](../../../../docs/PRD-v2-Agent/story-04-multi-lens.md)

## 备注
实现细节不写此处，留给代码 / commit / 执行日志。
