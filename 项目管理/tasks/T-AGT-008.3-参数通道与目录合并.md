---
id: T-AGT-008.3
parent: T-AGT-008
title: 参数通道与目录合并
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.2]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-008.3 · 参数通道与目录合并

## 目标
让对象型参数（如 SQL 的绑定参数数组）能经 `inputs` 通道进入执行器，而不是塞成 JSON 字符串。

## 验收标准（Given-When-Then）
- GWT-1：Given 一个声明对象参数的原语，When 模型给出对象实参，Then 经 `inputs` 核验后进入执行器。
- GWT-2：Given 一个标量参数原语，When 模型给出实参，Then 仍走 `values` 通道，存量行为不变。
- GWT-3：Given 同一 base 的多个版本，Then 工具目录仍折叠为一条。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) —— `parameters`（标量）与 `input_schema`（对象）两条通道
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环按通道填充实参

## 参考
- `l3/runtime/loop.py`（现按 `values` 传参）
- `l1/runner/runner.py` 的 `run(values, inputs=…)`

## 备注
**实现陷阱**：循环当前把 `call.arguments` 整体当 `values` 传（`loop.py:317`），而 `values` 只认 `ParameterSpec` 的标量；不改通道，每个对象型原语都要写 JSON 字符串 hack。
