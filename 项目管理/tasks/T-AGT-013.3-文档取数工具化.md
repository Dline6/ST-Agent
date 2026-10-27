---
id: T-AGT-013.3
parent: T-AGT-013
title: 文档取数工具化
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-013.3 · 文档取数工具化

## 目标
把已存在的文档取数面（`list_docs` / `read_doc` / `search_docs`）暴露成原语。

## 验收标准（Given-When-Then）
- GWT-1：Given 一批公告，When 调三个面，Then 分别返回元数据 / 正文 / 检索结果。
- GWT-2：Given「表在而文件缺」，Then 判**损坏**并提示，不返回空正文冒充。
- GWT-3：Given「文件在而表缺」，Then 判**孤儿**并可回收。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— `data_cache` 的两段构成
- [D-031](../../docs/技术架构-v2/01-平台共享契约.md) —— 文本存储与一致性判据

## 参考
- `l0/info/docs.py:141/195/221`

## 备注
D-031 ③ 已定「L0 提供文档取数面、Skill 不直读文件」——本叶只是把它投影成工具，不新增读取方式。
