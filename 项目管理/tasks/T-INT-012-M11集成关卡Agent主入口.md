---
id: T-INT-012
parent: null
title: M11 集成关卡 · Agent 主入口
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020, T-AGT-021, T-AGT-022.1, T-AGT-022.2, T-AGT-023.1, T-AGT-023.2, T-AGT-024.1, T-AGT-024.2, T-AGT-025, T-AGT-026.1, T-AGT-026.2, T-AGT-026.3, T-AGT-027.1, T-AGT-027.2, T-AGT-028.1, T-AGT-028.2, T-AGT-029.1, T-AGT-029.2, T-AGT-030.1, T-AGT-030.2, T-AGT-031.1, T-AGT-031.2, T-AGT-031.3, T-AGT-033.1, T-AGT-033.2, T-AGT-033.3, T-AGT-033.4, T-AGT-033.5, T-UI-036, T-UI-037.1, T-UI-037.2, T-UI-037.3, T-UI-037.4, T-UI-038]
status: done
decisions: [D-154, D-161]
verify: "离线 3 例 + live 1 例 + 全量 5203 passed / 18 deselected / 2 errors（test_shell 环境性）；见执行日志 [T-INT-012]"
---

# T-INT-012 · M11 集成关卡 · Agent 主入口

## 目标
在真链路上验收 M11：循环承接请求、过程可见、可停止、可插话、可换权限档。

## 验收标准（Given-When-Then）
- GWT-1：Given 输入「最近ST股票有没有什么热门板块?」，Then 循环启动、步骤可见、产出证据包，不出现 `empty`。
- GWT-2：Given 运行中，Then 停止 / 插话 / 权限切换三件均可用且留痕一致。
- GWT-3：Given 全量验收，Then 范围套件与 `verify_docs.py --strict` 全过。

## 接口面
**输入（逐条点名上游任务与落盘位置）**：

- `POST /api/chat`（`action=post` / 后台开跑）：默认入口承接与 `agent_run_id` 立即返回——[`T-AGT-029.1`](T-AGT-029.1-对话入口改造与会话预取.md)，实现见 `src/st_agent/composition/agent.py`、`src/st_agent/ui/surfaces/chat.py`。
- 会话上下文预取与历史/记忆切片：`T-AGT-009.*` / `T-AGT-033.3`，落 `src/st_agent/l3/chat/session.py` 与 `l3/runtime/context.py`。
- 运行身份、活动 run 注册表与 JSONL 事件日志：`T-AGT-022.1` / `T-AGT-022.2`，落 `execution_log/agent-run-events/<agent_run_id>.jsonl`。
- SSE 事件端点与断线回放：`T-AGT-023.1`（`GET /api/chat/run/<id>/events`），前端接入 `T-AGT-023.2`。
- 停止：`T-AGT-024.1` / `T-AGT-024.2`（`POST /api/chat/run/<id>/stop`，终止原因 `cancelled`）。
- 插话：`T-AGT-027.1` / `T-AGT-027.2`（`POST /api/chat/run/<id>/steer`，`steer` 事件与 `steered` 留痕）。
- 会话级权限档：`T-AGT-028.1` / `T-AGT-028.2`（`GET` / `POST /api/chat/session/tier`）。
- 运行面板字段：`T-AGT-026.1` / `.2` / `.3`（步骤流 / 计划 / 交还卡），前端 `ui/web/js/pages/chat.js`。
- 上下文信封、截断与应用量：`T-AGT-033.1`–`.5`，落 `l3/runtime/envelope.py`、`truncation.py`、`context.py`。
- 预算与工具设置面：`T-UI-036` / `T-UI-037.*` / `T-UI-038`，落 `ui/surfaces/settings/*` 与 `l3/runtime/bounds.py`。

**输出（本关卡交付）**：

- `tests/integration/test_m11_agent_entry.py`（新）：默认 pytest 内的离线装配验收——真组合根 + 真回环服务 + 脚本化出网一跳，覆盖 GWT-1 / GWT-2 的跨叶装配面。
- `tests/live/test_m11_agent_entry_live.py`（新）：`@pytest.mark.live` 的真实端点复跑，按 GWT-1 原句跑一次默认入口，证明真链路非 `empty`。
- 不新增公共 API、不改契约；若装配暴露真实缺口，仅在最小范围内修代码并在执行日志点名。

## 可关闭的遗留
无（已逐册核过 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) / [L5](../遗留问题/L5-遗留问题.md) / [L6](../遗留问题/L6-遗留问题.md) 的未闭区，无归属本关卡或解封条件为本关卡 `done` 的条目）。

## 假设与前提
- **A1**：本机 `.env` 具备 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`，真实端点可用。若错的影响：live 用例 skip，真链路 GWT-1 只剩离线脚本证据；验证方式：跑 `pytest -m live tests/live/test_m11_agent_entry_live.py`，skip 时把原因写进执行日志验证行。
- **A2**：真实端点收到 GWT-1 原句后可能不选择调用工具（模型行为），live 用例按既有 `tests/live/test_llm_tools_live.py` 先例在该情形显式 skip，不判失败。若错的影响：live 断言口径偏松，但不把模型选择误判成链路缺陷；验证方式：live 运行后检查事件流是否含 `step_started`。
- **A3**：M11 各叶的单点行为已由 `tests/integration/test_m11_*.py` 覆盖，本关卡只补跨叶装配（同一 run 上的入口 / 事件 / 停止 / 插话 / 档位 / 留痕一致），不重复单叶断言。若错的影响：关卡测试冗余或漏装配缺口；验证方式：对照五个既有 M11 文件逐条去重。
- **A4**：全量离线套件中 `tests/ui/test_shell.py` 的本机环境性 ERROR 与本次改动无关。若错的影响：全量计数出现 2 errors 需按既有判据排除；验证方式：错误栈与已登记环境性判据比对。
