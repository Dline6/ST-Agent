---
id: T-INT-010
parent: null
title: M9 集成关卡 · 开放能力面
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-013.1, T-AGT-013.2, T-AGT-013.3, T-AGT-014.1, T-AGT-014.2, T-AGT-014.3, T-AGT-015.1, T-AGT-015.2, T-AGT-015.3, T-AGT-016.1, T-AGT-016.2]
status: done
decisions: [D-137]
verify: 全量 4880 passed / 16 deselected / 2 errors（两例 tests/ui/test_shell.py 本机既有环境性，报错文本与前批一致）；tests/integration/test_m9_open_capabilities.py 6 例覆盖 GWT-1～4（缺公告/龙虎榜 → 两次确认续跑后真落缓存并 SQL 作答；出网关闭与未配置均显式 unavailable 且零请求零审计；写文件落 workspace 并可读回；web_search / data_fetch 的来源、时间与网关审计）；verify_docs.py --strict PASS。commit `a40fe4c`。见执行日志 T-INT-010 条目。
---

# T-INT-010 · M9 集成关卡 · 开放能力面

> **里程碑收口关卡**：`depends_on` 须覆盖本里程碑全部叶子（[检查 7](../tools/verify_docs.py)），由 `render_ledger.py render` 派生后核对。

## 目标
端到端验证：本地没有的数据，循环能自己补上，并如实标注来源。

## 验收标准（Given-When-Then）
- GWT-1：Given 本地缺某只 ST 股的最新公告与龙虎榜，When 用户提问，Then 循环经 `data_fetch` 或 `web_search` 取数、落缓存并回答。
- GWT-2：Given 出网关闭或未配置，Then 显式 `unavailable` 并给原因，不假装答过。
- GWT-3：Given 写入文件，Then 落在工作区分区且用户能找到。
- GWT-4：Given 全链路，Then 每条外网数据都带来源与时间，且出网有审计。

## 接口面

- 输入（消费的前置接口，逐条点名上游任务 id + 具体 API / 落盘位置）：
  - **取数与文档（T-AGT-013.1 / .2 / .3）**：`sk_data_fetch` / `sk_data_sync`（`src/st_agent/l1/builtin/data_fetch.py` / `data_sync.py`）经 `DataSyncRunner.fetch_items` / `start_items`（`src/st_agent/l0/sync_runner.py`）执行；数据集 id 取 `l0/market/datasets.py::DATASET_IDS`；真实抓取仍由 `BaoStockSync` / `InfoSync` 经 `EgressGateway`（`kind="data_fetch"`）出网；`sk_docs`（`l1/builtin/docs.py`）→ `LocalDocSource.list_docs` / `read_doc` / `search_docs` / `consistency`（`l0/info/docs.py`）。
  - **文件面（T-AGT-014.1 / .2 / .3）**：`sk_file_read` / `sk_file_glob` / `sk_file_grep` / `sk_file_write`（`l1/builtin/files.py`）经 `SkillSandbox` 核对 `local_read:<workspace/**>`、`local_read:<inbox/**>`、`local_write:<workspace/**>`，落盘经 `l0/file_workspace.py::FileWorkspace` → `Store` 的 `workspace` 分区。
  - **开放网络（T-AGT-015.1 / .2 / .3）**：`sk_web_search` / `sk_web_fetch`（`l1/builtin/web.py`）经 `WebAccess`（`store` / `vault` / 注入的 `http`）；开关与名单读 `l0/net/web_config.py::load_web_settings`（`web-egress/enabled` 缺省关、`web-egress/allowed-hosts` 缺省空），出网经 `EgressGateway.execute("web_search"|"web_fetch", …)`；配置族 `SearchFamily` / `WebEgressFamily`（`l1/registry/families.py`）。
  - **本地命令（T-AGT-016.1 / .2）**：`sk_run_command`（`l1/builtin/exec.py`）经 `CommandAccess` + `CommandRunner`，开关 `local-exec/enabled`（缺省关）。本关卡 GWT 不要求执行命令，仅确认其与原语面同处一个工具目录、分级为 `collaborative-required`（叶子已覆盖）。
  - **循环与授权（T-AGT-008 / 011 / 012）**：`run_agent_loop` / `resume_agent_run` / `conclude_agent_run`（`l3/runtime/`）、`_AgentInvestigator`（`composition/agent.py`）、`build_m5_runtime`（`composition/runtime/m5.py`）、`L1Runtime.bind_data_sync`；授权判据 `RuntimeAuthorization.decide("skill.<base>")` ＋ `DEFAULT_RISK_GRADING`（`l6/authorization.py`，写类 / 出网 / 命令 `collaborative-required`，只读 `autonomous-ok`）；批准供给 `SkillPermissionBook`。
  - **离线装配面**：`tests/integration/rig.py` 的 `seed_market_db` / `MarketData` / `RecordingSender`、`rig_m1.DeterministicUnderstander`、`rig_m5.ScriptedEndpoint` / `ENV`。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `tests/integration/rig_m9.py`——M9 关卡装配：真 `Store` + 真 `MarketDb` + 真 `EgressGateway`（审计开）+ 真 `build_m5_runtime`，只把**HTTP 发送口**与**信息面抓取器**换成可注入替身；`DataSyncRunner` 换绑假源工厂（`market_factory` / `info_factory`）后经 `L1Runtime.bind_data_sync` 接入。**测试专用**，不进生产 API、不改落盘契约。
  - `tests/integration/test_m9_open_capabilities.py`——GWT-1～4 的跨层装配用例。
  - **无新公共 API、无新落盘契约**——关卡只验收既有交付面；走查或用例若暴露缺陷，修复落在上述既有文件内，不新增跨层接口。
  - 交付物 = [执行日志](../执行日志.md) `[T-INT-010]` 条目（含「验证」行与假设复核）＋ 本任务 `verify` 字段 ＋ `render_ledger.py render` 派生的账本状态。

## 可关闭的遗留

逐册扫 [遗留问题](../遗留问题/README.md) 未闭区（2026-10-30）：

- `L0` 册：`C3`（1990 起全历史回填）· `C5`（沪深300 之外全市场码表）——归属均为**人决**，非本任务可闭。
- `L1` / `L2` / `L3` / `L6` 册：未闭区为空。
- `L5` 册：`A1`（真实渠道链路无真跑证据）——归属＝**人**（需真实 SMTP / Webhook 凭据），非本任务可闭。

**册内无命中**——本关卡不领取任何册内条目。

## 假设与前提

- **A1 · 离线套件以脚本端点代表模型选步**：本机无可用真实 LLM 端点，关卡沿用 M5/M8 的 `ScriptedEndpoint`（只换 HTTP 一跳，`LlmClient` / transport / 网关 / 能力探测全真）。故**模型决策质量不在本关卡范围**，关卡验证的是「脚本选出的步骤被真 L1 / 闸门 / 沙箱 / 留痕完整执行」。
  - 若错的影响：对「模型会不会自己想到取数」的验收强度被高估。验证方式：执行日志如实写明该边界；`-m live` 子集按工作流判断是否触发（本关卡不改 `l0/llm/**` / `l0/net/gateway.py` / `l1/runtime.py` 的 LLM 装配段，若不触发则写明理由）。
- **A2 · GWT-1「循环自己补上」内含一次确认**：`sk_data_fetch` 按 [D-145](../决策日志.md) 出厂判 `collaborative-required`，故循环必然在取数步交还一次，经用户 `approve` 续跑后落缓存。关卡**不放宽分级**走通，正是要验「写类 / 出网须确认」与「确认后真执行」两者同时成立。
  - 若错的影响：会误以为出厂零确认即可取数。验证方式：断言首次运行在 `sk_data_fetch` 处 `needs_confirmation`，且运行期档仍为 `autonomous`、风险清单未被测试改写；续跑后该步 `ok`。
- **A3 · 真 `InfoSync` + 注入抓取器可让「落缓存」为真**：只把网络那一跳换成 `FakeInfoFetcher`，`InfoSync` 的预过滤 / 入表 / 正文落 `data_cache` / 水位回写都是真的；断言以 `MarketDb` 行、`LocalDocSource.read_doc` 正文与 `sync_state` 为据。
  - 若错的影响：GWT-1 的「落缓存」退化为替身计数。验证方式：取数前后分别查 `announcement` / `dragon_tiger` 表与 `sync_state`，并读出落盘正文。
- **A4 · 「出网关闭 / 未配置」的显式不可用发生在确认之后**：闸门先于执行，故 `sk_web_search` 关闭态仍先交还；用户确认后原语在触碰网关前回 `unavailable`（含完整告知）。关卡按「交还 → approve → `unavailable`」验证，且断言零 HTTP、零审计、原因进入下一轮上下文。
  - 若错的影响：把「闸门拦截」误读成「出网开关生效」。验证方式：HTTP 替身 `calls == []`、`gateway.query(kind="web_search") == ()`、下一轮 prompt 含原因。
- **A5 · 出网审计在产品里缺省关，关卡显式开**：`net-audit/enabled` 缺省 `false`（02 §6 / D-073），GWT-4 的「出网有审计」须以 `audit=True` 装配 rig 才能看见记录；产品缺省关的语义由既有套件覆盖，本关卡不重证。
  - 若错的影响：可能把「开了才有的记录」说成出厂行为。验证方式：执行日志写明 rig 开关；用例只断言「开启时逐条可查、内容零明文」。
- **A6 · 两例 `tests/ui/test_shell.py` ERROR 为本机既有环境性**：`ShellError: 后端需要口令`——按既有判据排除，不诊断、不修；全量结果逐条比对报错文本，只在与前批一致时排除。
  - 若错的影响：可能漏掉混在同类名目下的新失败形态。验证方式：比对报错文本 + 单独复跑该文件确认形态不变。
- **未决**：无 open `Q-NNN`（[阻塞与未决](../阻塞与未决.md) 当前 0 open）。

## 涉及契约
- [02 §5 / §6 / §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 取数、出网与能力声明
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `local_read` / `local_write` / `exec_command` / `net_access` 声明与批准
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环、交还与证据包

## 参考
- 本里程碑 11 个叶子

## 备注
关卡 `depends_on` 须覆盖 M9 全部叶子（[检查 7](../tools/verify_docs.py)）。
