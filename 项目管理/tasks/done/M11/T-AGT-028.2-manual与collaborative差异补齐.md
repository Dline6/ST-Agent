---
id: T-AGT-028.2
parent: T-AGT-028
title: manual 与 collaborative 差异补齐
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-028.1]
status: done
decisions: [D-154, D-159]
verify: 执行日志 [T-AGT-028.2]——manual 每步单独批且不自动续跑（manual_step_done）、collaborative 确认后继续、红线三档皆 denied；同批范围 2545 passed / 2 errors；verify_docs --strict PASS；实现 commit f8432a3
---

# T-AGT-028.2 · manual 与 collaborative 差异补齐

## 目标
现两档在闸门上结论相同；给 `manual` 真实差异（每步单独批、不自动续跑），`collaborative` 保持一次确认放行本步。红线两档皆拒。

## 验收标准（Given-When-Then）
- GWT-1：Given `manual`，Then 每步单独确认且不自动续跑。
- GWT-2：Given `collaborative`，Then 一次确认可放行本步。
- GWT-3：Given 红线动作，Then 任何档位都拒绝。

## 接口面

**输入**

- `RuntimeAuthorization.decide` / `effective_tier(sid)`（本批 `.1` 扩展，`src/st_agent/l6/runtime_authorization.py`）——档位 × 风险类的闸门结论**两条档位仍一致**（须确认 / 红线拒绝），差异只在确认之后。
- `resume_agent_run`（[`T-AGT-011.3`](../M8/T-AGT-011.3-待确认与待输入步骤的一键续跑.md)，`src/st_agent/l3/runtime/continuation.py`）：`decision="approve"` 当前「先执行待确认步，再进下一轮循环」。
- `_AgentInvestigator.resume`（[`T-INT-006`](../M5/T-INT-006-M5集成关卡受控自主闭环.md)，`src/st_agent/composition/agent.py`）：续跑入口，持有判据面与运行的 `session_id`。

**输出**

- `resume_agent_run(..., continue_after_approval: bool = True)`：`False` ⇒ 执行完待确认步即以新终止原因收口，不进下一轮循环。
- 新终止原因 **`manual_step_done`**：`l3/runtime/loop.py` 的 `TerminationReason`、`l3/render/describe.py` 的 `AGENT_TERMINATION_LABELS`、`l3/runtime/report.py` 的零步映射同批补齐。
- `_AgentInvestigator._continues_after_approval(session)`：按 `effective_tier(session) == "manual"` 传 `False`；档位读不出 ⇒ 按协作档形态（闸门仍逐步 fail-closed）。
- 契约（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）：两档差异表 + `manual_step_done` 的语义。

## 可关闭的遗留
无（开工 2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无归属本任务的条目；检查 8 = 0）。

## 假设与前提
- **A1**：`manual` 的「不自动续跑」＝批准后**只执行该步即收口**、不发起下一轮模型调用（负责人 2026-11-01 拍定）。若错 → 需改为「续跑但每步再确认」，留痕形态与终止原因都要改。验证：`tests/l3/test_agent_resume.py::test_manual_approval_executes_the_step_and_stops`（模型脚本未被消费）。
- **A2**：用**新终止原因** `manual_step_done` 表示「按确认执行一步后主动停下」（负责人拍定，不复用 `denied`）。若错 → 留痕会与红线拒绝混淆。验证：`test_collaborative_approval_keeps_going_until_the_model_finishes` 对照 + `tests/l3/test_agent_report.py` 的终止原因穷尽映射。
- **A3**：两档对红线（`never-autonomous`）仍一律 `denied`，本次不放宽任何既有约束。验证：`tests/l6/test_runtime_authorization_session.py::test_red_line_is_denied_in_every_tier`。
