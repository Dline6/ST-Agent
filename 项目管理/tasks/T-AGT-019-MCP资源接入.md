---
id: T-AGT-019
parent: null
title: MCP resources 接入
story: ../../docs/PRD-v2-Agent/story-08-mcp-hub.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §3](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P2
milestone: M10
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-019 · MCP resources 接入

## 目标
把 MCP 的 resources（list / read）接进来——现在只接了 tools。

## 验收标准（Given-When-Then）
- GWT-1：Given 已连 MCP Server，When `list_resources`，Then 返回资源清单。
- GWT-2：Given `read_resource`，Then 返回内容并留痕。
- GWT-3：Given Server 不支持 resources，Then 显式 `unavailable`。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [03 §3](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— MCP 传输与能力
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) —— 工具暴露面

## 参考
- `l1/mcp/client.py` / `transports.py`

## 备注
只有接外部 MCP 时才用得上，故排 M10。
