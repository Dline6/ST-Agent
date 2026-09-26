"""MCP 权限的「声明 → 逐项批准」账本（T-L1-002.1；03 §5.4；01 §10）。

01 §10 的统一权限模型：能力（Skill / **MCP Server** / 导入物）在安装/挂载时
声明权限，用户逐项批准；「权限申请展示必须说明这个能力想做什么」。

本模块只管**批准状态**与**展示措辞**；运行时越界拦截归 L1 执行沙箱
（`T-L1-001.3`/`T-L1-001.5`），二者共用同一份 01 §10 语法与作用域口径
（[D-004](../决策日志.md)）。

布局（只经 ``Store`` 读写）：``config`` 分区 ``mcp-permission/<server_id>.json``。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.registry_types import parse_permission
from st_agent.l1.mcp.errors import McpPermissionError, McpValidationError
from st_agent.l1.mcp.ids import check_server_id
from st_agent.l1.mcp.models import PermissionApproval

__all__ = [
    "PERMISSION_PREFIX",
    "PERMISSION_KINDS",
    "McpPermissionBook",
    "describe_permission",
]

PERMISSION_PREFIX = "mcp-permission/"
"""``config`` 分区内批准状态记录的目录前缀。"""

PERMISSION_KINDS: dict[str, str] = {
    "local_read": "读取声明范围内的本地文件",
    "net_access": "访问声明的远程主机",
    "exec_command": "在本机执行命令（最高风险）",
}
"""01 §10 三项权限的中性说明（「这个 Server 想做什么」的措辞来源）。"""

_DECISION_LABELS = {"pending": "待批准", "approved": "已批准", "rejected": "已拒绝"}
"""批准状态的中文展示（权限申请展示用）。"""


def describe_permission(declaration: str) -> str:
    """把一条权限声明转成面向用户的中性说明（01 §10 展示要求）。"""
    try:
        action, scope = parse_permission(declaration)
    except Exception as exc:
        raise McpValidationError(f"权限声明非法：{declaration!r}（{exc}）") from exc
    what = PERMISSION_KINDS.get(action, action)
    if action == "exec_command":
        return f"{declaration} —— {what}"
    return f"{declaration} —— {what}（范围 {scope}）"


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class McpPermissionBook:
    """某台 MCP Server 的权限批准账本（落 ``config`` 分区）。

    :param store: ``Store`` 句柄
    """

    def __init__(self, store) -> None:
        self._store = store

    # ───────────────────────── 声明 ─────────────────────────

    def declare(self, server_id: str, permissions: tuple[str, ...] | list[str]) -> tuple[
        PermissionApproval, ...
    ]:
        """登记该 Server 声明的全部权限。

        初态 ``pending``；**已声明且仍在清单内的权限保留既有决定**（声明不变则
        批准不丢，避免更新记录时把用户已批准的权限打回待批）。
        """
        existing = {a.permission: a for a in self.approvals(server_id)}
        approvals = tuple(
            existing.get(d, PermissionApproval(permission=d, decision="pending"))
            for d in permissions
        )
        self._save(server_id, approvals)
        return approvals

    # ───────────────────────── 逐项批准 ─────────────────────────

    def approve(self, server_id: str, permission: str) -> PermissionApproval:
        """批准一条已声明的权限（未声明 → ``McpPermissionError``）。"""
        return self._decide(server_id, permission, "approved")

    def reject(self, server_id: str, permission: str) -> PermissionApproval:
        """拒绝一条已声明的权限（拒绝后不进入已批准集合）。"""
        return self._decide(server_id, permission, "rejected")

    # ───────────────────────── 读取 ─────────────────────────

    def approvals(self, server_id: str) -> tuple[PermissionApproval, ...]:
        """全部权限的批准状态（未登记 → 空元组；按声明顺序）。"""
        try:
            raw = self._store.get("config", self._path(server_id))
        except KeyError:
            return ()
        return self._parse(raw)

    def approved_permissions(self, server_id: str) -> tuple[str, ...]:
        """已批准的权限声明（**只有** ``approved`` 在其中）。"""
        return tuple(a.permission for a in self.approvals(server_id) if a.decision == "approved")

    def pending_permissions(self, server_id: str) -> tuple[str, ...]:
        """待批准的权限声明（03 §5.3 的 ``permission_pending`` 判据）。"""
        return tuple(a.permission for a in self.approvals(server_id) if a.decision == "pending")

    def describe(self, server_id: str) -> tuple[str, ...]:
        """权限申请展示（「这个 Server 想做什么」，逐条含当前批准状态）。"""
        return tuple(
            f"{describe_permission(a.permission)}｜{_DECISION_LABELS[a.decision]}"
            for a in self.approvals(server_id)
        )

    # ───────────────────────── 移除 ─────────────────────────

    def forget(self, server_id: str) -> None:
        """注销该 Server 的批准状态（幂等；Server 移除时调用——批准随 Server 走）。"""
        path = self._path(server_id)
        if path in self._store.list_files("config"):
            self._store.delete("config", path)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _decide(
        self, server_id: str, permission: str, decision: str
    ) -> PermissionApproval:
        approvals = self.approvals(server_id)
        for a in approvals:
            if a.permission == permission:
                decided = PermissionApproval(
                    permission=permission, decision=decision, decided_at=_now()  # type: ignore[arg-type]
                )
                self._save(server_id, tuple(
                    decided if x.permission == permission else x for x in approvals
                ))
                return decided
        raise McpPermissionError(
            f"MCP Server {server_id!r} 未声明权限 {permission!r}，不得批准/拒绝"
        )

    def _save(self, server_id: str, approvals: tuple[PermissionApproval, ...]) -> None:
        payload = [a.model_dump(mode="json") for a in approvals]
        self._store.put(
            "config", self._path(server_id),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    @staticmethod
    def _path(server_id: str) -> str:
        return f"{PERMISSION_PREFIX}{check_server_id(server_id)}.json"

    def _parse(self, raw: bytes) -> tuple[PermissionApproval, ...]:
        try:
            payload: Any = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise McpValidationError(f"权限批准记录损坏无法解析：{exc}") from exc
        if not isinstance(payload, list):
            raise McpValidationError("权限批准记录必须是数组")
        try:
            return tuple(PermissionApproval(**item) for item in payload)
        except (TypeError, ValidationError) as exc:
            raise McpValidationError(f"权限批准记录条目非法：{exc}") from exc
