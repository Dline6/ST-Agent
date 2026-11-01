---
id: T-AGT-025
parent: null
title: 工具调用自愈
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-020]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-025 · 工具调用自愈

## 目标
模型请求不存在的工具时，从硬终止改为回灌「目录无此工具 + 当前目录」允许重选；仅重复违规或到上界才终止。

## 验收标准（Given-When-Then）
- GWT-1：Given 模型请求不存在的工具，Then 回灌错误与当前目录，循环继续。
- GWT-2：Given 连续多次未知工具，Then 终止并如实标注。
- GWT-3：Given 正常工具调用，Then 行为逐字节不变。

## 接口面
（④ 对齐时补：`loop.py` 的 `unknown_tool` 分支；工作上下文的回灌段。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：回灌次数有上界，避免模型在同一错误上打转。）
