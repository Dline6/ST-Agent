---
id: T-ECO-001
parent: null
title: 分享物类型/格式 + 导出流程 + 来源追溯链
story: ../../docs/PRD-v2-Agent/story-10-skill-sharing.md
arch: ../../docs/技术架构-v2/09-生态与分享.md
arch_link: "[09 §1·§2·§5](../../../../docs/技术架构-v2/09-生态与分享.md)"
priority: P1
milestone: M4
depends_on: [T-L1-001, T-L1-003, T-L2-001, T-L4-001]
status: done
decisions: [D-043, D-079]
verify: 两叶齐备（`.1` 容器与格式 + `.2` 导出流程与卡片）· 批次验收兑现：四类分享物**同构三段容器**（明文可检视 · 与备份归档互不通用 · 格式版本按主版本判读）· `.stmem` **强制三步**（过滤 → 清单 → 用户确认）· **出处链**追加且历史不覆盖 · 分享卡片中性且不出网 · 新层 `eco` 已接入 `ci.yml` 与工作流范围映射（并按改动集模拟过范围判定）· 销 L0 册 `A4`（`export_all_safe` 保留不删）· 新增 69 例（容器 37 + 导出 32）· 范围 **561** / 全量 **2697**（复跑）全绿 · `verify_docs --strict` 检查 1–9 全过；留痕见 执行日志 `[T-ECO-001]`
---

# T-ECO-001 · 分享物类型/格式 + 导出流程 + 来源追溯链

## 目标
在本地优先约束下（[story-05](../../../../docs/PRD-v2-Agent/story-05-local-first.md)，不做中央商店服务端）把分享落成**本地文件**：交付 [09 §1](../../../../docs/技术架构-v2/09-生态与分享.md) 的**四类分享物统一容器**（`.stskill` / `.stflow` / `.stlens` / `.stmem`）、[09 §2](../../../../docs/技术架构-v2/09-生态与分享.md) 的**导出流程**（含 `.stmem` 的强制三步），以及 [09 §5](../../../../docs/技术架构-v2/09-生态与分享.md) 的**来源追溯链**。

四类分享物的**本体**均已由上游交付（2026-10-05 核实）：`.stskill` ← [`SkillDescriptor`](../../../../src/st_agent/contracts/capability_types.py)（[T-L1-001](../M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md)）· `.stflow` ← [`WorkflowDAG`](../../../../src/st_agent/l1/workflow/models.py)（[T-L1-003](../M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md)）· `.stlens` ← [`Lens`](../../../../src/st_agent/l4/lens.py)（[T-L4-001](../M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)）· `.stmem` ← [`FragmentPayload`](../../../../src/st_agent/l2/memory/sharing.py)（[T-L2-001](../M1/T-L2-001-节点边模型读写接口MemoryReaderWri.md)）。**缺的只有容器与导出面**——[`sharing.py`](../../../../src/st_agent/l2/memory/sharing.py) 的模块 docstring 已显式把容器留给本任务（「四类分享物同构的容器须待四类 payload 齐备才定稿」）。

**拆分**（命中[拆分触发](../../../工作流.md)两条：验收 GWT>5 · 跨多个契约小节与层）——父转为分组节点，交付由两叶承接：

- [`T-ECO-001.1`](T-ECO-001.1-四类分享物统一容器与格式.md)——**容器与格式**：`header` / `payload` / `manifest` 三段同构 · kind 与扩展名映射 · 格式版本（01 §9）· 校验和口径与复核 · 读取面（损坏显式报错）· 与备份归档容器的互不通用
- [`T-ECO-001.2`](T-ECO-001.2-四类导出流程与分享卡片.md)——**导出流程**：四类取材（`SkillRegistry` / `WorkflowStore` / `LensRoster` / `MemoryShare`）· `.stmem` 强制三步确认门 · 命名与描述的中性化拦截 · 出处链追加 · 分享卡片 · `save_to(path)`

[D-043](../../../决策日志.md) 否决的是「按分享物类型拆」（拆开会让四类同构容器在缺两个 payload 时定稿），本批按**阶段**拆，不触动该结论。

## 验收标准（Given-When-Then）
- Given 用户在 Studio 中创建了自建 Skill，When 点击导出，Then 生成 `.stskill` 文件（含完整定义 + 元数据 + 依赖声明 + 校验和），可复制到任何位置
- Given 自建工作流 / 自定义视角，When 导出，Then 同构产出 `.stflow` / `.stlens`——**一套**容器代码，不是四份实现
- Given 用户导出 Memory 片段，When 生成文件，Then 强制过滤私有 / 敏感节点 + 显示「这次导出包含以下公开信息」清单 + 用户确认后才生成
- Given 命名 / 描述含拟人化措辞，When 导出，Then 导出前拦截并点名（01 §6），不产文件
- Given 导入的分享物被再次导出，When 生成容器，Then 出处链**追加**一环且历史不覆盖（[09 §5](../../../../docs/技术架构-v2/09-生态与分享.md) 的多次转手）
- Given 非本族容器（备份归档）或损坏文件，When 交给读取面，Then 明确报错，**不**静默当作空容器

## 接口面
- **输入**（父级汇总；逐叶列于各自 `## 接口面`）：
  - [`T-L1-001`](../M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) 的 [`SkillDescriptor`](../../../../src/st_agent/contracts/capability_types.py)（`.stskill` 本体；11 组字段 + `provenance`）与 [`SkillRegistry`](../../../../src/st_agent/l1/skills/registry.py) 的 `get` / `get_latest` / `list_all`
  - [`T-L1-003`](../M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md) 的 [`WorkflowDAG`](../../../../src/st_agent/l1/workflow/models.py) 与 [`WorkflowStore`](../../../../src/st_agent/l1/workflow/store.py) 的 `get` / `get_latest` / `list_all`
  - [`T-L4-001`](../M2/T-L4-001-视角模型Lens常设阵容用户可增删.md) 的 [`Lens`](../../../../src/st_agent/l4/lens.py) 与 [`LensRoster`](../../../../src/st_agent/l4/roster.py) 的 `get` / `list_all`
  - [`T-L2-001`](../M1/T-L2-001-节点边模型读写接口MemoryReaderWri.md) 的 [`MemoryShare`](../../../../src/st_agent/l2/memory/sharing.py) 的 `plan()` / `export(confirmed_by="user")`
  - 契约面：[`Provenance`](../../../../src/st_agent/contracts/capability_types.py)（作者声明 / 出处链四字段）· [`SemVer`](../../../../src/st_agent/contracts/registry_types.py)（01 §9 版本语义）· [`NeutralityGuard.check_name`](../../../../src/st_agent/contracts/neutrality.py)（01 §6）
- **输出**（逐叶列于各自 `## 接口面`；父级汇总）：
  - 新层 `src/st_agent/eco/**`（[`tests/test_layering.py`](../../../../tests/test_layering.py) 已预留 `eco` 为最上层）：容器模型与四类导出入口
  - **落盘位置**：**无本机数据落点**——导出物是用户自持的明文副本，经 `save_to(path)` 写到用户显式指定的位置，**不进** `Store` 任何分区；本任务不改任何既有分区布局
  - **文档面**：[09 §1/§2/§5](../../../../docs/技术架构-v2/09-生态与分享.md) 为口径来源；若实现暴露缺口按[铁律 8](../../../工程宪法.md) 先改文档再改码（**预计不改** [01](../../../../docs/技术架构-v2/01-平台共享契约.md)——不新增契约 ID，`Provenance` / `SemVer` 已够用）

## 可关闭的遗留
- [L0 册 `A4`](../../../遗留问题/L0-遗留问题.md)（`export_all_safe` 无调用方，归宿＝分享物导出）→ **本批关闭**：口径定为**分享物不携带凭据安全视图**（分享物是明文可检视的对外文件，掩码视图 + 使用记录对受赠方无用且泄露使用模式；受赠方须自配自己的凭据），故 `export_all_safe` **保留不删**（不动已 `done` 的 [T-L0-002](../M0/T-L0-002-密钥与凭据子系统掩码显示使用记录.md) 交付面）。2026-10-05 开工 ④ 经负责人拍定（[D-079](../../../决策日志.md)）。
- 其余逐册读 [L0](../../../遗留问题/L0-遗留问题.md) / [L1](../../../遗留问题/L1-遗留问题.md) / [L2](../../../遗留问题/L2-遗留问题.md) / [L3](../../../遗留问题/L3-遗留问题.md) 未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」者（`A1b` 归 [T-ECO-002](T-ECO-002-导入校验流水线官方Skill索引生态边界.md)、[L3 册 `A2`](../../../遗留问题/L3-遗留问题.md) 归 [T-L5-002](../M3/T-L5-002-渠道适配器投递编排与升级链.md)）。

## 假设与前提
- **A1 · 四类本体都是可 JSON 往返的 pydantic 模型**——前提：2026-10-05 读码核实，`SkillDescriptor` / `WorkflowDAG` / `Lens` / `FragmentPayload` 皆为 frozen `BaseModel`，且 `FragmentPayload` 用判别联合 `AnyNode` 保子类字段（[`sharing.py`](../../../../src/st_agent/l2/memory/sharing.py) 已注明）。若错（某类本体含非 JSON 可表达字段或丢弃子类字段）：返工面＝该类的 payload 适配件。验证方式：四类各自的「打包 → 读取」往返用例逐字段比对。
- **A2 · 导出不落 `Store`**——前提：分享物是**用户自持的明文副本**（「可复制到任何位置」），与 [02 §8.1](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的备份归档（加密容器、含私有敏感数据）是两条互不通用的路径；落 `Store` 会把明文塞进加密库并造出第二份真相源。若错（产品要求导出物留在应用内可管理）：返工面＝新增分区与清空口径，属契约级变更。验证方式：用例断言导出路径**不写**任何 `Store` 分区（存根计数为 0）+ `save_to(path)` 只写用户给的那个路径。
- **A3 · 分享物不携带凭据安全视图**——前提：09 §1 的 `manifest` 字段表未列凭据，且 [09 §1](../../../../docs/技术架构-v2/09-生态与分享.md) 明言分享文件「明文可检视」；凭据面属「永不进导出物」一路（[02 §3](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。若错（产品要求随包带凭据声明）：返工面＝`manifest` 字段与 L0 册 `A4` 的处置。验证方式：`manifest` 无凭据字段的构造期断言 + L0 册 `A4` 的关闭留痕。
- **A4 · 校验和覆盖容器载荷**——前提：L2 的 [`ImportOrigin.checksum`](../../../../src/st_agent/l2/memory/models.py) 文档口径是「分享**文件**校验和（sha256 十六进制 64 位，09 §1 manifest）」，故本任务交付的校验和即导入侧待核对的那个数，两侧口径必须同一份实现才能互认。若错（两侧算法分叉）：返工面＝导入侧（[T-ECO-002](T-ECO-002-导入校验流水线官方Skill索引生态边界.md)）的校验步骤。验证方式：同字节重算一致 + 单字节改动即失败的用例；并与 L2 的 64 位十六进制形态对齐。
- **A5 · 中性化拦截只施于命名与描述**——前提：[09 §2](../../../../docs/技术架构-v2/09-生态与分享.md) 的措辞是「命名与描述过 01 §6 中性化校验」；`.stmem` 的节点内容属**数据展示**、不过输出校验（[D-053](../../../决策日志.md) 已定口径），故本批不对记忆节点文本施加校验。若错（要求记忆内容也过校验）：返工面＝`.stmem` 导出路径。验证方式：三类对象各自的拦截用例 + `.stmem` 的「节点原文原样导出」用例。

## 涉及契约
- [09 §1 分享物类型与文件格式 / §2 导出流程 / §5 来源追溯链](../../../../docs/技术架构-v2/09-生态与分享.md)——本任务的 What/How 来源
- [01 §6 中性化](../../../../docs/技术架构-v2/01-平台共享契约.md)（导出前拦截）· [01 §9 版本化规范](../../../../docs/技术架构-v2/01-平台共享契约.md)（`header` 的格式版本）· [01 §1 标识体系](../../../../docs/技术架构-v2/01-平台共享契约.md)（**不新增 ID 类**）
- [02 §8.1 备份归档](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)（与分享容器**同族但互不通用**的对侧）
- [04 §8](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)（`.stmem` 的隐私过滤与确认门，L2 侧已交付）

## 参考
- Story（What）：[skill-sharing](../../../../docs/PRD-v2-Agent/story-10-skill-sharing.md)（In Scope「导出格式规范」/「隐私分级」/「导入来源追溯」/「分享链接生成」）
- 决策：[D-043](../../../决策日志.md)（四类同构容器留在一条任务内——本批按阶段拆，不触动）· [D-044](../../../决策日志.md)（ECO 提 P1 + 层位置于 L4 之后）· [D-047](../../../决策日志.md)（新层落地即接入 `ci.yml` 与工作流映射）· [D-079](../../../决策日志.md)（本批拆分与 `A4` 处置）
- 上游交付方／取材面：[T-L1-001](../M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) · [T-L1-003](../M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md) · [T-L2-001](../M1/T-L2-001-节点边模型读写接口MemoryReaderWri.md) · [T-L4-001](../M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)
- 下游：[T-ECO-002](T-ECO-002-导入校验流水线官方Skill索引生态边界.md)（消费本容器做导入校验）· [T-INT-005](T-INT-005-M4集成关卡反思演进与生态闭环.md)（端到端复核）

## 备注
父任务只承载分组与批次验收口径；交付与逐条 GWT 在两叶。实现细节留给代码 / commit / 执行日志。
