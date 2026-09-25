"""LLM 端点模型（02 §4；frozen 值对象，构造期校验）。

- ``EndpointCapability``：端点声明的能力档位（上下文规模、结构化输出支持）
- ``CapabilityRequirement``：上层调用前声明的需求档位（协商输入）
- ``LlmEndpoint``：端点配置（标识/种类/提供方/能力/优先级/用途/凭据引用/
  离线级别/默认超时）；云端端点必须引用 ``secrets`` 分区凭据（只存标识，
  值经 ``CredentialVault.use()`` 运行时取用），本地端点不得带凭据引用
- ``LlmUsageRecord``：用量记录（仅计数 + 归属 + 结果，不含 prompt/response
  明文，存 ``execution_log`` 分区）
- ``StreamEvent``：统一流式协议事件（chunk / done / error 三态）
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm.errors import LlmValidationError

__all__ = [
    "ENDPOINT_ID_PATTERN",
    "CapabilityRequirement",
    "EndpointCapability",
    "LlmEndpoint",
    "LlmUsageRecord",
    "OfflineLevel",
    "StreamEvent",
    "check_endpoint_id",
    "estimate_tokens",
]

ENDPOINT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
"""端点标识形状（同时是 ``config`` 分区内的安全相对路径段）。"""

OfflineLevel = Literal["full", "degraded", "none"]
"""离线能力分级（02 §7；云端端点为 ``none``，本地端点默认 ``full``）。"""


def check_endpoint_id(value: str) -> str:
    """校验端点标识（非法 → ``LlmValidationError``，不抛裸 ValueError）。"""
    if not isinstance(value, str) or not ENDPOINT_ID_PATTERN.match(value):
        raise LlmValidationError(
            f"非法端点标识 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，≤64 字符）"
        )
    return value


def estimate_tokens(text: str) -> int:
    """本地 token 估算（无分词器时的计数口径：每 4 字符计 1 token，向上取整）。

    仅用于本地用量展示（02 §4「本地统计，不外发」）；提供方账单以其实际
    计量为准。空串计 0。
    """
    if not text:
        return 0
    return (len(text) + 3) // 4


class EndpointCapability(BaseModel):
    """端点能力档位声明（02 §4 能力协商；端点侧的供给描述）。"""

    model_config = ConfigDict(frozen=True)

    max_context_tokens: int = Field(ge=1)
    """端点支持的最大上下文 token 数。"""
    supports_structured_output: bool = False
    """是否支持结构化输出（JSON mode /  constrained decoding 等）。"""


class CapabilityRequirement(BaseModel):
    """调用方能力需求（02 §4 能力协商；调用侧的需求描述）。"""

    model_config = ConfigDict(frozen=True)

    min_context_tokens: int = Field(default=1, ge=1)
    """本次调用至少需要的上下文 token 数。"""
    needs_structured_output: bool = False
    """本次调用是否必须结构化输出。"""


class LlmEndpoint(BaseModel):
    """LLM 端点配置（GWT-1 多端点并存；存 ``config`` 分区，不含任何密钥明文）。"""

    model_config = ConfigDict(frozen=True)

    endpoint_id: str
    kind: Literal["cloud", "local"]
    """``cloud`` 经提供方直连（须凭据），``local`` 本地推理（禁凭据）。"""
    provider: str = Field(min_length=1, max_length=64)
    """提供方标识（如 openai-compatible / local-llama；仅路由用，不含密钥）。"""
    label: str = Field(default="", max_length=128)
    capability: EndpointCapability
    priority: int = Field(default=100, ge=0)
    """优先级（数字越小越优先；``list`` 按此排序）。"""
    purpose: str = Field(default="", max_length=256)
    """用途说明（如 日常用云端 / 敏感数据仅用本地）。"""
    credential_id: str | None = None
    """云端端点的凭据引用（``CredentialVault`` 标识；本地端点必须为 None）。"""
    offline_level: OfflineLevel = "none"
    """离线分级声明（02 §7；网关本体在 T-L0-004，此处只声明）。"""
    default_timeout_ms: int = Field(default=60_000, ge=1)
    """默认调用超时（毫秒；单次调用可覆盖）。"""
    created_at: datetime
    updated_at: datetime

    @field_validator("endpoint_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        return check_endpoint_id(v)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise LlmValidationError("时间必须带时区语义")
        return v

    @model_validator(mode="after")
    def _credential_matches_kind(self) -> "LlmEndpoint":
        if self.kind == "cloud" and not self.credential_id:
            raise LlmValidationError("云端端点必须引用凭据（credential_id 不得为空）")
        if self.kind == "local" and self.credential_id is not None:
            raise LlmValidationError("本地端点不得引用凭据（credential_id 必须为 None）")
        return self


class LlmUsageRecord(BaseModel):
    """LLM 用量记录（GWT-4：token 用量本地可见；只记计数，不记内容）。

    字段纪律：出现 ``prompt`` / ``response`` / ``text`` / ``value`` 即违规——
    用量只含 token 计数、耗时、归属与结果。
    """

    model_config = ConfigDict(frozen=True)

    endpoint_id: str
    timestamp: datetime
    initiator: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=256)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    status: Literal["ok", "failed"] = "ok"
    fail_reason: str = ""

    @field_validator("endpoint_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        return check_endpoint_id(v)

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise LlmValidationError("时间必须带时区语义")
        return v

    @model_validator(mode="after")
    def _totals_consistent(self) -> "LlmUsageRecord":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise LlmValidationError("total_tokens 必须等于 prompt_tokens + completion_tokens")
        if self.status == "failed" and not self.fail_reason.strip():
            raise LlmValidationError("失败用量须给出 fail_reason（禁止裸失败）")
        return self


class StreamEvent(BaseModel):
    """统一流式协议事件（GWT-3；三态互斥，构造期强制）。

    - ``chunk``：增量文本 ``text`` 非空
    - ``done``：流正常结束，携带 ``usage``（调用方凭此记用量）
    - ``error``：中断/超时/端点故障，携带 ``error_envelope``（01 §5 载体）
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["chunk", "done", "error"]
    text: str = ""
    usage: LlmUsageRecord | None = None
    error_envelope: ResultEnvelope | None = None

    @model_validator(mode="after")
    def _kind_shape(self) -> "StreamEvent":
        if self.kind == "chunk" and not self.text:
            raise LlmValidationError("chunk 事件必须携带非空增量文本")
        if self.kind == "done" and self.usage is None:
            raise LlmValidationError("done 事件必须携带用量记录")
        if self.kind == "error" and self.error_envelope is None:
            raise LlmValidationError("error 事件必须携带 ResultEnvelope")
        if self.kind in ("chunk", "done") and self.error_envelope is not None:
            raise LlmValidationError("chunk/done 事件不得携带 error_envelope")
        if self.kind in ("chunk", "error") and self.usage is not None:
            raise LlmValidationError("usage 只属于 done 事件")
        return self
