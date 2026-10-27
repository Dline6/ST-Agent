---
id: T-AGT-012.2
parent: T-AGT-012
title: 设置页暴露运行期档位与分级
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-012.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-012.2 · 设置页暴露运行期档位与分级

## 目标
让用户看得到、改得动、可回滚运行期授权口径。

## 验收标准（Given-When-Then）
- GWT-1：Given 设置页，When 打开，Then 显示 agent 档位与动作级分级清单及其缺省来源。
- GWT-2：Given 修改，Then 留痕于 `agent-change/` 且可回滚。
- GWT-3：Given 用户未改，Then 显示缺省值来源，不伪装成用户选择。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) —— 条目渲染与留痕

## 参考
- `l6/registry_adapter.py` 的 `agent_family`
- `ui/surfaces/evolution.py` 的既有演进授权页

## 备注
演进授权页已存在；本叶是**并列**的运行期授权页，不与演进面合并（[D-090] ① 的分设口径）。
