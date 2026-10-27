---
id: T-AGT-014.1
parent: T-AGT-014
title: 契约：local_write 与工作区分区
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-014.1 · 契约：local_write 与工作区分区

## 目标
权限动作增 `local_write:<path-scope>`；存储分区增**用户工作区 / 导出目录**。

## 验收标准（Given-When-Then）
- GWT-1：Given [01 §10](../../docs/技术架构-v2/01-平台共享契约.md)，When 增 `local_write`，Then 权限解析、沙箱核对、批准流三处同口径。
- GWT-2：Given [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md)，When 增工作区分区，Then 独立导出 / 独立清空成立，备份口径显式。
- GWT-3：Given 存量三类权限，Then 行为逐字节不变。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— 四类权限动作
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 第八个逻辑分区

## 参考
- `contracts/registry_types.py:163/166`（三动作 + 硬编码正则）

## 备注
**D-6 拍定**：写入落**新分区**（不走 `data_cache` 子目录、不落系统用户目录）——前者会与缓存语义混淆，后者绕开加密与一键备份。
