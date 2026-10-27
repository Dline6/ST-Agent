"""能力区 · MCP Hub（[T-UI-015.2]）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any
from st_agent.contracts.permissions import DECISION_LABELS, describe_permission
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.mcp.errors import (
    McpCancelledError,
    McpConnectionError,
    McpError,
    McpServerNotFoundError,
)
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _LOG,
    _contract_keys,
    _now,
    _optional_text,
    _required,
    _skill_descriptor_or_none,
)


def _mcp_error_envelope(exc: Exception) -> ResultEnvelope:
    """MCP 层异常 → 六态（**必须显式翻译**，[00 §6] 失败显式化）。

    - **未注册**（`McpServerNotFoundError`）⇒ `unavailable` ＋ 点名——与「请求不合契约」
      互不冒充；
    - **连不上 / 调用被中止**（`McpConnectionError` / `McpCancelledError`）⇒ `unavailable`
      ＋ 原因——目标不可达不是「工具坏了」，也不是用户的输入问题；
    - 其余（声明非法 / 重名 / 权限未声明 / 状态或映射非法）⇒ `validation_failed`。
    """
    if isinstance(exc, McpServerNotFoundError):
        return ResultEnvelope.unavailable(
            f"{exc}——该标识未注册（不是请求不合契约）", last_updated_at=_now()
        )
    if isinstance(exc, (McpConnectionError, McpCancelledError)):
        return ResultEnvelope.unavailable(f"{exc}", last_updated_at=_now())
    return ResultEnvelope.validation_failed(str(exc) or "MCP 请求不合契约")


def _mcp_server_row(face: Any, record: Any) -> dict[str, Any]:
    """一行 Server 摘要（状态由 `state_of` **纯读**推导，不产生副作用）。"""
    machine = face.machine
    state = machine.state_of(record.server_id) if machine is not None else ""
    return {
        "display_name": record.display_name,
        "server_id": record.server_id,
        "transport": _MCP_TRANSPORT_LABELS.get(record.transport, record.transport),
        "enabled": "启用" if record.enabled else "停用",
        "state": _MCP_STATE_LABELS.get(state, "—"),
        "permissions": len(record.permissions),
    }


def _mcp_tool_row(face: Any, mapping: Any) -> dict[str, Any]:
    """一行 tool 映射：状态 + 变化说明 + **派生描述体**的参数面（schema 已随派生落到 Skill）。"""
    descriptor = _skill_descriptor_or_none(face.skills, mapping.skill_id)
    return {
        "tool": mapping.tool,
        "skill_id": mapping.skill_id,
        "status": _MCP_MAPPING_LABELS.get(mapping.status, mapping.status),
        "version": mapping.mapping_version,
        "inputs": (
            _contract_keys(descriptor.input_schema) if descriptor is not None
            else "（描述体不在）"
        ),
        "change_note": mapping.change_note or "—",
    }


def _mcp_approval_slots(record: Any, approvals: tuple[Any, ...]) -> dict[str, Any]:
    """`permission_approval_card` 的槽（[01 §10]：逐条「这个 Server 想做什么」＋ 当前态）。

    措辞取**契约级**的唯一真相源 `describe_permission` / `DECISION_LABELS`，本层不另写词表。
    """
    return {
        "key": record.server_id,
        "source": f"MCP Server · {record.display_name}",
        "items": [
            {
                "permission": approval.permission,
                "state": approval.decision,
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "panel_field": {"widget": "逐项批准 / 拒绝"},
            }
            for approval in approvals
        ],
        "labels": {
            approval.permission: {
                "description": describe_permission(approval.permission),
                "chat_text": DECISION_LABELS.get(approval.decision, approval.decision),
            }
            for approval in approvals
        },
    }


def _mcp_approval_entries(record: Any, approvals: tuple[Any, ...]) -> list[dict[str, Any]]:
    """逐项批准 / 拒绝的处置条目（`setting_panel`：动作即取值，故只给两个动作按钮）。"""
    return [
        {
            "identifier": approval.permission,
            "description": describe_permission(approval.permission),
            "current": DECISION_LABELS.get(approval.decision, approval.decision),
            "options": [],
            "actions": ["approve", "reject"],
            "params": {"server_id": record.server_id, "permission": approval.permission},
        }
        for approval in approvals
    ]


MCP_APPROVAL_SURFACE = "mcp-permissions"
"""MCP 权限逐项批准面的面键（`setting_panel` 的 `surface` 槽；[`T-UI-015.2`]）。

与 `skill-params` / `LENS_SURFACE` 同为**枚举键**：路由在渲染件的固定表里解析，描述
不接受 URL / 方法（[01 §12]）。
"""


_MCP_APPROVAL_LABELS: dict[str, str] = {"approve": "批准", "reject": "拒绝"}
"""权限处置面的动作标签（生成文案槽 `labels`，过 [01 §6]）。"""


_MCP_ACTION_LABELS: dict[str, str] = {
    "enable": "启用",
    "disable": "停用",
    "remove": "移除",
    "test": "连接测试",
}
"""Hub 面板的 Server 动作标签（生成文案槽 `labels`）。`remove` 在界面上只对**本地 stdio**
之外一概可用——回收语义对两种传输一致（[03 §5.2]）。"""


_MCP_TRANSPORT_LABELS: dict[str, str] = {
    "stdio": "本地 stdio（子进程）",
    "remote": "远程 HTTP/SSE",
}
"""MCP 传输的中文名（系统文案；[03 §5.1] 两种传输）。"""


_MCP_STATE_LABELS: dict[str, str] = {
    "connected": "已连接",
    "disconnected": "未连接",
    "reconnecting": "重连中",
    "permission_pending": "待批准权限",
    "disabled": "已停用",
}
"""MCP Server 生命周期状态的中文名（系统文案；[03 §5.3] 五个状态）。"""


_MCP_MAPPING_LABELS: dict[str, str] = {
    "active": "生效",
    "pending-remap": "待重新映射",
    "vanished": "已失效",
}
"""tool → Skill 映射状态的中文名（系统文案；[03 §5.2] 三个状态）。`vanished` 追加一句
「tool 从 Server 消失」在行内说明——**不删记录**、可自愈，故措辞不带「已删除」。"""


_MCP_NOTICE_LIMIT = 20
"""Server 详情页单次列出的状态留痕条数上限（超出如实标注）。"""


class McpSurface(SurfaceBase):
    """能力区 · MCP Hub（[T-UI-015.2]）。"""

    def api_mcp_servers(self) -> dict[str, Any]:
        """Server 清单 → `table`（逐条并列，含各自的生命周期状态）。"""
        return self._mcp_call(
            "mcp-servers", self._mcp_servers_envelope
        )

    def api_mcp_server(self, *, server_id: str) -> dict[str, Any]:
        """Server 详情 → `report_card`（记录 / 状态 / 留痕 / 降级候选 / 数据外发风险五段）。"""
        if not server_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Server 标识（id）"))
        return self._mcp_call(
            "mcp-server",
            lambda face: self._mcp_server_envelope(face, server_id),
        )

    def api_mcp_tools(self, *, server_id: str) -> dict[str, Any]:
        """tool schema 浏览器 → `table`（逐 tool 的映射状态与**派生描述体**的参数面）。"""
        if not server_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Server 标识（id）"))
        return self._mcp_call(
            "mcp-tools",
            lambda face: self._mcp_tools_envelope(face, server_id),
        )

    def api_mcp_permissions(self, *, server_id: str) -> dict[str, Any]:
        """权限面 → 两段：`declaration`（`permission_approval_card`，只读声明）＋
        `decisions`（`setting_panel`，逐项批准 / 拒绝）。

        两段同出于一条端点：声明面讲「这个 Server 想做什么」（[01 §10]），处置面才是动作。
        `permission_approval_card` 渲染件**只渲染不落账**（其头注明写），故动作面另走
        `setting_panel`——同 `T-UI-004.4` 对导入审批的处理（声明 `report_card` ＋ 处置
        `setting_panel`）。
        """
        if not server_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 Server 标识（id）"))
        return self._mcp_parts(
            "mcp-permissions", ("declaration", "decisions"),
            lambda face: self._mcp_permissions_parts(face, server_id),
        )

    def api_mcp_server_create(self, body: dict[str, Any]) -> dict[str, Any]:
        """添加一台 Server（GWT-1）：保存即**自动连接测试**，成功才落盘（[03 §5.1]）。"""
        return self._mcp_call(
            "mcp-server-create",
            lambda face: self._mcp_server_create_envelope(face, body),
        )

    def api_mcp_server_update(self, body: dict[str, Any]) -> dict[str, Any]:
        """一台 Server 的动作（启用 / 停用 / 移除 / 连接测试）——四者皆显式动作。"""
        return self._mcp_call(
            "mcp-server-update",
            lambda face: self._mcp_server_update_envelope(face, body),
        )

    def api_mcp_permission_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """逐项批准 / 拒绝一条权限（[01 §10]：逐项、不合并）。"""
        return self._mcp_call(
            "mcp-permission-decide",
            lambda face: self._mcp_permission_decide_envelope(face, body),
        )

    def _mcp_call(self, where: str, fn: Callable[[Any], Any]) -> dict[str, Any]:
        """调一次 MCP 面操作：**先按 MCP 异常族翻成六态**，再交 `_call_face` 出信封。

        MCP 错误全部继承 ``Exception`` 而非 ``ValueError`` / ``KeyError``（见
        [`l1/mcp/errors.py`] 的模块取向），故 `_call_face` 的输入类分流**覆盖不到**——不翻译
        则「该 Server 未注册」「权限未声明」会被报成 `failed`，读起来是「工具坏了」（同
        [`T-UI-018.2`] 对 L0 备份族异常的处理）。
        """

        def guarded(face: Any) -> Any:
            try:
                return fn(face)
            except McpError as exc:
                return _mcp_error_envelope(exc)

        return self._call_face(where, self.mcp, "L1 能力（MCP）面", guarded)

    def _mcp_parts(
        self, where: str, keys: tuple[str, ...], fn: Callable[[Any], dict[str, Any]]
    ) -> dict[str, Any]:
        """多段 MCP 端点：**任一失败，各段都是同一支错误信封**（同 `_settings_parts` 口径）。

        分段端点**不能**经 `_call_face`——后者会把非信封的返回值包成 `ok`，于是
        `{declaration, decisions}` 会变成 `data` 里的一层，页面逐段找键就找不到了。
        """
        def blank(envelope: ResultEnvelope) -> dict[str, Any]:
            payload = envelope_payload(envelope)
            return {key: payload for key in keys}

        if self.restart_gate.reason:
            return blank(ResultEnvelope.unavailable(
                self.restart_gate.reason, last_updated_at=_now()
            ))
        face = self.mcp
        if face is None:
            return blank(ResultEnvelope.unavailable(
                "未接入 L1 能力（MCP）面，该端点不可用（装配归生产入口）",
                last_updated_at=_now(),
            ))
        try:
            return fn(face)
        except McpError as exc:
            return blank(_mcp_error_envelope(exc))
        except (ValueError, KeyError) as exc:
            _LOG.info("表现层端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
            return blank(ResultEnvelope.validation_failed(str(exc) or "请求不合契约"))
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/{where}-{uuid.uuid4().hex[:12]}"
            _LOG.exception("表现层端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
            return blank(ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref))

    def _mcp_server_record(self, face: Any, server_id: str) -> Any:
        """读一台 Server 的注册记录（不存在即抛，由 `_call_face` 归 `validation_failed`）。"""
        return face.servers.get_server(server_id)

    def _mcp_servers_envelope(self, face: Any) -> ResultEnvelope:
        servers = tuple(face.servers.list_servers())
        if not servers:
            return ResultEnvelope.empty(
                "尚未挂载任何 MCP Server——在上方「添加 Server」填入命令或 URL 即可挂载"
                "（03 §5.1）"
            )
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("display_name", "名称"),
                    Column("server_id", "标识"),
                    Column("transport", "传输"),
                    Column("enabled", "开关"),
                    Column("state", "状态"),
                    Column("permissions", "权限数", "number", digits=0),
                ),
                [_mcp_server_row(face, record) for record in servers],
            ),
            text_kinds=table_text_kinds(),
            title="MCP Server 清单",
        ))

    def _mcp_server_envelope(self, face: Any, server_id: str) -> ResultEnvelope:
        record = self._mcp_server_record(face, server_id)
        machine = face.machine
        sections: list[dict[str, Any]] = [
            {"title": "基本信息", "lines": [
                f"名称：{record.display_name}",
                f"标识：{record.server_id}",
                f"传输：{_MCP_TRANSPORT_LABELS.get(record.transport, record.transport)}",
                f"开关：{'启用' if record.enabled else '停用'}",
                f"声明权限：{len(record.permissions)} 条",
                f"挂载时刻：{record.created_at.isoformat()}",
            ]},
        ]
        if machine is not None:
            sections.append({"title": "生命周期状态", "lines": [
                f"当前：{_MCP_STATE_LABELS.get(machine.state_of(record.server_id), '—')}",
            ]})
            transitions = tuple(machine.transitions(record.server_id))[-_MCP_NOTICE_LIMIT:]
            sections.append({"title": "状态转移留痕", "lines": [
                f"{item.occurred_at.isoformat()} "
                f"{_MCP_STATE_LABELS.get(item.from_state, item.from_state)} → "
                f"{_MCP_STATE_LABELS.get(item.to_state, item.to_state)}"
                f"（{item.reason}）"
                for item in transitions
            ] or ["（无转移留痕）"]})
            notices = tuple(machine.notices(record.server_id))[-_MCP_NOTICE_LIMIT:]
            sections.append({"title": "提示留痕", "lines": [
                f"{item.occurred_at.isoformat()} [{item.kind}] {item.message}"
                for item in notices
            ] or ["（无提示）"]})
            # 降级候选**并列陈列**：不带排序位、不带推荐语（[03 §5.3] GWT-3）。
            candidates = tuple(machine.degrade_candidates(record.server_id))
            sections.append({"title": "降级候选（并列，不排序）", "lines": [
                f"{candidate.name}（{candidate.skill_id}）· "
                f"{'可用' if candidate.available else '不可用'}：{candidate.detail}"
                for candidate in candidates
            ] or ["（无候选）"]})
        if record.data_egress_risk:
            sections.append({"title": "数据外发风险", "lines": [record.data_egress_risk]})
        return ResultEnvelope.ok(self._gated_description(
            "report_card",
            slots={"sections": sections},
            text_kinds={"sections": "data"},
            title=f"{record.display_name} · 详情",
        ))

    def _mcp_tools_envelope(self, face: Any, server_id: str) -> ResultEnvelope:
        record = self._mcp_server_record(face, server_id)
        if face.mapper is None:
            return ResultEnvelope.unavailable(
                "未接入 tool 映射面，tool 浏览器不可读（装配归生产入口）", last_updated_at=_now()
            )
        mappings = tuple(face.mapper.mappings(record.server_id))
        if not mappings:
            return ResultEnvelope.empty(
                f"「{record.display_name}」还没有 tool 映射——权限全部批准后同步 tool 列表"
                "才会产生（03 §5.2）"
            )
        return ResultEnvelope.ok(self._gated_description(
            "table",
            slots=table_slots(
                (
                    Column("tool", "tool"),
                    Column("skill_id", "派生 Skill"),
                    Column("status", "映射状态"),
                    Column("version", "映射版本"),
                    Column("inputs", "参数面（派生的输入契约）"),
                    Column("change_note", "变化说明"),
                ),
                [_mcp_tool_row(face, mapping) for mapping in mappings],
            ),
            text_kinds=table_text_kinds(),
            title=f"{record.display_name} · tool 与映射",
        ))

    def _mcp_permissions_parts(self, face: Any, server_id: str) -> dict[str, Any]:
        """权限面的两段（**不做异常处理**——那归 `_mcp_parts` 的统一分流）。"""
        record = self._mcp_server_record(face, server_id)
        approvals = tuple(face.permissions.approvals(record.server_id))
        declaration = envelope_payload(ResultEnvelope.ok(self._gated_description(
            "permission_approval_card",
            slots=_mcp_approval_slots(record, approvals),
            text_kinds={"key": "data", "source": "data", "items": "data",
                        "labels": "generated"},
            title=f"{record.display_name} · 权限申请",
        )))
        if not approvals:
            # 没声明权限 ⇒ **没有可批准项**，不是「面读不到」（[01 §5] 六态不混用）。
            return {
                "declaration": declaration,
                "decisions": envelope_payload(ResultEnvelope.empty(
                    f"「{record.display_name}」未声明任何权限，无可批准项（01 §10 逐项批准）"
                )),
            }
        return {
            "declaration": declaration,
            "decisions": envelope_payload(ResultEnvelope.ok(self._gated_description(
                "setting_panel",
                slots={
                    "entries": _mcp_approval_entries(record, approvals),
                    "labels": dict(_MCP_APPROVAL_LABELS),
                    "surface": MCP_APPROVAL_SURFACE,
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title=f"{record.display_name} · 逐项批准",
            ))),
        }

    def _mcp_server_create_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        result = face.servers.add_server(
            str(_required(body, "server_id")),
            display_name=str(_required(body, "display_name")),
            transport=str(body.get("transport") or "stdio"),
            command=_optional_text(body.get("command")),
            args=tuple(str(item) for item in (body.get("args") or ())),
            env_refs=tuple(str(item) for item in (body.get("env_refs") or ())),
            url=_optional_text(body.get("url")),
            remote_acknowledged=bool(body.get("remote_acknowledged")),
            permissions=tuple(str(item) for item in (body.get("permissions") or ())),
        )
        if not result.ok:
            # 连接测试失败**不落盘**——如实回不可用 ＋ 原因（不是「工具坏了」）。
            return ResultEnvelope.unavailable(
                f"连接测试未通过，Server 未挂载：{result.reason}", last_updated_at=_now()
            )
        return ResultEnvelope.ok({
            "server_id": result.server_id,
            "connected": True,
            "protocol_version": result.protocol_version,
            "permissions": list(result.permissions),
            "tool_count": len(result.tools),
        })

    def _mcp_server_update_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        server_id = str(_required(body, "server_id"))
        action = str(_required(body, "action"))
        record = self._mcp_server_record(face, server_id)
        if action == "enable":
            if face.machine is None:
                return ResultEnvelope.unavailable(
                    "未接入 Hub 状态机，启停不可执行（装配归生产入口）", last_updated_at=_now()
                )
            face.machine.enable(record.server_id)
            return ResultEnvelope.ok({"server_id": record.server_id, "enabled": True})
        if action == "disable":
            if face.machine is None:
                return ResultEnvelope.unavailable(
                    "未接入 Hub 状态机，启停不可执行（装配归生产入口）", last_updated_at=_now()
                )
            face.machine.disable(record.server_id)
            return ResultEnvelope.ok({"server_id": record.server_id, "enabled": False})
        if action == "remove":
            if face.remove_server is None:
                return ResultEnvelope.unavailable(
                    "未接入移除序列，无法回收该 Server（装配归生产入口）", last_updated_at=_now()
                )
            # 走整条序列（disable → remove_server → 回收回调），不自行拼两步。
            face.remove_server(record.server_id)
            return ResultEnvelope.ok({"server_id": record.server_id, "removed": True})
        if action == "test":
            result = face.servers.test_connection(record.server_id)
            if not result.ok:
                return ResultEnvelope.unavailable(
                    f"连接测试未通过：{result.reason}", last_updated_at=_now()
                )
            return ResultEnvelope.ok({
                "server_id": result.server_id,
                "connected": True,
                "protocol_version": result.protocol_version,
                "tool_count": len(result.tools),
            })
        return ResultEnvelope.validation_failed(
            f"未知动作 {action!r}（可选：enable / disable / remove / test）"
        )

    def _mcp_permission_decide_envelope(self, face: Any, body: dict[str, Any]) -> ResultEnvelope:
        server_id = str(_required(body, "server_id"))
        permission = str(_required(body, "permission"))
        decision = str(_required(body, "decision"))
        if decision not in ("approve", "reject"):
            return ResultEnvelope.validation_failed(
                f"未知处置 {decision!r}（可选：approve / reject）"
            )
        record = self._mcp_server_record(face, server_id)
        approval = (
            face.permissions.approve(record.server_id, permission)
            if decision == "approve"
            else face.permissions.reject(record.server_id, permission)
        )
        return ResultEnvelope.ok({
            "server_id": record.server_id,
            "permission": approval.permission,
            "decision": approval.decision,
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        })
