---
id: T-INT-001
parent: null
title: M0 集成关卡 · 骨架打通冒烟
story: ../../docs/PRD-v2-Agent/README.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M0
depends_on: [T-SC-001.1, T-SC-001.2, T-SC-001.3, T-SC-001.4, T-SC-001.5, T-L0-001, T-L0-002, T-L0-003, T-L0-004, T-L0-005.1, T-L0-006, T-L1-001.1, T-L1-001.2, T-L1-001.3, T-L1-001.4, T-L1-001.5, T-L1-001.6, T-L1-002.1, T-L1-002.2, T-L1-002.3, T-L1-003, T-L1-004, T-L1-005, T-L1-006]
status: todo
decisions: []
verify:
---

# T-INT-001 · M0 集成关卡 · 骨架打通冒烟

## 目标
把 M0 分散在各任务里的交付（SC 契约 + L0 基座 + L1 能力池）**装配起来跑一次真实端到端**，用可重复的测试套件证明「骨架打通 / L1 最小闭环」这一里程碑目标成立——而不是把 22 个任务各自 done 当作等价物。

## 验收标准（Given-When-Then）
- GWT-1 装配即用：Given 全新空目录 + 口令，When 经 L1 组合根（T-L1-006）装配运行时（Store → 端点注册/凭据库/出网网关 → SkillRegistry（官方 Pack 播种）→ SkillRunner（注入沙箱）），Then 装配成功且官方 Pack 全部 Skill 可列出。
- GWT-2 一次执行的完整留痕：Given 已装配运行时 + 一个官方 Pack Skill（执行器用离线 Fake 数据），When 执行一次，Then 信封 `ok` · SkillRun 落盘可回放（输入快照/输出/耗时/依赖链）· Trace 含对应步骤 · 网关审计有记录 · 结果的 `as_of` 与证据引用口径符合 01 §5/§8。
- GWT-3 失败路径不编造：Given 数据源不可用，When 执行，Then 信封 `unavailable` 且带最后更新时间；Given 上游依赖 Skill 失败，When 执行下游，Then 下游 `dependency_failed`，不得用错误数据继续。
- GWT-4 越界拦截端到端：Given 已装配 runner + 沙箱，When 执行器向声明外主机出网或读声明外路径，Then 拦截 + `BehaviorViolation` 留痕 + 底层 sender/文件系统未被触碰。
- GWT-5 数据闭环：Given 已产生执行留痕，When 备份 → 完全清空 → 恢复 → 重新装配，Then 运行时可用且此前的 SkillRun 与审计记录可查。
- 回归：默认 `pytest` 全绿（含本关卡）；`verify_docs.py --strict` 通过；本关卡用例全部离线可跑（真实网络部分标 `live` 另跑）。

## 接口面
- 暂无（④ 对齐时补：**输入**＝T-L1-006 的装配入口、各任务已交付的公共 API 与落盘位置；**输出**＝`tests/integration/` 端到端冒烟套件与可重复的装配脚本 + 本里程碑接口的实际调用面清单）

## 假设与前提
- 暂无（④ 对齐时补：A<n> 编号，每条含前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`）

## 涉及契约
- [00-架构总览 §5](../../docs/技术架构-v2/00-架构总览.md) 端到端数据流（本关卡 GWT 的锚点）
- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 ResultEnvelope / §8 时间锚点 / §10 权限 / §11 事件
- [02-L0](../../docs/技术架构-v2/02-L0-本地优先基座.md) §2 存储 / §4 LLM 端点 / §6 出网审计 / §8 备份
- [03-L1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) §1 Skill Runtime / §1.5 执行沙箱

## 参考
- Story（What）：[PRD 索引](../../docs/PRD-v2-Agent/README.md)
- 里程碑定义：[AI-Coding项目管理方案 §7](../AI-Coding项目管理方案.md)
- 机制出处：集成关卡（[工作流.md](../工作流.md)）

## 备注
**不重复**各任务单测已覆盖的单元行为——只测装配关系与跨层数据流。用例放 `tests/integration/`；真实网络路径标 `@pytest.mark.live` 放 `tests/live/`，默认排除。实现细节不写此处，留给代码 / commit / 执行日志。
