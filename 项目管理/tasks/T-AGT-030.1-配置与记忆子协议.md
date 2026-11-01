---
id: T-AGT-030.1
parent: T-AGT-030
title: 配置与记忆子协议
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-029.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-030.1 · 配置与记忆子协议

## 目标
`configure` 与 `memory_op` 变为循环可调动作：调用即产出配置草稿卡 / 裁决卡并交还确认；记忆红线不可绕过。

## 验收标准（Given-When-Then）
- GWT-1：Given 循环判断要改配置，Then 产出配置草稿卡并交还确认。
- GWT-2：Given 记忆写入，Then 出裁决卡，红线不可绕过。

## 接口面
（④ 对齐时补：`ConfigDraftProtocol` / `ConflictAdjudicator` 作为循环动作的适配面。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：动作化不改变两套协议自身的校验与留痕。）
