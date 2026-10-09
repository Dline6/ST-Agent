---
id: T-INT-006
parent: null
title: M5 集成关卡 · 受控自主闭环
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M5
depends_on: [T-AGT-001, T-AGT-002, T-AGT-003, T-AGT-004.1, T-AGT-004.2, T-AGT-004.3, T-AGT-005.1, T-AGT-005.2, T-AGT-006, T-AGT-007, T-L6-004.1, T-L6-004.2, T-UI-005.1, T-UI-005.2, T-INT-005]
status: todo
decisions: [D-090, D-091]
verify:
---

# T-INT-006 · M5 集成关卡 · 受控自主闭环

## 目标

把 M5 分散在各任务里的交付**装配起来跑一次端到端**——[`T-AGT-001`](T-AGT-001-LLM工具调用通道.md) 的通道、[`T-AGT-002`](T-AGT-002-端点能力启动探测.md) 的能力档、[`T-AGT-003`](T-AGT-003-工具目录读面.md) 的目录、[`T-AGT-004`](T-AGT-004-循环运行时.md) 三叶的循环、[`T-AGT-005`](T-AGT-005-动作闸门接入.md) 的闸门、[`T-AGT-006`](T-AGT-006-第七类意图investigate与派发去向接线.md) 的意图与去向——接进 [`app.py`](../../src/st_agent/app.py) 的组合根，并在**生产入口** [__main__.py](../../src/st_agent/__main__.py) 生效。

单任务的 GWT 全是注入 Fake 的单测；**装配关系与跨层数据流**（L3 循环 → L1 执行 → L0 通道 → L6 授权 → L3 渲染）恰是它们集体覆盖不到的部分。

## 验收标准（Given-When-Then）

锚点取 [00 §5 端到端数据流](../../docs/技术架构-v2/00-架构总览.md) 与 [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 的九段契约，**不重复**单任务已覆盖的单元行为。

- **GWT-1 · 端到端多步自主查证**：Given 装配好的 M5 运行时 + 一个 Fake 端点（先要求调 A、见结果后要求调 B、再结束），When 用户走一次 `investigate` 全流程（意图 → 澄清 → 确认 → 派发），Then 产出为 `ok` 信封，两步**都真的经 L1 执行**，且链上**恰好两条 `skill_run` 步**。
- **GWT-2 · 闸门真的在链上**：Given 运行期档为 `collaborative`（缺省），When 同一次派发，Then **一步不执行**、产出显式说明下一步需确认；Given 档切到 `autonomous` 且动作落 `autonomous-ok` 范围，Then 执行。
- **GWT-3 · 跨层端口未反转**：Given 装配后的组合根，When 扫 import，Then `l3/runtime/**` **无** `st_agent.l6` import、`l3/**` **无** `st_agent.l4` import（`test_layering.py` 全绿），L6 授权面经**鸭子端口**注入。
- **GWT-4 · 能力档来自探测而非硬编码**：Given 端点在启动时被判为**不支持工具调用**，When 派发 `investigate`，Then `unavailable` + 显式降级告知；When 端点为支持，Then 正常跑——**两次断言取的是探测结果，不是 `LLM_CAPABILITY` 的缺省**。
- **GWT-5 · 留痕端到端可查**：Given GWT-1 的一次循环，When 取留痕，Then 可得 `agent_run_id` 的**步数 / 终止原因 / 上界**，并可经 `explain` 去向逐步展开完整 Trace。
- **GWT-6 · 既有链路不回归**：Given 装配后的运行时，When 跑 M0–M4 的既有端到端用例，Then 全绿——**既有六类意图与 L0–L6 数据流逐字节不变**。
- **GWT-7 · 生产入口生效**：Given [__main__.py](../../src/st_agent/__main__.py) 的默认组合根，When 启动，Then 探测已执行、循环端口已注入（**不是只在测试装配里成立**）。

## 接口面

- **输入**：M5 全部叶子的交付面（见 `depends_on`）+ [`T-INT-005`](done/M4/T-INT-005-M4集成关卡反思演进与生态闭环.md) 交付的 `M4Runtime`（组合根的既有形态）。
- **输出**：
  - [`app.py`](../../src/st_agent/app.py) 的 **M5 组合根**（循环装配 + 授权面注入 + 探测步骤接线；同 `_build_l4` / `_build_l6` 的私有栈构造器范式）。
  - [`__main__.py`](../../src/st_agent/__main__.py) 换默认组合根。
  - 新增 `tests/integration/rig_m5.py` + `tests/integration/test_m5_autonomy.py`。
  - `tests/live/test_llm_tools_live.py`（真端点下的工具调用链路；见备注）。

## 可关闭的遗留

- 无（开工 ④ 对齐时逐册读各遗留册未闭区复核）

## 假设与前提

- **A1 · 端到端用 Fake 端点而非依赖真实云端**——前提：既有集成关卡的范式（`rig_m3.py` / `rig_m4.py` 注入替身）保证**离线可重复**；真链路另由 live 子集覆盖。
  **若错的影响**：若端到端必须打真端点，用例会**不稳定且不可离线跑**，CI 也无法覆盖。
  **验证方式**：GWT-1..6 全部离线成立；GWT-7 只断言「装配已发生」，不打真端点。
- **A2 · `APP_ALLOWED_IMPORTS` 不需扩充**——前提：M5 的源码落在 `src/st_agent/l0` / `l1` / `l3` / `l6`，**均在既有允许集内**；`T-AGT-*` 是任务前缀组，**不对应新的源码层**。
  **若错的影响**：若真需要新源码层，须动 [`tests/test_layering.py`](../../tests/test_layering.py) 与层表——**那是架构级返工**，本任务开工时须先复核这一条。
  **验证方式**：`test_layering.py` 不改即绿（GWT-3）。
- **A3 · live 子集的触发面已扩大**——前提：[工作流](../工作流.md) §测试分层列的联网面触发点是 `src/st_agent/l0/llm/**` / `l0/net/gateway.py` / `l1/runtime.py` 的 LLM 装配段；M5 恰好**全落在**这三处（通道 + 探测 + 装配）。
  **若错的影响**：若漏跑 live，**CI 绿灯不覆盖真实链路**（这正是该条存在的原因）。
  **验证方式**：收工的验证行必须含 live 子集结果；本机无端点而 skip 时**写明 skip 与原因**。

## 涉及契约

- [00 §5 端到端数据流](../../docs/技术架构-v2/00-架构总览.md) · [00 §1.1 进程形态](../../docs/技术架构-v2/00-架构总览.md)
- [05 §10 自主查证循环](../../docs/技术架构-v2/05-L3-对话主入口.md) · [05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md)
- [02 §4 LLM 调用抽象](../../docs/技术架构-v2/02-L0-本地优先基座.md) · [01 §5](../../docs/技术架构-v2/01-平台共享契约.md) · [01 §7](../../docs/技术架构-v2/01-平台共享契约.md)
- [铁律 7](../../项目管理/工程宪法.md)（跨层经鸭子端口）· [铁律 10](../../项目管理/工程宪法.md)（无留痕不关闭）

## 参考

- [D-090](../决策日志.md)（十项决策与冻结范围）· [D-091](../决策日志.md)（契约先行）
- 先例：[`T-INT-003`](done/M2/T-INT-003-M2集成关卡多视角决策闭环.md) · [`T-INT-004`](done/M3/T-INT-004-M3集成关卡主动触达投递闭环.md) · [`T-INT-005`](done/M4/T-INT-005-M4集成关卡反思演进与生态闭环.md)
- 调研：[Agent 自主能力调研报告](../../docs/Agent自主能力调研报告.md) §11.2

## 备注

**live 子集**：本关卡动的正是 [工作流](../工作流.md) §测试分层点名的**真实 LLM 出网面**（`l0/llm/**` + `l1/runtime.py` 的 LLM 装配段），故收工时除跨层套件外**必须**跑 `python -m pytest -m live tests/live/test_llm_live.py`（及本任务新增的工具调用 live 用例），结果写进执行日志的**验证**行——本机无可用端点而 skip 时，把 skip 与原因一并写明。CI 恒排除 `live`，**绿灯不覆盖真实链路**。
