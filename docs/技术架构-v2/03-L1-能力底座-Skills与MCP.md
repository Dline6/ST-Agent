---
title: L1 · 能力底座：Skills Runtime 与 MCP Hub
layer: L1
priority: P0
depends_on: [01-平台共享契约, 02-L0-本地优先基座]
consumed_by: [04-L2-记忆图谱, 05-L3-对话主入口, 06-L4-多视角推理, 07-L5-主动触达, 09-生态与分享]
stories: [PRD Story 2 — Skills Runtime, Story 6 — Skill Studio, Story 8 — MCP Hub]
---

# 03 · L1 能力底座：Skills Runtime 与 MCP Hub

承载 [Story 2](../PRD-v2-Agent/story-02-skills-runtime.md)、[Story 6](../PRD-v2-Agent/story-06-skill-studio.md)、[Story 8](../PRD-v2-Agent/story-08-mcp-hub.md)。L1 由四个子系统组成：**Skill Runtime**（注册/调度/执行）、**官方 Skill Pack**、**Skill Studio**（可视化编排）、**MCP Hub**（外部能力挂载）。共享类型（SkillDescriptor、ResultEnvelope、权限模型）见 [01-平台共享契约](01-平台共享契约.md)。

## 1. Skill Runtime

### 1.1 职责

注册与发现、调度执行、依赖解析、参数校验、版本管理、执行留痕、输出复用。Skill 本身**无状态**——用户状态只存于 L2 记忆与配置注册表（这是可分享性的前提，见 [09-生态](09-生态与分享.md)）。

### 1.2 执行流水线

一次 Skill 调用 `skill_run_id` 的标准流水线：

1. **解析**：意图来源（L3 对话 / Studio / Deliberation / 工作流 / 定时调度）→ 匹配 SkillDescriptor
2. **参数确认**：参数超出合理范围 → `validation_failed`（拦截 + 说明理由，不允许保存）；经对话触发时展示参数确认卡（用户可调或用默认值）
3. **依赖解析**：递归解析 `dependencies`；**DAG 约束**——检测到循环依赖即拒绝（保存工作流与运行时双重检测）；上游失败 → 下游 `dependency_failed`，禁止用错误数据继续
4. **权限检查**：按 01 §10 权限模型核对本次执行所需权限
5. **执行与留痕**：执行器产出先经描述体 `output_schema` 核验（01 §2 方言澄清，判定件 `contracts.schema_check`）——不合契约 → `failed` + 违规点说明，**不进入下一步**；合契约则包装 ResultEnvelope；记录 SkillRun（输入快照、输出、耗时、错误、依赖链）并挂到 `trace_id`
6. **输出登记**：成功输出登记为可引用结果（见 §1.3）；**未过步骤 5 核验的载荷一律不登记**（禁止把不合契约的载荷按 `ok` 传播给复用方）

### 1.3 输出复用（数据协同）

- 每个 SkillRun 输出可被后续 Skill / 视角 / 报告通过 `skill_run_id` 引用，避免重复计算
- 复用时必须校验 `as_of`（01 §8）：引用过期快照时上层自行决定是否重算；运行时提供「freshness 查询」接口——其数据来源为 L0 数据缓存的同步状态表（见 [数据库设计 §05](../数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)，逐表水位与陈旧判据），不另建第二套口径

### 1.4 版本管理

- 官方 Pack 更新遵循 01 §9：展示变更日志 + 影响范围；用户逐 Skill 选择更新 / 跳过 / 锁定（`version_policy`）
- 契约变化（主版本变更）时，引用方（工作流、视角）自动标「待检查」，用户手动确认后升级——禁止自动破坏下游

### 1.5 执行沙箱

- Skill 执行在受限环境内进行：文件访问限于声明的 `local_read` 范围、网络限于声明的 `net_access` 模式、命令执行需 `exec_command` 审批
- 运行时行为越界 → 拦截 + `BehaviorViolation` 事件（01 §11）+ 警示「这个 Skill 行为异常」+ 提供禁用选项
- 沙箱是 [09-生态](09-生态与分享.md) 导入第三方 Skill 的恶意行为拦截基础

## 2. 官方 Skill Pack

首次安装即内置，无需下载。两 Bundle（名称沿用 PRD，Skill 清单为产品承诺的最小集，可增不可缺）：

### 2.1 公共认知 Bundle

| Skill | 职责 | 关键输出 |
| --- | --- | --- |
| `st-list-sync` | 同步交易所 ST/*ST 名单，识别新增/移除 | 名单变更 diff |
| `delisting-risk-scan` | 识别退市高危信号 | 可解释的触发原因（证据引用） |
| `unhat-eligibility-check` | 动态评估摘帽条件满足情况 | 条件清单 + 满足度 |
| `sector-heatmap` | 行业属性标记 + 板块热力图 + 风险评级 | 热力图数据 + 评级 |
| `sentiment-flow-analysis` | 情绪与资金流向分析（中性化，承接 v1 A3） | 高换手/高热度预警 |
| `fundamental-screening` | 基本面筛选与安全边际评估（承接 v1 A4） | 机构优选池 |

### 2.2 主动服务 Bundle

| Skill | 职责 |
| --- | --- |
| `stock-watch` | 多标的盯盘：关键词/财报日/异动/换手率触发条件可配 |
| `data-aggregate` | 数据聚合：表格卡/趋势图/简报输出 |
| `risk-alert` | 风险预警：退市倒计时/流动性枯竭等维度可扩展 |
| `opportunity-mine` | 机会挖掘：风格 + 板块偏好过滤，复用公共认知输出 |
| `portfolio-stress-test` | 组合压力测试：规则化情景推演 |
| `strategy-design` | 策略设计与回测：选股→信号→策略→回测四步链路 |

`strategy-design` 的特殊契约：**信号校验器**必须实现「未来函数」检测——用户设计的信号若依赖未来数据，校验即报错并指出问题；回测口径由用户设定，输出用户口径的回测报告。

## 3. 工作流模型（Skill Studio 的数据基础）

### 3.1 WorkflowDAG schema

| 字段 | 说明 |
| --- | --- |
| `flow_id` / `name` / `description` | 标识（`flow_id` 形态为 `wf_<注册名>_v<主>.<次>`，见 [01 §1](01-平台共享契约.md)；`name` 过中性化校验） |
| `nodes[]` | 每节点：引用 skill_id、参数绑定（含常量与上游输出引用） |
| `edges[]` | 数据流连线，携带类型契约（连线时校验输入输出 schema 匹配，不匹配提示「这里需要一个 XX 类型的输入」） |
| `groups[]` | 子流程分组（可整体作为复合 Skill 导出） |
| `schedule` | 触发方式：定时 / 事件 / 手动 |
| `version` | 版本历史（多版本共存、diff、一键回滚） |

### 3.2 复合 Skill

用户可将工作流保存为复合 Skill（「命名的工作流」）：对外暴露统一输入输出契约 + 参数，进入 Skill 库；导出为 `.stflow`/`.stskill`（见 [09-生态](09-生态与分享.md)）。复合 Skill 依赖的原 Skill 缺失时，导入方走依赖提示流程。

**落地口径**：

| 面 | 口径 |
| --- | --- |
| 标识 | `skill_id` 由工作流 base 派生——`wf_<名>_v<主>.<次>` → `sk_<名>_v<主>.<次>`（版本随 `version`）；重名即拒（更新走版本发布，不静默覆盖） |
| 输入输出契约 | 默认由**未连线端口**推导（`type` 恒为 `object`）；`properties` 取**节点限定点号键** `<node_id>.<prop>`——可无损回指节点与端口，跨节点同名不冲突 |
| 已连线判据 | 与 §3.1 的粗粒度节点到节点边模型对称：入端口「已连」= 该节点有入边 **或** 有同名绑定（`literal` / `ref`）；出端口「已连」= 该节点有出边 **或** 被其他节点以 `ref` 引用 |
| 显式覆盖 | 可显式给出 `input_schema` / `output_schema`，以显式为准；其 `properties` **键集必须与推导集相等**、同名键的 `type` 必须相等，其余关键字（`required` / 描述等）随显式——不一致即拒，不静默取其一 |
| 参数暴露 | 节点参数是内部绑定细节，**不**自动上浮；由 `exposed_params` 显式声明（`<node_id>.<参数名>`）才进入 `SkillDescriptor.parameters`，与其余 Skill 同走双通道改参 |
| 能力字段 | `offline_level` 取所引用 Skill 的最差（全 `full`→`full`；任一 `none`→`none`；否则 `degraded`）、`permissions` 取并集——复合体只声明其部件所能支持的面 |

依赖缺失时**不落半成品**：物化（注册进 Skill 库）以依赖齐全为前提并显式失败；用户在依赖未安装期间不丢工作，因为工作流定义本身已由 §3.1 的持久化承载，只是复合 Skill 的物化等待依赖就绪。

## 4. Skill Studio 子系统

承载 [Story 6](../PRD-v2-Agent/story-06-skill-studio.md)：

- **双通道**：可视化画布编辑 + 对话式生成。对话生成接口接收 L3 的「工作流草稿对象」（见 [05-L3 §5](05-L3-对话主入口.md)），落到画布后可视化微调
- **调试协议**：任何工作流可试跑——单步执行、输入注入、逐步输出快照、暂停/继续/中止；每次试跑留存历史可对比（落地口径见下方「调试协议落地口径」）
- **校验**：保存时执行成环检测（DAG 约束）+ 契约匹配校验 + 中性化命名校验（01 §6）
- **模板库**：官方预置工作流模板（每日 ST 简报 / 退市风险扫描 / 策略回测流水线等），可 fork 后修改
- **空状态**：无自建工作流时展示模板库 + 「用一句话描述你想要的工作流」入口

**编辑面落地口径**：

| 面 | 口径 |
| --- | --- |
| 编辑会话 | `CanvasEditor` = 内存编辑会话（草稿态**不落盘**）；每次写操作返回 `EditResult`（是否生效 + 新 DAG 视图 + 校验结论 + 人可读提示）。落盘只发生在「接受」 |
| 校验复用 | 编辑期与保存期是**同一判定件的两次调用**，不另造第二套：连线契约走 [01 §2](01-平台共享契约.md) 的 `check_link`、全图走 `validate_dag`、成环走 `validate.find_cycle`（与 `dependency_graph` / `topological_order` 并列的公共判定件） |
| 连线拦截范围 | `connect` 只拦**本连线造成**的问题——① 该边落在环上（拒，携环路径）② `check_link` 不匹配（拒，携「这里需要一个 XX 类型的输入」式说明）。草稿**既有**的语义问题不阻断后续编辑、只在 `EditResult` 中回显；被拒时画布状态不变 |
| 删节点 | 删除一个节点即**连带移除它的全部引用**——入射 / 出射连线、`ref` 参数绑定、分组内的成员位。依据：§3.1 的依赖图 = 边 ∪ `ref` 绑定（两者取并），删后依赖图不留悬空引用 |
| 分组 | `group` / `ungroup` 只动 `groups[]`，不改节点与连线；分组不参与依赖图 |
| 草稿接收契约 | 工作流草稿 = [05-L3 §5](05-L3-对话主入口.md) 四字段 + 工作流专用 `workflow_draft` 子对象（`name` / `description` / `nodes` / `edges` / `groups` / `schedule`，可含 ASCII `flow_name`）——身份字段 `flow_id` / `version` **不在草稿中**，由接收方派生 |
| 非法草稿边界 | **形状**非法（缺 `name` / 非法 `node_id` / 缺必填字段 / 无法派生注册名 / 违规携带 `flow_id` 或 `version` / 含无法识别的字段，即构造期即拒）→ 显式拒、**画布不开**；**语义**不合规（成环 / 契约不匹配 / 命名未中性化 / Skill 未注册 / 参数未声明）→ **照开画布**，违规清单随首次 `EditResult` 带回 |
| 注册名派生 | `flow_id` 的 `<注册名>` 取草稿的 ASCII `flow_name`；缺省时由展示名 `name` slug 化（小写、非 `[a-z0-9]` 转 `_`、去首尾 `_`）——slug 为空（如纯中文名）即拒并提示补 `flow_name`。画布期用临时身份 `wf_<注册名>_v0.0` 表「未发布」 |
| 接受 | 把画布期身份换成首版 `wf_<注册名>_v1.0`，经 `WorkflowStore.save` 落 `config` 分区 `workflow/<flow_id>.json`；base 已存在 → **拒**（改既有工作流走 `WorkflowStore.publish_version`，不属本流程） |
| 变更留痕 | 「接受」产生一条 `ChangeRecord`（[01 §7](01-平台共享契约.md)）并落 `execution_log` 分区 `workflow-change/<change_id>.json`——与 §5.3 的 `mcp-hub-change/` 同构（同分区不同前缀），`change_id` 即回滚单位 |
| 微调 / 拒绝 | 「微调」返回**同一个** `CanvasEditor` 句柄（会话不分裂）；「拒绝」丢弃会话、**不落盘**，`reason` 仅随返回值透传（反馈采集归 [08-L6 §1](08-L6-反思演进.md)） |

草稿态与落盘态的边界**只有一条**：编辑期一律不落盘，「接受」是唯一落盘点——由此「拒绝」天然无副作用、「微调」天然可无限次、无论编辑多久都不产生版本。

**调试协议落地口径**：

| 面 | 口径 |
| --- | --- |
| 试跑记录标识 | `trial_id`（[01 §1](01-平台共享契约.md) 契约 ID，本机生成 `trial_<uuid4 前 20 位>`）；落 `execution_log` 分区 `workflow-trial/<trial_id>.json`——与 §1.2 的 `skill-run/` 同分区**不同前缀**，试跑记录不被误当生产执行，两者互不串扫 |
| 试跑门禁 | 只拦**不可执行**类结构问题（成环 / 自环 / 重复节点标识 / 连线或引用指向不存在的节点）；其余发现（Skill 未注册、`skill_id` 形态非法、连线契约不匹配、参数未声明）不拦，落到节点级以失败信封装载——调试期正是要暴露它们 |
| 会话驱动 | 步进式状态机（**非**线程中断）：`idle → running｜paused → completed｜failed｜aborted`；`step` 推进一步，`run_to_end` 逐节点推进（每步前查暂停 / 中止标志），暂停与中止都生效在**节点边界**——进程被杀不在覆盖范围（崩溃恢复不属试跑语义） |
| 逐节点执行 | 经 §1.2 的完整流水线**逐节点**执行（同一 `trace_id`，逐节点追加 `skill_run` 步）；参数解析：`literal` 取字面值、`ref` 从上游节点输出信封的 `data` 按 `path` 取值——路径不存在即该节点失败，**不静默置空** |
| 输入注入 | 按节点注入（`{node_id: {参数名: 值}}`）：**覆盖**该节点参数绑定中的 `literal` 值，快照标注该值来源为注入；对 `ref` 绑定或节点未声明的参数名**不予注入并显式拒绝**（不静默丢弃） |
| 值来源标注 | 每个节点输入快照**逐参数**标注来源：注入 / 上游引用 / 字面量 / Skill 默认值 |
| 失败短路 | 节点信封非 `ok` / `empty` → 其**下游节点一律不执行**并标 `dependency_failed`（写明上游节点），试跑记录状态 `failed` |
| 输出复用面 | 试跑**不登记**输出复用（§1.3）——彩排产物不进生产复用面，正式执行不会取用试跑结果 |
| 历史对比 | 两次试跑记录按 `node_id` 对齐，逐节点列出输出差异（新增 / 消失 / 不同）；比对只读留存、不重跑 |

试跑与正式执行的差别**仅在**「可单步 / 可暂停中止 / 留试跑历史 / 不登记复用」四点——权限口径照旧（经 §1.5 的沙箱与已批准权限集合），不因调试而放宽。

## 5. MCP Hub 子系统

承载 [Story 8](../PRD-v2-Agent/story-08-mcp-hub.md)。产品内置 MCP Client，允许用户挂载任意 MCP Server：

### 5.1 传输与注册

- 支持 stdio（本地进程）与 HTTP/SSE（远程）两种传输；**默认只允许本地 stdio**，远程 Server 需显式开启并确认「数据会离开本机」警示
- Server 注册表：添加/删除/启用/禁用；添加时连接测试 + 权限申请展示（「这个 Server 想做什么」）+ 逐项批准

### 5.2 tool → Skill 自动映射

- MCP Server 的每个 tool 自动注册为 Skill（`source: mcp-mapped`），SkillDescriptor 元数据从 MCP schema 派生
- Server 禁用 → 其全部 Skill 立即从可用列表移除，进行中调用中止
- tool 契约变化 → 提示用户重新映射（映射关系版本化，遵循 01 §9）

### 5.3 状态机与降级

Server 生命周期状态：`connected` / `disconnected` / `reconnecting` / `permission_pending` / `disabled`。崩溃 → 明确提示 + 自动重连（次数可配）+ 可降级到官方数据源 Skill 兜底。Server 的全部网络请求经 L0 网关登记（[02-L0 §6](02-L0-本地优先基座.md)），在网络活动面板可见。

### 5.4 权限模型

沿用 01 §10 统一权限模型：MCP Server 按能力粒度声明（读文件/网络/执行命令），首次调用前逐项批准；远程 Server 额外标注数据外发风险。

## 6. 定时调度

- `stock-watch`、`data-aggregate`、`sector-heatmap`、每日报告（[07-L5](07-L5-主动触达.md)）、每周反思（[08-L6](08-L6-反思演进.md)）等均依赖调度器
- 调度器属 L1 基础设施：按 WorkflowDAG 的 `schedule` 与各 Skill 的频率参数触发；调度触发产生的执行同样走 §1.2 完整流水线（留痕、Trace、ResultEnvelope）
- 断网期间到期的调度任务：离线可执行者照常执行（用最后快照并标注），不可执行者在网络恢复后按用户配置补跑或跳过
