---
id: T-AGT-022.1
parent: T-AGT-022
title: 开跑铸号与活动 run 注册表
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-022.1 · 开跑铸号与活动 run 注册表

## 目标
在循环**开始前**铸出 `agent_run_id`，并以进程内注册表持有活动 run 的控制句柄；收口留痕沿用同一标识（现状是 `conclude_agent_run` 跑完才铸号）。

## 验收标准（Given-When-Then）
- GWT-1：Given 一次运行开始，When 读取返回，Then `agent_run_id` 已存在且与收口留痕同一标识。
- GWT-2：Given 活动 run，Then 可按 id 取到控制句柄；结束后注册表移除该 run。
- GWT-3：Given 同一会话已有活动 run，When 再次启动，Then 显式拒绝（D-154 B9）。

## 接口面
（④ 对齐时补：`report.py` 的铸号点、`composition/agent.py` 的运行入口、活动 run 注册表的读写面。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：注册表为进程内瞬态、不落盘；跨进程恢复不在本叶。）
