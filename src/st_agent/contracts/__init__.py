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
    "ConclusionRef",
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
    "MemoryNodeId",
    "PlatformId",
    "ResultEnvelope",
    "STEP_TYPES",
    "SignalId",
    "SkillId",
    "SkillRunId",
    "StockId",
    "Trace",
    "TraceError",
    "TraceId",
    "TraceStep",
    "digest_of",
]
