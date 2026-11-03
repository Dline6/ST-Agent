---
id: T-AGT-033.3
parent: T-AGT-033
title: 任务锚点与历史装载
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-009.2, T-AGT-029.1]
status: todo
decisions: [D-090, D-142, D-154]
verify:
---

# T-AGT-033.3 · 任务锚点与历史装载

## 目标

把“会话主任务”做成**独立于历史预算的锚点**：跟进语（「继续」等）解析回原任务；历史装载优先保原问题与近端消息，遇到超大消息可跳过/摘要而不是 `break` 丢光更早内容；当前消息不在 task 与 history 重复；模型可见历史用紧凑摘要 + 证据引用，而不是整份 UI 出站载荷。**记忆查询的主题来源与任务类型亲和归 [`T-MEM-001.3`](T-MEM-001.3-任务类型亲和与主题来源.md)**，本叶只提供“主任务”锚点。

## 验收标准（Given-When-Then）

- GWT-1：Given 会话已有主任务、用户只输入「继续」，Then 工作上下文的“主任务”为原任务，当前消息以“跟进”标记进入；历史预算再紧也不能挤出主任务锚点。
- GWT-2：Given 历史中有一条 7000+ 字符的助手载荷，Then 原问题仍被保留（锚点/跳过超大消息/摘要三者之一），不因 `break` 被丢掉；预算 4000 时也不只剩最后一条失败消息。
- GWT-3：Given 当前消息与 task 相同，Then history 中不重复出现该消息（或明确标注“本次消息”）。
- GWT-4：Given 助手轮历史，Then 模型看到紧凑摘要 + 证据引用（`skill_run_id` / `log_ref` / 关键结论），UI 仍能拿到完整出站载荷；两者不混为一份。
- GWT-5：Given 会话有分支，Then 历史沿当前活动叶子回溯（若保留分支能力），不默认取“最早子节点”。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`ChatSession` / `ChatMessage` / `SessionStore.chain`（[`session.py`](../../src/st_agent/l3/chat/session.py)）· `assemble_context` 的消息选择（[`session.py`](../../src/st_agent/l3/chat/session.py)）· `_context_inputs`（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）· `start_turn`（[`facades/chat.py`](../../src/st_agent/composition/facades/chat.py)）· `WorkContext._render_history_lines`（[`context.py`](../../src/st_agent/l3/runtime/context.py)）。
- 输出：会话主任务字段/锚点 · 跟进语解析 · 历史选择策略（原问题 + 近端 + 跳过超大）· 当前消息去重 · 模型可见历史摘要 ·（可选）活动叶子。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：主任务可以由确定性规则（最近一条非跟进用户消息 + 可选摘要）维护，不额外引入一次 LLM 调用；若错，改为轻量解析轮并计入预算。
- **A2**：模型可见历史摘要只做“指向 + 结论”，不复制完整载荷；若错，预算会重新被 UI 结构吃掉。
- **A3**：分支能力当前未在生产链路使用；GWT-5 是防回归项，若确认不做分支可显式裁掉。
- **A4**：记忆主题/亲和不在本叶；主任务只作为字段提供给 [`T-MEM-001.3`](T-MEM-001.3-任务类型亲和与主题来源.md) 消费。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §1 会话模型 · §10 工作上下文与取舍

## 备注

证据：预算 16000 的真实会话模拟只保留最后 4 条消息，原问题被 13:22:56 的 7086 字符载荷挡住；预算 4000 只剩最后一条失败消息。`assemble_context` 用 `break`（`session.py:546`），不跳过大的；`_render_history_lines` 原样输出 `出站载荷`（`context.py:381`）；`start_turn` 先 append 当前消息再取历史，造成 task/history 重复。
