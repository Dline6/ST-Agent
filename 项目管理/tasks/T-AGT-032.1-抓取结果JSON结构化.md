---
id: T-AGT-032.1
parent: T-AGT-032
title: 抓取结果 JSON 结构化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone:
depends_on: [T-AGT-015.2, T-AGT-017.2]
status: done
decisions: [D-090, D-167]
verify:
---

# T-AGT-032.1 · 抓取结果 JSON 结构化

## 目标

让 `sk_web_fetch` 对 `application/json` 响应返回**结构化载荷**（解析后的 dict/list，或明确的字段投影），而不是把原始 JSON 文本塞进 `text` 再被 `sk_result_read` 二次转义；模型应能直接按字段读取，不必在多层 `\"` 中猜。

## 验收标准（Given-When-Then）

- GWT-1：Given `Content-Type: application/json` 且正文是合法 JSON，Then `envelope.data` 提供可直接按字段读取的结构化形态；保留 `fetched_at` / `url` / `truncated` 等元数据。
- GWT-2：Given 正文不是合法 JSON，Then 回退为文本形态并在 `note` / 原因中显式说明，不把坏 JSON 当成结构化成功。
- GWT-3：Given `sk_result_read` 回读该结果，Then 返回的分片是可读的 JSON 文本，不出现 `{\"text\": \"{\\\"rc\\\"...` 这类双重转义。
- GWT-4：Given 大数组/大对象，Then 仍受工作上下文预算与截断策略约束，不因结构化而绕过体量控制。

## 接口面

- **输入**：`extract_fetched_text`（[`l0/net/fetch.py`](../../src/st_agent/l0/net/fetch.py)，L0 只解码、不解析 JSON）· `sk_web_fetch` 执行器（[`l1/builtin/web.py`](../../src/st_agent/l1/builtin/web.py)，L1 用标准库 `json` 解析）· 结果回读 `_payload_text`（[`l1/builtin/results.py`](../../src/st_agent/l1/builtin/results.py)）。
- **输出**：`sk_web_fetch` 输出 schema 新增 `format`（`json` / `text`）· `data`（对象）· `records`（数组）；合法 JSON 且完整报文不超 `max_chars` 时给结构化字段、不再把原 JSON 塞进 `text`；超限时只给截断文本并显式标注；坏 JSON 回退文本并在 `note` 写明原因。`sk_result_read` 的分片来自 `_payload_text(envelope.data)`，结构化后不再出现被转义成字符串的 JSON。

## 可关闭的遗留

无（L0 / L1 / L3 遗留册未闭区均无归属本任务或解封于本任务 `done` 的条目）。

## 假设与前提

- **A1**：`sk_web_fetch` 的公开输出 schema 可以新增字段而不破坏既有消费方。若错的影响：需版本化或保留 `text` 兼容。验证方式：既有 `tests/l0/test_web_fetch.py` / `tests/l1/test_builtin_web.py` 全绿，新增用例只读新增字段。
- **A2**：JSON 解析在 L1 完成，用标准库 `json`，不引第三方依赖。若错的影响：改用 L0 已有解析器并调整层间分工。验证方式：L1 用例断言对象 / 数组 / 坏 JSON 三态。
- **A3**：结构化后仍保留「网页正文是不可信数据」的提示，不因变成 dict 放松注入防护。若错的影响：模型可能把正文当指令；验证方式：结构化成功用例断言 `note` 含该提示。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 取数原语与结果外部化

## 备注

证据：`run_6a57d206aaa34420a92a` 的 `envelope.data` 为 `{url, host, title, text: "{\"rc\":0,...}"}`；`sk_result_read` 又对整条 dict 做一次 `json.dumps`，模型看到的摘要含 `\\\"content_type\\\"`。
