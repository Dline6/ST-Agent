"""LLM 端点抽象（02-L0 §4）。

- 端点配置存 ``config`` 分区（只存凭据**引用**，不含明文，GWT-1）
- 调用前能力协商（GWT-2），统一流式协议（GWT-3）
- 云端 Key 唯一经 ``CredentialVault.use()`` 取用，prompt/response 不落盘，
  用量只记计数入 ``execution_log``（GWT-4）
- 失败一律走 ``ResultEnvelope``（GWT-5）
- 真实发包经 [出网网关](l0/net/gateway.py) 的**按次 sender**通道（T-L0-016）：
  ``openai_compat_sender_factory`` 把 prompt / key 放进 sender 闭包，网关只见字节数

对外统一从 ``st_agent.l0.llm`` import。
"""

from st_agent.l0.llm.client import (
    LLM_USAGE_PREFIX,
    LlmClient,
    TransportCancelledError,
    TransportError,
    TransportTimeoutError,
    TransportUnavailableError,
)
from st_agent.l0.llm.errors import (
    LlmError,
    LlmExistsError,
    LlmNotFoundError,
    LlmValidationError,
)
from st_agent.l0.llm.http_transport import (
    CHAT_COMPLETIONS_PATH,
    OpenAiRoute,
    Post,
    openai_compat_sender_factory,
    provider_hosts_of,
)
from st_agent.l0.llm.models import (
    CapabilityRequirement,
    EndpointCapability,
    LlmEndpoint,
    LlmUsageRecord,
    StreamEvent,
    check_endpoint_id,
    estimate_tokens,
)
from st_agent.l0.llm.registry import ENDPOINT_PREFIX, EndpointRegistry

__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "ENDPOINT_PREFIX",
    "LLM_USAGE_PREFIX",
    "CapabilityRequirement",
    "EndpointCapability",
    "EndpointRegistry",
    "LlmClient",
    "LlmEndpoint",
    "LlmError",
    "LlmExistsError",
    "LlmNotFoundError",
    "LlmUsageRecord",
    "LlmValidationError",
    "OpenAiRoute",
    "Post",
    "StreamEvent",
    "TransportCancelledError",
    "TransportError",
    "TransportTimeoutError",
    "TransportUnavailableError",
    "check_endpoint_id",
    "estimate_tokens",
    "openai_compat_sender_factory",
    "provider_hosts_of",
]
