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
status: todo
decisions: [D-090, D-091]
verify:
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
  - [`SkillRegistry`](../../src/st_agent/l1/skills/registry.py)（`get` / `list_all` 与描述体形态）· [`SkillDescriptor`](../../src/st_agent/l1/skills/registry.py)（[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 的字段集）· [`ParameterSpec`](../../src/st_agent/contracts/capability_types.py)。
  - [`T-L1-013`](done/M2/T-L1-013-官方Pack泛化为类型化官方资源容器.md) 的官方 Pack 播种面（目录应含官方 12 个 Skill + 用户自建 + MCP 映射）。
  - 契约口径：[01 §2](../../docs/技术架构-v2/01-平台共享契约.md)（唯一来源）· [01 §9 版本化规范](../../docs/技术架构-v2/01-平台共享契约.md)（base 语义）。
- **输出**：
  - `src/st_agent/l1/**` 的工具目录读面（含 base 取点与参数面派生）。
  - 供 [`T-AGT-004.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md) 消费的**条目形态**——**它是跨任务交接点，形态一旦定下即不宜轻改**。
  - 测试：`tests/l1` 的目录用例（GWT-1..6）。

## 可关闭的遗留

- 无（开工 ④ 对齐时逐册读各遗留册未闭区复核；本任务为 M5 新立，无既有归属条目）

## 假设与前提

- **A1 · 工具目录住 `SkillRegistry` 侧而非 `SkillRunner`**——前提：目录是**描述体**的投影（注册表本职），`SkillRunner` 是**执行面**；住在 runner 上会让执行器承担"呈现能力清单"的职责。
  **若错的影响**：调用方取目录要经执行器，执行器与呈现耦合；返工面 = 投影件搬家与调用点改口。
  **验证方式**：GWT-6 以「只读、注册表不变」钉住纯投影性；④ 对齐时确认落点。
- **A2 · 参数面只投影 `parameters` 与 `input_schema`，不含 `output_schema`**——前提：工具调用的**入参**才由模型填；出参由执行器返回、模型直接看结果。把 `output_schema` 塞进工具条目会无谓膨胀上下文（[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 说暴露**哪些字段**属实现口径）。
  **若错的影响**：条目偏大，长目录下挤占窗口；返工面 = 投影件字段集。
  **验证方式**：GWT-3 只钉参数面；字段集的取舍留 ④ 对齐确认。
- **A3 · 本条不含「工具检索 / 延迟加载」**——前提：[D-090](../决策日志.md) 冻结范围明列不做；官方 12 个 Skill 的量级不需要。
  **若错的影响**：MCP 大量挂载后目录可能挤爆窗口——那时才需按需检索。
  **验证方式**：本任务**不交付**检索面；若 ④ 对齐判定必要，须回 [D-090](../决策日志.md) 复议范围。

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
