"""出网审计网关（02-L0 §6 §7）。

- 一切出网经 ``EgressGateway`` 执行并留痕（GWT-1/2）
- 离线三级清单 ``full`` / ``degraded`` / ``none``（GWT-3）
- LLM 真实发送经 ``llm_transport`` 适配器接入（GWT-4，兑现 T-L0-003 遗留）
- 失败一律走 ``ResultEnvelope``（GWT-5）

对外统一从 ``st_agent.l0.net`` import。
"""

from st_agent.l0.net.errors import (
    CapabilityExistsError,
    CapabilityNotFoundError,
    EgressCancelledError,
    EgressError,
    EgressTimeoutError,
    EgressUnavailableError,
    NetError,
    NetValidationError,
)
from st_agent.l0.net.gateway import (
    CAPABILITY_PREFIX,
    NET_LOG_PREFIX,
    EgressGateway,
    Sender,
    SenderResult,
)
from st_agent.l0.net.models import (
    CapabilityRecord,
    NetworkEvent,
    NetworkRequestKind,
    NetworkStatus,
    OfflineReport,
    check_capability_id,
)

__all__ = [
    "CAPABILITY_PREFIX",
    "NET_LOG_PREFIX",
    "CapabilityExistsError",
    "CapabilityNotFoundError",
    "CapabilityRecord",
    "EgressCancelledError",
    "EgressError",
    "EgressGateway",
    "EgressTimeoutError",
    "EgressUnavailableError",
    "NetError",
    "NetValidationError",
    "NetworkEvent",
    "NetworkRequestKind",
    "NetworkStatus",
    "OfflineReport",
    "Sender",
    "SenderResult",
    "check_capability_id",
]
