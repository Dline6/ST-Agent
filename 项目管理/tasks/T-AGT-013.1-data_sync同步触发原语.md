---
id: T-AGT-013.1
parent: T-AGT-013
title: data_sync（同步触发原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-010.2]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-013.1 · data_sync（同步触发原语）

## 目标
让循环按需触发同步，并在产出里如实标注「同步中 / 已完成 / 失败」。

## 验收标准（Given-When-Then）
- GWT-1：Given 数据陈旧，When 调用 `data_sync`，Then 触发同步并返回运行态；等待策略可指定。
- GWT-2：Given 写类动作，Then 授权闸门恒需确认。
- GWT-3：Given 已有运行中的批次，When 再次触发，Then 显式拒绝而非并发。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 同步批次
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 写类动作的风险类

## 参考
- `l0/sync_runner.py` 的 `start_items` / `job` / `cancel`

## 备注
**它是写操作**：风险级不得低于 `collaborative-required`，且不得因「只是同步」而被放宽。
