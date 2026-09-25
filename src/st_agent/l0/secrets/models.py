"""凭据与使用记录模型（02 §3；frozen 值对象，构造期校验）。

- ``CredentialRecord``：内部形态（含明文 ``value``，仅内存短暂持有；
  ``__repr__`` 强制掩码，防明文进日志）。
- ``CredentialView``：对外形态（无 ``value``，仅掩码；界面/列表/导出共用）。
- ``CredentialUsageRecord``：凭据使用记录（时刻/发起方/目的/数据量/结果）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from st_agent.l0.secrets.errors import CredentialValidationError
from st_agent.l0.secrets.masking import mask_secret

__all__ = [
    "CREDENTIAL_ID_PATTERN",
    "KNOWN_KINDS",
    "CredentialRecord",
    "CredentialUsageRecord",
    "CredentialView",
]

CREDENTIAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
"""凭据标识形状（同时是 ``secrets`` 分区内的安全相对路径段）。"""

KNOWN_KINDS = ("llm_api_key", "smtp_credential", "webhook_url")
"""首批凭据种类；``kind`` 允许自定义取值（形状校验，不做成员白名单）。"""


def check_credential_id(value: str) -> str:
    """校验凭据标识（非法 → ``CredentialValidationError``，不抛裸 ValueError）。"""
    if not isinstance(value, str) or not CREDENTIAL_ID_PATTERN.match(value):
        raise CredentialValidationError(
            f"非法凭据标识 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，≤64 字符）"
        )
    return value


class CredentialRecord(BaseModel):
    """凭据内部记录（含明文，仅 ``CredentialVault`` 持有并即时转加密落盘）。"""

    model_config = ConfigDict(frozen=True)

    credential_id: str
    kind: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=128)
    value: str = Field(min_length=1, max_length=8192)
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)

    @field_validator("credential_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        return check_credential_id(v)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise CredentialValidationError("时间必须带时区语义")
        return v

    def __repr__(self) -> str:  # GWT-5：repr 永不携带明文
        return (
            f"CredentialRecord(credential_id={self.credential_id!r}, kind={self.kind!r}, "
            f"value={mask_secret(self.value)!r}, version={self.version!r})"
        )

    def __str__(self) -> str:  # 与 repr 同口径
        return self.__repr__()

    def view(self) -> "CredentialView":
        """降级为对外视图（丢弃明文，只留掩码）。"""
        return CredentialView(
            credential_id=self.credential_id,
            kind=self.kind,
            label=self.label,
            masked=mask_secret(self.value),
            created_at=self.created_at,
            updated_at=self.updated_at,
            version=self.version,
        )


class CredentialView(BaseModel):
    """凭据对外视图（无明文字段；界面掩码/列表/导出物统一用此形态，GWT-2/5）。"""

    model_config = ConfigDict(frozen=True)

    credential_id: str
    kind: str
    label: str = ""
    masked: str
    created_at: datetime
    updated_at: datetime
    version: int = 1


class CredentialUsageRecord(BaseModel):
    """凭据使用记录（GWT-3：时刻/发起方/目的/数据量/结果；存 ``execution_log`` 分区）。"""

    model_config = ConfigDict(frozen=True)

    credential_id: str
    timestamp: datetime
    initiator: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=256)
    data_bytes: int = Field(default=0, ge=0)
    status: Literal["ok", "failed"] = "ok"

    @field_validator("credential_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        return check_credential_id(v)

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise CredentialValidationError("时间必须带时区语义")
        return v
