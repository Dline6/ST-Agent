---
id: T-AGT-028.1
parent: T-AGT-028
title: 会话级覆盖与三档切换
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-020]
status: done
decisions: [D-154, D-159]
verify: 执行日志 [T-AGT-028.1]——会话覆盖判据单点（L6）+ 组合根按签名绑会话 + GET/POST 会话档位端点 + 右栏档位选择器；范围 2545 passed / 2 errors（既有环境性）；verify_docs --strict PASS；实现 commit f8432a3
---

# T-AGT-028.1 · 会话级覆盖与三档切换

## 目标
窗口内切换 `manual` / `collaborative` / `autonomous`；覆盖为会话内瞬态，下一步生效；全局切换仍走既有配置与留痕。

## 验收标准（Given-When-Then）
- GWT-1：Given 会话内切到 `manual`，Then 下一步只读动作也需确认。
- GWT-2：Given 切回 `autonomous`，Then 只读自动、写仍需确认。
- GWT-3：Given 会话结束，Then 覆盖消失，不影响全局配置。

## 接口面

**输入**

- `RuntimeAuthorization`（[`T-AGT-005.1`](done/M5/T-AGT-005.1-运行期授权档位与共享清单.md)，`src/st_agent/l6/runtime_authorization.py`）：`tier()` 全局档、`decide(action_id)` 判据、`classify` 转发共享清单（单一事实源）。本叶在其读面与判据参数上扩展，**不新建第二套判据**。
- `ActionGate`（[`T-AGT-005.2`](done/M5/T-AGT-005.2-逐步闸门与终止交还.md)，`src/st_agent/l3/runtime/gate.py`）：`authorize(skill_id, tool_name, arguments)`，判据端口鸭子口 `decide(action_id)`——**本批零改动**。
- `_AgentInvestigator`（[`T-INT-006`](done/M5/T-INT-006-M5集成关卡受控自主闭环.md)，`src/st_agent/composition/agent.py`）：每次运行构造 `ActionGate(...)`，运行携带 `session_id`（`investigate` / `resume`）。
- `DialogFacade`（`src/st_agent/composition/facades/chat.py`）· `ChatSurface`（`src/st_agent/ui/surfaces/chat.py`）· 路由表（`src/st_agent/ui/server.py`）· 前端右栏（`src/st_agent/ui/web/js/pages/chat.js` 的 `mountChat`）。

**输出**

- **L6**：`session_scoped`（能力标记，`True`）、`session_tier(sid)`、`effective_tier(sid)`、`set_session_tier(sid, tier)`、`clear_session_tier(sid)`；`decide(action_id, session_id=None)`（无会话即按全局档）。
- **组合根**：`_SessionBoundDecisions`（`composition/agent.py`）——把判据端口按运行会话绑定；`_AgentInvestigator._gate_for(session)`。
- **门面 / 装配**：`DialogFacade.authorization` 注入 + `session_tier(sid)` / `set_session_tier(sid, tier)`（返回 `ResultEnvelope`）；`_build_l3(authorization=…)`；M4 组合根传 `l6.agent_authorization`（M1–M3 无 L6 ⇒ 窗口控件显式不可用）。
- **端点**（[00 §1.1](../../docs/技术架构-v2/00-架构总览.md)）：`GET /api/chat/session/tier?id=<sid>`、`POST /api/chat/session/tier`（体 `{session_id, tier}`，`tier` 空＝恢复跟随全局）；载荷 `{session_id, tier, source(global|session), global_tier, tiers}`。
- **前端**：右栏顶部「授权档位」选择器（跟随全局 + 三档）与来源标注；无会话 / 未接入时显式禁用并给原因。

## 可关闭的遗留
无（开工 2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3`/`C5`、L5 余 `A1` 皆**人决**，L1–L3 与 L6 无未闭项；无归属本任务的条目，检查 8 = 0）。

## 假设与前提
- **A1**：覆盖的 holder 放 L6 `RuntimeAuthorization`（与档位判据同处，判据不出现第二份）。若错 → 出现两套档位口径、闸门与窗口漂移。验证：`tests/l6/test_runtime_authorization_session.py` 的 `test_face_declares_session_scoped_support` 与 `tests/integration/test_m11_session_tier.py`（窗口改档直达闸门）。
- **A2**：「会话结束」按「进程结束 / 显式恢复跟随全局 / 覆盖只对该 `session_id` 生效」兑现——产品当前**没有**会话删除动作，不新增（负责人 2026-11-01 拍定）。若错 → GWT-3 需补一个会话终止面。验证：`test_gwt3_override_disappears_with_the_process`、`test_override_only_affects_its_own_session`。
- **A3**：前端不缓存档位——切会话、建会、刷新后都向端点取值。若错 → 显示可能与实际判据不一致。验证：真浏览器走查中新建会话（禁用态）、建会后取值、刷新后恢复覆盖态三次观察一致。
