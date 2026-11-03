---
id: T-AGT-024
parent: null
title: 取消与安全停止
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-022.1, T-AGT-023.1]
status: done
decisions: [D-154]
verify:
---

# T-AGT-024 · 取消与安全停止

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-024.1`](T-AGT-024.1-取消透传与cancelled终止.md) / [`T-AGT-024.2`](T-AGT-024.2-停止端点与停止按钮.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让运行可被用户停止，且停止语义诚实。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
子叶并集（详表见各叶）：
- **输入**：`T-AGT-022.1` 的活动 run 控制句柄与注册表 · `T-AGT-022.2` 的事件日志 · `T-AGT-023.1` 的后台派发与 SSE · `T-AGT-023.2` 的前端运行态 · L0 既有的 `LlmClient.invoke(cancel=…)`。
- **输出**：循环的 `cancel` 透传与 `cancelled` 终止（`.1`）· `POST /api/chat/run/<id>/stop` 与前端停止按钮（`.2`）。

## 可关闭的遗留
无（子叶并集：L0–L6 册当前 0 条与本批相关的未闭项；L5 `A1` 为真实渠道凭据的人决项）。

## 假设与前提
子叶并集：`.1` 的 A1–A4（边界式取消、L0 逐块检查、单一事件载体、缺省逐字节不变）与 `.2` 的 A1–A4（协作式停止、no-op 语义、停止后无新事件、沿用既有回读渲染）。
