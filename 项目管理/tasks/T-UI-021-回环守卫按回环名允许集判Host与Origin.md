---
id: T-UI-021
parent: null
title: 回环守卫按回环名允许集判 Host/Origin（放行 localhost 写法）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §1.1](../../docs/技术架构-v2/00-架构总览.md)"
priority: P1
milestone: M6
depends_on: [T-UI-001.2]
status: done
decisions: [D-113]
verify: 范围套件（contracts ∪ layering ∪ tools ∪ integration ∪ ui）**1171 passed / 0 failed**（另 2 errors 为 `test_shell.py` 走 `default_root()` 的环境性老毛病，非回归）· 新用例 **8 例**（守卫判定 5 + 真服务 3，后者含**按主机名 `localhost`** 可达一条）+ 一键脚本告警改写 · **红-绿已验**（`git stash` 掉 `security.py` 后 4 例红）· A1 有正面证据（本机 `localhost` 解析 `::1`→`127.0.0.1`，回落可达）· `verify_docs --strict` 检查 1–9 全过、0 断链 · 未触及联网面 ⇒ 未触发 `-m live` · **未做浏览器走查**（验证止于真回环服务 + 真 OS 名字解析）· 见 [执行日志](../执行日志.md) `[T-UI-021]`
---

# T-UI-021 · 回环守卫按回环名允许集判 Host/Origin（放行 localhost 写法）

## 目标

把 [`RequestGuard`](../../src/st_agent/ui/security.py) 的 `Host` / `Origin` 判定从「与绑定地址**字面相等**」改为「落在**回环名允许集**内」，使 `http://localhost:<port>` 与 `http://127.0.0.1:<port>` 同样可用；一并改写一键脚本里那句「NOT localhost」的绕行告警。**DNS rebinding 防线不变**——被挡的仍是攻击者可控的域名（[D-113](../决策日志.md)）。

**缺陷性质**：本项**不是守卫失效，而是实现比安全目标严**。[D-060](../决策日志.md) ③F 的原文只说「绑 `127.0.0.1` + 随机端口 + 令牌 + `Origin` 校验防 DNS rebinding」，**从未有一条决策说要拒 `localhost`**。它是把 `Host` 校验实现成 `!= self.authority` 带来的副作用，此前只活在 [`start-st-agent.bat:115`](../../start-st-agent.bat) 的绕行注释与走查笔记里，**未进任何遗留册**。

## 验收标准（Given-When-Then）

- GWT-1：Given 服务绑 `127.0.0.1`（默认），When 以 `http://localhost:<port>/#t=<token>` 首导航，Then 200 回静态壳，**不再**是 403 错误 JSON
- GWT-2：Given 页面由 `localhost` 加载，When 发数据面请求（`Origin: http://localhost:<port>`），Then 通过（`Host` 与 `Origin` 两处都按允许集判）
- GWT-3：Given 攻击者可控的名，When 送 `Host: evil.example` / `localhost.evil.example` / 别名配错端口，Then 一律 403（允许集是**整串精确匹配**，非前缀）
- GWT-4：Given 绑定名**非回环**（如 LAN IP），When 送 `Host: localhost:<port>`，Then 403——允许集的扩张以「绑定名本身是回环」为前提，不是无条件放行
- GWT-5：Given 全项目相对链接，When 跑 `python tools/verify_docs.py --strict`，Then 检查 1–9 全过、0 断链

## 接口面

- **输入（消费的前置接口）**：[`T-UI-001.2`](done/M1/T-UI-001.2-骨架通信面与组件注册表宿主.md) 交付的 [`RequestGuard`](../../src/st_agent/ui/security.py) 与 [`serve()`](../../src/st_agent/ui/server.py) 的 `host` / `port`（**均已交付**，无待交付上游）
- **输出（本任务交付的公共 API / 落盘位置）**：
  - [`security.py`](../../src/st_agent/ui/security.py) 的 `RequestGuard.authorities` / `.origins`（frozenset 形态，取代原 `authority` / `origin` 单值属性）与常量 `LOOPBACK_NAMES`；`check()` 判定语义随之变更
  - 测试：[`tests/ui/test_ui_security.py`](../../tests/ui/test_ui_security.py)（判定层 +5）· [`tests/ui/test_ui_server.py`](../../tests/ui/test_ui_server.py)（真服务 +3，含**按主机名 `localhost`** 可达一条）
  - [`start-st-agent.bat`](../../start-st-agent.bat) 第 115 行告警改写
  - **无新契约面、无新端点、无落盘形态变更**——不动 [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md)，[铁律 8](../工程宪法.md) 不被触发

## 可关闭的遗留

无（开工查各层册未闭区：L0–L6 相关段皆 0 未闭，无一条的「归属」或「解封条件」指向本任务；[阻塞与未决.md](../阻塞与未决.md) 当前 0 open）。

## 假设与前提

- **A1**：绑 IPv4 的服务，浏览器访问 `http://localhost:<port>` 能到达（Windows 上 `localhost` 优先解析 `::1`，靠 happy-eyeballs 回落到 IPv4）。**若错的影响**：放宽守卫也不够，须另加 `::1` 的绑定或允许项。**验证方式**：本机真浏览器走查 + 真服务用例（`test_ui_server.py` 两条）；**已复核成立**
- **A2**：攻击者无法产出 `Host: localhost:<port>`（`localhost` 是 RFC 6761 保留名，不可注册、不可改解析）。**若错的影响**：放宽即引入新的攻击面。**验证方式**：GWT-3 三条用例；**已复核成立**
- **A3**：绑定名非回环时行为逐字节不变。**验证方式**：`test_non_loopback_binding_does_not_admit_localhost`；**已复核成立**
- **A4**：「`localhost` 不可达」不是任何既有 GWT 的验收面，只是未决策的实现副作用。**若错的影响**：某任务已断言拒绝 `localhost`，改它须同步该任务留痕。**验证方式**：全仓 grep 断言 + 既有用例全绿（范围套件跑通即证）；**已复核成立**——无任何既有用例断言 `localhost` 被拒

## 涉及契约（链接到具体小节）

- [00-架构总览 §1.1 进程形态](../../docs/技术架构-v2/00-架构总览.md)（本地回环服务的安全口径，经 [D-060](../决策日志.md) ③F 与 [D-113](../决策日志.md) 承接）

## 参考

- 决策：[D-060](../决策日志.md)（回环安全口径取 F）· [D-113](../决策日志.md)（本条的实现期订正）
- 先例：[T-UI-002.2](done/M3/T-UI-002.2-桌面壳本体窗口托盘自启与通知接线.md)（壳开窗即用 `127.0.0.1`，本项不改变壳侧行为）
- **本批 ⑥ 验证期发现、另立**：[T-UI-022](T-UI-022-回环服务拒绝请求时排空请求体.md)（拒绝路径不读请求体 ⇒ Windows 连接复位盖掉 401/404；本机范围套件里 `test_ui_face_ports.py::test_new_paths_are_get_only[/api/graph/status]` 的偶发 FAILED 即此）

## 备注

一处**未进册的流程漏项**由此暴露：「localhost 撞 403」自 `T-UI-001.2` 起就存在，却一直只活在脚本注释与走查笔记里，没有任何遗留册条目接住它。本批已按册规登记（[D-113](../决策日志.md)），不留新的悬空项。本批 ⑥ 又发现第二条同类（[T-UI-022](T-UI-022-回环服务拒绝请求时排空请求体.md)），同批立项接住。
