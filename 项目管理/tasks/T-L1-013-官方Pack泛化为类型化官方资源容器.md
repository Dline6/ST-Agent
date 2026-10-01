---
id: T-L1-013
parent: null
title: 官方 Pack 泛化为类型化官方资源容器
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1–§2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P1
milestone: M2
depends_on: []
status: done
decisions: [D-070]
verify: 四叶全 done（`.1` 契约 / `.2` 容器 / `.3` 命令与规则库 / `.4` Onboarding）· 全量 pytest 2267 passed / 8 deselected · verify_docs.py --strict 全过 · 销 L3 册 A1 · 执行日志 [T-L1-013] 2026-10-01
---

# T-L1-013 · 官方 Pack 泛化为类型化官方资源容器

## 目标

把「官方 Pack」从**只装 Skill 描述体**（[`l1/skills/pack.py`](../../src/st_agent/l1/skills/pack.py) 的 `OFFICIAL_PACK`）泛化为**类型化官方资源容器**，使架构文档里三处「可随官方 Pack 更新」的声明**字面成立**：

- [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 的中立性规则库
- [04 §7](../../docs/技术架构-v2/04-L2-记忆图谱.md) 的 Onboarding 问题清单
- [05 §8](../../docs/技术架构-v2/05-L3-对话主入口.md) 的快捷指令注册表

容器承载**类型化资源条目**（`ResourceEntry(kind, version, payload)`），kinds 走**开放注册**（不预置未实现的 kind，避免死代码）；L1 提供统一装载入口按 kind 分发到各消费方的 loader。本任务同时**关闭 [L3 册 `A1`](../遗留问题/L3-遗留问题.md)**（快捷指令「可随官方 Pack 更新」的 Pack 侧承载）。

实现顺序遵守 [铁律 8](../../项目管理/工程宪法.md)：先改 [01 平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) → 同步各层架构文档 → 最后改代码。

## 拆分记录（③ 已执行）

命中拆分触发（涉及 4 份架构文档 + 4 层代码，远超 5 条 GWT；横跨 [01 §6/§7/§9](../../docs/技术架构-v2/01-平台共享契约.md)、[03 §1/§2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)、[04 §7](../../docs/技术架构-v2/04-L2-记忆图谱.md)、[05 §8](../../docs/技术架构-v2/05-L3-对话主入口.md) 多个契约小节；预计 Agent 会话轮次 > 20），已拆为叶子子任务：

- `T-L1-013.1` **契约与架构**——01 新增「官方资源包」节（`ResourceEntry` / kinds / 装载语义），03 §1/§2、04 §7、05 §8、01 §6 声明四类资源住 Pack 并经注入取
- `T-L1-013.2` **L1 容器本体与装载入口**——`st_agent/l1/pack/`（`ResourceEntry` / 开放 kind 注册 / 容器 / `load_official_pack` 按 kind 分发）；Skill 种子并入 `skill` kind
- `T-L1-013.3` **命令种子与规则库接线**——L3 `command` kind（`register_source` 接线）★**销 L3 册 `A1`**；contracts `rulepack` kind（解析器 + 惰性解析）
- `T-L1-013.4` **Onboarding 接线与收口**——L2 `onboarding_questions` kind（config 条目缺省），装配分发

`.3` / `.4` 均依赖 `.2`（需先有容器与装载入口）；`.2`–`.4` 依赖 `.1`（铁律 8：契约先行）。父任务 `status` / `depends_on` 由 children 聚合派生，不手填。

## 验收标准（Given-When-Then）

父任务验收由 children 聚合——各叶子的 GWT 见其任务文件。

## 接口面

- **输入（消费的前置接口）**
  - 待 `.1` 交付：01 新增「官方资源包」节的容器契约（`ResourceEntry` 形状 / kinds 注册语义 / 装载语义）
  - 既有：03 §2 的 `OFFICIAL_PACK` 播种面（[`l1/skills/pack.py`](../../src/st_agent/l1/skills/pack.py)）· 01 §7 配置注册表门面（[`l1/registry/facade.py`](../../src/st_agent/l1/registry/facade.py)，`T-L1-012` 交付）
- **输出（本任务交付的公共 API / 落盘位置）**
  - 新增 `src/st_agent/l1/pack/`：容器与装载入口（API 面待 `.1` 契约定稿后由 `.2` 实填）
  - 四类消费方接线：L3 `CommandRegistry.register_source` / contracts `NeutralityGuard` / L2 `OnboardingProtocol` / L1 `SkillRegistry`

## 可关闭的遗留

- [L3 册 `A1`](../遗留问题/L3-遗留问题.md)——**本批关闭**（归属＝本任务；由 `.3` 兑现 Pack 侧承载，收工时移入册尾「已闭（备查）」）

## 假设与前提

- **A1 · 容器的 kinds 走开放注册、不预置未实现项**：契约只定义容器与「kind → loader」的分发语义，具体 kinds（`skill` / `command` / `rulepack` / `onboarding_questions`）由各层 loader 注册。若错（须在契约里固定 closed enum）：返工面＝01 新节的 kinds 表述 + `.2` 容器实现。
- **A2 · 各层保留既有缺省作「无 Pack 时」回退**：Pack 缺席时 `CommandRegistry()` / `NeutralityGuard()` / `OnboardingProtocol` 行为**逐字节不变**（既有用例不因本任务变红）。若错：返工面＝各层缺省语义与全部相关用例。
- **A3 · 规则库经 contracts 级解析器 + 惰性解析**（本任务最大风险点）：规则库有 14 处默认构造（含 [L4](../../src/st_agent/l4/deliberation.py) 的模块级常量 `_OUTPUT_CHECK = NeutralityGuard()`，import 期即固化），而 `contracts` 是最底层、不能反向读 L1。故在 contracts 加一个可被装配层设置的解析器，`NeutralityGuard` 改为**取用时**解析（而非构造时），使 Pack 的规则库对全部消费点生效。若错（人不接受 contracts 级全局钩子，或改为「只在显式注入点生效」）：返工面＝contracts 规则库解析机制 + L4 模块级常量的惰性化 + `.3` 接线。
- **A4 · 容器住 L1**：Pack 是 L1 的产物（[03 §2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)），泛化容器仍在 L1；L2/L3 连 L1 loader 属向下依赖（铁律 7）。若错：返工面＝容器归属与层间方向。

## 涉及契约

- [01 §6 中性化校验](../../docs/技术架构-v2/01-平台共享契约.md)（规则库「可随官方 Pack 更新」）· [§7 配置元模型](../../docs/技术架构-v2/01-平台共享契约.md) · [§9 版本化规范](../../docs/技术架构-v2/01-平台共享契约.md)（更新语义）· **新增节：官方资源包**
- [03 §1 Skill Runtime / §2 官方 Skill Pack](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（容器承载与装载入口）
- [04 §7 Onboarding 协议](../../docs/技术架构-v2/04-L2-记忆图谱.md)（问题清单「可随官方 Pack 更新」）
- [05 §8 快捷指令](../../docs/技术架构-v2/05-L3-对话主入口.md)（注册表「可随官方 Pack 更新」）

## 参考

- 来源：[L3 册 `A1`](../遗留问题/L3-遗留问题.md)（`T-L3-001.3` 开工 ④ 对齐选型）· [D-053](../../项目管理/决策日志.md) ③（§8 更新通道选型 G）
- 先例：[`T-L1-012`](T-L1-012-统一配置注册表门面.md)（L1 自持面 + 01 §7 契约先行 + 跨层经适配器/装配注入）
- 兄弟叶：[`.1`](T-L1-013.1-契约与架构.md) · [`.2`](T-L1-013.2-L1容器本体与装载入口.md) · [`.3`](T-L1-013.3-命令与规则库接线.md) · [`.4`](T-L1-013.4-Onboarding接线与收口.md)

## 备注

实现细节不写此处，留给代码 / commit / 执行日志。
