---
id: T-AGT-016.2
parent: T-AGT-016
title: 命令能力声明与开启
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-016.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-016.2 · 命令能力声明与开启

## 目标
把命令执行登记为离线能力，并在设置页显式开关。

## 验收标准（Given-When-Then）
- GWT-1：Given [02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md)，When 登记 `local-exec`，Then 离线清单显示「不可用」。
- GWT-2：Given 默认，Then 禁用。
- GWT-3：Given 用户显式开启，Then 留痕且可回滚。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 能力声明表

## 参考
- `l0/net/capabilities.py`（`DEFAULT_CAPABILITIES`）

## 备注
`local-exec` 与 `net-search` 是本批新增的两条出厂声明。
