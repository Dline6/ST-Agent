---
id: T-MEM-001.7
parent: T-MEM-001
title: 记忆写入生产路由与空存储初始化
story: ../../docs/PRD-v2-Agent/story-03-memory-graph.md
arch: ../../docs/技术架构-v2/04-L2-记忆图谱.md
arch_link: "[04 §3](../../docs/技术架构-v2/04-L2-记忆图谱.md)"
priority: P0
milestone: M12
depends_on: [T-L2-002.1, T-L2-002.2, T-L2-004.1]
status: todo
decisions: []
verify:
---

# T-MEM-001.7 · 记忆写入生产路由与空存储初始化

## 目标

让记忆“写得进、读得到”：把 `DialogFacade.write_preference` 接到生产路由（当前只有方法，没有 API/UI 调用）；明确普通对话中**用户显式偏好**的捕获路径（`user_stated` 直写）；补齐首启/空存储初始化（onboarding 或等价 seed），使 `memory` 分区不是只有 `manifest.bin`，查询能返回非空结果。

## 验收标准（Given-When-Then）

- GWT-1：Given 用户通过对话/界面显式表达一条偏好，Then 经生产路由写入一个 `user_stated` 节点，返回 `memory_node_id` / `type`，并留痕；不要求用户再点一次确认（04 §3.2 用户自述直写）。
- GWT-2：Given 普通对话里的**推断**内容，Then 不因本叶被自动写入；推断写入仍走写入白名单（direct/propose）与冲突队列（04 §4）。
- GWT-3：Given 全新存储 `memory` 分区只有 `manifest.bin`，Then 首启 onboarding（或等价初始化）能创建初始节点；完成后查询返回非空；Given 用户跳过 onboarding，Then 空状态显式可查，不假装有记忆。
- GWT-4：Given 写入失败（节点非法/存储不可用），Then 返回显式 `validation_failed` / `unavailable`，不抛裸异常；Given 读后写，Then `sk_memory_read`/`MemoryReader.query` 能看到新节点。
- GWT-5：Given 生产路由，Then 不新增第二套写入实现——仍经 `MemoryWriter` / `MemoryGraph` / `OnboardingProtocol` 的既有面。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`DialogFacade.write_preference`（[`facades/chat.py`](../../src/st_agent/composition/facades/chat.py)）· `MemoryWriter` / `MemoryGraph`（[`writer.py`](../../src/st_agent/l2/memory/writer.py) / [`graph.py`](../../src/st_agent/l2/memory/graph.py)）· `OnboardingProtocol`（[`onboarding.py`](../../src/st_agent/l2/memory/onboarding.py)）· UI 记忆面（[`ui/surfaces/memory.py`](../../src/st_agent/ui/surfaces/memory.py)）· 写端点白名单（[`ui/server.py`](../../src/st_agent/ui/server.py)）。
- 输出：偏好写入生产端点/入口 · 显式偏好捕获口径 · 首启/空存储初始化 · 写后读回用例。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：`user_stated` 直写符合 04 §3.2，不过白名单门；若错，需改走提案/裁决。
- **A2**：普通对话的“显式偏好”识别口径在 ④ 定（确定性关键词/句式，或由模型调用写入动作）；不做无边界自动抽取。
- **A3**：空存储初始化优先复用 `OnboardingProtocol`，不新造 seed 格式；若需要内置 seed 数据，另记决策。

## 涉及契约（链接到具体小节）

- [04-L2-记忆图谱](../../docs/技术架构-v2/04-L2-记忆图谱.md) §3.2 用户显式写入 · §4 写入策略 · §7 Onboarding 与空状态
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §9 memory_op / 偏好写入支

## 备注

证据：`write_preference` 只在 [`facades/chat.py`](../../src/st_agent/composition/facades/chat.py) 定义，`grep` 无生产调用；真实 `D:\Documents\STAgent\memory` 只有 `manifest.bin`，无节点/边，`MemoryReader.query` 每次都返回空。`ui/server.py` 已有 `/api/memory/*` 读面与 onboarding 提交，但没有 `write_preference` 路由。
