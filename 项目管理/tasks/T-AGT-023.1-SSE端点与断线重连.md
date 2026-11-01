---
id: T-AGT-023.1
parent: T-AGT-023
title: SSE 端点与断线重连
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.2]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-023.1 · SSE 端点与断线重连

## 目标
提供 `GET /api/chat/run/<id>/events`：先补发已发生事件，再续推新事件；支持 `Last-Event-ID` 与心跳。

## 验收标准（Given-When-Then）
- GWT-1：Given 活动 run，When 连接端点，Then 收到已发生事件并按序续推新事件。
- GWT-2：Given 请求带 `Last-Event-ID`，Then 从该序号之后补发。
- GWT-3：Given 无活动 run 或 id 不存在，Then 显式空态 / 错误，不挂空连接。

## 接口面
（④ 对齐时补：`ui/server.py` 路由、`ui/surfaces/chat.py` 取数面、事件日志读取口。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：本地单用户，连接数按 1–2 估算；不做鉴权以外的限流。）
