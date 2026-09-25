"""出网审计网关模型（02 §6；frozen 值对象，构造期校验）。

- ``NetworkRequestKind``：五类出网需求（数据源抓取 / LLM 调用 / 触达渠道 /
  远程 MCP / 官方索引浏览——GWT-1 强制经行口径）
- ``NetworkEvent``：一条出网审计记录（02 §6 表字段；只记字节数，
  永不含 prompt/response/key 明文字段，GWT-4/5）
- ``CapabilityRecord``：一项平台能力的离线声明（02 §7 三级 + 降级标注）
- ``OfflineReport``：离线清单查询结果（可用 / 受限 / 不可用三类并列）
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from st_agent.contracts.capability_types import OfflineLevel
from st_agent.l0.net.errors import NetValidationError

__all__ = [
    "CAPABILITY_ID_PATTERN",
    "HOST_PATTERN",
    "CapabilityRecord",
    "NetworkEvent",
    "NetworkRequestKind",
    "NetworkStatus",
    "OfflineReport",
    "check_capability_id",
]

NetworkRequestKind = Literal[
    "data_fetch", "llm_call", "channel_delivery", "remote_mcp", "index_browse",
]
"""五类出网需求（02 §6「包括」清单的机器可读副本）。"""

NetworkStatus = Literal["ok", "unavailable", "failed", "cancelled"]
"""出网执行结果（``failed`` 含超时；取消单独列支供面板过滤）。"""

#: 能力标识形状（同时是 ``config`` 分区内的安全相对路径段）
CAPABILITY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: 目标主机形状（域名 / IP / localhost 均可；禁 URL scheme 与路径——
#: target_host 只登记主机，不登记完整 URL，避免 query 明文泄漏）
HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*|"
    r"localhost|\d{1,3}(\.\d{1,3}){3})$"
)

_CONTENT_FIELDS = ("prompt", "response", "text", "value", "key", "secret",
                   "password", "token", "authorization")
"""内容/凭据字段黑名单（构造期拒绝，GWT-4/5 零泄漏）。"""


def check_capability_id(value: str) -> str:
    """校验能力标识（非法 → ``NetValidationError``，不抛裸 ValueError）。"""
    if not isinstance(value, str) or not CAPABILITY_ID_PATTERN.match(value):
        raise NetValidationError(
            f"非法能力标识 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，≤64 字符）"
        )
    return value


def _check_no_content_fields(purpose: str, where: str) -> str:
    lowered = purpose.lower()
    for field in _CONTENT_FIELDS:
        if field in lowered:
            raise NetValidationError(
                f"{where} 疑似携带内容/凭证明文（命中 {field!r}）；"
                "审计记录只记目的说明与字节数"
            )
    return purpose


class NetworkEvent(BaseModel):
    """一条出网审计记录（02 §6 表；GWT-2 面板展示与导出的数据形态）。

    字段纪律：``purpose`` 只写人可读目的说明，出现内容/凭据字段名即拒；
    ``detail`` 为执行结果说明（发包异常原文），不扫内容词——零泄漏靠网关
    签名只收字节数保证（调用方无入口传入明文）；字节数字段只计数；
    本模型无任何明文字段（GWT-4/5）。
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    initiator: str = Field(min_length=1, max_length=128)
    """发起方（系统组件 / skill_id / mcp_server_id）。"""
    kind: NetworkRequestKind
    target_host: str = Field(min_length=1, max_length=253)
    """目标主机（只记主机，不记完整 URL）。"""
    purpose: str = Field(min_length=1, max_length=256)
    """目的（人可读，如「BaoStock 行情日线同步」）。"""
    bytes_out: int = Field(ge=0)
    bytes_in: int = Field(ge=0)
    status: NetworkStatus = "ok"
    detail: str = Field(default="", max_length=512)
    """结果说明（如失败原因；中性措辞，不含明文）。"""

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise NetValidationError("时间必须带时区语义")
        return v

    @field_validator("target_host")
    @classmethod
    def _host_shape(cls, v: str) -> str:
        if "://" in v or "/" in v or "?" in v:
            raise NetValidationError(
                f"target_host 只登记主机，不得含 scheme/路径/query: {v!r}"
            )
        if not HOST_PATTERN.match(v):
            raise NetValidationError(f"非法目标主机: {v!r}")
        return v

    @field_validator("purpose")
    @classmethod
    def _no_content(cls, v: str) -> str:
        return _check_no_content_fields(v, "审计记录")


class CapabilityRecord(BaseModel):
    """一项平台能力的离线声明（02 §7；登记在 ``config`` 分区）。

    ``offline_level`` 复用 01 契约 ``OfflineLevel``（宪法第 8 条：接口变更有序——
    契约已有口径此处只 import，不另立）。
    """

    model_config = ConfigDict(frozen=True)

    capability_id: str
    display_name: str = Field(min_length=1, max_length=128)
    offline_level: OfflineLevel
    degrade_note: str = Field(default="", max_length=512)
    """降级标注（``degraded`` 必填：本地替代说明或快照过期时间，GWT-3）。"""

    @field_validator("capability_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        return check_capability_id(v)

    @model_validator(mode="after")
    def _degraded_needs_note(self) -> "CapabilityRecord":
        if self.offline_level == "degraded" and not self.degrade_note.strip():
            raise NetValidationError("degraded 能力必须带降级标注（本地替代说明或快照过期时间）")
        return self


class OfflineReport(BaseModel):
    """离线清单查询结果（02 §7 离线横幅三类清单的数据形态）。"""

    model_config = ConfigDict(frozen=True)

    available: tuple[CapabilityRecord, ...] = ()
    """``full``：断网完全可用。"""
    limited: tuple[CapabilityRecord, ...] = ()
    """``degraded``：可用但受限（每项带标注）。"""
    unavailable: tuple[CapabilityRecord, ...] = ()
    """``none``：必须联网。"""
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise NetValidationError("时间必须带时区语义")
        return v
