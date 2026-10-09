---
id: T-AGT-007
parent: null
title: 循环上界开放为配置项
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M5
depends_on: [T-AGT-004.2]
status: done
decisions: [D-090, D-099]
verify: 新用例 14 例（GWT-1..5 + 族适配器 + 非法取值 fail-closed + 损坏文件；GWT-5 真 SkillRunner + 真 Store + 真官方 Pack 驱动真 run_agent_loop）+ 组合根级集成断言 1 例（tests/integration/test_config_registry.py）· 范围 1050 passed（243.45s）· 全量 3814 passed / 0 failed / 14 deselected（873.47s；基线 3786 +28 逐项归因无残差）· verify_docs --strict 检查 1–9 全过 0 断链 · 未触发联网面 · 见执行日志 [T-AGT-007]
---

# T-AGT-007 · 循环上界开放为配置项

## 目标

把自主查证循环的两条**上界**（步数 / LLM 调用次数）从「仅装配方可注入的参数」升为**端用户可调的可配置项**——关闭 [L3 册 `A3`](../../../遗留问题/L3-遗留问题.md)。

1. **配置项登记**：把 [`run_agent_loop`](../../../../src/st_agent/l3/runtime/loop.py) 已带缺省的 `max_steps`（8）/ `max_llm_calls`（12）两条取值登记为 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 配置项 `investigate.max_steps` / `investigate.max_llm_calls`（`scope=global`），经 L1 统一配置注册表门面**对话 + 面板双通道可改 / 变更留痕 / 可回滚**。
2. **owner 与适配器**：owner 住 L3（`st_agent.l3.runtime`），族适配器住 L3 侧、由组合根**注入** L1 门面（[铁律 7](../../../工程宪法.md)「层间只向下依赖」——先例 [`AgentFamily`](../../../../src/st_agent/l6/registry_adapter.py) / [`MemoryPolicyFamily`](../../../../src/st_agent/l2/memory/registry_adapter.py)）。
3. **消费缝**：给出「登记项 → 生效上界」的读取面（`LoopBounds.steps()` / `.llm_calls()`，缺省回落），供 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md) 装配循环时喂 [`run_agent_loop`](../../../../src/st_agent/l3/runtime/loop.py)。本任务**不装配循环本体**（那是 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)）。
4. **契约先行**（[铁律 8](../../../工程宪法.md)）：先改 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)（登记两条目）与 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（现行「系统预算、非用户可调项」口径改写），再改代码。

## 验收标准（Given-When-Then）

- **GWT-1 · 登记面可见且双通道同源**：Given 组合根装配了 L3，When 经门面 `entry("investigate.max_steps")` / `list(scope="global")` 取，Then 两条目在册——`scope=global`、`default` 分别 8 / 12、`value_schema` 含取值域、且**同时**带 `description_for_chat`（对话通道）与 `panel_form_spec`（面板通道）。
- **GWT-2 · 落值生效 + 留痕**：Given 用户经门面 `set("investigate.max_steps", 5, trace_id=…)`，When 读回，Then `LoopBounds.steps()` 得 5，且产生**一条** `ChangeRecord`（`config_id` / 旧值 / 新值 / `trace_ref` 齐备，可回滚）；值**未变**时不产生记录。
- **GWT-3 · 缺省回落**：Given 从未落过值，When 读，Then 得缺省 8 / 12（**不报错**）。
- **GWT-4 · 非法取值 fail-closed**：Given 落 `0` / 负数 / 布尔 / 非整数 / 超上限，When `apply`，Then 抛 `RegistryValidationError` 且**不落盘**（读回仍是原值/缺省），**不静默截断**。
- **GWT-5 · 端用户改的值真的落到循环**：Given 落 `max_steps=2`，When 用 `LoopBounds.steps()` 的值驱动 [`run_agent_loop`](../../../../src/st_agent/l3/runtime/loop.py)（模型永不结束），Then 在**第 2 步**触界终止（终止原因 `step_limit`）——配置项 ↔ 循环行为同一口径。

## 接口面

- **输入**：
  - [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md) 的循环入口与上界缺省——[`run_agent_loop(max_steps=…, max_llm_calls=…)`](../../../../src/st_agent/l3/runtime/loop.py) · `DEFAULT_MAX_STEPS`（8）/ `DEFAULT_MAX_LLM_CALLS`（12）（**单一事实源，本任务不另立常量**）。
  - L1 统一配置注册表门面（[`T-L1-012`](../M2/T-L1-012-统一配置注册表门面.md) / [D-067](../../../决策日志.md)）：[`ConfigRegistryFacade.register_family` / `entry` / `list` / `set`](../../../../src/st_agent/l1/registry/facade.py)；条目形态 [`ConfigEntry` / `ChangePolicy` / `PanelField`](../../../../src/st_agent/contracts/registry_types.py)。
  - `Store`（`config` 分区落值 / `execution_log` 分区留痕）。
  - 先例：[`NetAuditFamily`](../../../../src/st_agent/l1/registry/families.py)（标量开关 + 补落 `ChangeRecord`）· [`AgentFamily`](../../../../src/st_agent/l6/registry_adapter.py)（上层 owner 经组合根注入 L1 门面）。
  - 契约口径：[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)（登记形态与三能力）· [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（上界与终止）· [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（澄清协议 / 无 target 意图）。
- **输出**（本任务交付的公共 API 与落盘位置）：
  - **新增** [`src/st_agent/l3/runtime/bounds.py`](../../../../src/st_agent/l3/runtime/bounds.py)：`LoopBounds`（`steps()` / `llm_calls()` / `entries()` / `entry(config_id)` / `set_steps` / `set_llm_calls`）+ 常量 `INVESTIGATE_CONFIG_PREFIX = "investigate."` / `MAX_STEPS_CONFIG_ID = "investigate.max_steps"` / `MAX_LLM_CALLS_CONFIG_ID = "investigate.max_llm_calls"` / `loop_bounds_config_entries()`；落盘 `config` 分区 `investigate/max_steps.json` / `investigate/max_llm_calls.json`（`config_id` 主干即文件名，[01 §7 既有一不变量](../../../../src/st_agent/l1/registry/naming.py)）。
  - **新增** [`src/st_agent/l3/runtime/registry_adapter.py`](../../../../src/st_agent/l3/runtime/registry_adapter.py)：`InvestigateFamily` / `investigate_family(bounds)`（`config_prefix="investigate."`、`scope="global"`，`apply` 委托 `LoopBounds`）。
  - **只增** [`src/st_agent/app.py`](../../../../src/st_agent/app.py)：`_build_l3` 内建 `LoopBounds(store)` 并 `runtime.config_registry.register_family(investigate_family(bounds))`；`_L3Stack` 增 `bounds` 字段（供 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md) 取用）。**不**新增 M5 组合根、**不**改 [`__main__.py`](../../../../src/st_agent/__main__.py)（循环装配归 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)）。
  - 测试：**新增** `tests/l3/test_loop_bounds.py`（GWT-1–5 + 族适配器 + 非法取值）。
  - **供 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md) 的读取缝**：`LoopBounds(store).steps()` / `.llm_calls()` → `run_agent_loop(..., max_steps=…, max_llm_calls=…)`，缺省值即 [`run_agent_loop`](../../../../src/st_agent/l3/runtime/loop.py) 的缺省。

## 可关闭的遗留

- **L3 册 `A3`**（[L3-遗留问题.md](../../../遗留问题/L3-遗留问题.md)）——**本批关闭**。处置：本条即其「解封条件」的兑现——负责人 2026-10-08 拍定**开放端用户可调**，并选定**落 01 §7 配置项**（非意图参数）；契约两处口径同批改写，代码落 `investigate.*` 族。收工按册规规则 3 移入册尾「已闭（备查）」+ 写关闭留痕。
- 其余各册（2026-10-08 开工逐册读未闭区复核）：L0 册 `C3` / `C5`（皆**人决**）· L1 册全段闭 · L2 册全段闭 · L5 册空 · L6 册 `B1`（待人立项）——均「归属 / 解封条件」**非本任务**，无本批可闭项。

## 假设与前提

- **A1 · 上界走配置项而非意图参数**（本次 ④ 对齐拍定）——前提：配置项是「**长期偏好**」（改一次、此后每次查证都按它），意图参数是「**单次请求**」的取值；预算属前者。[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的问项是「一问一参、本次请求」语义，用预算去问等于每次查证都拦一道。
  **若错的影响**：若判定应走意图参数，返工面 = 改成 `INTENT_PARAM_SPECS` 条目（[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的意图级参数声明），并回退本任务的 01 §7 条目——**那是另一种交互形态**，非加一行。
  **验证方式**：GWT-1 断言条目在 01 §7 登记面（非意图参数面）；[`T-AGT-006`](T-AGT-006-第七类意图investigate与派发去向接线.md) 的 `INTENT_PARAM_SPECS` **一字不改**（既有「确认卡恰好一条目」用例仍绿）。
- **A2 · owner 住 L3、适配器经组合根注入 L1 门面**——前提：[铁律 7](../../../工程宪法.md)（L3 > L1，L1 不得 import L3）；先例 [`AgentFamily`](../../../../src/st_agent/l6/registry_adapter.py) / [`MemoryPolicyFamily`](../../../../src/st_agent/l2/memory/registry_adapter.py) 同裁。
  **若错的影响**：若把族塞进 L1 的 `families.py`，L1 将反向 import L3 → `tests/test_layering.py` 红。
  **验证方式**：`tests/test_layering.py` 不改即绿；适配器文件住 `src/st_agent/l3/` 下。
- **A3 · 缺省值沿用 `run_agent_loop` 的既有缺省**——前提：`DEFAULT_MAX_STEPS=8` / `DEFAULT_MAX_LLM_CALLS=12` 已是单一事实源（[`T-AGT-004.2 A2`](T-AGT-004.2-循环驱动、协议端口与上界.md)）；配置项 `default` 直接取它们，**不另立数字**。
  **若错的影响**：两处各写一个 8，改缺省时只改一处 → 登记面与运行期缺省分叉。
  **验证方式**：GWT-3 断言未落值时 `LoopBounds` 返回与 `DEFAULT_*` 同源；代码以 `from ...loop import DEFAULT_MAX_STEPS` 引用。
- **A4 · 取值域＝整数 ≥1 且 ≤ 上限**——前提：循环入口已校验「≥1 的整数」（[`run_agent_loop`](../../../../src/st_agent/l3/runtime/loop.py)），上限是**防跑飞**的护栏（这正是上界当初被称「系统预算」的原因）。**上限取值属实现口径**，建议**步数 ≤ 32 / 调用次数 ≤ 64**（缺省值的 4× / 5× 量级）。**跨字段约束（`llm_calls > steps`）不做**——两值独立可设（负责人原话「两个取值都可自主选择」），任意组合下循环行为均正确（先到者终止）。
  **若错的影响**：若不要上限，A4 降为「仅 ≥1 下界」即除；若认为应约束跨字段，返工面 = `apply` 加一条交叉校验。
  **验证方式**：GWT-4 断非法取值 fail-closed；GWT-5 断合法临界值生效。
- **A5 · 改预算需确认（`change_policy.requires_confirmation=True`）**——前提：改上界会改自主循环的**成本 / 封禁暴露面**，与项目「不静默放宽」的一贯取向一致（对照 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 运行期授权档位）。
  **若错的影响**：若判为免确认，改一行布尔即除。
  **验证方式**：GWT-1 断 `change_policy.requires_confirmation is True`。
- **A6 · 本任务不装配循环**——前提：[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 明写「循环的**装配**归 M5 集成关卡」；本任务只交配置面 + 读取缝，装配（protocol / runner / tools / gate / store）归 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)。
  **若错的影响**：越界装配会与 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md) 声明的交付面重叠。
  **验证方式**：本任务不改 [`__main__.py`](../../../../src/st_agent/__main__.py)、不新增 `build_m5_runtime`；GWT-5 用**直调** [`run_agent_loop`](../../../../src/st_agent/l3/runtime/loop.py) 验，端到端生效由 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md) 断。
- **A7 · 契约改动面＝三处，均活跃区**——前提：[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)（登记两条目）+ [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（口径改写）；**不改** [05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)。[`T-AGT-006 A2`](T-AGT-006-第七类意图investigate与派发去向接线.md) 的核心断言（**无意图级参数**）**仍然成立**，故该已 `done` 任务文件**不改写**（冻结口径，见 [D-099](../../../决策日志.md)）。
  **若错的影响**：漏改任一处的「非用户可调项」字样 → 文档自相矛盾（`verify_docs.py` 不断此，靠人读）。
  **验证方式**：全库 grep `非用户可调` / `系统预算` 于 [05](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 归零；`verify_docs.py --strict` 检查 1–9 全过。

## 涉及契约

- [01 §7 配置元模型](../../../../docs/技术架构-v2/01-平台共享契约.md)（登记两条 `investigate.*` 条目 / 登记形态与三能力 / `change_policy`）
- [05 §10 自主查证循环](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（上界与终止——「系统预算」口径改写为「可配置项」）
- [05 §3.2 澄清协议](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（`investigate` 仍无**意图级参数**，但原因改写为「上界走配置项」）
- [铁律 3 可配置性优先](../../../工程宪法.md) · [铁律 7 层间只向下依赖](../../../工程宪法.md) · [铁律 8 接口变更有序](../../../工程宪法.md)

## 参考

- [D-090](../../../决策日志.md)（⑥ 双上界 / M5 登记）· [D-099](../../../决策日志.md)（本批：配置项开放 + owner 落点 + 取值域）
- 遗留来源：[`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md) ④ 对齐「两个取值都可自主选择、不写死」（2026-10-07）+ [L3 册 `A3`](../../../遗留问题/L3-遗留问题.md)
- 先例：[`T-L1-012`](../M2/T-L1-012-统一配置注册表门面.md)（门面交付 / [D-067](../../../决策日志.md)）· [`T-AGT-005.1`](T-AGT-005.1-运行期授权档位与共享清单.md)（上层 owner 经适配器注入门面 + 留痕）
- 上游：[`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md)
- 下游：[`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)（消费读取缝装配循环）
