---
id: T-AGT-030.2
parent: T-AGT-030
title: 分析与训练子协议
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-029.1]
status: done
decisions: [D-154, D-160]
verify: 执行日志 [T-AGT-030.2]——analyze / train / explain 三动作接入循环（分歧图卡 / 训练会话回灌 / 当前链卡片），与 .1 同批收口；范围 1461 passed、定向 90 passed、verify_docs --strict PASS、dev 面 trace_timeline 渲染正常；实现 commit 83072c7
---

# T-AGT-030.2 · 分析与训练子协议

## 目标
`analyze` / `train` / `explain` 变为循环可调动作，分别触发 L4 分歧图、L6 训练协议、Trace 展开。

## 验收标准（Given-When-Then）
- GWT-1：Given 复杂决策，Then 触发 L4 并出分歧图。
- GWT-2：Given 训练修正，Then 走 L6 协议。
- GWT-3：Given 追问推理，Then 展开对应 Trace。

## 接口面

**输入**

- `DispatchBus` 的 `deliberations` / `trainings` 端口（`src/st_agent/l3/dispatch/bus.py`；L4 的 `AnalyzeService` 由 M2 组合根注入、L6 的 `TrainingProtocol` 由 M4 组合根注入，L3 不 import L4 / L6）。
- `describe_divergence_map` / `describe_trace`（`src/st_agent/l3/render/describe.py`）：分歧图 / 推理链 → `divergence_map` / `trace_timeline` 描述件。
- 循环当前的 `Trace`（`run_agent_loop` 的 `chain`）：`sk_explain` 展开**本次运行**的链。
- `_AgentInvestigator`（`src/st_agent/composition/agent.py`）与 `build_m5_runtime`（`src/st_agent/composition/runtime/m5.py`）：端口注入点。
- `install_builtins` / `BUILTIN_PRIMITIVES`（`src/st_agent/l1/builtin/install.py`）与风险分级清单（`src/st_agent/l6/authorization.py`）。

**输出**

- **工具目录**：新增内置描述体 `sk_analyze` / `sk_train` / `sk_explain`；直接调用回 `unavailable` + 点名循环。
- **L3 适配面**：`src/st_agent/l3/runtime/subprotocols.py` 的三个分支——`sk_analyze` → `deliberations.analyze(...)` + `describe_divergence_map`；`sk_train` → `trainings.start(...)`；`sk_explain` → `describe_trace(chain)`。
- **循环**：`sk_analyze` / `sk_explain` 成功产卡即复用 `T-AGT-030.1` 的 `card_ready` 终止与 `LoopOutcome.cards`；`sk_train` 返回训练会话数据（`TrainingSession`）作为普通动作结果，循环可继续（不落账、不产卡）。
- **门面 / 表现层**：与 `.1` 同一条 `description` 通路；`analyze` 的分歧图直接走既有 `divergence_map` 渲染件，`explain` 走 `trace_timeline`。
- **风险清单**：三条动作显式登记为 `autonomous-ok`（`train.start` 只产复述、不落账）。
- **契约**：[05 §3.1](../../docs/技术架构-v2/05-L3-对话主入口.md) 子协议动作表、[§7](../../docs/技术架构-v2/05-L3-对话主入口.md) Trace 展开、[§10](../../docs/技术架构-v2/05-L3-对话主入口.md) 动作与 `card_ready`；[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 白名单键空间补三条动作标识。

## 可关闭的遗留
无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3`/`C5`、L5 余 `A1` 皆**人决**，L1–L3 与 L6 无未闭项；无归属本任务的条目，检查 8 = 0）。

## 假设与前提
- **A1**：三条动作**无副作用**（`analyze` 只跑读取型视角链、`train.start` 只产未确认复述、`explain` 只读链），故显式登记 `autonomous-ok`；训练落账仍只经 `confirm` + `record`。若错 → 闸门拦下、动作不可达，须改风险类或走待确认续跑。验证：清单断言 + GWT 用例。
- **A2**：`sk_explain` 展开**本次 run 的当前链**；跨 run 的旧链追问不在本叶范围（留痕只存 `skill_run_id`，L3 不持 Trace 存储）。若人裁要求按 `agent_run_id` 回看旧链 → 需新增 Trace 重建 / 解析端口。验证：GWT-3 用例只断言当前链。
- **A3**：`sk_train` 返回 `TrainingSession` 数据并**不终止**循环，也不新增训练卡组件；用户对复述的确认是下一轮的事，落账路径不变。若人裁要求卡片交还 → 需新增 `training` 描述件 / 组件（01 §12 契约先行）。验证：GWT-2 用例断言返回 `ok` + 会话字段，且不写盘。
- **A4**：L3 仍不 import L4 / L6，一律经鸭子端口；端口由 M5 组合根在 `build_m4_runtime` 之后绑定到 `_AgentInvestigator`（M1–M4 未装配循环时三条动作显式不可用）。若错 → 会破坏铁律 7 或出现两套装配。验证：`tests/test_layering.py` + 集成用例。
- **A5**：`analyze` 的分歧图随动作结果交还，**不重复**携带各视角 Trace；需要追问时由下一步 `sk_explain` 或既有 `analyze` 派发面承接。若人裁要求同卡带链 → 复用 `InvestigationOutcome` 的 `traces` 字段扩展。验证：GWT-1 用例断言分歧图描述件存在。
