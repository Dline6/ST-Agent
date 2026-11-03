---
id: T-MEM-001.4
parent: T-MEM-001
title: 读取时钟与长运行衰减
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P1
milestone: M12
depends_on: [T-L2-001.3, T-L2-002.3]
status: todo
decisions: []
verify:
---

# T-MEM-001.4 · 读取时钟与长运行衰减

## 目标

让 `MemoryReader` 的“当前时刻”随每次查询推进，而不是在构造时用 `now()` 冻结：长运行循环的时近度衰减、置信度衰减/重估在后续查询中可见；测试仍可注入固定时钟保持确定性。

## 验收标准（Given-When-Then）

- GWT-1：Given 构造时传固定 `now`，Then 多次查询结果逐字节一致（测试语义不变）。
- GWT-2：Given 构造时传可调时钟/不传，Then 每次 `query` 使用当前时刻，长运行中 `_recency` 与 `confidence.effective` 随时间推进。
- GWT-3：Given 组合根只构造一次 reader（[`m1.py`](../../src/st_agent/composition/runtime/m1.py)），Then 后续查询仍拿到推进后的时刻，不需要重建 reader。
- GWT-4：Given 时区，Then 所有时间带时区（01 §8），不出现 naive datetime 比较。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`MemoryReader.__init__(now=...)` / `query`（[`reader.py`](../../src/st_agent/l2/memory/reader.py)）· `ConfidenceModel.effective(node, now=...)`（[`confidence.py`](../../src/st_agent/l2/memory/confidence.py)）· 组合根构造（[`m1.py`](../../src/st_agent/composition/runtime/m1.py)）。
- 输出：`now` 支持 callable 或 per-query 注入 · 组合根改为传时钟函数 · 确定性测试。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：`now` 参数向后兼容：传 `datetime` 仍按固定时钟；传 `Callable[[], datetime]` 按调用取时；不传用系统当前时刻。
- **A2**：置信度模型已接受 `now` 参数（[`ConfidenceModel.effective`](../../src/st_agent/l2/memory/confidence.py)）；本叶只改 reader 的取时点。
- **A3**：长运行中推进时近度可能让旧记忆分数下降，这是预期行为；不另做“运行内冻结”的特例。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3.1 时近度 · §5 置信度衰减
- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §8 时区口径

## 备注

证据：`MemoryReader.__init__` 保存 `self._fixed_now = now`；`query` 取 `moment = self._fixed_now if self._fixed_now is not None else _now()`；组合根 `MemoryReader(graph, now=now(), ...)` 在构造时冻结。
