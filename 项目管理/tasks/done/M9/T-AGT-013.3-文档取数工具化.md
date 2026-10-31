---
id: T-AGT-013.3
parent: T-AGT-013
title: 文档取数工具化
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §2.1](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-145]
verify: "全量 4723 passed / 16 deselected / 2 errors（两例 test_shell 环境性）；新增 tests/l0/test_info_base.py 一致性 2 例、tests/l1/test_builtin_m9.py::TestDocs 4 例；见 执行日志 [T-AGT-013.3]"
---

# T-AGT-013.3 · 文档取数工具化

## 目标
把已存在的文档取数面（`list_docs` / `read_doc` / `search_docs`）暴露成原语。

## 验收标准（Given-When-Then）
- GWT-1：Given 一批公告，When 调三个面，Then 分别返回元数据 / 正文 / 检索结果。
- GWT-2：Given「表在而文件缺」，Then 判**损坏**并提示，不返回空正文冒充。
- GWT-3：Given「文件在而表缺」，Then 判**孤儿**并可回收。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`：`install_builtins(context=…)` 与参数双通道（标量 `parameters`、对象 `input_schema`）。
  - L0 `st_agent.l0.info.docs.LocalDocSource` 的三面：`list_docs` / `read_doc` / `search_docs`（元数据经 `MarketDb.query`、正文经 `DocStore`；Skill 不直读文件，D-031）。
  - **本任务新增** `LocalDocSource.consistency(domain=None)`：读两域元数据表引用的 `file_path` 与盘上文本文件集合，复用 `audit_docs` 给出 `missing_file` / `orphan_file`。
  - `T-AGT-012.1` 运行期授权判据（只读 → `autonomous-ok`）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新原语 `sk_docs`（`sk_docs_v1.0`，`source="builtin"`）：`operation` ∈ `list` / `read` / `search` / `audit`；其余参数 `domain` / `code` / `doc_id` / `query` / `limit`。
  - `LocalDocSource.consistency(...)` 公共读面（只读，不删除）。
  - 风险规则 `skill.sk_docs → autonomous-ok`（写在 `skill.` 通则之前）。

## 可关闭的遗留
无（同 `T-AGT-013.1` 的逐册核对结论：无归属或解封条件指向本任务者）。

## 假设与前提
- **A1（工具形态）**：三个面 + 一致性核对收进**一条**原语（`operation` 枚举），不拆三条——控制工具目录规模（循环工作上下文预算），三面语义不变。若错 ⇒ 模型侧发现粒度不合；验证：单测逐 operation 覆盖 GWT-1～3。
- **A2（损坏不冒充）**：`read` 遇「表在而文件缺」直接透传 L0 的 `unavailable`（不返回空正文）。验证：seed 一行元数据但不落正文文件，断言 `unavailable` 且原因含「损坏」。
- **A3（孤儿只判不收）**：`audit` 返回 `missing_file` 与 `orphan_file`，孤儿项标 `recyclable=true`；**不**在只读工具里执行删除（写动作须确认，若要求模型可回收需另立写类原语）。若错 ⇒ 回收需人工 / 后续任务；验证：单测断言孤儿被点名且工具未删除任何文件。
- **A4（只读风险类）**：文档三面只读本地缓存（`data_cache` 数据访问，不在 `local_read` 权限范围）→ `autonomous-ok`。验证：L6 用例断言 `classify("skill.sk_docs") == "autonomous-ok"`。

## 涉及契约
- [02 §2.1](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— `data_cache` 的两段构成
- [D-031](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 文本存储与一致性判据

## 参考
- `l0/info/docs.py:141/195/221`

## 备注
D-031 ③ 已定「L0 提供文档取数面、Skill 不直读文件」——本叶只是把它投影成工具，不新增读取方式。
