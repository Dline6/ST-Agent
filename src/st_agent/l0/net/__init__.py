"""出网审计网关（02-L0 §6 §7）。

- 一切出网经 ``EgressGateway`` 执行；逐次留痕（审计）**可选项、默认关**（GWT-1/2）
- 审计落盘为**分段日志**（`put` 次数与请求数解耦；`query(since)` 可跳段）
- 离线三级清单 ``full`` / ``degraded`` / ``none``（GWT-3）
- LLM 真实发送经 ``llm_transport`` 适配器接入（GWT-4，兑现 T-L0-003 遗留）
- 失败一律走 ``ResultEnvelope``（GWT-5）

对外统一从 ``st_agent.l0.net`` import。
"""

from st_agent.l0.net.audit_config import (
    AUDIT_CONFIG_ID,
    AUDIT_POLICY_PATH,
    DEFAULT_AUDIT_ENABLED,
    NetAuditPolicy,
    get_audit_policy,
    net_audit_config_entries,
    set_audit_policy,
)
from st_agent.l0.net.errors import (
    CapabilityExistsError,
    CapabilityNotFoundError,
    EgressCancelledError,
    EgressError,
    EgressTimeoutError,
    EgressUnavailableError,
    NetAuditConfigError,
    NetError,
    NetValidationError,
)
from st_agent.l0.net.gateway import (
    AUDIT_SEGMENT_SIZE,
    CAPABILITY_PREFIX,
    NET_AUDIT_DISABLED_REF,
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
    "AUDIT_CONFIG_ID",
    "AUDIT_POLICY_PATH",
    "AUDIT_SEGMENT_SIZE",
    "CAPABILITY_PREFIX",
    "DEFAULT_AUDIT_ENABLED",
    "NET_AUDIT_DISABLED_REF",
    "NET_LOG_PREFIX",
    "CapabilityExistsError",
    "CapabilityNotFoundError",
    "CapabilityRecord",
    "EgressCancelledError",
    "EgressError",
    "EgressGateway",
    "EgressTimeoutError",
    "EgressUnavailableError",
    "NetAuditConfigError",
    "NetAuditPolicy",
    "NetError",
    "NetValidationError",
    "NetworkEvent",
    "NetworkRequestKind",
    "NetworkStatus",
    "OfflineReport",
    "Sender",
    "SenderResult",
    "check_capability_id",
    "get_audit_policy",
    "net_audit_config_entries",
    "set_audit_policy",
]
