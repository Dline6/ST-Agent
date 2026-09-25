"""ST Agent · 平台共享契约类型（01-平台共享契约的单一真相源）。

任何层（L0–L6）实现只能 ``from st_agent.contracts import ...``；
各层不得私造跨层格式（技术架构总览 §3.2）。

契约文档：docs/技术架构-v2/01-平台共享契约.md（schema 不锁定序列化格式，
本实现选 JSON 兼容的 pydantic v2 frozen 模型；字段表→模型的映射见各模块 docstring）。
"""

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import (
    ID_ALIASES,
    ID_KINDS,
    ID_REGISTRY,
    AnnouncementId,
    ChangeId,
    DatasetSnapshotId,
    DeliveryId,
    FeedbackId,
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
    "ChangeId",
    "ChangePolicy",
    "ChangeRecord",
    "ConclusionRef",
    "ConfigEntry",
    "ContractViolation",
    "DatasetSnapshotId",
    "DeliveryId",
    "EnvelopeStatus",
    "EvidenceRef",
    "FeedbackId",
    "ID_ALIASES",
    "ID_KINDS",
    "ID_REGISTRY",
    "LensId",
    "LensOpinion",
    "MemoryNodeId",
    "NeutralityFinding",
    "NeutralityGuard",
    "NeutralityVerdict",
    "PanelField",
    "ParameterSpec",
    "PermissionAction",
    "PlatformId",
    "Provenance",
    "ResultEnvelope",
    "STEP_TYPES",
    "SemVer",
    "SignalId",
    "SkillDescriptor",
    "SkillId",
    "SkillRunId",
    "StockId",
    "Trace",
    "TraceError",
    "TraceId",
    "TraceStep",
    "VersionBump",
    "default_rulepack",
    "digest_of",
    "parse_permission",
    "validate_permissions",
]
