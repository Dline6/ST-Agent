"""密钥与凭据子系统门面 ``CredentialVault``（02 §3）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 凭据本体 → ``secrets`` 分区 ``cred/<credential_id>.json``
  （整文件 AES-256-GCM 加密落盘，GWT-1；``secrets`` 独立派生密钥由 T-L0-001 保证）
- 使用记录 → ``execution_log`` 分区 ``cred-usage/<credential_id>/<ts>-<rand>.json``
  （凭据与审计分离：``secrets`` 只存密钥材料；文件名时戳无冒号，Windows 可建）

读取默认只给掩码（GWT-2）；完整值仅 ``use()`` 一次性返回并同步记一条
使用记录（GWT-3）；吊销/重设后旧值不可恢复，使用记录保留（GWT-4）；
``CredentialRecord`` 的 repr/str 强制掩码，导出物只含视图与使用记录（GWT-5）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from pydantic import ValidationError

from st_agent.l0.secrets.errors import (
    CredentialExistsError,
    CredentialNotFoundError,
    CredentialValidationError,
)
from st_agent.l0.secrets.models import (
    CredentialRecord,
    CredentialUsageRecord,
    CredentialView,
    check_credential_id,
)

__all__ = ["CRED_PREFIX", "USAGE_PREFIX", "CredentialVault"]

CRED_PREFIX = "cred/"
"""``secrets`` 分区内凭据文件的目录前缀。"""

USAGE_PREFIX = "cred-usage/"
"""``execution_log`` 分区内使用记录的目录前缀。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


def _checked(record_cls, **fields):
    """构造 pydantic 模型，把 ``ValidationError`` 统一包成 ``CredentialValidationError``。"""
    try:
        return record_cls(**fields)
    except ValidationError as exc:
        raise CredentialValidationError(f"凭据数据非法：{exc}") from exc


class CredentialVault:
    """凭据子系统门面（02 §3；凭据明文只在内存短暂持有，不落日志）。"""

    def __init__(self, store) -> None:
        self._store = store

    # ───────────────────────── 写入：新增 / 重设 / 吊销 ─────────────────────────

    def add(
        self,
        credential_id: str,
        kind: str,
        value: str,
        label: str = "",
    ) -> CredentialView:
        """新增凭据（已存在 → ``CredentialExistsError``，更新走 ``rotate``）。"""
        now = _now()
        record = _checked(
            CredentialRecord,
            credential_id=credential_id,
            kind=kind,
            label=label,
            value=value,
            created_at=now,
            updated_at=now,
        )
        path = self._cred_path(record.credential_id)
        if path in self._store.list_files("secrets"):
            raise CredentialExistsError(
                f"凭据 {record.credential_id!r} 已存在；更新请用 rotate，不得静默覆盖"
            )
        self._store.put("secrets", path, record.model_dump_json().encode("utf-8"))
        return record.view()

    def rotate(self, credential_id: str, new_value: str) -> CredentialView:
        """重设凭据值（版本 +1；旧文件整体覆盖，旧值不可恢复，GWT-4）。"""
        check_credential_id(credential_id)
        old = self._load(credential_id)
        if not new_value or len(new_value) > 8192:
            raise CredentialValidationError("新凭取值非法（须 1..8192 字符）")
        record = _checked(
            CredentialRecord,
            credential_id=old.credential_id,
            kind=old.kind,
            label=old.label,
            value=new_value,
            created_at=old.created_at,
            updated_at=_now(),
            version=old.version + 1,
        )
        self._store.put("secrets", self._cred_path(credential_id), record.model_dump_json().encode("utf-8"))
        return record.view()

    def revoke(self, credential_id: str) -> CredentialView:
        """吊销凭据（删本体文件；使用记录保留可查，GWT-4）。返回吊销回执（掩码视图）。"""
        check_credential_id(credential_id)
        record = self._load(credential_id)
        self._store.delete("secrets", self._cred_path(credential_id))
        return record.view()

    # ───────────────────────── 读取：默认掩码 ─────────────────────────

    def get_view(self, credential_id: str) -> CredentialView:
        """读取凭据对外视图（仅掩码；界面/列表默认走此接口，GWT-2）。"""
        return self._load(credential_id).view()

    def list_credentials(self) -> tuple[CredentialView, ...]:
        """列出全部凭据（仅掩码视图，按标识排序）。"""
        views = [
            self._load(name[len(CRED_PREFIX):-len(".json")])
            .view()
            for name in self._store.list_files("secrets")
            if name.startswith(CRED_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(views, key=lambda v: v.credential_id))

    def use(
        self,
        credential_id: str,
        initiator: str,
        purpose: str,
        data_bytes: int = 0,
    ) -> str:
        """取用凭据明文（唯一返回明文的出口），同步记一条 ``ok`` 使用记录（GWT-3）。

        不存在/已吊销 → ``CredentialNotFoundError``（且不记记录：无归属主体）。
        """
        record = self._load(credential_id)
        self.record_usage(
            credential_id, initiator=initiator, purpose=purpose,
            data_bytes=data_bytes, status="ok",
        )
        return record.value

    # ───────────────────────── 使用记录 ─────────────────────────

    def record_usage(
        self,
        credential_id: str,
        initiator: str,
        purpose: str,
        data_bytes: int = 0,
        status: str = "ok",
    ) -> CredentialUsageRecord:
        """补记一条使用记录（供调用方回填 ``failed`` 等下游结果；记录本身不含明文）。"""
        entry = _checked(
            CredentialUsageRecord,
            credential_id=credential_id,
            timestamp=_now(),
            initiator=initiator,
            purpose=purpose,
            data_bytes=data_bytes,
            status=status,
        )
        stamp = entry.timestamp
        fname = f"{int(stamp.timestamp() * 1_000_000):020d}-{os.urandom(4).hex()}.json"
        self._store.put(
            "execution_log",
            f"{USAGE_PREFIX}{entry.credential_id}/{fname}",
            entry.model_dump_json().encode("utf-8"),
        )
        return entry

    def query_usage(
        self,
        credential_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[CredentialUsageRecord, ...]:
        """按凭据查使用记录（时戳升序；``since``/``until`` 须带时区，GWT-3 自查口径）。"""
        check_credential_id(credential_id)
        for bound in (since, until):
            if bound is not None and bound.tzinfo is None:
                raise CredentialValidationError("查询边界时间必须带时区语义")
        prefix = f"{USAGE_PREFIX}{credential_id}/"
        entries = [
            _checked(
                CredentialUsageRecord,
                **json.loads(
                    self._store.get("execution_log", name).decode("utf-8")
                ),
            )
            for name in self._store.list_files("execution_log")
            if name.startswith(prefix) and name.endswith(".json")
        ]
        entries.sort(key=lambda e: e.timestamp)
        return tuple(
            e for e in entries
            if (since is None or e.timestamp >= since)
            and (until is None or e.timestamp <= until)
        )

    # ───────────────────────── 安全导出（仅掩码） ─────────────────────────

    def export_safe(self, credential_id: str) -> dict:
        """导出单个凭据的安全形态（视图 + 使用记录；无明文字段，GWT-5）。"""
        return {
            "credential": self.get_view(credential_id).model_dump(mode="json"),
            "usage": [e.model_dump(mode="json") for e in self.query_usage(credential_id)],
        }

    def export_all_safe(self) -> dict:
        """导出全部凭据的安全形态（备份/分享物只允许用此口径，GWT-5）。"""
        return {
            v.credential_id: self.export_safe(v.credential_id)
            for v in self.list_credentials()
        }

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _cred_path(credential_id: str) -> str:
        check_credential_id(credential_id)
        return f"{CRED_PREFIX}{credential_id}.json"

    def _load(self, credential_id: str) -> CredentialRecord:
        """读回凭据内部记录（不存在 → ``CredentialNotFoundError``）。"""
        check_credential_id(credential_id)
        try:
            raw = self._store.get("secrets", self._cred_path(credential_id))
        except KeyError as exc:
            raise CredentialNotFoundError(
                f"凭据 {credential_id!r} 不存在或已吊销"
            ) from exc
        try:
            return _checked(CredentialRecord, **json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CredentialNotFoundError(
                f"凭据 {credential_id!r} 记录损坏无法解析"
            ) from exc
