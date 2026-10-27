---
id: T-AGT-017.2
parent: T-AGT-017
title: 大结果外部化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-AGT-017.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-017.2 · 大结果外部化

## 目标
把现在的「截断成指针」升级为「写盘 + 按需回读」。

## 验收标准（Given-When-Then）
- GWT-1：Given 超阈值结果，When 回填，Then 写盘并给指针，模型可按需回读。
- GWT-2：Given 未超阈值，Then 原样。
- GWT-3：Given 回读越界，Then 拦截。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §5](../../docs/技术架构-v2/01-平台共享契约.md) —— 日志入口与指针
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 回填语义

## 参考
- `l3/runtime/context.py:64`（`RESULT_TRUNCATE_CHARS`）

## 备注
现状是「截断 + 指针，模型看不到内容只能再查一次」——本叶补上回读通路。
