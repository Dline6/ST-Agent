---
id: T-AGT-024.2
parent: T-AGT-024
title: 停止端点与停止按钮
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-024.1, T-AGT-023.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-024.2 · 停止端点与停止按钮

## 目标
`POST /api/chat/run/<id>/stop` 与前端停止按钮；停止后展示已执行步骤与写操作。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中，When 点停止，Then 调用停止端点并展示已执行步骤。
- GWT-2：Given 无活动 run，Then 停止为 no-op 并提示。
- GWT-3：Given 停止完成，Then 不再有新事件。

## 接口面
（④ 对齐时补：`ui/server.py` 路由、`ui/surfaces/chat.py`、前端运行态控件。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：停止是协作式的：请求即标记，实际终止在下一个边界。）
