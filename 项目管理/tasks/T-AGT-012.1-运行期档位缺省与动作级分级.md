---
id: T-AGT-012.1
parent: T-AGT-012
title: 运行期档位缺省与动作级分级
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/08-L6-反思演进.md
arch_link: "[08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)"
priority: P0
milestone: M8
depends_on: []
status: todo
decisions: [D-137]
verify:
---

# T-AGT-012.1 · 运行期档位缺省与动作级分级

## 目标
agent 档独立缺省 `autonomous`，风险清单按动作细化（具体规则先于 `skill.` 泛化规则）。

## 验收标准（Given-When-Then）
- GWT-1：Given 出厂配置，When 循环调用**只读**原语，Then 自主执行，不再要求逐步确认。
- GWT-2：Given 写类 / 出网动作，Then 仍需确认；`memory.*` 恒 `never-autonomous`。
- GWT-3：Given 档位条目或清单损坏，Then fail-closed（不放行）。
- GWT-4：Given 演进档位 `evolution.authorization`，Then **不被**本项改变。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) —— 两条档位条目互不影响
- [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) —— 清单是单一事实源

## 参考
- `l6/authorization.py` 的 `classify`（按序取首个前缀命中）
- `l6/runtime_authorization.py`

## 备注
`classify` 是顺序匹配，故「具体规则写在 `skill.` 之前」即可细化，**不需要改判据机制**，只改清单内容与缺省值。
