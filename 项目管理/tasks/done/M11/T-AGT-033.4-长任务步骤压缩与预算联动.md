---
id: T-AGT-033.4
parent: T-AGT-033
title: 长任务步骤压缩与预算联动
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-004.2, T-AGT-031.2, T-AGT-033.1, T-AGT-033.3]
status: done
decisions: [D-090, D-154, D-166]
verify: D-166；范围套件 4386 passed / 4 例测试期望滞后（已修正并定向复验）/ 2 例本机 test_shell 环境性 ERROR；改动区复跑 655 passed；live 3 passed；verify_docs --strict PASS。详见执行日志对应条目；commit `00f11a5`。
---

# T-AGT-033.4 · 长任务步骤压缩与预算联动

## 目标

给“步骤永不丢弃”补上配套机制：近期步骤保留全文，更早步骤压成**证据摘要**（工具、参数、结论、引用），使步骤体量不再线性撑爆上下文；`max_steps` / `max_llm_calls` 与上下文信封由同一套总 token/时间预算推导；`over_budget` 从“只标一个 flag”变为“压缩→压缩后仍超则显式 `context_overflow`（带续跑）”；去掉“建议 32 步 / 32000 字符”这类互相矛盾的数字。

## 验收标准（Given-When-Then）

- GWT-1：Given 23 个真实步骤，Then 上下文在信封内装下（近期全文 + 更早摘要），不需要把历史/记忆全部丢掉；Given 32 步，Then 不因目录+步骤自然超限而无声失败。
- GWT-2：Given 超过信封，Then 先压缩旧步骤（保留工具/参数/结论/引用），再考虑终止；压缩后仍超则返回显式 `context_overflow` 并给出续跑入口，不落到提供方报错。
- GWT-3：Given 总 token / 总时间 / 总费用预算，Then 运行在耗尽前停止并如实标注；不再只靠单次 60s 超时兜底。
- GWT-4：Given 用户设置 `max_steps` / `context_budget`，Then 界面或预检提示两者与信封的关系；不出现“1000 步 + 16000 字符”这类必然超限的组合。
- GWT-5：Given 步骤压缩，Then 留痕仍指向原始 `skill_run_id` / `log_ref`，摘要不冒充完整结果，不造第二真相源。
- GWT-6：Given 两次相同输入，Then 压缩结果确定（同一顺序、同一摘要规则），可单测。

## 接口面

- **输入**：
  - `_fit_budget` / `WorkStep` / `TruncatedResult`（[`context.py`](../../../../src/st_agent/l3/runtime/context.py)）；
  - `run_agent_loop` 的装配与终止（[`loop.py`](../../../../src/st_agent/l3/runtime/loop.py)）；
  - `LoopBounds`（[`bounds.py`](../../../../src/st_agent/l3/runtime/bounds.py)）· `AgentRunRecord`（[`report.py`](../../../../src/st_agent/l3/runtime/report.py)）；
  - 设置页预算条目（[`ui/surfaces/settings/shared.py`](../../../../src/st_agent/ui/surfaces/settings/shared.py)）。
- **输出**：
  - `WorkContext.compacted_steps`：近期 3 步保留全文、更早步骤压成**证据摘要**（工具 / 参数 / 结论 / `skill_run_id`）；
  - 取舍顺序固定为 记忆 → 历史 → 插话 → 步骤压缩；压缩只改模型可见形态，Trace / `skill_run` 原件不动；
  - 压缩后估算仍超端点窗口 → 终止原因 `context_overflow`（可续跑）；仅超安全比例标 `over_budget`；
  - `LoopOutcome` / `AgentRunRecord` 记录生效窗口、可用 token 与信封说明，供预检与留痕核对。

## 可关闭的遗留

无（逐册复核 L0 / L1 / L2 / L3 / L5 / L6 未闭区：无归属＝本任务、或无解封条件＝本任务 `done` 的条目）。

## 假设与前提

- **A1**：压缩只作用于“更早步骤”的模型可见形态，不改 `skill-run` 原件、不改 Trace；近期步骤保留全文。
- **A2**：总 token/时间/费用预算是运行级硬边界，模型不能自行抬高；模型可经 [`T-AGT-033.6`](../../T-AGT-033.6-Agent自调申请与进展检查.md) 在边界内申请。
- **A3**：`context_overflow` 是新的显式终止原因（或复用现有失败语义并加机器可读标记），需要契约先行。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志 · §7 配置项
- [05-L3-对话主入口](../../../../docs/技术架构-v2/05-L3-对话主入口.md) §10 上界与终止 · 规模预算
- [14-desktop-form](../../../../docs/PRD-v2-Agent/14-desktop-form.md) §3 运行面板

## 备注

证据：真实运行 23 步重建后约 17110 字符（还只配了一个占位工具），加真实目录 11927 约 29000，而出厂预算 16000；步骤永不参与取舍（`context.py:601`），`over_budget` 只标 flag；44 步运行跑 11 分钟；建议值 32 步 ≈ 24k + 目录 ≈ 36k，与建议预算 32000 矛盾。
