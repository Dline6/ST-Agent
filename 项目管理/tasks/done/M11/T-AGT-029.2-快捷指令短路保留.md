---
id: T-AGT-029.2
parent: T-AGT-029
title: 快捷指令短路保留
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-029.1]
status: done
decisions: [D-154, D-156]
verify: 见执行日志 [T-AGT-029.2]（范围套件同批 2192 passed / 2 errors；定向 `/盯盘` 确认卡 + 未知指令 empty；真浏览器：`/盯盘` 同步出确认卡、无运行态；commit 1be347e）
---

# T-AGT-029.2 · 快捷指令短路保留

## 目标
`/` 开头的快捷指令仍在入口短路，直接取意图模板与参数预填，不进循环。

## 验收标准（Given-When-Then）
- GWT-1：Given `/盯盘`，Then 短路取意图模板与参数预填。
- GWT-2：Given 未知指令，Then 显式报错，不交循环。

## 接口面
**输入**：
- `CommandRegistry.resolve(raw)`（`T-L3-001.x` 交付）：指令名 → `QuickCommand`（意图模板 + `param_defaults`）。
- `IntentProtocol._from_command`（`T-AGT-021` 后的现状）：命中即取意图模板与参数预填，目标解析不出仍按 §3.2 升级 investigate。

**输出**：`POST /api/chat` 的 `/` 分支继续走 `DialogFacade.post`（理解 → 澄清 → 确认卡），不进入 `.1` 的默认循环分支；未命中指令回 `CommandRegistry.resolve` 的显式信封（`empty` / `validation_failed`），不交循环。

## 可关闭的遗留
无（同 `.1` 的逐册核查）。

## 假设与前提
- **A4**：快捷指令语义与现状逐字节一致（`/` 前缀判定、模板与预填来源、未命中指令的错误信封）。**若错**：快捷指令用例与前端指令菜单会同时暴露；**验证**：`tests/l3/test_chat_session.py` 既有快捷指令用例 + 新增入口分支用例。
