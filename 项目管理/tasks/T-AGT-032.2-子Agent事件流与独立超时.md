---
id: T-AGT-032.2
parent: T-AGT-032
title: 子 Agent 事件流与独立超时
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone:
depends_on: [T-AGT-018.2, T-AGT-023.2]
status: done
decisions: [D-090, D-154, D-167]
verify:
---

# T-AGT-032.2 · 子 Agent 事件流与独立超时

## 目标

让派生子 Agent 拥有与主循环同级的运行事件流（可回放、可展示进度）与独立的超时/重试策略；父运行不再在子 Agent 静默运行的 90 秒里没有任何进度事件，也不被同一个 60s 端点超时连带杀死。

## 验收标准（Given-When-Then）

- GWT-1：Given 子 Agent 运行，Then 产生 `agent-run-events/<child_run_id>.jsonl` 并可在运行面板展开查看；父事件流有 `sub_agent_started` / `sub_agent_finished` 或等价进度事件。
- GWT-2：Given 子 Agent 调用超时，Then 父运行收到明确的失败事件（含子运行 id、失败原因、已执行步数），不把 90 秒空窗留给用户。
- GWT-3：Given 子 Agent 超时，Then 可按配置重试或降级为“父循环自行继续”，不默认把整条父运行拖入 `llm_failed`。
- GWT-4：Given 子 Agent 的步骤进入父预算，Then 运行面板对 `executed_steps` 与 `steps` 的口径差异给出说明。

## 接口面

- **输入**：`run_agent_loop` 的派生分支与 `NestedRecorder`（[`loop.py`](../../src/st_agent/l3/runtime/loop.py)）· `RunEventLog`（[`events.py`](../../src/st_agent/l3/runtime/events.py)）· 组合根的活动表 / 事件日志（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）· `LoopBounds`（[`bounds.py`](../../src/st_agent/l3/runtime/bounds.py)）· 运行面板（[`run_events.js`](../../src/st_agent/ui/web/js/run_events.js) / [`chat.js`](../../src/st_agent/ui/web/js/pages/chat.js)）。
- **输出**：`NestedRecorder(outcome, agent_run_id)`（落痕与事件共用开跑前铸好的子运行 id）· `NestedEventFactory` / `NestedEventStream`（组合根铸号、登记活动表、写 `agent-run-events/<child_run_id>.jsonl`）· 父事件 `sub_agent_started`（子 id / task / trace / 时限 / 尝试次数）· 子循环独立 `subagent_llm_timeout_ms`（缺省 120000）与 `subagent_llm_max_attempts`（缺省 2），对应 01 §7 两条配置项 · 运行面板的子运行事件回放与 `executed_steps` / `steps` 口径说明。

## 可关闭的遗留

无（L0 / L1 / L3 遗留册未闭区均无归属本任务或解封于本任务 `done` 的条目）。

## 假设与前提

- **A1**：子运行事件可以与父运行共用同一事件模型与 SSE 通道，不必新造类型族。若错的影响：需定义 `parent_agent_run_id` 关联字段。验证方式：子事件日志按 `agent_run_id` 独立回放、父流只增 `sub_agent_started`。
- **A2**：子 Agent 的独立超时属于运行预算，可在 01 §7 登记为全局配置项，不要求新增用户意图参数。若错的影响：需在设置页开放意图级参数。验证方式：新增两条 `investigate.subagent_*` 条目并由设置页预算段呈现。
- **A3**：子 Agent 目前 `session_id=None`，事件流只按 `agent_run_id` 寻址；活动表登记不占会话互斥。若错的影响：需为子运行定义会话身份。验证方式：集成用例断言父子活动态与事件回放互不冲突。
- **A4**：`NestedRecorder` 的签名扩展只影响仓内调用方（组合根 / 测试）。若错的影响：外部注入方需同步改签名。验证方式：全仓 grep 与范围套件覆盖。

## 涉及契约（链接到具体小节）

- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 子 Agent 与运行事件
- [14-desktop-form](../../docs/PRD-v2-Agent/14-desktop-form.md) §3 运行面板

## 备注

证据：`agr_fb0eabca867247beb4ae` 子运行 4 轮调用、66.1s 超时；父运行事件在 13:28:44–13:30:11 之间没有进度事件，只有结束后的一条 `sub_agent`。子运行只有 `agent-run` 记录，无 `agent-run-events`。
