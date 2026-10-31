---
id: T-AGT-019
parent: null
title: MCP resources 接入
story: ../../docs/PRD-v2-Agent/story-08-mcp-hub.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §5.5](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P2
milestone: M10
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-152]
verify: 执行日志 [T-AGT-019]——capabilities 判定与 -32601 → unavailable、分页 100 页上限、text/blob 内容规范化；两条原语经 SkillRunner 留痕；范围 2701 passed / verify_docs --strict PASS；实现 commit fe88dbe
---

# T-AGT-019 · MCP resources 接入

## 目标
把 MCP 的 resources（list / read）接进来——现在只接了 tools。

## 验收标准（Given-When-Then）
- GWT-1：Given 已连 MCP Server，When `list_resources`，Then 返回资源清单。
- GWT-2：Given `read_resource`，Then 返回内容并留痕。
- GWT-3：Given Server 不支持 resources，Then 显式 `unavailable`。

## 接口面
- 输入（消费的前置接口）：
  - `McpServerRegistry`（`src/st_agent/l1/mcp/registry.py:73`）——Server 记录、启用态与 `_make_transport`（stdio / 远程经 L0 网关）。
  - `McpTransport.initialize()` 的回执（`src/st_agent/l1/mcp/transports.py:182`）——Server capabilities；本任务把它留存为 `McpTransport.capabilities`，作 GWT-3 的能力判定依据。
  - `install_builtins` 的注入面（`src/st_agent/l1/runtime.py:463`）——新增注入键 `mcp_servers`。
  - `DEFAULT_RISK_GRADING`（`src/st_agent/l6/authorization.py:83`）——新原语的动作级分级。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `McpClient.list_resources()` / `McpClient.read_resource(uri)`（`src/st_agent/l1/mcp/client.py`）；新模型 `McpResourceSpec` / `McpResourceContent`（`src/st_agent/l1/mcp/models.py`）。
  - `McpServerRegistry.list_resources(server_id)` / `McpServerRegistry.read_resource(server_id, uri)`（`src/st_agent/l1/mcp/registry.py`）。
  - 内置原语 `sk_mcp_list_resources` / `sk_mcp_read_resource`（新增 `src/st_agent/l1/builtin/mcp_resources.py`，登记进 `BUILTIN_PRIMITIVES`）。
  - 契约 [03 §5.5](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)（新增 resources 小节）；分级规则落 `src/st_agent/l6/authorization.py`。
  - 测试：新增 `tests/l1/test_mcp_resources.py` / `tests/l1/test_builtin_mcp_resources.py`；同步 `tests/l1/test_builtin_data.py` 种子表与 `tests/l6/test_authorization.py`。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 兜底）。

## 假设与前提
- **A1**：能力判定取 `initialize` 回执的 `capabilities.resources`；Server 未声明该能力、或 `resources/list|read` 返回 JSON-RPC `-32601`，一律显式 `unavailable`，不发第二次请求。若错的影响：未声明能力却实现 resources 的 Server 会被按不可用拒绝。验证方式：真 stdio stub 的「支持 / 不支持」两种形态各一例。
- **A2**：`list_resources` 自动跟随 `nextCursor` 直到取尽；页数上限 100，超限显式失败而不是静默截断。若错的影响：超大清单会显式报错。验证方式：分页 stub 用例断言跨页拼接与上限。
- **A3**：`read_resource` 把 `contents` 原样规范化为 `uri` / `mime_type` / `text` / `blob`（`text` 与 `blob` 恰一）；大内容交给循环既有截断 + `sk_result_read`，不在此另设截断。若错的影响：上下文超长由既有阈值兜住。验证方式：文本 / blob 两例。
- **A4**：两条原语分级均 `autonomous-ok`（读操作；远程 Server 的数据外发在注册时已显式确认，调用经 L0 网关审计）。若错的影响：读到远程资源不再逐步确认。验证方式：`tests/l6/test_authorization.py` 分级用例。
- **A5**：原语 `permissions=()`——能力经**注入的** MCP 注册表消费，Server 自身权限在 `mcp-permission` 账本逐项批准，原语不另开出口。若错的影响：沙箱不额外拦原语本身。验证方式：安装期权限校验 + 既有 sandbox 用例。
- **A6**：留痕沿用 `SkillRunner` 的 `skill_run` 记录，不另建审计分区。若错的影响：资源读取的审计只在 execution_log。验证方式：经 `SkillRunner.run` 的用例断言记录可查。

## 涉及契约
- [03 §5.5](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— MCP resources（本任务新增小节）
- [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 工具暴露面（`builtin` 来源与注册面＝可见面）
- [01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 权限模型（Server 侧声明与批准）

## 参考
- `l1/mcp/client.py` / `transports.py`

## 备注
只有接外部 MCP 时才用得上，故排 M10。
