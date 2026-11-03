---
id: T-MEM-001.2
parent: T-MEM-001
title: 中文主题检索与相关性排序
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P0
milestone: M12
depends_on: [T-L2-001.3]
status: todo
decisions: []
verify:
---

# T-MEM-001.2 · 中文主题检索与相关性排序

## 目标

让主题检索对中文可用：不再只按空白/标点切词（中文整句会变成一个 token），不再要求整句 token 出现在节点文本里；改用中文可用的分词/字符 n-gram + 重叠命中，排序从“置信度 + 时近度”恢复为主题相关性参与。保持确定性、无外部 NLP 依赖优先。

## 验收标准（Given-When-Then）

- GWT-1：Given 主题「最近哪些股票板块比较热门」与节点文本「关注新能源板块」，Then 主题命中率 > 0（不再因整句 token 不匹配而为 0）。
- GWT-2：Given 中文主题与中文节点，Then 切分产物是可复算的短 token（字符 bigram/trigram 或等价），不是整句；Given 英文/空格分隔主题，Then 原行为仍可用。
- GWT-3：Given 多个候选节点，Then 主题相关的节点排序高于仅靠置信度/时近度的节点；Given 主题为空，Then 退化为置信度 + 时近度（与现状一致）。
- GWT-4：Given 同一输入两次查询，Then 排序逐字节一致；不引入非确定性分词/随机。
- GWT-5：Given 中文停用词/单字噪声，Then 有明确处理口径（至少不因单字高频词把无关节点顶上来）。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`_TOPIC_SEPARATORS` / `_tokens` / `_topic_hit` / `_score`（[`reader.py`](../../src/st_agent/l2/memory/reader.py)）· `node_text`（[`models.py`](../../src/st_agent/l2/memory/models.py)）。
- 输出：中文主题 tokenizer（新模块或 reader 内部函数）· 命中/排序口径 · 中文主题用例。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：优先不引第三方分词依赖；字符 n-gram + 停用词/短 token 过滤足够满足 GWT；若实测不足，再评估轻量分词库。
- **A2**：排序权重（主题/置信度/时近度）保持现有量级，只修复命中口径；若需调权重，另记决策。
- **A3**：主题命中仍是词法面，不引入向量/语义检索（D-137 D-5 冻结范围不变）。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3.1 相关性与排序

## 备注

证据：`_tokens` 只按 `[\s,，。;；:：/|、!！?？\-—]+` 切分；`_topic_hit` 用 `token.lower() in haystack`，中文整句 token 要求整句出现在节点文本里，实际命中率通常为 0，排序退化为置信度 + 时近度。
