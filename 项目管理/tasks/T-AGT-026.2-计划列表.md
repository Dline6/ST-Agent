---
id: T-AGT-026.2
parent: T-AGT-026
title: 计划列表
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-026.1]
status: done
decisions: [D-154, D-158]
verify: 见执行日志 [T-AGT-026.2]（同批范围套件 2202 passed / 2 例既有环境性 ERROR；定向两例；真浏览器走查；verify_docs --strict PASS；commit 7f29443）
---

# T-AGT-026.2 · 计划列表

## 目标
只在模型主动调 `sk_plan_update` 时显示计划，状态随事件更新；未给出则不显示。

## 验收标准（Given-When-Then）
- GWT-1：Given 模型调 `sk_plan_update`，Then 计划列表出现且状态更新。
- GWT-2：Given 模型未给计划，Then 不显示计划块（不伪造）。

## 接口面

- **输入**：
  - `plan_updated` 事件（[`l3/runtime/loop.py`](../../src/st_agent/l3/runtime/loop.py) 的 `PLAN_UPDATE_TOOL` 分支）：`plan` 为 `[{title, status}]`、`status ∈ {pending, active, done}`，`error` 为规范化失败说明；事件经 `T-AGT-023.1` 的 SSE 到达前端；
  - `T-AGT-023.2` 的 `applyRunEvent`（[`pages/chat.js`](../../src/st_agent/ui/web/js/pages/chat.js)）与运行态消息结构；
  - `PlanStep`（[`l3/runtime/context.py`](../../src/st_agent/l3/runtime/context.py)）的状态枚举。
- **输出**：
  - 运行面板计划块：仅在收到 `plan_updated` 且 `plan` 非空时显示；状态随每次 `plan_updated` **整体替换**并更新标签；`error` 非空时如实显示一条说明；
  - 样式落 [`app.css`](../../src/st_agent/ui/web/css/app.css)；**无后端改动**（事件字段已具备）；
  - 未收到计划 ⇒ 不出计划块，不代模型编计划（05 §10「计划来源」）。

## 可关闭的遗留

无。开工逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3` / `C5` / `D1`–`D3` 与 L5 `A1` 皆「人决」，L1 `E` 段待人立项、L2 / L3 / L6 均 0 条，无归属 / 解封条件是本任务或本批的条目。

## 假设与前提

- **A1**：计划状态枚举沿用 `pending` / `active` / `done`（与 `PlanStep` 一致）；前端只做**展示标签映射**，不改枚举、不新增状态。
  - 若错的影响：模型给的状态被误读或需要改契约枚举。
  - 验证方式：用例断言事件 `plan` 的状态值落在三值内；前端标签映射沿用该三值。
- **A2**：「未给出不显示」按**是否收到 `plan_updated`** 判定；显式清空（`plan=[]`）即隐藏，不保留旧计划。
  - 若错的影响：模型没给计划时伪造一个，或清空后仍显示旧计划。
  - 验证方式：前端走查两场景（未调 `sk_plan_update` / 显式清空）。
- **A3**：计划由 `plan_updated` **整体替换**（事件无增量语义），与服务端 `current_plan` 的整份快照一致。
  - 若错的影响：前端残留已删除的计划步。
  - 验证方式：多轮计划更新用例断言面板与最后一次事件一致。
