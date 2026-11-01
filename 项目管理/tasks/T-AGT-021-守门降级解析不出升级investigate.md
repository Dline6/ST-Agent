---
id: T-AGT-021
parent: null
title: 守门降级：解析不出升级 investigate
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020]
status: done
decisions: [D-154]
verify: 执行日志 [T-AGT-021]——范围套件 1390 passed（contracts/layering/tools/module_size/integration/l3）；定向 65 passed；verify_docs --strict PASS
---

# T-AGT-021 · 守门降级：解析不出升级 investigate

## 目标
`query` / `configure` 的目标解析不出时，不再返回 `empty`，改为升格为 `investigate`（用户原话作 `task`）并产出确认卡，使能力池外的开放式问题能进入循环。

## 验收标准（Given-When-Then）
- GWT-1：Given 输入「最近ST股票有没有什么热门板块?」，When `post`，Then 出 `investigate` 确认卡（target 为原话），不再 `empty`。
- GWT-2：Given 目标 Skill 已注册或匹配成功，Then 仍按原去向出卡，不降级。
- GWT-3：Given 理解端口未注入，Then 仍返回 `unavailable`（不硬猜）。

## 接口面

**输入**
- 用户原话：`DialogFacade.post(text)`（[`composition/facades/chat.py`](../../src/st_agent/composition/facades/chat.py) 的 `post`）← UI `POST /api/chat` 的 `text`（同文件 `_turn` 默认分支）。
- 理解端口与目标解析依赖：组合根注入的 `IntentProtocol`（`understander` / `matcher` / `descriptors`）；生产根 `build_m5_runtime`，M1 测试根 `build_m1_runtime`。L3 不 import L1，依赖全经注入。

**输出**
- `IntentDraft(intent="investigate", target=<用户原话>, via_matcher=False)`——进程内产物，不落盘。
- `IntentProtocol.confirm()` 产出 `IntentConfirmation`（条目仍只有「意图」一条，`target` 为原话）；`DialogFacade.post` 回 `TurnResult(confirmation=…)`，`_turn` 回 `needs_confirmation: true`。
- 确认后经 `DispatchBus` → `_AgentInvestigator.investigate` 取 `values["task"]` 或 `confirmation.target` 作任务（[`composition/agent.py`](../../src/st_agent/composition/agent.py) 的 `investigate`），循环留痕落 `execution_log`（下游既有路径，本任务不改）。

## 可关闭的遗留

无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区；`T-AGT-02x` / `T-AGT-030` 在各册无命中，L3 册当前 0 条未闭）。

## 假设与前提

- **A1**：`investigate` 的任务由确认卡的 `target` 承载（`_AgentInvestigator` 既有取值链 `values["task"] → confirmation.target`），不新增 `task` 意图参数——[05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md) 明示 `investigate` 无意图级参数声明。若错：卡片缺任务、循环走 `validation_failed`。验证：GWT-1 断言 `confirmation.target == 原话`，并断言派发不落「缺少任务描述」。
- **A2**：`/` 快捷指令路径的目标解析失败同样升级——否则会留下「可确认但派发必然失败」的卡（§3.2 取向）。若错（负责人要求指令失败另走提示）：仅在 `_from_command` 回退即可。验证：新增指令路径用例断言升级为 `investigate`。
- **A3**：升级在协议层 `IntentProtocol.understand` 内完成，门面只删守门分支——所有入口（现 HTTP 与后续 `T-AGT-029.1` 改造）自动一致。若错：需在门面另设分支。验证：M1 集成 `post` 经门面观察到 `investigate` 卡。
