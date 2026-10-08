---
id: T-AGT-004.2
parent: T-AGT-004
title: 循环驱动、协议端口与上界
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M5
depends_on: [T-AGT-004.1, T-AGT-001]
status: done
decisions: [D-090, D-091]
verify: 新用例 24 例（GWT-1..7 + 闸门三态 + 端点能力 + 目录外调用 + 指针真实存在；执行面一律真 SkillRunner + 真 Store + 真官方 Pack）· 范围 1003 passed（217.34s）· 全量 3740 passed / 0 failed / 14 deselected（964.37s；基线 3654 核账 +86 逐项归因无残差）· verify_docs --strict 检查 1–9 全过 0 断链 · 未触发联网面（自愿补跑 live 2 passed 作旁证）· 见执行日志 [T-AGT-004]
---

# T-AGT-004.2 · 循环驱动、协议端口与上界

## 目标

立起循环的**心脏**——四段一轮的驱动，含可替换的协议端口与两道上界：

1. **协议端口**（[D-090](../决策日志.md) D-a）：把「工具调用协议」抽成**可替换端口**——本叶交 **T1（原生 function calling）** 的实现，接 [`T-AGT-001`](T-AGT-001-LLM工具调用通道.md) 的 L0 通道；T2（`response_format`）/ T3（prompt 约定 JSON）日后作为**同接口的另两个实现**补入，**循环本体不改**。
2. **四段驱动**（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)）：出动作 → 过闸门 → 执行 → 回填，循环直至「模型给出结束」或触界。
3. **双上界**（[D-090](../决策日志.md) ⑥）：**步数**与 **LLM 调用次数**双上界，**到界即终止并如实标注**，**不假装完成**。
4. **闸门为注入端口**：每步执行前经**注入的授权端口**；**缺省不注入即 fail-closed（全部不放行）**——本叶**不**实现 T-3 闸门本体（那是 [`T-AGT-005`](T-AGT-005-动作闸门接入.md)），但**假定它存在**并接住。
5. **执行经 L1 既有流水线**：调 [`SkillRunner.run`](../../src/st_agent/l1/runner/runner.py)——参数 / 输入 / 输出核验、依赖 DAG、沙箱、留痕**全部沿用**，**不绕过任何一环**。
6. **决策不入 Trace 步**（[01 §4](../../docs/技术架构-v2/01-平台共享契约.md)）：模型选步**不产步骤**，动作由 `SkillRunner` 自然追加 `skill_run` 步。

## 验收标准（Given-When-Then）

- **GWT-1 · 多步闭环**：Given 一个 Fake 端点先要求调 A、看到 A 结果后要求调 B、再给出结束，When 驱动，Then 两步**都被执行**且顺序正确（**看结果再决定下一步**是本能力的核心）。
- **GWT-2 · 决策不入步、动作入步**：Given 上述跑完，When 取 Trace，Then 步数为 **2 条 `skill_run`**（各对应一次执行），**无**「模型决策」类步骤、**无**为决策新造的型。
- **GWT-3 · 缺省闸门 fail-closed**：Given **未注入**授权端口，When 驱动，Then **一步都不执行**，循环以「闸门缺失」显式结束（**不静默放行**）。
- **GWT-4 · 双上界到界即停**：Given 模型永不给出结束，When 驱动，Then 在**步数**或**LLM 调用次数**先到者处终止，且终止原因**如实标注为「达到上界」**。
- **GWT-5 · 跑在上界的边界上**：Given 恰好在第 N 步给出结束（N ＝ 上界），When 驱动，Then **正常完成**（边界不误判为触界）。
- **GWT-6 · 不绕过 L1 流水线**：Given 一次调用参数越界 / 无批准权限，When 驱动，Then 得 L1 的 `validation_failed` 信封（**由既有流水线判**，循环不自行判参数）。
- **GWT-7 · 协议端口可替换**：Given 换一个同接口的协议实现（Fake），When 驱动，Then 循环行为不变（**只换端口，不动本体**）。

## 接口面

- **输入**：
  - [`.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md) 的工作上下文形态与工具目录装配件。
  - [`T-AGT-001`](T-AGT-001-LLM工具调用通道.md) 的 `tools` 入参与工具调用出参。
  - [`SkillRunner.run`](../../src/st_agent/l1/runner/runner.py)（执行面；含 `approved_permissions` 与 `initiator` / `purpose` 口径）。
  - 契约口径：[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) · [01 §4 步骤口径](../../docs/技术架构-v2/01-平台共享契约.md)。
- **输出**：
  - 新增 [`src/st_agent/l3/runtime/protocol.py`](../../src/st_agent/l3/runtime/protocol.py)：`ToolCallProtocol`（端口接口：`tool_calls_supported()` / `invoke_turn(context, *, initiator, purpose)`）、`TurnResult`（文本 / 工具调用 / 用量 / 端点侧失败）、**T1** 实现 `NativeToolCallProtocol`（薄包 `LlmClient.invoke(tools=…)`，目录经 `tools` 入参、不并进 prompt）。
  - 新增 [`src/st_agent/l3/runtime/loop.py`](../../src/st_agent/l3/runtime/loop.py)：`run_agent_loop()`（四段驱动）、`LoopOutcome`、`TerminationReason`（十种终止原因）、`GateDecision` / `GateVerdict`、**上界参数** `max_steps` / `max_llm_calls`（**带默认值、可注入**，不做模块常量）。
  - **供 [`T-AGT-005`](T-AGT-005-动作闸门接入.md) 注入的授权端口名与调用点**：`:data:`AGENT_GATE_METHOD`＝`"authorize"`，调用点＝每个待执行动作**执行之前**，以 `gate.authorize(skill_id=…, tool_name=…, arguments=…)` 问一次；返回 `GateDecision`（或 verdict 字符串）方才执行，**缺省不注入即 fail-closed**。
  - 供 [`.3`](T-AGT-004.3-产出接入、留痕与失败语义.md) 消费的循环结果形态即 `LoopOutcome`（`termination` / `reason` / `steps` / `llm_calls` / `trace` / `answer` / `pending` / `gate_reason` / 生效上界 / `failure`）。
  - 测试：`tests/l3/test_agent_loop.py`（GWT-1..7 + 闸门三态 + 端点能力 + 目录外调用 + 指针真实存在 + 非法入参；执行面一律**真** `SkillRunner` + 真 `Store`）。

## 可关闭的遗留

- 无（2026-10-07 开工逐册读未闭区复核，同 [`.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md) 所记：全册 `T-AGT` / 循环 / 工具调用**零命中**，无归属本任务的条目）

## 假设与前提

- **A1 · 授权端口缺省 fail-closed（全不放行）**——前提：[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 要求每步过闸门，而 [D-090](../决策日志.md) 的 T-3 闸门由 [`T-AGT-005`](T-AGT-005-动作闸门接入.md) 交付；若本叶缺省放行，两叶之间的**任何中间态**都会变成「无闸门执行」。
  **若错的影响**：默认放行 = 在闸门落地前先有了一个能自主跑工具的循环——**安全边界倒置**。
  **验证方式**：GWT-3 以「未注入即一步不执行」钉死；`tests/l3` 的该用例是**不可放宽的红线用例**。
- **A2 · 上界取「步数 + LLM 调用次数」两条**——前提：[D-090](../决策日志.md) ⑥ 拍定；两条都直接可观测。**「上界取值」本身属实现口径**（可调），契约只要求存在且触界如实标注。
  **若错的影响**：若 ④ 对齐判定还要 token / wall-clock 上界，返工面 = 上界判定加一维。
  **验证方式**：GWT-4 / GWT-5 钉「触界即停」与「边界不误判」；取值在 ④ 对齐时定 —— **已定**（负责人 2026-10-07）：两条取值做成 `run_agent_loop` 的**注入参数**（`max_steps` 缺省 8 / `max_llm_calls` 缺省 12，调用上界有意高于步数以免剪掉收尾那一问），**不做模块常量**。**端用户级可调**（01 §7 配置项 / 05 §3.2 意图参数）与 [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 现行口径「上界是系统预算、非用户可调项」相抵，**本批未做**，登记见 [L3 册](../遗留问题/L3-遗留问题.md)。
- **A3 · 循环不自行判参数合法性**——前提：参数 / 输入 / 输出核验均由 L1 执行面判（[03 §1.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 的流水线），循环只搬运与呈现。
  **若错的影响**：若循环自判，会与 L1 出现**两套参数判定**，且 L1 的 `validation_failed` 违规点被吞成循环自己的失败。
  **验证方式**：GWT-6 以「由 L1 判」钉住（断言拿到的是 L1 的信封）。
- **A4 · 并行工具调用不在本期**——前提：[D-090](../决策日志.md) 冻结范围明列不做。
  **若错的影响**：顺序驱动在长链上较慢；改并行须动驱动结构与 Trace 步序。
  **验证方式**：本叶只实现顺序驱动；GWT-1 的步序断言即其形态声明。

## 涉及契约

- [05 §10 自主查证循环](../../docs/技术架构-v2/05-L3-对话主入口.md)（四段循环 / 上界与终止）
- [01 §4 Trace 的步骤口径](../../docs/技术架构-v2/01-平台共享契约.md)
- [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md)（每步失败走信封）
- [01 §10 权限声明模型](../../docs/技术架构-v2/01-平台共享契约.md)（执行面逐项核对）

## 参考

- [D-090](../决策日志.md)（D-a 协议端口 / D-c 双上界 / M2）· [D-091](../决策日志.md)（契约落点 ③）
- 调研：[Agent 自主能力调研报告](../../docs/Agent自主能力调研报告.md) §8.3 M2 / §8.7
- 上游：[`.1`](T-AGT-004.1-工具目录消费与循环工作上下文装配.md) · [`T-AGT-001`](T-AGT-001-LLM工具调用通道.md)
- 下游：[`T-AGT-005`](T-AGT-005-动作闸门接入.md) · [`.3`](T-AGT-004.3-产出接入、留痕与失败语义.md)
