---
id: T-AGT-014.3
parent: T-AGT-014
title: 文件写原语
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-014.2]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-014.3 · 文件写原语

## 目标
提供 `file_write`，落工作区分区，进备份范围。

## 验收标准（Given-When-Then）
- GWT-1：Given 工作区内路径，When 写，Then 落盘且产品能找到该文件。
- GWT-2：Given 未声明或未批准 `local_write`，Then 拦截。
- GWT-3：Given 并发写同一文件，Then 串行化或显式拒绝。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `local_write`
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 工作区分区

## 参考
- `l1/sandbox/sandbox.py` 需新增 `write_file` 核对

## 备注
**最高风险的一叶**：写盘会改变用户环境。默认须显式开启，且写入范围限定在工作区分区。
