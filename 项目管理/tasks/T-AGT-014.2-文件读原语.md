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
status: done
decisions: [D-137]
verify: 三条只读原语 + 截断指针 + 收件目录边界全绿；见执行日志 [T-AGT-014.2]
---

# T-AGT-014.2 · 文件读原语

## 目标
提供 `file_read` / `file_glob` / `file_grep`，经沙箱 `local_read` 核对。

## 验收标准（Given-When-Then）
- GWT-1：Given 声明范围内的路径，When 读，Then 返回内容。
- GWT-2：Given 越界路径，Then 拦截且**不读取**。
- GWT-3：Given 超大文件，Then 截断并给指针。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-014.1`——`local_read` 作用域语义与 `workspace` 分区。
  - `T-AGT-008.3`——内置原语注册面与实参双通道（`ctx.inputs`）。
  - [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的收件目录（`<存储根>/inbox`）。
  - L1 沙箱的 `SandboxSession.read_file`（`l1/sandbox/sandbox.py`）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `sk_file_read` / `sk_file_glob` / `sk_file_grep`（声明 `local_read:<workspace/**>` 与 `local_read:<inbox/**>`）。
  - L0 `FileWorkspace`（`src/st_agent/l0/file_workspace.py`）：`split` / `read` / `read_text` / `list_files`。

## 可关闭的遗留
无（六册未闭区逐册核过，无归属＝本任务的条目）。

## 假设与前提
- **A1** 读取只列 / 读普通文本文件，二进制与超大文件如实跳过或回 `unavailable`，不冒充。若错的影响：模型把空文本当文件内容。验证方式：`tests/l1/test_builtin_files.py` 的二进制与截断用例。
- **A2** 越界 / 未批准由沙箱拦截且不执行读取。若错的影响：越权读取。验证方式：未批准用例与 `FileWorkspace.split` 边界用例。
- **A3** 大文件以 `max_chars` 截断并回 `next_offset`。若错的影响：长文件被静默截断。验证方式：截断续读用例。
- **A4** 收件目录只读、按 realpath 判界。若错的影响：符号链接越界读。验证方式：`test_file_workspace` 的 `TestInbox` / `TestPathBoundary`。

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `local_read` 的作用域
- [D-031](../../docs/技术架构-v2/01-平台共享契约.md) —— 读缓存数据不属于 `local_read` 范围

## 参考
- `l1/sandbox/sandbox.py:237`（`read_file` 核对已实现、无执行器使用）

## 备注
`local_read` 的语义边界须先厘清：D-031 ③ 已定「读 `data_cache` 内的缓存数据**不是** `local_read` 的范围」。
