---
id: T-AGT-023.2
parent: T-AGT-023
title: 前端事件接入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-023.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-023.2 · 前端事件接入

## 目标
前端建立 SSE 连接、按事件更新运行态，并在断线时自动重连。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行开始，Then 前端建立连接并进入运行态。
- GWT-2：Given 连接中断，Then 自动重连并从最后序号补发。
- GWT-3：Given 运行结束，Then 关闭连接且不重复渲染。

## 接口面
（④ 对齐时补：`ui/web/js/api.js` / `busy.js` / 组件目录的连接与状态接口。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：WebView2 支持 EventSource；不支持时退化为轮询（需显式标注）。）
