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
status: todo
decisions: [D-090, D-154]
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

待 ④ 对齐实填。预计落点：

- 输入：`run_agent_loop` 的 `nested_recorder` / `SPAWN_AGENT_TOOL`（[`loop.py`](../../src/st_agent/l3/runtime/loop.py)）· `RunEventLog`（[`events.py`](../../src/st_agent/l3/runtime/events.py)）· 子运行留痕（[`report.py`](../../src/st_agent/l3/runtime/report.py)）· 运行面板（[`run_events.js`](../../src/st_agent/ui/web/js/run_events.js)）。
- 输出：子运行事件日志 · 父运行进度事件 · 子 Agent 独立超时/重试配置 · 面板口径说明。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：子运行事件可以与父运行共用同一事件模型与 SSE 通道，不必新造类型；若错，需定义 `parent_agent_run_id` 关联。
- **A2**：子 Agent 的独立超时属于运行预算，不要求新增用户意图参数；若错，需在设置页开放。
- **A3**：子 Agent 目前 `session_id=None`，事件流只按 `agent_run_id` 寻址，不需要会话身份。

## 涉及契约（链接到具体小节）

- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 子 Agent 与运行事件
- [14-desktop-form](../../docs/PRD-v2-Agent/14-desktop-form.md) §3 运行面板

## 备注

证据：`agr_fb0eabca867247beb4ae` 子运行 4 轮调用、66.1s 超时；父运行事件在 13:28:44–13:30:11 之间没有进度事件，只有结束后的一条 `sub_agent`。子运行只有 `agent-run` 记录，无 `agent-run-events`。
