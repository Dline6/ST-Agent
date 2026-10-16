---
id: T-INT-007
parent: null
title: M6 集成关卡 · 界面完备
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M6
depends_on: [T-UI-006, T-UI-007, T-UI-008, T-UI-009.1, T-UI-009.2, T-UI-010.1, T-UI-010.2, T-UI-011.1, T-UI-011.2, T-UI-012.1, T-UI-012.2, T-UI-012.3, T-UI-013, T-UI-014.1, T-UI-014.2, T-UI-014.3, T-UI-015.1, T-UI-015.2, T-UI-015.3, T-UI-016.1, T-UI-016.2, T-UI-016.3, T-UI-017.1, T-UI-017.2, T-UI-017.3, T-UI-018.1, T-UI-018.2, T-UI-018.3, T-UI-021, T-UI-022]
status: done
decisions: [D-103, D-122]
verify: "定向 tests/integration/test_m6_ui_complete.py 19 passed（GWT-1..6）· **红-绿已验**（删 `ui_ports` 的 studio ⇒ 端口集断言红；删 `PAGE_PATHS` 的 /workspace ⇒ 漂移断言红；各自只命中一条）· 全量 pytest 4335 passed / 16 deselected / 2 errors（1078.76s；两例为本机已知环境性 test_shell 口令错误，非回归）· live 子集 2 passed（真端点，无 skip）· verify_docs --strict PASS（0 断链）→ 执行日志 [T-INT-007]"
---

# T-INT-007 · M6 集成关卡 · 界面完备

> **本任务在 M6 各叶立项前先建**——[verify_docs 检查 7](../../../tools/verify_docs.py) 要求「有活跃任务的里程碑必有活跃 `T-INT-*` 覆盖」，故它与 M6 首叶同批落地；`depends_on` 随各叶立项逐条补齐（[工作流](../../../工作流.md)「集成关卡」节）。

## 目标

M6 的收口关卡：把 M6 各叶交付的界面**装配成一条真能用的端到端链路**——按 [00 §5 端到端数据流](../../../../docs/技术架构-v2/00-架构总览.md)，在真组合根 + 真回环服务上验证「[11-sitemap](../../../../docs/PRD-v2-Agent/11-sitemap.md) 声明的页面集逐个可达、六态如实呈现、写面经**固定回环路由**生效、中性化门在出站点生效」。单叶用例全是注入替身的分面测试，装配关系与跨屏数据流恰是它们集体覆盖不到的部分。

**锚点**：验收标准取自 [00 §5](../../../../docs/技术架构-v2/00-架构总览.md) 的八步生命周期与所辖层间的接口约定，逐条改写为 GWT；**不重复**单叶已覆盖的单元行为。

## 验收标准（Given-When-Then）

> 锚点取 [00 §5](../../../../docs/技术架构-v2/00-架构总览.md) 的八步生命周期与 [11-sitemap §2.2/§3](../../../../docs/PRD-v2-Agent/11-sitemap.md)，逐条改写为 GWT；**不重复**单叶已覆盖的单元行为。全部经**真组合根 + 真回环面**（`build_m5_runtime` ＋ `serve(**ui_ports(root))`）在真 HTTP 连接上验。
>
> **一处装配事实**：M6 **没有独立的「M6 组合根」**——表现为挂在同一个根上的端点与门面，装配缝是生产入口 [`__main__.py`](../../../../src/st_agent/__main__.py) 的**端口映射**。故 GWT-6 直接钉那张表（见 `## 接口面` 输出）。

- **GWT-1 · 页面集逐条可达且不多不少**：Given 装配好的真服务，When 逐条 GET [11-sitemap §2.2](../../../../docs/PRD-v2-Agent/11-sitemap.md) 声明的 **19 条**路径（`/` ＋ `PAGE_PATHS` 18 条），Then 每条 200 且回**同一份**静态壳；When GET **未声明**路径，Then **404**（「可达」因此不是「全路径都回壳」的假象）；When 取回 `/js/pages.js`，Then 其登记集 == 后端声明集，且每条 `statusPath` **不是 404**、回六态之一；When 取壳引用的资产，Then 各自 200 且 content-type 正确。
- **GWT-2 · 六态如实呈现（横切成不变量）**：Given 真服务，When 走查十四个读端点，Then 每条信封的 `status` ∈ 六态、**都带** `render` 语义段、非 ok **必带** `reason`；且六态各自**至少真实出现一次**——`ok`/`empty`/`unavailable`/`validation_failed` 取自真端点，`failed`/`dependency_failed` 取自 dev 样本面（真装配下这两态**只由真故障或装配错误产生**，故不注入故障去造，见 A1）。
- **GWT-3 · 写面只走固定回环路由**：Given 真服务，When POST **写表外**路径（含合法令牌），Then 404 且真存储的文件路径集**一字不变**；When 对写表内**每一条**发无令牌请求，Then 401 且**不发生派发**（同一按存储路径集判据）；When 一条**真跨面**写往返（读端点产出的描述 → 钉到工作区 → 读回 → 取钉），Then 落盘可见、取钉后回 `empty` ＋ 原因。
- **GWT-4 · 中性化门在出站点生效 + 描述形态**：Given **装配出来的那个** `UiApp` 实例，When 投一份「真描述 + 违规 `generated` 文案」（`title` 恒为生成文案，[01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)），Then 阻断为 `validation_failed`、原因指名**中性校验**、且**不回显违规原文**；When 走查各分区产出的 `ok` 描述，Then `component_type` 均属**已登记型**、`text_kinds` **逐槽**有标注。
- **GWT-5 · 拒绝路径不丢信封**（合验 [`T-UI-021`](T-UI-021-回环守卫按回环名允许集判Host与Origin.md) / [`T-UI-022`](T-UI-022-回环服务拒绝请求时排空请求体.md)）：Given 真服务，When 发跨源 `Origin` / 外来 `Host`，Then 403；When 缺令牌，Then 401；When 用未支持的方法，Then 501——**每条的响应都真的抵达**（不是连接复位）；When 被拒绝的是**大体（2 MiB）**请求，Then **复用同一条连接**的下一次请求仍正常（读净的判据；单发看不出）。
- **GWT-6 · 生产入口与外露端口面**：Given `ui_ports`，When 与 `serve` 的**端口形参名**相比，Then 逐名相等（加面未注入 / 注入了不存在的键，两向漂移都在此现形）；Given 生产入口，When 透传端口，Then 用的**正是**这张表（替身钉住）；Given 真根，Then 十三个端口一个不少、各面探针全 `ok`（「接线」与「取不到数」不混用）。


## 接口面

- **输入**（逐条给上游任务 id + 具体 API / 落盘位置；均为 `done`）：
  - [`T-UI-011.2`](T-UI-011.2-全IA页面登记与导航收敛.md) 的页面集：[`ui/app.py`](../../../../src/st_agent/ui/app.py) 的 `PAGE_PATHS`（18 条）与 [`web/js/pages.js`](../../../../src/st_agent/ui/web/js/pages.js) 的 19 条登记（含 `path` / `statusPath` / `root`）。
  - [`T-UI-011.1`](T-UI-011.1-多面端口注入与各面门面装配.md) 的装配缝：[`ui/server.py`](../../../../src/st_agent/ui/server.py) 的 `serve(*, host, port, dev, token, <13 个面端口>) -> RunningUi`（`RunningUi` 持 `host` / `port` / `token` / `app`，是上下文管理器）与 `_GET_ROUTES` / `_POST_ROUTES`；[`ui/app.py`](../../../../src/st_agent/ui/app.py) 的 `build_ui(...)`、`UiApp` 各面字段、`api_face_status`。
  - [`T-UI-007`](T-UI-007-视觉设计规范研判页方向与token层.md) / [`T-UI-008`](T-UI-008-前端资产打包模式补全.md) 的资产面：`web/css/tokens.css` · `app.css` · `web/index.html` 的引用链；dev 面由 `dev` 开关剔除（GWT-2 的 `failed`/`dependency_failed` 经它呈现）。
  - [`T-UI-010.1`](T-UI-010.1-组件型补齐屏形态对位与四型落地.md) 的型表：[`contracts/ui_description.py`](../../../../src/st_agent/contracts/ui_description.py) 的 `IMPLEMENTED_COMPONENT_TYPES`（18 型）与 `UiDescription`。
  - [`T-UI-009.2`](T-UI-009.2-记忆区持仓行情读面页与鸭子端口.md) `GET /api/memory/holdings` · [`T-UI-012.1`](T-UI-012.1-对话骨架与快捷指令.md) `GET /api/chat/{context,commands}` ＋ `commands` 端口 · [`T-UI-013`](T-UI-013-工作区钉住的持久组件.md) `WorkspaceFacade`（`pins` / `pin` / `unpin` / `refresh`）＋ `GET /api/workspace/pins` 与 `POST /api/workspace/{pin,unpin,refresh}` · [`T-UI-014.1`](T-UI-014.1-记忆区三视图与画像卡.md) / [`.2`](T-UI-014.2-节点详情与修正历史.md) / [`.3`](T-UI-014.3-导入导出面板与Onboarding.md) 的记忆区端点族 · [`T-UI-015.1`](T-UI-015.1-Skill库浏览详情参数与执行日志.md) / [`.2`](T-UI-015.2-MCPHub服务器与权限确认.md) / [`.3`](T-UI-015.3-Studio附加面与工作流调试.md) 的能力区端点族 ＋ `studio` 端口 · [`T-UI-016.1`](T-UI-016.1-触发进度与视角阵容.md) / [`.2`](T-UI-016.2-分歧图与视角详情.md) / [`.3`](T-UI-016.3-推理链面板与决策记录.md) 的推理区端点族 ＋ `/api/chat` 的 `traces` 键 · [`T-UI-017.1`](T-UI-017.1-日报与推送历史.md) / [`.2`](T-UI-017.2-预算渠道与情境模式.md) / [`.3`](T-UI-017.3-疲劳反馈与调整询问.md) 的触达区端点族 · [`T-UI-018.1`](T-UI-018.1-四设置页与配置落值面.md) / [`.2`](T-UI-018.2-备份恢复与完全清空.md) / [`.3`](T-UI-018.3-网络活动与离线横幅.md) 的设置区端点族 ＋ `RestartGate`。
  - [`T-UI-021`](T-UI-021-回环守卫按回环名允许集判Host与Origin.md) 的判定面：`RequestGuard.check` / `.authorities` / `.origins` / `LOOPBACK_NAMES`（[`ui/security.py`](../../../../src/st_agent/ui/security.py)）。
  - [`T-UI-022`](T-UI-022-回环服务拒绝请求时排空请求体.md) 的不变量：`UiRequestHandler._drain_request_body` / `_read_json_body`，以及「写出任何响应之前，声明的请求体已被读净」。
  - 出站点闸门：[`ui/neutrality_gate.py`](../../../../src/st_agent/ui/neutrality_gate.py) 的 `NeutralityGate.check` ＋ [`ui/app.py`](../../../../src/st_agent/ui/app.py) 的 `UiApp._gate_description` / `api_description`（[01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2 · [01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md) 的 `text_kinds` 分栏）。
  - 判据：[11-sitemap §2.2 页面路径](../../../../docs/PRD-v2-Agent/11-sitemap.md)（19 条）与 [§3 各 Flow](../../../../docs/PRD-v2-Agent/11-sitemap.md) · [00 §5](../../../../docs/技术架构-v2/00-架构总览.md) / [§1.1](../../../../docs/技术架构-v2/00-架构总览.md) · [01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md) 六态。
  - 先例（装配与替身范式）：`tests/integration/rig_m5.py` / `test_m5_autonomy.py` · `tests/integration/test_ui_*_wiring.py`（各叶的真根 + 真服务往返；本关卡**不重复**其面内行为）· `tests/ui/test_ui_shell_routing.py`（既有的 `PAGE_PATHS` ↔ `pages.js` 漂移守卫，本关卡不重写）。
- **输出**（本任务交付的公共 API / 落盘位置）：
  - **新增** [`tests/integration/rig_m6.py`](../../../../tests/integration/rig_m6.py)：`SHELL_PATHS`（`/` ＋ `PAGE_PATHS`）· `serve_port_names()`（由 `serve` 签名取端口集）· `UiClient`（带令牌的真 HTTP 客户端，支持连接复用与看原始体，并持 `app`）· `ui_server(m5, *, dev)`（按生产端口映射起真服务的上下文管理器）· `store_paths(root)`（零副作用判据）· `declared_pages_js_paths` / `declared_pages_js_status_paths` · `seeded_m6(root)`。
  - **新增** [`tests/integration/test_m6_ui_complete.py`](../../../../tests/integration/test_m6_ui_complete.py)：GWT-1..6（19 用例）。
  - **只增** [`src/st_agent/__main__.py`](../../../../src/st_agent/__main__.py)：`ui_ports(runtime) -> dict[str, Any]`——表现层端口 → 组合根句柄的**唯一映射**（13 键；`eco` ↔ `ecosystem` 是唯一名不同的一对，故不能靠 `getattr` 批量取）；`run_backend` 改为 `serve_ui(host=…, port=…, dev=…, **ui_ports(runtime))`；`__all__` 加 `ui_ports`。
  - **不动**：`01-平台共享契约` 与各层架构（无契约变更）· `PAGE_PATHS` / 路由表 / 各面门面 · 各叶既有用例与前端守卫 · `tests/test_layering.py`。

## 可关闭的遗留

- 无。开工 ④ 逐册读 [遗留问题](../../../遗留问题/README.md) 各册**未闭区**：L0 册余 `C3` / `C5`（归属＝**人决**）与 `D1`–`D3`（归属＝**人**）· L5 册余 `A1`（归属＝**人**，需真实端点凭据）· L1 / L2 / L3 / L6 四册未闭为 **0 条**。无「归属＝本任务」或「解封条件＝本任务 `done`」的条目（[`verify_docs.py`](../../../tools/verify_docs.py) 检查 8 基线 0 处）。

## 假设与前提

- **A1 · `failed` / `dependency_failed` 经 dev 样本面呈现，不注入真故障**——前提：真装配下这两态只由**真故障**（面抛非 `ValueError`）与**装配错误**（`WorkspaceFacade.refresh` 的两条「装配有误」）产生，硬造即等于**把故障注入装配**而不是验证装配；样本面走的是与真实描述**同一条**出站校验路径。
  **若错的影响**：若要求必须由真端点产生，需在装配里埋一个必抛的门面——用例会变成「验我们自己埋的故障」。
  **验证方式**：`test_failed_and_dependency_failed_come_from_the_sample_surface` 两支（dev 服务上六态可取、非 dev 服务上该面 404）。
- **A2 · 中性化门的违规注入直接投给装配出的真 `UiApp`，不走真对话链路**——前提：模型/用户文本按 [D-053](../../../决策日志.md) 属 `data` 槽、**不整串复检**，拿模型输出撞门反而会**证伪契约**；而 `UiApp.api_description` 正是出站点的那个函数。
  **若错的影响**：若要求「必须经真对话注入」，则需一条**把模型文案标为 `generated`** 的链路——那要先改 [01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md) 的分栏口径。
  **验证方式**：GWT-4 断言阻断发生在中性门（原因含「中性校验」）且不回显原文；且真描述（同一份、仅换 `title`）在未改标题时**照常出站**，证明不是「描述本身不合法」。
- **A3 · `ui_ports` 落在 `__main__.py`（唯一注入点），不新建 M6 组合根**——前提：M6 的交付是挂在既有根上的端点与门面，没有独立的装配阶段；入口的内联端口清单是那份映射**实际所在**，收敛为函数即成为单源。
  **若错的影响**：若判为「不该动生产入口」，则改由 rig 经 `inspect.signature(serve)` 推导 ＋ 一张显式映射表——代价是多一份镜像与漂移风险，且「入口漏注一面」不再可断言。
  **验证方式**：`test_the_entry_injects_exactly_the_serve_port_map`（键集与 `serve` 形参逐名相等 ＋ 替身钉住入口用的是这张表）。
- **A4 · 未触发「联网面必跑 live」，但仍跑一次并留痕**——前提：M6 不动 `src/st_agent/l0/llm/**` / `l0/net/gateway.py` / `l1/runtime.py` 的 LLM 装配段（本批只动 `__main__.py` 的端口映射与新增两个测试文件）。
  **若错的影响**：若漏跑 live，**CI 绿灯不覆盖真实链路**（该条存在的原因）。
  **验证方式**：验证行必须含 live 子集结果；无可用端点而 skip 时**写明 skip 与原因**（不许默默略过）。
- **A5 · 「零副作用」以真存储的**文件相对路径集**前后不变为判据**——前提：写面一旦真跑过，无论落到哪个分区路径集都会变，比断言某个读端点更直接。
  **若错的影响**：若某条拒绝路径会以「改写既有文件内容而不新增文件」的方式产生副作用，本判据看不出（当前写面均为新增 / 删除行文件，暂不涉及）。
  **验证方式**：GWT-3 两支（表外 404 后、写表内逐条 401 后，路径集均不变）。
- **A6 · 关卡必跑全量且只跑一次**——前提：[工作流](../../../工作流.md)「集成关卡」规定关卡 ⑥ 跑**全量**（全量被强制的唯一场景），且「改动封闭后再跑、跑时不动码」。
  **若错的影响**：跑两次即白费约一刻钟，且中途改码会让结论作废。
  **验证方式**：验证行给出全量的计数与耗时；跑之前所有代码与文档改动已封闭。
- **A7 · 「恢复失败落 `failed`」未采用**（实现中发现）——原打算以 `POST /api/settings/restore` 传错误归档路径产生真 `failed`，核实后该路径经 `_store_errors` 归入输入/完整性两类，且在真根上构造它需要先有一个**半损坏的归档**；改用 A1 的口径。
  **若错的影响**：无（A1 已覆盖该态）。
  **验证方式**：GWT-2 的样本面支；真端点的失败类由各叶用例覆盖（本关卡不重复）。


## 涉及契约

- [00 §5 端到端数据流](../../../../docs/技术架构-v2/00-架构总览.md) / [§1.1 进程形态](../../../../docs/技术架构-v2/00-架构总览.md)——后端口常驻 + UI 可分离，关卡在真回环面上验
- [01 §5 ResultEnvelope](../../../../docs/技术架构-v2/01-平台共享契约.md) 六态 · [§6 中性化校验](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2 · [§12 UI 描述](../../../../docs/技术架构-v2/01-平台共享契约.md)（渲染前校验门与降级口径）
- [11-sitemap](../../../../docs/PRD-v2-Agent/11-sitemap.md)——页面集是覆盖面的判据

## 参考

- 决策：[D-103](../../../决策日志.md)（M6 / M7 里程碑登记与 M6 批次一）
- 前置（M6 全部叶，16 条）：[`T-UI-006`](T-UI-006-IA补章受控自主分区与页面Flow.md) · [`T-UI-007`](T-UI-007-视觉设计规范研判页方向与token层.md) · [`T-UI-008`](T-UI-008-前端资产打包模式补全.md) · [`T-UI-009.1`](T-UI-009.1-表格槽词汇与数字涨跌渲染原语.md) · [`T-UI-009.2`](T-UI-009.2-记忆区持仓行情读面页与鸭子端口.md) · [`T-UI-010.1`](T-UI-010.1-组件型补齐屏形态对位与四型落地.md) · [`T-UI-010.2`](T-UI-010.2-页面集与导航模型.md) · [`T-UI-011.1`](T-UI-011.1-多面端口注入与各面门面装配.md) · [`T-UI-011.2`](T-UI-011.2-全IA页面登记与导航收敛.md) · [`T-UI-012`](T-UI-012-Chat主界面对话流与生成式UI宿主.md) · [`T-UI-013`](T-UI-013-工作区钉住的持久组件.md) · [`T-UI-014`](T-UI-014-记忆区三视图画像卡与节点详情.md) · [`T-UI-015.1`](T-UI-015.1-Skill库浏览详情参数与执行日志.md) · [`T-UI-015.2`](T-UI-015.2-MCPHub服务器与权限确认.md) · [`T-UI-015.3`](T-UI-015.3-Studio附加面与工作流调试.md) · [`T-UI-016`](T-UI-016-推理区触发进度分歧图与视角详情.md) · [`T-UI-017`](T-UI-017-触达区日报历史与预算渠道配置.md) · [`T-UI-018`](T-UI-018-设置区与全局要素.md)
- 先例：M0–M5 各关卡（`T-INT-001` ~ `T-INT-006`）· 关卡口径见 [工作流](../../../工作流.md)「集成关卡」
- 测试口径：[测试分层](../../../工作流.md)——关卡 ⑥ 必跑**全量** `pytest`（离线部分），跨层套件无条件随跑

## 备注

功能交付任务的关卡**不得**标 `gate: skip`（[工作流](../../../工作流.md)）。每新增一个 M6 叶，同批把其 id 补进本任务 `depends_on`——否则检查 7 报「依赖未覆盖本里程碑任务」。

**M6 无独立的「M6 组合根」**：表现为挂在既有 `build_m5_runtime` 之上的端点与门面，**装配缝是生产入口的端口映射**（[`ui_ports`](../../../../src/st_agent/__main__.py)）。本关卡据此把装配面钉在那张表上（GWT-6）——它是「新增了一个面却没接上」这类缺口的**唯一**机械可查点。
