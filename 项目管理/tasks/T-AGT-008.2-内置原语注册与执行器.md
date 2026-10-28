---
id: T-AGT-008.2
parent: T-AGT-008
title: 内置原语注册与执行器
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.1]
status: done
decisions: [D-137, D-139]
verify: 执行日志 [T-AGT-008]——安装面幂等/重绑/冲突/依赖缺失与流水线同口径用例（tests/l1/test_builtin.py）；全量 4578 passed / 2 例既有环境性 ERROR
---

# T-AGT-008.2 · 内置原语注册与执行器

## 目标
L1 在同一注册面登记内置原语并分派执行；权限、沙箱、留痕沿用既有流水线。

## 验收标准（Given-When-Then）
- GWT-1：Given 一条 builtin 描述体，When 经 `SkillRunner` 执行，Then 参数 / 输入 / 输出核验、权限逐项核对、`skill_run` 步与 `execution_log` 与 Skill 同口径。
- GWT-2：Given 未注册执行器，When 执行，Then 显式失败并点名（不静默空跑）。
- GWT-3：Given 原语越界（未声明或未批准的权限），Then 沙箱拦截且**不执行原动作**。

## 接口面
- 输入（消费的前置接口）：
  - [`T-AGT-008.1`](T-AGT-008.1-契约工具源加builtin.md) 交付的 `source="builtin"`（本叶依赖它，不重复改契约枚举）。
  - `SkillRegistry.register`（`src/st_agent/l1/skills/registry.py:165`）与 `skill_id_for`（`src/st_agent/l1/skills/ids.py:82`）——描述体与 id 拼接的唯一入口。
  - `SkillRunner.register_executor` / `run`（`src/st_agent/l1/runner/runner.py:143` / `:220`）——参数 / 输入 / 输出核验、依赖 DAG、权限逐项核对、沙箱与留痕**全部沿用**。
  - `SkillSandbox.session`（`src/st_agent/l1/sandbox/sandbox.py:118`）——文件 / 网络 / 命令三类出口的拦截面。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新增 `src/st_agent/l1/builtin/`：`BuiltinPrimitive`（`base` / `version` / `name` / `description` / `input_schema` / `output_schema` / `parameters` / **必填** `permissions` / `offline_level` / `requires` / 执行器工厂）与 `install_builtins(registry, runner, *, context=None, primitives=None) -> tuple[str, ...]`。
  - 安装语义：**幂等**——已存在的 builtin 描述体跳过、执行器每次重绑（执行器不持久化）；同 `skill_id` 的**非 builtin** 冲突显式拒（不静默覆盖）；`requires` 所列依赖在 `context` 中缺失即在**安装期**显式拒。
  - `BUILTIN_PRIMITIVES` 种子表（本叶为空；后续原语叶按同一形态追加）。
  - 聚合错误类型 `BuiltinRegistrationError`（`l1/builtin/errors.py`）。
  - **不改** `SkillRunner` / `SkillSandbox` 行为；**不接组合根**——首个原语叶（`T-AGT-010.1`）在组合根以 `context` 注入并调用，M8 关卡 `T-INT-009` 做端到端。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**：内置原语的 `permissions` **必须显式声明**（模型字段无默认值）；空声明只用于「仅经注入取数面、不发三类出口」者（同官方 Pack 口径），碰文件 / 网络 / 命令者必须声明。若错的影响：种子形态与安装期校验要改，权限闸门可能被空声明绕过。验证方式：`permissions` 必填的构造期用例 + 沙箱拦截用例。
- **A2**：安装幂等＝描述体已存在即跳过、执行器每次重绑；同 id 非 builtin 冲突显式拒。若错的影响：进程重启后内置原语「注册在、执行器不在」，调用恒失败。验证方式：重复安装两次 + 冲突场景用例。
- **A3**：本叶**不接组合根**、种子表为空，交付的是注册与分派面；首个原语叶随其 L0 句柄一并在组合根调用。若错的影响：本叶结束时出厂仍无可调用原语，需在本叶补一条真实原语并把 L0 句柄接进 L1 装配。验证方式：M8 集成关卡 `T-INT-009` 的端到端。

## 涉及契约
- [03 §1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— 执行器注册与分派
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— 权限声明的逐项核对

## 参考
- `l1/runner/runner.py`（`register_executor`）
- `l1/sandbox/sandbox.py`（三类出口）

## 备注
官方 Pack 目前一律 `permissions=()`——原语**不得**沿用这条，凡碰文件 / 网络 / 命令者必须显式声明并经批准。
