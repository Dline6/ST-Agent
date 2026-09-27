"""权限批准账本的共用簿记（01 §10；Skill 与 MCP 两个账本共用一套实现）。

MCP Server 的批准账本（:mod:`st_agent.l1.mcp.permissions`）与 Skill 的批准账本
（:mod:`st_agent.l1.skills.permissions`）在**簿记语义**上完全同形——声明登记
（声明不变则保留既有决定）、逐项批准 / 拒绝、按状态读取、展示措辞、注销。
差别只有三处：**键的校验形态**、**落盘前缀**、**错误措辞里对键的称呼**。
本模块把那套同形逻辑收在一处，两个账本各自只提供这几个差异（「口径只有一份」）。

落盘（只经 ``Store`` 读写）：``config`` 分区 ``<prefix><key>.json``，载荷为
``PermissionApproval`` 的 JSON 数组，**按声明顺序**。

.. note::
   账本**不**校验权限声明的语法——那是
   :func:`st_agent.contracts.registry_types.validate_permissions` 的职责，
   由各自的注册入口在 :meth:`declare` 之前完成。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.permissions import (
    DECISION_LABELS,
    PermissionApproval,
    describe_permission,
)

__all__ = ["PermissionBook"]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class PermissionBook:
    """权限批准账本的共用实现（01 §10 逐项批准）。

    子类只给这几样：落盘前缀、键校验、键的称呼、以及两类错误的具体类型。

    :param store: ``Store`` 句柄
    :param prefix: ``config`` 分区内的目录前缀（形如 ``foo/``）
    :param check_key: 键形态校验（返回原值；非法即抛 ``validation_cls`` 形态的错误）
    :param key_label: 错误措辞里对键的称呼（如 ``MCP Server`` / ``Skill``）
    :param error_cls: 权限**操作**非法的类型（如未声明的权限不得批准 / 拒绝）
    :param validation_cls: 键形态非法 / **记录损坏**的类型
    """

    def __init__(
        self,
        store,
        *,
        prefix: str,
        check_key: Callable[[str], str],
        key_label: str,
        error_cls: Callable[[str], Exception],
        validation_cls: Callable[[str], Exception],
    ) -> None:
        self._store = store
        self._prefix = prefix
        self._check_key = check_key
        self._key_label = key_label
        self._err = error_cls
        self._verr = validation_cls

    # ───────────────────────── 声明 ─────────────────────────

    def declare(
        self, key: str, permissions: tuple[str, ...] | list[str]
    ) -> tuple[PermissionApproval, ...]:
        """登记该能力声明的全部权限。

        初态 ``pending``；**已声明且仍在清单内的权限保留既有决定**（声明不变则
        批准不丢，避免更新记录时把用户已批准的权限打回待批）。
        """
        existing = {a.permission: a for a in self.approvals(key)}
        approvals = tuple(
            existing.get(d, PermissionApproval(permission=d, decision="pending"))
            for d in permissions
        )
        self._save(key, approvals)
        return approvals

    # ───────────────────────── 逐项批准 ─────────────────────────

    def approve(self, key: str, permission: str) -> PermissionApproval:
        """批准一条已声明的权限（未声明 → ``error_cls``）。"""
        return self._decide(key, permission, "approved")

    def reject(self, key: str, permission: str) -> PermissionApproval:
        """拒绝一条已声明的权限（拒绝后不进入已批准集合）。"""
        return self._decide(key, permission, "rejected")

    # ───────────────────────── 读取 ─────────────────────────

    def approvals(self, key: str) -> tuple[PermissionApproval, ...]:
        """全部权限的批准状态（未登记 → 空元组；按声明顺序）。"""
        try:
            raw = self._store.get("config", self._path(key))
        except KeyError:
            return ()
        return self._parse(raw)

    def approved_permissions(self, key: str) -> tuple[str, ...]:
        """已批准的权限声明（**只有** ``approved`` 在其中）。"""
        return tuple(
            a.permission for a in self.approvals(key) if a.decision == "approved"
        )

    def pending_permissions(self, key: str) -> tuple[str, ...]:
        """待批准的权限声明（03 §5.3 的 ``permission_pending`` 判据）。"""
        return tuple(
            a.permission for a in self.approvals(key) if a.decision == "pending"
        )

    def describe(self, key: str) -> tuple[str, ...]:
        """权限申请展示（「这个能力想做什么」，逐条含当前批准状态）。"""
        return tuple(
            f"{describe_permission(a.permission)}｜{DECISION_LABELS[a.decision]}"
            for a in self.approvals(key)
        )

    # ───────────────────────── 移除 ─────────────────────────

    def forget(self, key: str) -> None:
        """注销该能力的批准状态（幂等；能力移除时调用——批准随能力走）。"""
        path = self._path(key)
        if path in self._store.list_files("config"):
            self._store.delete("config", path)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _decide(self, key: str, permission: str, decision: str) -> PermissionApproval:
        approvals = self.approvals(key)
        for a in approvals:
            if a.permission == permission:
                decided = PermissionApproval(
                    permission=permission, decision=decision,  # type: ignore[arg-type]
                    decided_at=_now(),
                )
                self._save(key, tuple(
                    decided if x.permission == permission else x for x in approvals
                ))
                return decided
        raise self._err(
            f"{self._key_label} {key!r} 未声明权限 {permission!r}，不得批准/拒绝"
        )

    def _save(self, key: str, approvals: tuple[PermissionApproval, ...]) -> None:
        payload = [a.model_dump(mode="json") for a in approvals]
        self._store.put(
            "config", self._path(key),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _path(self, key: str) -> str:
        return f"{self._prefix}{self._check_key(key)}.json"

    def _parse(self, raw: bytes) -> tuple[PermissionApproval, ...]:
        try:
            payload: Any = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise self._verr(f"权限批准记录损坏无法解析：{exc}") from exc
        if not isinstance(payload, list):
            raise self._verr("权限批准记录必须是数组")
        try:
            return tuple(PermissionApproval(**item) for item in payload)
        except (TypeError, ValidationError) as exc:
            raise self._verr(f"权限批准记录条目非法：{exc}") from exc
