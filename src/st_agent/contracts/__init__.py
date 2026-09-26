"""ST Agent · 平台共享契约类型（01-平台共享契约的单一真相源）。

任何层（L0–L6）实现只能 ``from st_agent.contracts import ...``；
各层不得私造跨层格式（技术架构总览 §3.2）。

契约文档：docs/技术架构-v2/01-平台共享契约.md（schema 不锁定序列化格式，
本实现选 JSON 兼容的 pydantic v2 frozen 模型；字段表→模型的映射见各模块 docstring）。
"""

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import (
    FLOW_ID_PATTERN,
    ID_ALIASES,
    ID_KINDS,
    ID_REGISTRY,
    AnnouncementId,
    ChangeId,
    DatasetSnapshotId,
    DeliveryId,
    FeedbackId,
    FlowId,
    LensId,
    MemoryNodeId,
    PlatformId,
    SignalId,
    SkillId,
    SkillRunId,
    StockId,
    TraceId,
)
from st_agent.contracts.result_envelope import (
    EnvelopeStatus,
    EvidenceRef,
    ResultEnvelope,
)
from st_agent.contracts.schema_check import (
    DIALECT_KEYWORDS,
    SCHEMA_TYPES,
    LinkCheck,
    PayloadCheck,
    SchemaViolation,
    check_link,
    check_payload,
)
from st_agent.contracts.capability_types import (
    LensOpinion,
    ParameterSpec,
    Provenance,
    SkillDescriptor,
)
from st_agent.contracts.neutrality import (
    NeutralityFinding,
    NeutralityGuard,
    NeutralityVerdict,
    default_rulepack,
)
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
    PermissionAction,
    SemVer,
    VersionBump,
    parse_permission,
    validate_permissions,
)
from st_agent.contracts.time_events import (
    CORE_EVENTS,
    DataAnchor,
    PlatformEvent,
    StalenessVerdict,
)
from st_agent.contracts.trace import (
    STEP_TYPES,
    ConclusionRef,
    Trace,
    TraceError,
    TraceStep,
    digest_of,
)

__all__ = [
    "AnnouncementId",
    "CORE_EVENTS",
    "ChangeId",
    "ChangePolicy",
    "ChangeRecord",
    "ConclusionRef",
    "ConfigEntry",
    "ContractViolation",
    "DataAnchor",
    "DatasetSnapshotId",
    "DeliveryId",
    "DIALECT_KEYWORDS",
    "EnvelopeStatus",
    "EvidenceRef",
    "FeedbackId",
    "FLOW_ID_PATTERN",
    "FlowId",
    "ID_ALIASES",
    "ID_KINDS",
    "ID_REGISTRY",
    "LensId",
    "LensOpinion",
    "LinkCheck",
    "MemoryNodeId",
    "NeutralityFinding",
    "NeutralityGuard",
    "NeutralityVerdict",
    "PanelField",
    "ParameterSpec",
    "PayloadCheck",
    "PermissionAction",
    "PlatformEvent",
    "PlatformId",
    "Provenance",
    "ResultEnvelope",
    "SCHEMA_TYPES",
    "STEP_TYPES",
    "SchemaViolation",
    "SemVer",
    "SignalId",
    "SkillDescriptor",
    "SkillId",
    "SkillRunId",
    "StalenessVerdict",
    "StockId",
    "Trace",
    "TraceError",
    "TraceId",
    "TraceStep",
    "VersionBump",
    "check_link",
    "check_payload",
    "default_rulepack",
    "digest_of",
    "parse_permission",
    "validate_permissions",
]
