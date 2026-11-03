---
id: T-AGT-033.5
parent: T-AGT-033
title: 截断、回读与预览派生
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-033.1, T-AGT-004.1, T-AGT-017.2]
status: todo
decisions: [D-090, D-145, D-160]
verify:
---

# T-AGT-033.5 · 截断、回读与预览派生

> 本叶由原 `T-AGT-031.4` 迁入：截断/回读属于上下文装载，不再挂在 T-AGT-031 下。

## 目标

让“单条结果上限 / 截断预览 / 回读分片 / `sk_result_read` 缺省与上限”全部从**本轮的步骤预算**派生，而不是四处固定数字（1200 / 200 / 2000 / 8000）。模型可在运行时钳制范围内自选 `max_chars`，但按运行时给出的分片参数回读**不得再次被截断**；截断件不再把 `log_ref` 路径暴露成可读文件。

## 验收标准（Given-When-Then）

- GWT-1：Given 一条 7,149 字符的抓取结果，Then 回读提示给出的分片大小保证“读回来的这一片不会被二次截断”；按 `next_offset` 续读可以完整取回，不出现“读一次又被截一次”。
- GWT-2：Given 本轮步骤预算 `S` 与预期剩余步数 `N`，Then 单条结果上限 = `clamp(S / N, 下限, 上限)`（例如 400–4000）；截断预览 = `min(400, 上限/3)`；`sk_result_read` 的缺省值 = 上限减 JSON 包装开销，上限由运行时钳制。所有值随预算变化，不写死。
- GWT-3：Given 模型自选 `max_chars`，Then 运行时把它钳制在 `[1, 当前可用分片]`；钳制不静默——在结果里说明实际生效值。
- GWT-4：Given 同一 `skill_run_id` 的同一 offset 已被读取，Then 工作上下文给出“已读”提示或去重，不把重复读取当作新的有效证据。
- GWT-5：Given 截断件进入上下文，Then 只呈现 `skill_run_id` / `next_offset` / `read_tool` 与预览；`log_ref` 路径不进入模型可见面，或显式标注“仅留痕，不可直接读”。
- GWT-6：Given 预算/端点变化，Then 同一结果的分片参数随之变化；不存在“用户把总预算调到 256k，单条仍只给 200 字符”的脱钩。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`ContextEnvelope`（[`T-AGT-033.1`](T-AGT-033.1-预算信封与计量口径.md)）· `result_will_truncate` / 预览判定（[`truncation.py`](../../src/st_agent/l3/runtime/truncation.py)）· 回读提示（[`context.py`](../../src/st_agent/l3/runtime/context.py)）· `sk_result_read` 的 `_normalize_max_chars` / `_payload_text`（[`results.py`](../../src/st_agent/l1/builtin/results.py)）。
- 输出：从步骤预算派生的单条上限/预览/分片参数 · 运行时钳制 · “已读 offset”提示或去重 · 截断件的模型可见形态（不含 `log_ref` 路径）。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：L1 不得 import L3，故 `sk_result_read` 与 `truncation.py` 的数值需要经契约/配置或测试钉住，不能直接共享常量。
- **A2**：回读结果本质上是“按需取回的证据”，可豁免普通结果截断；若错，则必须把 `max_chars` 上限压到“包装后仍低于阈值”。
- **A3**：去重只影响上下文提示，不改变真实执行与留痕；若错，需在 L1 增加读取状态。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 大结果外部化与回读

## 备注

证据：`truncation.py:28` 阈值 1200；`context.py:739` 回读提示推荐 `max_chars=2000`；`results.py:36` 缺省 2000、上限 8000。`agr_ec73267803164d28a851` 的 23 个主步骤中 20 步是 `sk_result_read`，14 步结果仍为 truncated，模型多次重复读 offset 0。用户把总预算抬到 256k 后，单条仍只给 200 字符预览——因为两个旋钮没有联动。
