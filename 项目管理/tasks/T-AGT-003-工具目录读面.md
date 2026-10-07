---
id: T-AGT-003
parent: null
title: 工具目录读面（SkillDescriptor 到工具条目的投影）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P0
milestone: M5
depends_on: []
status: done
decisions: [D-090, D-091, D-095]
verify: 范围 1491 passed（375.77s）· 全量 3654 passed / 0 failed / 14 deselected（831.08s）· 新用例 52 例（GWT-1..6 + 跨层字母表守卫）· verify_docs --strict 检查 1–9 全过 0 断链 · 未触发联网面 ⇒ 不跑 live · 见执行日志 [T-AGT-003]
---

# T-AGT-003 · 工具目录读面（SkillDescriptor 到工具条目的投影）

## 目标

按 [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 的**唯一来源**口径，给 L1 加一个**工具目录读面**：把 `SkillDescriptor` 投影成**模型可见的工具条目**，供循环（[`T-AGT-004.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md)）取用。

1. **目录读面**：列出当前可调用的 Skill 及其**工具条目**（工具名 / 描述 / 参数面）。既有的 [`SkillRunner.match`](../../src/st_agent/l1/runner/runner.py) 只做查询文本匹配，**无列举面**——本任务补它。
2. **工具名取 base**：`skill_id` 的**版本剥离**身份（取点同 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的作用域族），使同 base 的版本升级映射到**同一个工具名**。
3. **参数面派生**：由 `parameters`（[`ParameterSpec`](../../src/st_agent/contracts/capability_types.py)）与 `input_schema`（[01 §2 方言](../../docs/技术架构-v2/01-平台共享契约.md)）派生——**不另造第二套参数词汇**。
4. **来源无差别**：`source` 为 `mcp-mapped` 的映射能力与官方 / 自建 / 导入**同路**，一律经描述体。
5. **不新增能力登记**：本面是**既有注册表的投影**，不是第二份清单。

## 验收标准（Given-When-Then）

- **GWT-1 · 目录即注册表**：Given 注册表内有 N 个 Skill，When 取工具目录，Then 得 N 个条目，**不多不少**（不遗漏、不臆造）。
- **GWT-2 · 工具名是 base**：Given 同 base 的两个版本（如 `sk_x` 的 v1.0 与 v1.1），When 投影，Then 二者得**同一工具名**。
- **GWT-3 · 参数面从描述体派生**：Given 一个带 `parameters` 与 `input_schema` 的描述体，When 投影，Then 条目含可读的参数名 / 类型 / 默认值 / 取值域，且 **`ParameterSpec` 的约束（`min_value` / `max_value` / `choices`）不丢**。
- **GWT-4 · MCP 映射来源同路**：Given 一个 `source="mcp-mapped"` 的 Skill，When 投影，Then 与官方来源**同形**产出（无特例分支）。
- **GWT-5 · 不吞失败**：Given 描述体缺失 / 形态坏，When 取目录，Then **显式失败**（信封 + 违规点），**不留半个条目、不静默跳过**。
- **GWT-6 · 纯投影、无副作用**：Given 取目录，When 比对注册表状态，Then 注册表**逐字节不变**（本面只读）。

## 接口面

- **输入**：
  - [`SkillRegistry.list_all()`](../../src/st_agent/l1/skills/registry.py)（既有，`T-L1-001.1` 交付）→ `tuple[SkillDescriptor, ...]`；描述体落 `config` 分区 `skill-registry/<skill_id>.json`。
  - [`SkillDescriptor`](../../src/st_agent/contracts/capability_types.py) / [`ParameterSpec`](../../src/st_agent/contracts/capability_types.py)（既有契约类型，字段集见 [01 §2](../../docs/技术架构-v2/01-平台共享契约.md)）。
  - [`SkillRegistry`](../../src/st_agent/l1/skills/registry.py) 的 `get_latest(base)` / `list_versions(base)`（既有）——base 折叠时的取版入口。
  - 官方 Pack 播种面（[`T-L1-013`](done/M2/T-L1-013-官方Pack泛化为类型化官方资源容器.md) 已交付）→ [`src/st_agent/l1/skills/pack.py`](../../src/st_agent/l1/skills/pack.py) 的 `ensure_official_pack`，12 个 base；用户自建与 MCP 映射的派生描述体经同一注册表落盘（`T-L1-002.2`），**本面不区分来源**。
  - 契约口径：[01 §2 唯一来源 + 版本折叠口径](../../docs/技术架构-v2/01-平台共享契约.md) · [01 §9 base 语义](../../docs/技术架构-v2/01-平台共享契约.md) · [03 §1 工具目录读面](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)。
- **输出**：
  - 新增 [`src/st_agent/l1/skills/tool_catalog.py`](../../src/st_agent/l1/skills/tool_catalog.py)：`ToolEntry`（frozen：`name`＝base / `skill_id`＝执行目标 / `description` / `parameters`（object 根 JSON Schema，可直喂 L0 `ToolSpec`）+ `project_tool_entry()` / `project_catalog()` 两个纯函数。
  - [`SkillRegistry.tool_catalog()`](../../src/st_agent/l1/skills/registry.py)：薄读面（委托 `list_all()` 后按 base 折叠），经 [`st_agent.l1.skills`](../../src/st_agent/l1/skills/__init__.py) 导出。
  - 供 [`T-AGT-004.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md) 消费的**条目形态＝`ToolEntry` 四字段 + 「同 base 折叠为一条、执行目标取最高版本」**——**它是跨任务交接点，形态一旦定下即不宜轻改**（[D-095](../决策日志.md) ④）。
  - 测试：`tests/l1/test_tool_catalog.py`（GWT-1..6 + 一条跨层字母表守卫）。

## 可关闭的遗留

- 无（2026-10-07 开工逐册读未闭区复核：[L0 册](../遗留问题/L0-遗留问题.md) 余 `C3` / `C5` 皆**人决**、[L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) / [L5](../遗留问题/L5-遗留问题.md) 册各 **0 未闭**、[L6 册](../遗留问题/L6-遗留问题.md) 余 `B1`（主动提案落地点→Studio 草稿面，待人立项，与本任务不同面）——**无归属本任务的条目**；全册 grep `工具目录` / `工具条目` / `T-AGT` 零命中）

## 假设与前提

- **A1 · 工具目录住 `SkillRegistry` 侧而非 `SkillRunner`**——前提：目录是**描述体**的投影（注册表本职），`SkillRunner` 是**执行面**；住在 runner 上会让执行器承担"呈现能力清单"的职责。
  **若错的影响**：调用方取目录要经执行器，执行器与呈现耦合；返工面 = 投影件搬家与调用点改口。
  **验证方式**：GWT-6 以「只读、注册表不变」钉住纯投影性；④ 对齐时确认落点（**已确认**：纯函数模块 `l1/skills/tool_catalog.py` + 注册表薄方法 `tool_catalog()`——[D-095](../决策日志.md) ③）。
- **A2 · 参数面只投影 `parameters` 与 `input_schema`，不含 `output_schema`**——前提：工具调用的**入参**才由模型填；出参由执行器返回、模型直接看结果。把 `output_schema` 塞进工具条目会无谓膨胀上下文（[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 说暴露**哪些字段**属实现口径）。
  **若错的影响**：条目偏大，长目录下挤占窗口；返工面 = 投影件字段集。
  **验证方式**：GWT-3 只钉参数面；字段集的取舍留 ④ 对齐确认（**已确认**：`output_schema` 不进条目，且条目**不带 `source` 字段**——[D-095](../决策日志.md) ④）。
- **A3 · 本条不含「工具检索 / 延迟加载」**——前提：[D-090](../决策日志.md) 冻结范围明列不做；官方 12 个 Skill 的量级不需要。
  **若错的影响**：MCP 大量挂载后目录可能挤爆窗口——那时才需按需检索。
  **验证方式**：本任务**不交付**检索面；若 ④ 对齐判定必要，须回 [D-090](../决策日志.md) 复议范围（**已确认不交付**）。
- **A4 · 工具面按 base 折叠、执行目标取最高版本**——前提：循环的**调用面是能力**而非版本（[01 §2 版本折叠口径](../../docs/技术架构-v2/01-平台共享契约.md)；能力身份＝base），且自主调用**没有**引用方的锁定语义（`version_policy=locked` 是既有引用方 —— 工作流 / 视角 —— 的语义，循环不产生引用）。
  **若错的影响**：若循环须按锁定版本调用，返工面 = 条目增版本选择面（`version_policy` / 可指定版本）与执行解析改口，且模型可见面会重新出现同名工具。
  **验证方式**：GWT-2 钉住「同 base 两版本 ⇒ 同一条目 / 同一工具名」；条目**不暴露版本号**（无版本选择面），GWT-1 按**能力数**计条目数。

## 涉及契约

- [01 §2 SkillDescriptor](../../docs/技术架构-v2/01-平台共享契约.md)（工具暴露面的唯一来源）
- [01 §9 版本化规范](../../docs/技术架构-v2/01-平台共享契约.md)（base 取点）
- [03 §1–§2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（Skill 注册与描述体）
- [铁律 7 层间只向下依赖](../../项目管理/工程宪法.md)（本面只读自家注册表，不向上取）

## 参考

- [D-090](../决策日志.md)（D-b / 冻结范围）· [D-091](../决策日志.md)（契约落点 ②）
- 调研：[Agent 自主能力调研报告](../../docs/Agent自主能力调研报告.md) §8.3 M1
- 上游：[`T-L1-001`](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) · [`T-L1-013`](done/M2/T-L1-013-官方Pack泛化为类型化官方资源容器.md)
- 下游：[`T-AGT-004.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md)
