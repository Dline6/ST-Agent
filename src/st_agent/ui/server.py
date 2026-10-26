"""本机回环 HTTP 服务（[00 §1.1]；[D-060] ③ 取 F）。

只绑 ``127.0.0.1``；端口取 ``0`` 由 OS 分配（避开可猜的固定端口）；每次启动换令牌。
实现用**标准库**——与「L1 手写 JSON-RPC、不引官方 MCP SDK」同一取向（[D-063] ②）。

**UI 只是客户端**：本服务只读各层公共 API，不直开存储（单一写者＝后端进程，[02 §2.4]）。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from st_agent.contracts.result_envelope import ResultEnvelope

from st_agent.ui.app import PAGE_PATHS, UiApp, build_ui
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.security import new_token

__all__ = ["RunningUi", "UiServer", "serve"]

_LOG = logging.getLogger("st_agent.ui.server")

_MAX_BODY_BYTES = 256 * 1024
"""对话请求体的读取上限（回环本机、单轮文本；超限截断后按畸形处理，不整块吃进内存）。"""

_DRAIN_CHUNK_BYTES = 64 * 1024
"""读净请求体时的分块大小——只决定**读取**的内存占用（丢掉的字节不累计）。"""

_CONTENT_TYPES = {    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

_GET_ROUTES: dict[str, Callable[[UiApp, str], dict]] = {
    "/api/health": lambda app, _q: app.api_health(),
    # 面可用性探针（`.1` 交付；各叶在此表**只增**各自的取数端点）。
    "/api/reflection/status": lambda app, _q: app.api_face_status("reflection"),
    "/api/evolution/status": lambda app, _q: app.api_face_status("reflection"),
    "/api/eco/status": lambda app, _q: app.api_face_status("eco"),
    # M6 各面探针（[`T-UI-011.1`]）：与新端口**逐名对应**（端口名＝面名），故都是同一条
    # `api_face_status`——未接线即 `unavailable` + 点名，不 500、不装空表。
    "/api/workspace/status": lambda app, _q: app.api_face_status("workspace"),
    "/api/skills/status": lambda app, _q: app.api_face_status("skills"),
    "/api/mcp/status": lambda app, _q: app.api_face_status("mcp"),
    "/api/graph/status": lambda app, _q: app.api_face_status("graph"),
    "/api/deliberation/status": lambda app, _q: app.api_face_status("deliberation"),
    "/api/delivery/status": lambda app, _q: app.api_face_status("delivery"),
    "/api/settings/status": lambda app, _q: app.api_face_status("settings"),
    # 反思中心（`T-UI-004.2`）
    "/api/reflection/reports": lambda app, q: app.api_reflection_report(week=_query(q, "week")),
    "/api/reflection/reports/trace": lambda app, q: app.api_reflection_trace(
        week=_query(q, "week")
    ),
    "/api/reflection/feedback-capture": lambda app, _q: app.api_reflection_feedback_capture(),
    "/api/reflection/proposals": lambda app, _q: app.api_reflection_proposals(),
    "/api/reflection/experiments": lambda app, _q: app.api_reflection_experiments(),
    "/api/reflection/training": lambda app, _q: app.api_reflection_training(),
    # Studio 画布（`T-UI-005.1`）——已交会话一览 / 画布 / 可选 Skill 清单
    "/api/studio/sessions": lambda app, _q: app.api_studio_sessions(),
    "/api/studio/canvas": lambda app, q: app.api_studio_canvas(
        proposal_id=_query(q, "proposal_id")
    ),
    "/api/studio/skills": lambda app, _q: app.api_studio_skills(),
    # 演进面（`T-UI-004.3`）
    "/api/evolution/changes": lambda app, _q: app.api_evolution_changes(),
    "/api/evolution/authorization": lambda app, _q: app.api_evolution_authorization(),
    # 生态面（`T-UI-004.4`）
    "/api/eco/export/plan": lambda app, _q: app.api_eco_export_plan(),
    "/api/eco/inbox": lambda app, _q: app.api_eco_inbox(),
    "/api/eco/index": lambda app, _q: app.api_eco_index(),
    "/api/eco/imports": lambda app, _q: app.api_eco_imports(),
    "/api/eco/violations": lambda app, _q: app.api_eco_violations(),
    "/api/eco/violations/history": lambda app, _q: app.api_eco_violation_history(),
    # 记忆区（`T-UI-009.2`）：持仓 / 关注行情读面——两段各一条，面键由路径定
    "/api/memory/holdings": lambda app, _q: app.api_memory_positions("holdings"),
    "/api/memory/watchlist": lambda app, _q: app.api_memory_positions("watchlist"),
    # 记忆区图谱面（[`T-UI-014.1`]）：三视图走**同一条**查询层、画像卡 ≤5 标签——两条**只读**
    # 取数面；视图名由**查询串**给（[11-sitemap §2.2]：带标识的面走查询串，路径集不变）。
    "/api/memory/browse": lambda app, q: app.api_memory_browse(
        view=_query(q, "view") or "list",
        topic=_query(q, "topic"),
        task_type=_query(q, "task_type") or "chat",
    ),
    "/api/memory/profile": lambda app, _q: app.api_memory_profile(),
    # 记忆中详情 / 修正历史（[`T-UI-014.2`]）：`?node=<id>` 与 `?node=<id>&view=revisions`——
    # 带标识的面走**查询串**（[11-sitemap §2.2]：路径集仍是有限字面量集合，不引入动态段）。
    "/api/memory/node": lambda app, q: app.api_memory_node(node_id=_query(q, "id")),
    "/api/memory/node/revisions": lambda app, q: app.api_memory_node_revisions(
        node_id=_query(q, "id")
    ),
    # 导入导出面板与 Onboarding（[`T-UI-014.3`]）：计划 / 状态两条**只读**。
    "/api/memory/export/plan": lambda app, _q: app.api_memory_export_plan(),
    "/api/memory/onboarding": lambda app, _q: app.api_memory_onboarding(),
    # Chat 主界面（[`T-UI-012.1`]）：上下文卡片与快捷指令清单——两条**只读**取数面，
    # 供首页常驻卡片与 `/` 补全菜单；未接端口即 `unavailable` + 点名（写面仍只 `/api/chat`）。
    "/api/chat/context": lambda app, _q: app.api_chat_context(),
    "/api/chat/commands": lambda app, _q: app.api_chat_commands(),
    # 工作区（[`T-UI-013`]）：钉住物的**描述序列**读面——**一条只读**。写面（钉住 / 取消
    # 钉住 / 重解析）在下面 `_POST_ROUTES`，因为三条都是**显式动作**、非幂等读面。
    "/api/workspace/pins": lambda app, _q: app.api_workspace_pins(),
    # 推理区（[`T-UI-016.1`]）：视角阵容读面——**一条只读**；逐条启用态 + 启停 / 删除动作
    # （内置只给停用，[06 §1]），路由由 `surface` 面键在渲染件的固定表里解析。
    "/api/deliberation/lenses": lambda app, _q: app.api_deliberation_lenses(),
    # 视角定义读面（[`T-UI-016.2`]）：`?lens=<id>`——带标识的面走**查询串**（[11-sitemap §2.2]：
    # 路径集仍是有限字面量集合，不引入动态段）。
    "/api/deliberation/lens": lambda app, q: app.api_deliberation_lens(
        lens_id=_query(q, "id")
    ),
    # 决策留痕读面（[`T-UI-016.3`]）：[06 §2.4] 写下的 L2 `history` 节点，逐条并列。
    "/api/deliberation/decisions": lambda app, _q: app.api_deliberation_decisions(),
    # 设置区（[`T-UI-018.1`] / [`.2`] / [`.3`]）：存储 / LLM 提供方 / 数据源 / 隐私四页，
    # 维护面三条声明（备份 / 恢复 / 清空），网络活动面板（`?hours=`），离线横幅。
    # 带参数的读面走**查询串**（[11-sitemap §2.2]：路径集仍是有限字面量集合，不引动态段）。
    "/api/settings/storage": lambda app, _q: app.api_settings_storage(),
    "/api/settings/llm": lambda app, _q: app.api_settings_llm(),
    "/api/settings/data": lambda app, _q: app.api_settings_data(),
    # 轻量批次快照（[D-130]）：运行中轮询只走这一条，不触发整库统计。
    "/api/settings/data/job": lambda app, _q: app.api_settings_data_job(),
    "/api/settings/sources": lambda app, _q: app.api_settings_sources(),
    "/api/settings/privacy": lambda app, _q: app.api_settings_privacy(),
    "/api/settings/maintenance": lambda app, _q: app.api_settings_maintenance(),
    "/api/settings/network": lambda app, q: app.api_settings_network(hours=_query(q, "hours")),
    "/api/settings/offline": lambda app, _q: app.api_settings_offline(),
    # 能力区 · Skill 库（[`T-UI-015.1`]）：清单 / 详情 / 参数面板 / 执行日志——四条**只读**。
    # 带标识的面走**查询串**（[11-sitemap §2.2]：路径集仍是有限字面量集合，不引入动态段）；
    # 参数落值那条是**显式动作**，在下面 `_POST_ROUTES`。
    "/api/skills/catalog": lambda app, _q: app.api_skills_catalog(),
    "/api/skills/detail": lambda app, q: app.api_skills_detail(skill_id=_query(q, "skill")),
    "/api/skills/params": lambda app, q: app.api_skills_params(skill_id=_query(q, "skill")),
    "/api/skills/logs": lambda app, q: app.api_skills_logs(
        skill_id=_query(q, "skill"), limit=_query(q, "limit")
    ),
    # 能力区 · MCP Hub（[`T-UI-015.2`]）：清单 / 详情 / tool 浏览器 / 权限面四读。
    # 带标识的面一律走**查询串**（[11-sitemap §2.2]）；三条写面在下面 `_POST_ROUTES`。
    "/api/mcp/servers": lambda app, _q: app.api_mcp_servers(),
    "/api/mcp/server": lambda app, q: app.api_mcp_server(server_id=_query(q, "id")),
    "/api/mcp/tools": lambda app, q: app.api_mcp_tools(server_id=_query(q, "id")),
    "/api/mcp/permissions": lambda app, q: app.api_mcp_permissions(server_id=_query(q, "id")),
    # 能力区 · Studio 附加面（[`T-UI-015.3`]）：自建工作流 / 属性 / 版本历史 / 模板库 /
    # 模板预览 / 试跑历史——六条**只读**（带标识的面一律走查询串，[11-sitemap §2.2]）；
    # 版本动作、模板 fork 与试跑驱动三条是**显式动作**，在下面 `_POST_ROUTES`。
    "/api/studio/workflows": lambda app, _q: app.api_studio_workflows(),
    "/api/studio/workflow": lambda app, q: app.api_studio_workflow(
        flow_id=_query(q, "flow")
    ),
    "/api/studio/versions": lambda app, q: app.api_studio_versions(
        flow_id=_query(q, "flow"), a=_query(q, "a"), b=_query(q, "b")
    ),
    "/api/studio/templates": lambda app, _q: app.api_studio_templates(),
    "/api/studio/template": lambda app, q: app.api_studio_template(
        template_id=_query(q, "id")
    ),
    "/api/studio/trials": lambda app, q: app.api_studio_trials(
        flow_id=_query(q, "flow"), a=_query(q, "a"), b=_query(q, "b")
    ),
    # 触达区（[`T-UI-017`]）：每日报告 / 已生成日清单 / 推送历史 / 触达配置面板 /
    # 五动作反馈对话框 / 疲劳面——六条**只读**。带标识与参数的面一律走**查询串**
    # （[11-sitemap §2.2]：路径集仍是有限字面量集合，不引入动态段）；三条写面在下面
    # `_POST_ROUTES`（生成 / 配置落值 / 疲劳答复）。
    "/api/delivery/report": lambda app, q: app.api_delivery_report(day=_query(q, "day")),
    "/api/delivery/reports": lambda app, q: app.api_delivery_reports(limit=_query(q, "limit")),
    "/api/delivery/history": lambda app, q: app.api_delivery_history(limit=_query(q, "limit")),
    "/api/delivery/config": lambda app, _q: app.api_delivery_config(),
    "/api/delivery/feedback": lambda app, q: app.api_delivery_feedback(
        delivery_id=_query(q, "delivery")
    ),
    "/api/delivery/fatigue": lambda app, _q: app.api_delivery_fatigue(),
    "/api/delivery/inquiries": lambda app, _q: app.api_delivery_inquiries(),
}
"""`GET` 数据面的路由表：路径 → `(UiApp, 查询串)` → 载荷。**只此一处**判定「某路径存在与否」
——散落的 `if path == …` 分支会随着端点增多而漂移（[`T-UI-003`] 的同型取向）。"""

_POST_ROUTES: dict[str, Callable[[UiApp, dict], dict]] = {
    "/api/chat": lambda app, body: app.api_chat(body),
    # 反思中心的写面（`T-UI-004.2`）：反馈采集与提案处置
    "/api/reflection/feedback": lambda app, body: app.api_reflection_feedback(body),
    "/api/reflection/proposals/decide": lambda app, body: app.api_reflection_decide(body),
    # 主动提案落 Studio（`T-L6-004.2`）：交 Studio 落画布 + 接受 / 否决
    "/api/reflection/proposals/studio": lambda app, body: app.api_reflection_studio_handoff(body),
    "/api/reflection/proposals/studio/decide": lambda app, body: (
        app.api_reflection_studio_decide(body)
    ),
    # Studio 画布编辑（`T-UI-005.1`）：增删节点 / 连线断线 / 建分组解散
    "/api/studio/edit": lambda app, body: app.api_studio_edit(body),
    # 演进面的写面（`T-UI-004.3`）：一键回滚 / 应用设置 / 出厂重置
    "/api/evolution/changes/rollback": lambda app, body: app.api_evolution_rollback(body),
    "/api/evolution/authorization": lambda app, body: app.api_evolution_set(body),
    "/api/evolution/factory-reset": lambda app, body: app.api_evolution_factory_reset(body),
    # 生态面的写面（`T-UI-004.4`）：导出 / 导入校验与逐项批准与安装 / 禁用越界能力
    "/api/eco/export": lambda app, body: app.api_eco_export(body),
    "/api/eco/import/review": lambda app, body: app.api_eco_import_review(body),
    "/api/eco/import/permissions": lambda app, body: app.api_eco_import_permissions(body),
    "/api/eco/import/decide": lambda app, body: app.api_eco_import_decide(body),
    "/api/eco/import/install": lambda app, body: app.api_eco_import_install(body),
    "/api/eco/violations/disable": lambda app, body: app.api_eco_disable(body),
    # 记忆区图谱面的写面（[`T-UI-014.3`]）：导出确认门 + Onboarding 提交——两条均为**显式
    # 确认**后的写；未经确认（`confirmed_by != "user"`）即拒（[04 §8] 的强制三步）。
    "/api/memory/export": lambda app, body: app.api_memory_export(body),
    "/api/memory/onboarding": lambda app, body: app.api_memory_onboarding_submit(body),
    # 工作区（[`T-UI-013`]）：钉住 / 取消钉住 / 重解析——三条均为**显式动作**。
    # 重解析去哪个来源由**服务端**按记录下来的来源面键决定（[01 §12]：动作不进描述，
    # 描述里既没有 URL 也没有方法），故请求体只给 `description` / `description_id`。
    "/api/workspace/pin": lambda app, body: app.api_workspace_pin(body),
    "/api/workspace/unpin": lambda app, body: app.api_workspace_unpin(body),
    "/api/workspace/refresh": lambda app, body: app.api_workspace_refresh(body),
    # 推理区（[`T-UI-016.1`]）：页内**自带触发**一次编排 + 自定义视角的增删启停——三条
    # `POST`（`analyze` 会真跑 Skill / 读记忆，非幂等读面，故走写表）。
    "/api/deliberation/analyze": lambda app, body: app.api_deliberation_analyze(body),
    "/api/deliberation/lenses": lambda app, body: app.api_deliberation_lens_create(body),
    "/api/deliberation/lenses/update": lambda app, body: (
        app.api_deliberation_lens_update(body)
    ),
    # 决策沉淀（[`T-UI-016.3`]）：决策 + 理由 + 采纳 / 忽略视角 → L2 `history` 节点。
    "/api/deliberation/decision": lambda app, body: app.api_deliberation_decision(body),
    # 设置区的写面（[`T-UI-018`]）：设置落值（经 [01 §7] 统一注册表）/ 备份 / 恢复 / 清空 /
    # 凭据增改吊销 / 网络活动导出——六条全部是**显式动作**，故进写表。
    "/api/settings/apply": lambda app, body: app.api_settings_apply(body),
    "/api/settings/store/relocate": lambda app, body: app.api_settings_store_relocate(body),
    "/api/settings/llm/bootstrap": lambda app, body: app.api_settings_llm_bootstrap(body),
    "/api/settings/data/sync": lambda app, body: app.api_settings_data_sync(body),
    "/api/settings/data/preflight": lambda app, body: app.api_settings_data_preflight(body),
    "/api/settings/data/clear": lambda app, body: app.api_settings_data_clear(body),
    "/api/settings/data/sync/cancel": lambda app, body: (
        app.api_settings_data_sync_cancel(body)
    ),
    "/api/settings/convert": lambda app, body: app.api_settings_convert(body),
    "/api/settings/backup": lambda app, body: app.api_settings_backup_run(body),
    "/api/settings/restore": lambda app, body: app.api_settings_restore(body),
    "/api/settings/wipe": lambda app, body: app.api_settings_wipe(body),
    "/api/settings/credential": lambda app, body: app.api_settings_credential(body),
    "/api/settings/online": lambda app, body: app.api_settings_online(body),
    "/api/settings/network/export": lambda app, body: app.api_settings_network_export(body),
    # 能力区 · Skill 库（[`T-UI-015.1`]）：参数落值——经 `SkillRegistry.set_parameters`
    # （[03 §1.1] 的唯一写口），超范围即拒不保存，故是**显式动作**而非读面。
    "/api/skills/params": lambda app, body: app.api_skills_params_set(body),
    # 能力区 · MCP Hub（[`T-UI-015.2`]）：挂载一台 Server / 启停移除与连接测试 / 逐项批准。
    # 三条都是**显式动作**——挂载会真连一次（失败不落盘），移除走整条回收序列。
    "/api/mcp/servers": lambda app, body: app.api_mcp_server_create(body),
    "/api/mcp/servers/update": lambda app, body: app.api_mcp_server_update(body),
    "/api/mcp/permissions": lambda app, body: app.api_mcp_permission_decide(body),
    # 能力区 · Studio 附加面（[`T-UI-015.3`]）：版本激活 / 回滚、模板 fork 交画布会话、
    # 试跑驱动（起跑 / 单步 / 跑到结束 / 暂停 / 继续 / 中止）——三条都是显式动作。
    "/api/studio/workflow/version": lambda app, body: app.api_studio_version_set(body),
    "/api/studio/template/fork": lambda app, body: app.api_studio_template_fork(body),
    "/api/studio/trial": lambda app, body: app.api_studio_trial(body),
    # 触达区（[`T-UI-017`]）：生成当日报告（对同一纯函数的显式重放）/ 触达配置落值
    # （预算格位 / 渠道链 / 情境模式 / 日报时刻 / 疲劳阈值——写面按条目取值形态分流）/
    # 疲劳答复（经 L3 采集面铸 FeedbackEvent 发 `FeedbackRecorded`）——三条都是**显式动作**。
    "/api/delivery/report/build": lambda app, body: app.api_delivery_report_build(body),
    "/api/delivery/config": lambda app, body: app.api_delivery_config_apply(body),
    "/api/delivery/fatigue/answer": lambda app, body: app.api_delivery_fatigue_answer(body),
    # 壳意图（[`T-UI-027.1`]）：`Ctrl+W` 收托盘 / `Ctrl+Q` 退出——动作到后端，再由后端沿
    # 既有 stdout 控制管道回传壳（壳无 JS 桥，不开专有通道）。非壳会话显式 unavailable。
    "/api/shell/intent": lambda app, body: app.api_shell_intent(body),
}
"""`POST` 数据面的路由表：路径 → `(UiApp, body)` → 载荷。**非本表内的 POST 一律 404**。

受限写端点（提案处置 / 回滚 / 导出导入 / 授权切换 / 出厂重置）由各叶在此表登记；
请求体一律经 :meth:`UiRequestHandler._read_json_body` 归一（畸形 / 超大 → `{}`，由门面 fail-closed），
且**响应写出前体必读净**（:meth:`UiRequestHandler._drain_request_body`，含表外路径的 404）。
"""


def _query(raw: str, name: str) -> str:
    """取查询串里的一个参数（缺省空串；只取首个值，同既有 dev 端点的口径）。"""
    return parse_qs(raw).get(name, [""])[0]


def _dispatch_guarded(where: str, call: Callable[[], dict]) -> dict:
    """跑一次端点，**任何逸出的异常都转成信封**（[00 §6] 失败显式化）。

    各端点自身已按「输入类失败 / 其余」分流（见 `UiApp._call_face`）；本条是**兜底**，
    拦的是「参数在进入端点之前就抛」那一类——例如请求体的必填项在路由 lambda 里求值。
    没有它，异常会从 `http.server` 逸出、客户端只看到断连而拿不到任何信封（[`T-UI-003`]
    在 `/api/chat` 上遇到过的同一形态，2026-10-07 由 [`T-UI-004.3`] 在演进端点上再撞一次）。
    """
    try:
        return call()
    except (ValueError, KeyError) as exc:
        _LOG.info("端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
        return envelope_payload(
            ResultEnvelope.validation_failed(str(exc) or "请求不合契约")
        )
    except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
        log_ref = f"ui/route-{uuid4().hex[:12]}"
        _LOG.exception("端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
        return envelope_payload(
            ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref)
        )


def _restart_blocked(app: Any, path: str) -> dict | None:
    """重启闸门：破坏性操作（清空 / 恢复）之后，**全部**数据面端点一律 `unavailable`。

    闸门放在**传输层**而不是某个面里——各端点构造 `unavailable` 的姿势不止一处（有的走
    `UiApp._call_face`、有的自己判面缺席），只在其中一处设闸会漏掉另一些。`/api/health`
    除外：壳要靠它判断「服务还活着吗」，把它一起拦掉会让页面连不上而非显示原因。
    """
    reason = getattr(getattr(app, "restart_gate", None), "reason", None)
    if not reason or path == "/api/health":
        return None
    return envelope_payload(
        ResultEnvelope.unavailable(reason, last_updated_at=datetime.now().astimezone())
    )


class UiRequestHandler(BaseHTTPRequestHandler):
    """回环请求处理。

    ``app`` 由 :func:`serve` 在**绑定后**以子类属性注入——每个服务实例一个子类，
    避免同进程内多个实例互相串台。
    """

    app: UiApp

    server_version = "STAgentUI/0.1"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        _LOG.debug("%s - %s", self.address_string(), fmt % args)

    # ── 路由 ────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 —— http.server 规定的接口名
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        # `GET` 常态无体；带体的 `GET`（本服务不产这种形态，但客户端可发）同样要在响应前
        # 读净，否则关闭连接时对端拿到复位 —— 见 :meth:`_drain_request_body`。
        self._drain_request_body()

        verdict = self.app.guard.check(self.headers, require_token=path.startswith("/api/"))
        if not verdict.allowed:
            self._send_json(verdict.status, {"error": verdict.reason})
            return

        handler = _GET_ROUTES.get(path)
        if handler is not None:
            blocked = _restart_blocked(self.app, path)
            if blocked is not None:
                self._send_json(200, blocked)
                return
            self._send_json(200, _dispatch_guarded(path, lambda: handler(self.app, parsed.query)))
            return

        if path == "/api/dev/sample":
            wanted = parse_qs(parsed.query).get("status", [""])[0]
            payload = self.app.api_dev_sample(wanted)
            if payload is None:
                self._send_text(404, "未找到")
                return
            self._send_json(200, payload)
            return

        if path == "/api/dev/description":
            wanted = parse_qs(parsed.query).get("kind", [""])[0]
            described = self.app.api_dev_description(wanted)
            if described is None:
                self._send_text(404, "未找到")
                return
            self._send_json(200, described)
            return

        if path in PAGE_PATHS or path in ("/", "/index.html"):
            self._send_text(200, self.app.index_html())
            return

        asset = self.app.static_file(path)
        if asset is None:
            self._send_text(404, "未找到")
            return
        self._send_file(asset)

    def do_POST(self) -> None:  # noqa: N802 —— http.server 规定的接口名
        """数据面的写端点，路径全部取自 :data:`_POST_ROUTES`（表外一律 404）。

        校验与 GET 同一档——`/api/*` 必带令牌（`Origin` 校验防 DNS rebinding）；
        请求体经 `_read_json_body` 归一为 dict；组合根未注入门面时各端点自身回
        `unavailable`（不伪造，见 `UiApp.api_chat` 与各面端点）。

        **每条出口都先读净请求体**（含三条拒绝分支）——写完响应就关闭一条接收队列里还有
        未读数据的连接会让对端拿到连接复位，把本该显示的信封盖成网络错误，见
        :meth:`_drain_request_body`。
        """
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        verdict = self.app.guard.check(self.headers, require_token=path.startswith("/api/"))
        if not verdict.allowed:
            self._drain_request_body()
            self._send_json(verdict.status, {"error": verdict.reason})
            return

        handler = _POST_ROUTES.get(path)
        if handler is None:
            self._drain_request_body()
            self._send_text(404, "未找到")
            return
        blocked = _restart_blocked(self.app, path)
        if blocked is not None:
            self._drain_request_body()
            self._send_json(200, blocked)
            return
        body = self._read_json_body()
        self._send_json(200, _dispatch_guarded(path, lambda: handler(self.app, body)))

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        """`http.server` 自己的错误出口（未支持的方法 ⇒ 501 等），同走「响应前读净」。

        只有**这次请求的头已解析出来**时体长才已知——畸形请求行 / 超长请求行这些路径
        `parse_request` 已提前返回（`self.headers` 尚未赋值），在那里跳过。
        """
        if getattr(self, "headers", None) is not None:
            self._drain_request_body()
        super().send_error(code, message, explain)

    # ── 请求体 ──────────────────────────────────────────────────────────────
    def _content_length(self) -> int:
        """声明的请求体长度（缺失 / 非数字 / 负数一律 0）。"""
        try:
            return max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            return 0

    def _drain_request_body(self, consumed: int = 0) -> None:
        """把声明的请求体**读满并丢弃**（内存 O(一个分块)）。

        关闭一条**接收队列里还有未读数据**的连接会让对端拿到连接复位（本机 Windows 实测
        `ConnectionAbortedError: [WinError 10053]`）——本该 point at 401/403/404 的
        `ResultEnvelope` 会被盖成一句无信息量的网络错误（[00 §6 失败显式化]）。故**任何
        响应写出之前**都先读净。

        判据是「关闭时接收队列为空」，故按**声明的全长**读：实测只排一部分**没用**——
        4 MiB 体留 1 MiB 未读仍 20 次里中 1 次，读满则 40/40。``consumed`` 是调用方已经
        读走的前缀（:meth:`_read_json_body` 截断解析的那一段）；**只读声明的长度**，
        绝不多读一个字节。
        """
        left = self._content_length() - max(0, consumed)
        while left > 0:
            chunk = self.rfile.read(min(_DRAIN_CHUNK_BYTES, left))
            if not chunk:
                break
            left -= len(chunk)

    def _read_json_body(self) -> dict:
        """读并解析 JSON 请求体（畸形 / 超大一律回空体，由门面 fail-closed）。

        超过 `_MAX_BODY_BYTES` 的部分**不参与解析**，但仍要读走——截断即丢下未读数据，
        同样会在关闭连接时复位对端（见 :meth:`_drain_request_body`）。
        """
        length = self._content_length()
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, _MAX_BODY_BYTES))
        self._drain_request_body(consumed=len(raw))
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # ── 发送 ────────────────────────────────────────────────────────────────
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_text(self, status: int, text: str) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/html; charset=utf-8")

    def _send_file(self, path: Path) -> None:
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self._send_bytes(200, path.read_bytes(), content_type)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 用户数据不进任何中间缓存；同时**不发任何 Access-Control-Allow-***（见 security 模块）
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


class UiServer(ThreadingHTTPServer):
    """回环服务本体（只绑回环地址由调用方保证）。"""

    daemon_threads = True
    allow_reuse_address = True


@dataclass
class RunningUi:
    """一个已启动的服务实例（上下文管理器；退出即关）。"""

    app: UiApp
    server: UiServer
    token: str

    @property
    def host(self) -> str:
        return str(self.server.server_address[0])

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "RunningUi":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    dev: bool = False,
    token: str | None = None,
    chat: object = None,
    reflection: object = None,
    eco: object = None,
    memory: object = None,
    workspace: object = None,
    skills: object = None,
    mcp: object = None,
    graph: object = None,
    deliberation: object = None,
    delivery: object = None,
    settings: object = None,
    studio: object = None,
    commands: object = None,
    shell: bool = False,
    shell_control: Callable[[str], None] | None = None,
) -> RunningUi:
    """绑定 → 按**实际端口**装配 → 起服务线程 → 返回句柄。

    端口取 ``0`` 时由 OS 分配；守卫必须拿到真实端口才能校验 ``Host`` / ``Origin``，
    故顺序是「先绑、后装配」。前四个端口（``chat`` / ``reflection`` / ``eco`` / ``memory``）
    与 M6 的七个（[`T-UI-011.1`]）、``studio``（[`T-UI-015.3`]）、``commands``（[`T-UI-012.1`]）均透传给 :func:`build_ui`
    （组合根注入薄门面；缺省全 ``None`` ⇒ 各面 fail-closed）。
    """
    resolved_token = token or new_token()
    server = UiServer((host, port), UiRequestHandler)
    app = build_ui(
        host=str(server.server_address[0]),
        port=int(server.server_address[1]),
        dev=dev,
        token=resolved_token,
        chat=chat,
        reflection=reflection,
        eco=eco,
        memory=memory,
        workspace=workspace,
        skills=skills,
        mcp=mcp,
        graph=graph,
        deliberation=deliberation,
        delivery=delivery,
        settings=settings,
        studio=studio,
        commands=commands,
        shell=shell,
        shell_control=shell_control,
    )
    server.RequestHandlerClass = type("BoundUiRequestHandler", (UiRequestHandler,), {"app": app})
    threading.Thread(target=server.serve_forever, name="st-agent-ui", daemon=True).start()
    return RunningUi(app=app, server=server, token=resolved_token)
