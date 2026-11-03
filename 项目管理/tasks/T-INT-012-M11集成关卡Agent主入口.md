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
status: todo
decisions: [D-154, D-161]
verify:
---

# T-INT-012 · M11 集成关卡 · Agent 主入口

## 目标
在真链路上验收 M11：循环承接请求、过程可见、可停止、可插话、可换权限档。

## 验收标准（Given-When-Then）
- GWT-1：Given 输入「最近ST股票有没有什么热门板块?」，Then 循环启动、步骤可见、产出证据包，不出现 `empty`。
- GWT-2：Given 运行中，Then 停止 / 插话 / 权限切换三件均可用且留痕一致。
- GWT-3：Given 全量验收，Then 范围套件与 `verify_docs.py --strict` 全过。

## 接口面
（④ 对齐时补：各叶交付面的真实装配（组合根 + UI + L3 循环 + L6 授权）。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：真 LLM 端点可用；不可用时写明显式 skip 原因。）
