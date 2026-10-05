---
id: T-ECO-002
parent: null
title: 导入校验流水线 + 官方 Skill 索引 + 生态边界
story: ../../docs/PRD-v2-Agent/story-10-skill-sharing.md
arch: ../../docs/技术架构-v2/09-生态与分享.md
arch_link: "[09 §3·§4·§6](../../docs/技术架构-v2/09-生态与分享.md)"
priority: P1
milestone: M4
depends_on: [T-L1-002, T-ECO-001.1, T-L1-001, T-L1-003, T-L1-009, T-L2-004.2, T-L4-001, T-L0-004]
status: done
decisions: [D-080]
verify: 两叶齐备（`.1` 导入校验流水线 + `.2` 官方索引与生态边界）· 批次验收兑现：五段流水线（含 fail-closed 批准门与四类身份保留安装）· `index_browse` 接入网关（**销 L0 册 `A1b`**）· 生态边界与空状态 · 交接点复用（格式校验 / `.stmem` 收件侧 / 权限账本 / 运行时防护四环不重造）· 范围 **743** / 全量 **2744** 全绿 · `verify_docs --strict` 全过；留痕见 执行日志 `[T-ECO-002]`
---

# T-ECO-002 · 导入校验流水线 + 官方 Skill 索引 + 生态边界

## 目标
补上本地分享的**收件侧**：交付 [09 §3](../../docs/技术架构-v2/09-生态与分享.md) 的**导入校验流水线**（格式校验 → 依赖解析 → 权限审核 → 用户批准 → 安装）、[09 §4](../../docs/技术架构-v2/09-生态与分享.md) 的**只读官方 Skill 索引**，以及 [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的**生态边界**落点。

**已由上游交付、本批只消费**（2026-10-05 读码核实）：

- **格式校验这一环已现成**——[`ShareContainer.from_bytes`](../../src/st_agent/eco/container.py)（[T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md)）已按「完整性 / 版本先于载荷结构」的次序做完三段齐备 → 主版本 → 校验和 → 载荷解析，损坏 / 非本族 / 异主版本逐类显式报错。[09 §3](../../docs/技术架构-v2/09-生态与分享.md) 第 1 条因此**不重造**。
- **`.stmem` 的收件侧已现成**——[`FragmentImporter.import_fragment`](../../src/st_agent/l2/memory/importer.py)（[T-L2-004.2](done/M1/T-L2-004.2-记忆片段导出隐私过滤清单确认门.md)）已实现隐私复核、`source: inferred` 改写、幂等跳过与 `memory-import/` 留痕。
- **权限「声明 → 逐项批准」的账本已现成**——[`SkillPermissionBook`](../../src/st_agent/l1/skills/permissions.py)（[T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md)）与展示措辞 [`describe_permission`](../../src/st_agent/contracts/permissions.py)；L3 的 [`CapabilityApprovalPanel`](../../src/st_agent/l3/approval/panel.py)（[T-L3-006](done/M2/T-L3-006-能力安装与导入审批面.md)）是它的双通道表现面。
- **运行时防护（§3.4）已现成**——[L1 执行沙箱](../../src/st_agent/l1/sandbox/sandbox.py)（[T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md)）的越界拦截 + `BehaviorViolation` 事件；本批**只核验其接线成立**，不改沙箱。

缺的是中间三段（**依赖解析 / 权限审核 / 安装**）与索引、边界两面。

**拆分**（命中[拆分触发](../工作流.md)两条：验收 GWT>5 · 跨多个契约小节与层）——父转为分组节点，交付由两叶承接：

- [`T-ECO-002.1`](T-ECO-002.1-导入校验流水线.md)——**导入校验流水线**（09 §3）：容器读回 → 依赖解析（对本地 Skill 库）→ 权限审核（登记 + 展示「这个 Skill 想做什么」）→ 用户批准 → 安装（四类各自的落点）+ 来源追溯
- [`T-ECO-002.2`](T-ECO-002.2-官方Skill索引与生态边界.md)——**官方 Skill 索引与生态边界**（09 §4 · §6）：只读索引条目与浏览面 · 索引浏览经 L0 网关 `kind=index_browse` 留痕（销 L0 册 `A1b`）· 生态边界「不做」清单 + 空状态判据

不按分享物类型拆的理由同 [D-043](../决策日志.md)（同构的流水线拆开会让四类各写一份）；本批按**阶段**拆，与 [D-079](../决策日志.md) 对 [T-ECO-001](T-ECO-001-分享物类型格式导出流程来源追溯链.md) 的拆法同构。

## 验收标准（Given-When-Then）
- Given 用户导入他人分享的 `.stskill` 文件，When 校验，Then 显示「这个 Skill 想做什么」（权限申请 + 依赖列表 + 来源追溯）+ 用户批准后安装
- Given 导入的 Skill 依赖用户没有的其他 Skill，When 检测，Then 明确提示缺失依赖 + 提供获取途径（官方 Pack / 外部下载），**不自动安装**
- Given 用户浏览官方 Skill 索引，When 查看，Then 显示官方 Pack 更新 + 认证社区 Skill 推荐（含描述 + 外部下载地址 + 校验和）
- Given 用户查看某个导入 Skill 的来源，When 打开详情，Then 显示完整追溯（分享者、时间、校验和、导入历史）
- 边缘：导入文件格式损坏 → 明确报错 + 不安装
- 空状态：新用户未导入任何第三方 Skill → 显示「你的 Skill 库目前只有官方 Pack」+ 引导浏览索引
- 运行时（**核验既有交付，不重造**）：导入的 Skill 试图读取超出声明范围的本地文件 → 拦截 + 「这个 Skill 行为异常」警示 + 禁用选项

## 接口面
- **输入**（父级汇总；逐叶列于各自 `## 接口面`）：
  - [T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md) 的 [`ShareContainer.from_bytes`](../../src/st_agent/eco/container.py)（格式校验这一环）
  - [T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) 的 [`SkillRegistry`](../../src/st_agent/l1/skills/registry.py)（`get` / `get_latest` / `register` / `list_all` / `pending_updates`）与 [`SkillDescriptor`](../../src/st_agent/contracts/capability_types.py)
  - [T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md) 的 [`SkillPermissionBook`](../../src/st_agent/l1/skills/permissions.py)（`declare` / `approve` / `reject` / `approvals`）与 [`describe_permission`](../../src/st_agent/contracts/permissions.py)
  - [T-L1-003](done/M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md) 的 [`WorkflowStore.save`](../../src/st_agent/l1/workflow/store.py) · [T-L4-001](done/M2/T-L4-001-视角模型Lens常设阵容用户可增删.md) 的 [`LensRoster`](../../src/st_agent/l4/roster.py) · [T-L2-004.2](done/M1/T-L2-004.2-记忆片段导出隐私过滤清单确认门.md) 的 [`FragmentImporter`](../../src/st_agent/l2/memory/importer.py)
  - [T-L0-004](done/M0/T-L0-004-出网审计网关离线能力分级.md) 的 [`EgressGateway`](../../src/st_agent/l0/net/gateway.py)（`execute` / `query`；`kind=index_browse` 已在其合法类目内）
  - 契约面：[`Provenance`](../../src/st_agent/contracts/capability_types.py) · [`SemVer`](../../src/st_agent/contracts/registry_types.py) · [01 §10 权限模型](../../docs/技术架构-v2/01-平台共享契约.md) · [01 §9 版本化](../../docs/技术架构-v2/01-平台共享契约.md)
- **输出**（逐叶列于各自 `## 接口面`；父级汇总）：
  - [`src/st_agent/eco/import_pipeline.py`](../../src/st_agent/eco/)（导入校验流水线）· `src/st_agent/eco/index.py`（官方索引与生态边界）
  - 用例 `tests/eco/test_import_pipeline.py` · `tests/eco/test_index.py`
  - **落盘位置**：导入物落各自归属层的既有分区（`config/skill-registry/` · `config/workflow/` · `config/lens-roster/` · `memory/` + `execution_log/memory-import/`），**不新造分区**；索引资源不落用户数据分区
  - **文档面**：[09 §3/§4/§6](../../docs/技术架构-v2/09-生态与分享.md) 为口径来源；若实现暴露缺口按[铁律 8](../工程宪法.md) 先改文档再改码
- **降级路径**：格式校验与 `.stmem` 收件侧**不重造**（复用既有交付）；运行时防护**不重造**（核验 L1 沙箱已接线）

## 可关闭的遗留
- [L0 册 `A1b`](../遗留问题/L0-遗留问题.md)（官方 Skill 索引浏览的审计调用点未接入，`kind=index_browse`）→ **本批关闭**：归属 = [`T-ECO-002.2`](T-ECO-002.2-官方Skill索引与生态边界.md)（索引浏览接入网关）。
- 其余逐册读 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) 未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」者（[L0 册 `A5`](../遗留问题/L0-遗留问题.md) 归 L5 升级链、[L0 册 `C5`](../遗留问题/L0-遗留问题.md) 等归**人决**）。

## 假设与前提
- **A1 · 导入的落点皆为各层既有公开写面**——前提：`SkillRegistry.register` / `WorkflowStore.save` / `FragmentImporter.import_fragment` / `LensRoster` 足以承载四类的收件侧。**`.stlens` 的面本批补齐**——[`LensRoster`](../../src/st_agent/l4/roster.py) 只有 `add_custom`（重新生成 `lens_id`），2026-10-05 ④ 对齐拍定本批给 L4 补一个身份保留的安装面（`.1` 交付，只增不改）。若错：返工面＝对应类的安装适配器（流水线形态不变）。验证方式：四类各自的「导入 → 取出 → 逐字段比对」用例（`.stlens` 的 `kind` 按 `.1` `A1` 口径归为 `custom`）。
- **A2 · 权限审批沿用 base 键（批准随能力跨版本）**——前提：[`SkillPermissionBook`](../../src/st_agent/l1/skills/permissions.py) 的键是 `base`，故同一 base 的次版本升级不必重批。若错（要求逐版本重批）：返工面＝审批键口径（动已 `done` 的 [T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md)）。验证方式：审批态跨版本存活的用例。
- **A3 · 官方索引取「经 L0 网关拉取」的载体**（2026-10-05 ④ 对齐拍定）——[09 §4](../../docs/技术架构-v2/09-生态与分享.md) 原措辞含糊，本批拍定后已把索取通道写进该节（[铁律 8](../工程宪法.md)，改码之前），`index_browse` 由此有唯一调用点、[L0 册 `A1b`](../遗留问题/L0-遗留问题.md) 得以关闭。若改判本地载体：返工面＝`.2` 的浏览面与 `A1b` 的处置。验证方式：`.2` GWT-1 的审计断言。
- **A4 · 导入不新增契约 ID 类**——前提：[01 §1](../../docs/技术架构-v2/01-平台共享契约.md) 的 ID 类目已覆盖 skill_id / flow_id / lens_id / memory_node_id，来源追溯沿用 [`Provenance`](../../src/st_agent/contracts/capability_types.py)。若错：返工面＝[01 §1](../../docs/技术架构-v2/01-平台共享契约.md) 先行（铁律 8）。验证方式：实现面 grep 无新 ID 类 + 断链校验。

## 涉及契约
- [09 §3 导入校验流水线 / §4 官方 Skill 索引 / §6 生态边界](../../docs/技术架构-v2/09-生态与分享.md)——本任务的 What/How 来源
- [01 §10 权限声明模型](../../docs/技术架构-v2/01-平台共享契约.md)（逐项批准）· [01 §9 版本化规范](../../docs/技术架构-v2/01-平台共享契约.md) · [01 §1 标识体系](../../docs/技术架构-v2/01-平台共享契约.md)（**不新增 ID 类**）
- [02 §6 出网审计网关](../../docs/技术架构-v2/02-L0-本地优先基座.md)（`index_browse` 类目与留痕）
- [03 §1.5 执行沙箱](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（§3.4 运行时防护的承载方，本批只核验）
- [04 §8](../../docs/技术架构-v2/04-L2-记忆图谱.md)（`.stmem` 收件侧，L2 侧已交付）

## 参考
- Story（What）：[skill-sharing](../../docs/PRD-v2-Agent/story-10-skill-sharing.md)（In Scope「导入校验」/「依赖解析」/「官方 Skill 索引」/「导入来源追溯」；Out of Scope 中央商店 / 付费 / 自动更新订阅）
- 决策：[D-043](../决策日志.md)（四类同构不按类型拆）· [D-044](../决策日志.md)（ECO 提 P1 + 层位置于 L4 之后）· [D-079](../决策日志.md)（[T-ECO-001](T-ECO-001-分享物类型格式导出流程来源追溯链.md) 的阶段拆法，本批同构）
- 上游交付方／取材面：[T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md) · [T-L0-004](done/M0/T-L0-004-出网审计网关离线能力分级.md) · [T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) · [T-L1-003](done/M0/T-L1-003-工作流模型WorkflowDAG复合SkillS.md) · [T-L1-009](done/M0/T-L1-009-Skill侧权限批准落点.md) · [T-L2-004.2](done/M1/T-L2-004.2-记忆片段导出隐私过滤清单确认门.md) · [T-L4-001](done/M2/T-L4-001-视角模型Lens常设阵容用户可增删.md)
- 下游：[T-INT-005](T-INT-005-M4集成关卡反思演进与生态闭环.md)（端到端复核「导出 → 导入校验」闭环）

## 备注
父任务只承载分组与批次验收口径；交付与逐条 GWT 在两叶。实现细节留给代码 / commit / 执行日志。
