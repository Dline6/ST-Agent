---
id: T-AGT-012
parent: null
title: 自主度出厂口径
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/08-L6-反思演进.md
arch_link: "[08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md)"
priority: P0
milestone: M8
depends_on: []
status: done
decisions: [D-137, D-143]
verify: 两叶合成——012.1 出厂自主的只读原语 + 012.2 设置页；见执行日志
---

# T-AGT-012 · 自主度出厂口径

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-012.1`](T-AGT-012.1-运行期档位缺省与动作级分级.md) / [`T-AGT-012.2`](T-AGT-012.2-设置页暴露运行期档位与分级.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让「按风险分级」在**出厂配置下**就成立——现在出厂一步都执行不了。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由档位与设置页两叶合成。

## 接口面
- 输入（消费的前置接口）：
  - 共享清单 `DEFAULT_RISK_GRADING` / `classify()`（[`l6/authorization.py`](../../../../src/st_agent/l6/authorization.py)）
  - `RuntimeAuthorization` 档位面（[`l6/runtime_authorization.py`](../../../../src/st_agent/l6/runtime_authorization.py)）
  - 内置只读原语 `sk_local_sql` / `sk_data_status`（[`l1/builtin/`](../../../../src/st_agent/l1/builtin)）
  - 01 §7 统一门面与 `agent_family`（[`l6/registry_adapter.py`](../../../../src/st_agent/l6/registry_adapter.py)）
  - 设置区读 / 写面、路由与前端页面登记（[`ui/`](../../../../src/st_agent/ui)）
- 输出（本任务交付的公共 API / 落盘位置）：
  - `DEFAULT_AGENT_TIER = "autonomous"` 与只读原语的两条具体分级规则（`.1`）
  - `RuntimeAuthorization.tier_source()` / `.rollback(change_id)`（`.2`）
  - `ReflectionFacade.agent_authorization()` / `.rollback_agent_change()`（`.2`）
  - 设置区 `?view=agent` 页 + `GET /api/settings/agent-authorization` / `POST /api/settings/agent-authorization/rollback`（`.2`）
  - 契约 / IA：[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) / [08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md) / [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [11-sitemap](../../../../docs/PRD-v2-Agent/11-sitemap.md)

## 可关闭的遗留
无（两叶逐册核过，均无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）

## 假设与前提
- A1：两叶合成交付，父节点不单独验收；接口面按各叶 `## 接口面` 汇总。若错：父节点被误当实现任务。验证：`.1` / `.2` 各自 GWT 与 `render_ledger.py` 的父状态派生。
- A2：出厂自主只放开**显式登记为只读**的原语，不放开整张清单、不改演进档（同 `.1` A1–A2）。若错：GWT-1 / GWT-4 不成立。验证：`.1` 的清单顺序与档位独立用例。
- A3：设置页以设置区页内视图承载，回滚经新固定路由（同 `.2` A1–A3）。若错：须改 IA 路径集与回滚机制。验证：`.2` 的 UI / 集成用例。

## 涉及契约
- [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 运行期授权条目
- [08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md) —— 共享风险清单

## 参考
- 调研报告 §13.4
- `l6/authorization.py:64/95`
- `tests/integration/test_m5_autonomy.py:132`

## 备注
**这是整个改造的开关**：不改它，加多少工具都是摆设。且它可单独先发——现有 12 个 skill 立刻可自主串联。
