---
id: T-AGT-028
parent: null
title: 权限档窗口化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-020]
status: done
decisions: [D-154, D-159]
verify:
---

# T-AGT-028 · 权限档窗口化

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-028.1`](T-AGT-028.1-会话级覆盖与三档切换.md) / [`T-AGT-028.2`](T-AGT-028.2-manual与collaborative差异补齐.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把三档权限开进对话窗口，并支持会话级覆盖。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
子叶并集，详见各叶：

- [`T-AGT-028.1`](T-AGT-028.1-会话级覆盖与三档切换.md)：L6 `RuntimeAuthorization` 的会话覆盖读 / 写面、组合根的会话绑定判据适配器、`GET` / `POST /api/chat/session/tier`、右栏档位选择器。
- [`T-AGT-028.2`](T-AGT-028.2-manual与collaborative差异补齐.md)：`resume_agent_run(..., continue_after_approval=False)` 与新终止原因 `manual_step_done`（`manual` 每步单独批、不自动续跑）。

## 可关闭的遗留
无（两叶的遗留面并集皆「无」；开工 2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 未闭区，检查 8 = 0）。

## 假设与前提
子叶假设并集，详见各叶 A1–A3：覆盖 owner 落 L6 一处判据（[`T-AGT-028.1`](T-AGT-028.1-会话级覆盖与三档切换.md) A1）·「会话结束」＝进程结束 / 显式恢复跟随全局（同叶 A2）· `manual` 批准后只执行该步即收口并以 `manual_step_done` 收口（[`T-AGT-028.2`](T-AGT-028.2-manual与collaborative差异补齐.md) A1/A2）。
