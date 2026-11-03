---
id: T-AGT-030
parent: null
title: 子协议动作化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-029.1]
status: done
decisions: [D-154, D-160]
verify:
---

# T-AGT-030 · 子协议动作化

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-030.1`](T-AGT-030.1-配置与记忆子协议.md) / [`T-AGT-030.2`](T-AGT-030.2-分析与训练子协议.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把五个业务协议变成循环可调用的动作。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面

**输入**

- L3 既有协议面：`ConfigDraftProtocol`（`l3/config/draft.py`）、`ConflictAdjudicator`（`l3/conflict/adjudication.py`）；`DispatchBus` 的 `deliberations` / `trainings` 鸭子端口（L4 `AnalyzeService` / L6 `TrainingProtocol` 由组合根注入，L3 不 import L4 / L6）。
- `describe_draft` / `describe_adjudication` / `describe_divergence_map` / `describe_trace`（`l3/render/describe.py`）与循环当前 `Trace`。
- 循环拦截点 `run_agent_loop`（`l3/runtime/loop.py`）、组合根 `_build_l3` / `_AgentInvestigator` / `build_m5_runtime`。
- 工具目录 `install_builtins` / `BUILTIN_PRIMITIVES`（`l1/builtin/install.py`）与风险分级清单（`l6/authorization.py`）。

**输出**

- 五个内置描述体：`sk_configure` / `sk_memory_op` / `sk_analyze` / `sk_train` / `sk_explain`（模型可见面；直接调用回 `unavailable` + 点名循环）。
- L3 适配面 `src/st_agent/l3/runtime/subprotocols.py`（`SubprotocolPorts` + `handle(...)`）。
- 循环：`run_agent_loop(..., subprotocols=...)`、终止原因 `card_ready`、`LoopOutcome.cards`；门面 `InvestigationOutcome.cards` → 响应 `description`（助手轮 payload 持久化）。
- 风险清单五条 `autonomous-ok`；契约 05 §3.1 / §5 / §7 / §9 / §10 与 01 §7 同批更新。

## 可关闭的遗留
无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3`/`C5`、L5 余 `A1` 皆**人决**，L1–L3 与 L6 无未闭项；无归属本任务的条目，检查 8 = 0）。

## 假设与前提
- **A1**：五个动作均无副作用（只产卡 / 读卡 / 产未确认复述），显式 `autonomous-ok`；写入仍走既有确认路径。若错 → 闸门拦下、动作不可达。验证：清单断言 + 各 GWT 用例。
- **A2**：`card_ready` 为新终止原因，不可续跑；卡片经响应 `description` 交还。若错 → 改映射与门面。
- **A3**：动作按控制动作拦截执行，不产 `skill_run` / Trace 步；运行表以终止原因 + 说明留痕，卡本体在助手轮 payload。若错 → 改 L1 真执行并补留痕字段。
- **A4**：`sk_configure` 采用循环合成面（草稿卡即确认面），不走派发前的「已确认意图卡」前置。若错 → 循环内先补一张意图确认卡。
- **A5**：`sk_memory_op` 只列 / 取待裁决卡，不新开提案写入；`sk_train` 不终止、不新增训练卡组件；`sk_explain` 只展开本次 run 的当前链。若任一与人的预期不符 → 对应叶子补适配面（见各叶假设）。
