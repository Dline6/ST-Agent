---
id: T-AGT-014.2
parent: T-AGT-014
title: 文件读原语
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-014.1, T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-014.2 · 文件读原语

## 目标
提供 `file_read` / `file_glob` / `file_grep`，经沙箱 `local_read` 核对。

## 验收标准（Given-When-Then）
- GWT-1：Given 声明范围内的路径，When 读，Then 返回内容。
- GWT-2：Given 越界路径，Then 拦截且**不读取**。
- GWT-3：Given 超大文件，Then 截断并给指针。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `local_read` 的作用域
- [D-031](../../docs/技术架构-v2/01-平台共享契约.md) —— 读缓存数据不属于 `local_read` 范围

## 参考
- `l1/sandbox/sandbox.py:237`（`read_file` 核对已实现、无执行器使用）

## 备注
`local_read` 的语义边界须先厘清：D-031 ③ 已定「读 `data_cache` 内的缓存数据**不是** `local_read` 的范围」。
