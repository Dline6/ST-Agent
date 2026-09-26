---
id: T-L1-007
parent: null
title: stdio 审计边界校正 + MCP 派生 Skill 回收（L1 遗留 A1/A2 收口）
story: ../../docs/PRD-v2-Agent/story-08-mcp-hub.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
priority: P0
milestone: M0
depends_on: [T-L1-001.1, T-L1-002.1, T-L1-002.2]
status: done
decisions: [D-023, D-024]
verify: pytest 898 passed / 0 failed（本批 +18 用例：反注册 4 · 失效与回收 11 · 回调接线 3）· `verify_docs.py` 检查 1–8 全 0（断链 0 / 账本 SYNC / 过期措辞 0 / 假设·接口面 0 缺失）· 未触 L0 与 01 契约（`git status --short` 仅 docs/技术架构-v2 两份 + `l1/{skills,mcp}` + tests/l1 + 项目管理/）· 执行日志 `[T-L1-007]` 2026-09-26T21:16 · PR 待建
---

# T-L1-007 · stdio 审计边界校正 + MCP 派生 Skill 回收（L1 遗留 A1/A2 收口）

## 目标
收口 [L1 遗留册](../遗留问题/L1-遗留问题.md) 仅剩的两条（原归属「人」，本次修正为归属本任务）：
① **A1**——[02 §1](../../docs/技术架构-v2/02-L0-本地优先基座.md) 依赖规则与 §6 开篇、[03 §5.3](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 目前声明「本地 stdio MCP Server 进程……其网络行为仍受 L0 审计」，而实现只覆盖**经网关的请求**（子进程自开的 socket 在 Python 进程内拦不住）——把该声明校正为**显式已知边界 + 缓解项**；
② **A2**——`remove_server` 或 tool 从 Server 消失后，`mcp-mapping/<server>/<tool>.json` 与派生 `skill-registry/<skill_id>.json` 原样留存、仍可被 `list_all()` / 直接 id 寻址到——补**回收**能力。

## 验收标准（Given-When-Then）
- GWT-1 文档如实：Given 02 §1 依赖规则 / §6 出网审计开篇与 03 §5.1 / §5.3，When 读订正后的文本，Then 不再声称「stdio 子进程**自身**的出网受 L0 审计」，而是显式标注为已知边界并给出缓解项（默认仅本地 stdio、`network` 权限逐项批准、Server 禁用即摘其全部 Skill、Server 由用户自选本地二进制）；`verify_docs.py --strict` 断链 0。
- GWT-2 反注册：Given 某 base 有多个版本且挂着待检查标记，When `SkillRegistry.unregister(base)`，Then 该 base **全部版本**描述体与 `skill-update/<base>.json` 标记一并删除；其他 base 与 `execution_log` 审计**不受影响**；base 不存在 → `SkillNotFoundError`（不静默 no-op）。
- GWT-3 Server 移除即回收：Given 一台已装载映射的 Server，When `McpServerRegistry.remove_server`（已接线回调），Then 其 `mcp-mapping/<sid>/*.json` 全部删除、派生 Skill 全部反注册（`list_all()` 与 `get()` 均寻址不到）；非 `mcp-mapped` 来源的 Skill 不受影响；**未接线回调时行为与既有完全一致**。
- GWT-4 tool 消失标失效：Given 某 Server 的某个 tool 从 `tools/list` 中消失，When `sync_tools`，Then 该 tool 的映射标 `vanished`（含变化说明），其派生 Skill 不在 `available_skills()` 中；When 该 tool 回归，Then 映射自愈回 `active`、Skill 重新可用。
- GWT-5 回归：既有用例全绿；`verify_docs.py --strict` PASS；账本 `render` 后 SYNC。

## 接口面
- **输入（消费的前置接口）**
  - T-L1-002.1 `McpServerRegistry.remove_server` / `get_server` / `list_servers`（[registry.py:167](../../src/st_agent/l1/mcp/registry.py)·[:185](../../src/st_agent/l1/mcp/registry.py)·[:194](../../src/st_agent/l1/mcp/registry.py)）
  - T-L1-002.2 `McpSkillMapper.mappings` / `available_skills`（[mapping.py:365](../../src/st_agent/l1/mcp/mapping.py)·[:388](../../src/st_agent/l1/mcp/mapping.py)）、映射记录 `McpToolMapping`（[:149](../../src/st_agent/l1/mcp/mapping.py)）、`MAPPING_PREFIX`（[:68](../../src/st_agent/l1/mcp/mapping.py)）
  - T-L1-001.1 `SkillRegistry.list_versions` / `list_all` / `_skill_path` / `_update_path`（[registry.py:271](../../src/st_agent/l1/skills/registry.py)·[:284](../../src/st_agent/l1/skills/registry.py)·[:311](../../src/st_agent/l1/skills/registry.py)·[:316](../../src/st_agent/l1/skills/registry.py)）、`base_of`（[ids.py:55](../../src/st_agent/l1/skills/ids.py)）
- **输出（本任务交付的公共 API / 落盘位置）**
  - `SkillRegistry.unregister(base) -> tuple[str, ...]`（新增，[registry.py](../../src/st_agent/l1/skills/registry.py)）——删该 base 全部版本 + 待检查标记，返回被删 `skill_id`；只动 `config` 分区、不动 `execution_log`
  - `MappingStatus` 增 `"vanished"`；`McpSkillMapper.recycle_server(server_id) -> tuple[str, ...]`（新增，[mapping.py](../../src/st_agent/l1/mcp/mapping.py)）——删该 Server 全部映射记录 + 反注册派生 Skill
  - `McpServerRegistry.__init__` 增**可选**形参 `on_server_removed: Callable[[str], None] | None = None`（缺省 `None` 时 `remove_server` 行为逐字节不变），由 [T-L1-006](T-L1-006-L1运行时装配组合根.md) 组合根接线到 `mapper.recycle_server`
  - 文档校正：[02 §1 / §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)、[03 §5.1 / §5.3](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
  - 落盘变化：无新分区；回收即删既有 `config` 分区条目

## 可关闭的遗留
- **L1 册 `A1`**（stdio 子进程自身出网不在审计范围；原归属「人」→ 本批**修正归属**为 `T-L1-007`）：**本批关闭**——02/03 文档校正（GWT-1）+ 决策 [D-023](../决策日志.md)。
- **L1 册 `A2`**（映射记录与派生 Skill 不回收；原归属「人」→ 本批**修正归属**为 `T-L1-007`）：**本批关闭**——GWT-2 / GWT-3 / GWT-4 + 决策 [D-024](../决策日志.md)。
- 本任务**新登记**（回收动作暴露的相邻边界，见假设 A5）：① `config/mcp-lifecycle/<server_id>.json` 成为孤儿；② `config/sandbox-disabled/<skill_id>.json` 陈旧旗标（硬删后同 id 重注册会被旧旗标误伤）；③ WorkflowDAG 对已回收 Skill 的悬空引用（[validate.py:201](../../src/st_agent/l1/workflow/validate.py) 报 `unknown_skill`，[composite.py:126](../../src/st_agent/l1/workflow/composite.py) 等未 catch 会抛）。
- 其余无命中（扫 L1 册未闭区：A3→`T-L1-004`、A4→`T-ECO-001`、A5→`T-L5-002` 非本任务；L0 册未闭区 0 条）。

## 假设与前提
- **A1 · stdio 子进程自身出网在本项目内**不可拦截**，只做文档如实 + 产品面缓解**：彻底覆盖需 OS 级进程网络隔离（沙箱 / 代理注入），跨平台代价远超本任务。若错（要求真隔离）：返工面＝需另立任务做沙箱/代理注入，本任务的文档校正反而要再改回去。验证：GWT-1（文本不含「受 L0 审计」式声明）。
- **A2 · 硬回收只针对「Server 被显式移除」这类持久性用户动作；tool 消失视为**可逆**，标 `vanished` 不硬删**：`skill_id` 由 `(server, tool)` 确定性派生，故 tool 回归时同一 id 可自愈复用；硬删会丢掉版本历史、且瞬态探测（Server 侧临时少列一个 tool）会造成不可逆损失。若错（两端都要硬删）：返工面＝`sync_tools` 消失分支 + 自愈语义删除。验证：GWT-4 的 vanished 与自愈两条用例。
- **A3 · 回收由 `remove_server` 的可选回调注入触发，不由 `.1` 反向 import `.2`**：包内依赖须保持 `mapping → registry` 单向（[mapping.py:52](../../src/st_agent/l1/mcp/mapping.py)），回调是唯一不破环的接线。若错（被判定为隐藏副作用 / 要求显式调用）：返工面＝接线方式（改为组合根显式按序调用）+ D-024 修订。验证：GWT-3 的「未接线时行为不变」用例。
- **A4 · 反注册的粒度是「base 全部版本 + 待检查标记」，不提供单版本删除、不删 `execution_log`**：单版本删除会让 `get_latest` / `list_versions` 语义碎裂；`execution_log` 是 append-only 审计（02 §6），不可注销。若错（要求按版本回收）：返工面＝`unregister` 签名 + 调用方。验证：GWT-2 的「多版本 + 标记同清」「审计不受影响」两条用例。
- **A5 · 相邻孤儿（lifecycle 状态记录 / sandbox-disabled 旗标 / DAG 悬空引用）本批**不处理**，仅登记**：三者分属 `.3` 状态机、sandbox 与 workflow 面，改动面超出 A2 声明范围；登记后由后续任务或人认领。若错（要求一并清）：返工面＝回收动作 + 各归属模块 API。验证：本批不承诺（执行日志「遗留」行 + 册内新条目即为留痕）。

## 涉及契约（链接到具体小节）
- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §9 版本化（反注册与版本语义的关系；本任务不改契约）
- [02-L0 §1](../../docs/技术架构-v2/02-L0-本地优先基座.md) 依赖规则 / [§6](../../docs/技术架构-v2/02-L0-本地优先基座.md) 出网审计（措辞校正）
- [03-L1 §5.1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 传输与注册 / [§5.2](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) tool → Skill 映射 / [§5.3](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 状态机与降级（回收语义）

## 参考
- Story（What）：[mcp](../../docs/PRD-v2-Agent/story-08-mcp-hub.md)
- 上游：[T-L1-002.1](T-L1-002.1-传输与Server注册表权限批准.md)（假设 A4 即 A1 的来源）· [T-L1-002.2](T-L1-002.2-tool映射Skill与版本化.md)（假设 A8 即 A2 的来源）· [T-L1-001.1](T-L1-001.1-注册发现参数校验版本管理.md)（`SkillRegistry` 的交付方）

## 备注
回收口径的跨任务复用规则见 [D-024](../决策日志.md)；文档边界定案见 [D-023](../决策日志.md)。回调接线方 [T-L1-006](T-L1-006-L1运行时装配组合根.md)（组合根，尚未开工）开工时须按 `disable → cancel_inflight → remove_server → recycle_server` 顺序接线。实现细节不写此处，留给代码 / commit / 执行日志。
