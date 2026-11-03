---
id: T-AGT-031.3
parent: T-AGT-031
title: LLM 失败重试与 llm_failed 可续跑
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-004.2, T-AGT-011.3]
status: todo
decisions: [D-090, D-154]
verify:
---

# T-AGT-031.3 · LLM 失败重试与 llm_failed 可续跑

## 目标

对可重试的 LLM 失败（超时、429、5xx、连接重置）做**有界退避重试**，不把一次瞬时失败升级为整条循环终止；重试耗尽后，`llm_failed` 也进入**可续跑**路径，用户可从已收集证据继续，而不是重新发送原文、丢掉全部步骤。

## 验收标准（Given-When-Then）

- GWT-1：Given 可重试失败，Then 按配置做 2–3 次退避重试，重试次数与原因进入运行事件与用量；重试不重复执行已完成的工具步，不重复计入步数。
- GWT-2：Given 4xx / 认证 / 能力协商 / 校验失败，Then 不重试，直接按现有失败语义终止并如实呈现。
- GWT-3：Given 重试耗尽仍失败，Then 产出 `llm_failed` 且 `agent_continuation.kind = "retry"`，携带旧 `agent_run_id`、已执行步骤引用与失败原因；前端提供“从已有证据继续”。
- GWT-4：Given 用户选择继续，Then 以新一轮循环承接旧步骤（`resumed_from` 指回旧运行），不重跑旧步骤、不复制载荷；新运行的任务仍为原任务而非「继续」。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`ResultEnvelope` 失败分类（[`client.py`](../../src/st_agent/l0/llm/client.py) / [`net/errors.py`](../../src/st_agent/l0/net/errors.py)）· 循环终止分支（[`loop.py`](../../src/st_agent/l3/runtime/loop.py)）· 续跑终止集合（[`continuation.py`](../../src/st_agent/l3/runtime/continuation.py)）· 续跑载荷（[`facades/chat.py`](../../src/st_agent/composition/facades/chat.py)）· 前端失败重试（[`chat.js`](../../src/st_agent/ui/web/js/pages/chat.js)）。
- 输出：重试策略与配置项 · `llm_failed` 的 `agent_continuation` · 前端“从已有证据继续”入口 · 运行事件中的重试记录。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：重试只发生在 LLM 调用层，不重放工具副作用；若错，需要引入幂等键或步骤级检查点。
- **A2**：`llm_failed` 续跑复用现有 `resume_agent_run` 的 prior_steps 重建路径；若错，需新增“从运行留痕重建工作上下文”的独立路径。
- **A3**：重试次数属于运行预算，计入 `max_llm_calls` 的方式需明确（建议计入总调用次数，但不计入步数）。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 自主查证循环 · 一键续跑

## 备注

证据：`loop.py:660` 对任何 `turn.envelope` 直接 `finish("llm_failed")`；`continuation.py:74` 的可续跑终止只有 `needs_confirmation` / `needs_user_input`；前端失败重试走 `send(retryText)`，重发原文而不是 `resume`。
