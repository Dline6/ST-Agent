---
id: T-ECO-002.1
parent: T-ECO-002
title: 导入校验流水线
story: ../../docs/PRD-v2-Agent/story-10-skill-sharing.md
arch: ../../docs/技术架构-v2/09-生态与分享.md
arch_link: "[09 §3](../../docs/技术架构-v2/09-生态与分享.md)"
priority: P1
milestone: M4
depends_on: [T-ECO-001.1, T-L1-001, T-L1-002, T-L1-003, T-L1-009, T-L2-004.2, T-L4-001]
status: done
decisions: [D-080]
verify: 导入五段落地（格式校验复用容器读取面 · 依赖解析只报不装 · 权限登记 + fail-closed 批准门 · 四类按身份安装）· `.stlens` 身份保留安装面落 L4（`install_shared`，`kind` 归 `custom`；只增不改）· `.stflow`/`.stlens` 落 `execution_log/share-import/` 留痕（`.stskill`/`.stmem` 不重复记账）· 新增 32 例 · 范围 **743** / 全量 **2744** 全绿 · `verify_docs --strict` 全过；留痕见 执行日志 `[T-ECO-002.1]`
---

# T-ECO-002.1 · 导入校验流水线

## 目标
交付 [09 §3](../../docs/技术架构-v2/09-生态与分享.md) 的五段流水线——**格式校验 → 依赖解析 → 权限审核 → 用户批准 → 安装**，把一份分享文件变成已安装能力，并如实留下**来源追溯**（[09 §5](../../docs/技术架构-v2/09-生态与分享.md) 的记账面）。

两环复用既有交付，本叶**不重造**：

- **第 1 段「格式校验」**＝[`ShareContainer.from_bytes`](../../src/st_agent/eco/container.py)（[T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md)）——损坏 / 非本族 / 异主版本 / 校验和不符逐类已显式报错；本叶只把它接进流水线并转成导入面的失败形态。
- **§3.4「运行时防护」**＝[L1 执行沙箱](../../src/st_agent/l1/sandbox/sandbox.py)（[T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md)）——本叶**只核验其接线成立**（导入物装上后仍受沙箱约束），不改沙箱一行。

**未安装前不得落任何东西**：`inspect` 是**只读**动作（第 2、3 段），权限声明的登记（`declare`，初态 `pending`）是它唯一的写副作用；真正的「安装」只在用户批准后发生（第 5 段）。

## 验收标准（Given-When-Then）
- **GWT-1**：Given 一份 `.stskill` 容器（含权限声明与依赖声明），When `inspect`，Then 产出「这个 Skill 想做什么」——**权限申请列表**（逐条中性措辞 [`describe_permission`](../../src/st_agent/contracts/permissions.py) + 当前批准态）+ **依赖清单**（逐条标注本地已有 / 缺失）+ **来源追溯**（分享者 / 导入时间 / 校验和 / 出处链），且**未安装**能力
- **GWT-2**：Given `inspect` 检出缺失依赖，When 报告，Then 逐条点名缺失的 `skill_id` + 给出获取途径（官方 Pack / 外部下载，[09 §3](../../docs/技术架构-v2/09-生态与分享.md) 第 2 条），**不自动安装**依赖、**不因缺依赖而静默成功**
- **GWT-3**：Given 用户在权限审核面逐项批准了全部声明，When `install`，Then 能力入库且 `source="imported"`、`provenance` 载来源追溯；**任一权限仍为 `pending` / `rejected` 即拒装**（fail-closed，[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)）
- **GWT-4**：Given 损坏 / 非本族（备份归档）/ 异主版本 / 校验和不符的文件，When 交给导入面，Then 按类**明确报错**且**不安装**（复用容器读取面的三类报错，不吞成「导入失败」）
- **GWT-5**：Given 一份 `.stmem` 容器，When 导入，Then 经 [L2 `FragmentImporter`](../../src/st_agent/l2/memory/importer.py) 入库（`source: inferred`、幂等、`memory-import/` 留痕），且导入时间 / 校验和 / 出处链取自容器 `manifest`
- **GWT-6**：Given 已安装的导入物，When 取它的来源追溯，Then 得到完整一环（分享者 / 导入时间 / 校验和 / 出处链），与 [09 §5](../../docs/技术架构-v2/09-生态与分享.md) 的 `provenance` 形状一致

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API/落盘位置）：
  - [`ShareContainer.from_bytes`](../../src/st_agent/eco/container.py) / `.share_type` / `.payload` / `.manifest`（[T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md)）——格式校验 + 三段取材；`manifest.dependencies` / `manifest.provenance` / `manifest.checksum` 是第 2、3、6 段的输入
  - [`SkillRegistry`](../../src/st_agent/l1/skills/registry.py)（[T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md)）：`get(skill_id)`（依赖解析判「本地已有」）· `register(base, *, version, …, source="imported", provenance=…)`（安装落点，落 `config/skill-registry/<skill_id>.json`）· `list_all()`
  - [`SkillPermissionBook`](../../src/st_agent/l1/skills/permissions.py)（[T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md)）：`declare(base, permissions)`（登记，初态 `pending`，落 `config/skill-permissions/<base>.json`）· `approvals(base)` / `approved_permissions(base)` / `pending_permissions(base)`（批准门判据）
  - [`describe_permission`](../../src/st_agent/contracts/permissions.py)（[T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md)）——「想做什么」的中性措辞单一口径
  - [`WorkflowStore.save(dag)`](../../src/st_agent/l1/workflow/store.py)（[T-L1-003](done/M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md)）——`.stflow` 安装落点（落 `config/workflow/`）；依赖解析对其 `nodes[].skill_id` 去重集
  - [`FragmentImporter.import_fragment(payload, *, origin, confirmed_by="user")`](../../src/st_agent/l2/memory/importer.py) + [`ImportOrigin`](../../src/st_agent/l2/memory/models.py)（[T-L2-004.2](done/M1/T-L2-004.2-记忆片段导出隐私过滤清单确认门.md)）——`.stmem` 安装落点（落 `memory/` + `execution_log/memory-import/<import_id>.json`）
  - [`LensRoster`](../../src/st_agent/l4/roster.py) 的 `get` / **`install_shared(lens)`**（[T-L4-001](done/M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)；`install_shared` 为**本叶新增**的身份保留安装面，见 `A1`）——`.stlens` 的取面与安装面
  - 契约面：[`Provenance`](../../src/st_agent/contracts/capability_types.py)（来源追溯形状）· [`SkillSource`](../../src/st_agent/contracts/capability_types.py) 的 `"imported"` 分支（`sharer` / `imported_at` / `checksum` 必填，构造期已强制）
- **输出**（本叶交付的公共面与落点）：
  - [`src/st_agent/eco/import_pipeline.py`](../../src/st_agent/eco/)：`ShareImporter`（`inspect(blob) -> ImportPlan` / `install(plan, *, confirmed_by) -> ImportOutcome`）· `ImportPlan`（权限项 + 依赖项 + 来源追溯 + 容器）· `DependencyGap`（缺失 `skill_id` + 获取途径）· `ImportOutcome` · `ShareImportRecord`（**仅 `flow` / `lens`** 落 `execution_log/share-import/<import_id>.json` 的导入留痕——这两类本体无 `provenance` 字段，`skill` 的来源在描述体、`mem` 的在 L2 `memory-import/`，**不重复记账**）
  - [`src/st_agent/l4/roster.py`](../../src/st_agent/l4/roster.py)：新增 `LensRoster.install_shared(lens)`（身份保留的导入安装面，见 `A1`）
  - [`src/st_agent/eco/errors.py`](../../src/st_agent/eco/errors.py) 增 `ShareImportError`（导入面失败：未批准 / 已存在 / 缺分享者 / 安装面缺失——与 `ShareFormatError` 分工：后者是「文件坏了」，前者是「文件是好的但这次导入不成立」）
  - 用例 [`tests/eco/test_import_pipeline.py`](../../tests/eco/) + [`tests/l4/`](../../tests/l4/) 的 `install_shared` 用例
  - **落盘位置**：**不新造数据分区**——`config/skill-registry/` · `config/skill-permissions/` · `config/workflow/` · `config/lens-roster/` · `memory/` · `execution_log/memory-import/`（各归属层既有落点）+ `execution_log/share-import/`（本叶新增，仅 flow / lens 的导入留痕，与 L2 `memory-import/` 同族）

## 可关闭的遗留
- 无（[L0 册 `A1b`](../遗留问题/L0-遗留问题.md) 归父任务下的 [`T-ECO-002.2`](T-ECO-002.2-官方Skill索引与生态边界.md)；逐册读 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) 未闭区，无「归属＝本叶」或「解封条件＝本叶 `done`」者）

## 假设与前提
- **A1 · 四类落点皆有「按分享方标识原样落库」的公开写面，`.stlens` 需本批补齐**——前提（2026-10-05 读码核实 + ④ 对齐拍定）：`SkillRegistry.register`（由 `skill_id` 拆 base/version）· `WorkflowStore.save`（保留 `flow_id`）· `FragmentImporter.import_fragment`（保留 `memory_node_id`）三者皆原样落库；[`LensRoster`](../../src/st_agent/l4/roster.py) 只有 `add_custom`（用 `LensId.generate()` **重新生成** `lens_id`）⇒ **本批给 L4 补一个身份保留的安装面**（`LensRoster.install_shared(lens)`：保留 `lens_id`、强制 `kind="custom"`（导入物不是官方预置，且须可删）、命名过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)、`skill_bundle` 存在性经 `SkillRegistry` 解析、同 id 已存在即拒），只增不改既有行为。若错：返工面＝该安装面。验证方式：`.stlens` 的「导入 → 取出 → 逐字段比对（除 `kind` 按本口径归为 `custom`）」用例；⑥ 因触及 L4 需并跑 `tests/l4`。
- **A2 · 依赖解析只在本地 Skill 库内判，不递归解析**——前提：[09 §3](../../docs/技术架构-v2/09-生态与分享.md) 第 2 条要求「对照 manifest 依赖声明检查本地 Skill 库」，未要求拉取传递依赖。若错（要求解析到二跳 / 自动补装）：返工面＝依赖解析步。验证方式：缺失依赖只报一层、且**不自动安装**的用例。
- **A3 · 权限审批沿用 `base` 键，故同一 base 的新版本导入不重批**——前提：[`SkillPermissionBook`](../../src/st_agent/l1/skills/permissions.py) 的键为 `base`（批准随能力跨版本存活）。若错：返工面＝审批键口径（动已 `done` 的 [T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md)）。验证方式：先装 v1 批准、再导 v2 时审批态仍在的用例。
- **A4 · 导入物经共享落点入库，只新增一条审计前缀**——前提：[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的注册表与各层既有分区已覆盖；列表面用既有 `list_all()` 按 `source=="imported"` 过滤；`flow` / `lens` 的来源留痕复用 `execution_log` 分区（02 §6 的 append-only 审计语义），只新增 `share-import/` 前缀，**不新增分区**。若错（要求来源随本体走）：返工面＝`Lens` / `WorkflowDAG` 的模型字段（契约级）。验证方式：用例断言导入只写既有分区、不产生新分区；`execution_log` 下新前缀仅 `share-import/` 一条。

## 涉及契约
- [09 §3 导入校验流水线](../../docs/技术架构-v2/09-生态与分享.md)——本叶的 What/How 来源（五段与「不安装」的两处硬约束）
- [09 §5 来源追溯链](../../docs/技术架构-v2/09-生态与分享.md)（GWT-6 的答案面）
- [01 §10 权限声明模型](../../docs/技术架构-v2/01-平台共享契约.md)（逐项批准 / fail-closed）· [01 §9 版本化规范](../../docs/技术架构-v2/01-平台共享契约.md)（依赖声明的 `skill_id` 版本语义）· [01 §1 标识体系](../../docs/技术架构-v2/01-平台共享契约.md)（**不新增 ID 类**）
- [03 §1.5 执行沙箱](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（§3.4 运行时防护，本叶只核验）
- [04 §8](../../docs/技术架构-v2/04-L2-记忆图谱.md)（`.stmem` 收件侧，L2 侧已交付）
- [00 §6 失败显式化](../../docs/技术架构-v2/00-架构总览.md)（损坏 / 未批准 / 缺依赖一律显式报错）

## 参考
- Story（What）：[skill-sharing](../../docs/PRD-v2-Agent/story-10-skill-sharing.md)（GWT「导入他人分享的 `.stskill`」/「依赖缺失提示获取途径」/「查看导入 Skill 的来源」/ 边缘「格式损坏不安装」）
- 决策：[D-043](../决策日志.md)（四类同构不按类型拆）· [D-079](../决策日志.md)（阶段拆法先例）
- 上游交付方／取材面：[T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md) · [T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) · [T-L1-003](done/M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md) · [T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md) · [T-L2-004.2](done/M1/T-L2-004.2-记忆片段导出隐私过滤清单确认门.md) · [T-L4-001](done/M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)
- 同批：[`T-ECO-002.2`](T-ECO-002.2-官方Skill索引与生态边界.md)（父任务 [`T-ECO-002`](T-ECO-002-导入校验流水线官方Skill索引生态边界.md) 的另一叶）

## 备注
本叶只做流水线本体；索引导航与生态边界归 `.2`。实现细节留给代码 / commit / 执行日志。
