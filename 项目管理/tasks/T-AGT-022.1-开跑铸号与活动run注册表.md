---
id: T-AGT-022.1
parent: T-AGT-022
title: 开跑铸号与活动 run 注册表
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020]
status: done
decisions: [D-154]
verify: "执行日志 [T-AGT-022.1]；新增 6 例；范围套件 1407 passed；commit 待回填"
---

# T-AGT-022.1 · 开跑铸号与活动 run 注册表

## 目标
在循环**开始前**铸出 `agent_run_id`，并以进程内注册表持有活动 run 的控制句柄；收口留痕沿用同一标识（现状是 `conclude_agent_run` 跑完才铸号）。

## 验收标准（Given-When-Then）
- GWT-1：Given 一次运行开始，When 读取返回，Then `agent_run_id` 已存在且与收口留痕同一标识。
- GWT-2：Given 活动 run，Then 可按 id 取到控制句柄；结束后注册表移除该 run。
- GWT-3：Given 同一会话已有活动 run，When 再次启动，Then 显式拒绝（D-154 B9）。

## 接口面

**输入**（本任务消费的上游接口）
- `st_agent.l3.runtime.report.new_agent_run_id()`——`agent_run_id` 的既有铸造口径（`AgentRunId.generate()`，[01 §1](../../docs/技术架构-v2/01-平台共享契约.md)），本任务只改「何时铸」，不改形态。
- `st_agent.composition.agent._AgentInvestigator.investigate / .resume`——组合根 `build_m5_runtime` 注入的循环端口（`src/st_agent/composition/runtime/m5.py`），开跑与续跑的唯二入口。
- `st_agent.l3.runtime.report.conclude_agent_run` / `continuation.resume_agent_run`——收口落痕的两个出口；本任务给两者各增可选 `agent_run_id` 入参，缺省行为不变（存量调用方零改动）。

**输出**（本任务交付的公共 API 与落盘位置）
- `st_agent.l3.runtime.ActiveRun`——活动 run 的控制句柄：`agent_run_id` / `session_id` / `started_at` / `cancel_requested` / `request_cancel()`。
- `st_agent.l3.runtime.ActiveRunRegistry`——进程内注册表：`register(agent_run_id, session_id)`（同会话冲突抛 `RunAlreadyActiveError`）、`get(agent_run_id)`、`active_for_session(session_id)`、`finish(agent_run_id)`、`active_ids()`；线程安全（后台 run 是 T-AGT-023/029 的事，本叶先用锁兜住跨线程读写）。
- `st_agent.l3.runtime.RunAlreadyActiveError`——同会话冲突的显式错误（`AgentRuntimeValidationError` 子类，总线按 `validation_failed` 呈现）。
- `_AgentInvestigator.runs`——注册表读面（组合根可注入共享实例；缺省自建）。
- `conclude_agent_run(..., agent_run_id=...)` / `resume_agent_run(..., agent_run_id=...)`——收口沿用调用方铸的标识。
- **落盘**：无新增落盘。注册表是进程内瞬态，结束后移除。

## 可关闭的遗留
无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 done」的条目；检查 8 兜底）。

## 假设与前提
- **A1**（注册表为进程内瞬态、不落盘）——若错的影响：跨进程（常驻后端 ↔ 可分离 UI）看不到活动 run，停止 / 重连需另立持久化面。验证方式：实现中不加任何 `Store` 写；重启进程后注册表为空由结构保证，`T-AGT-024.*` / `T-AGT-023.1` 开工时复核。
- **A2**（会话级互斥只在 `session_id` 非空时生效）——若错的影响：无会话的单次查证可并发，可能在「同一时刻只有一个活动 run」上留下缺口。验证方式：GWT-3 只对带会话的两次启动断言拒绝；无会话路径的并发语义在 `T-AGT-029.1` 入口改造时复核。
- **A3**（本叶仍**同步执行**：开跑即铸号，但 `investigate` 调用仍等循环跑完才返回）——若错的影响：`POST /api/chat` 立即返回 `agent_run_id` 的后台化需在 `T-AGT-029.1` / `T-AGT-023` 补齐入口，届时本叶的注册表可直接复用。验证方式：GWT-1 以「循环执行期间注册表可见该 id」为判据，不以「调用立即返回」为判据。
- **A4**（控制句柄本期只承载身份与「取消已请求」标记）——若错的影响：取消向执行面的透传与 `cancelled` 终止归 `T-AGT-024.1`，需扩展句柄而非重造注册表。验证方式：本叶只断言 `get()` 可取到句柄、`finish()` 后移除；不实现取消终止。
