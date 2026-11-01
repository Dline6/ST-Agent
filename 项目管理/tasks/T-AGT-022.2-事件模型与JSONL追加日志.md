---
id: T-AGT-022.2
parent: T-AGT-022
title: 事件模型与 JSONL 追加日志
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-022.2 · 事件模型与 JSONL 追加日志

## 目标
按 D-154 B5 定义运行事件，带单调序号追加写 JSONL，供 SSE 重连与崩溃后回放。

## 验收标准（Given-When-Then）
- GWT-1：Given 一次运行，Then 事件按发生顺序追加，序号单调递增。
- GWT-2：Given 运行结束，When 读取 JSONL，Then 能完整回放该 run。
- GWT-3：Given 事件类型不在枚举内，Then 显式失败，不静默丢弃。

## 接口面
（④ 对齐时补：事件枚举与 JSONL 落点（`execution_log` 下）；`emit` 回调的签名。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：事件只落客观字段，不含模型中间推理。）
