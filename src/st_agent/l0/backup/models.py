"""备份归档 / 留存策略的数据模型（02 §8–§9）。

- ``BackupManifest`` —— 归档内清单（各分区版本、校验和、创建时间；02 §8.1）
- ``RestoreReport`` —— 恢复结果（还原分区 + 文件数，供上层显式告知）
- ``WipeReport`` —— 清空结果（删除范围 + 备份文件自理提示）
- ``RetentionPolicy`` —— 留存策略（chat_history / execution_log 天数；02 §9）
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ConfigEntry,
    PanelField,
)
from st_agent.l0.storage.manifest import compute_digest

__all__ = [
    "BACKUP_FORMAT_VERSION",
    "BACKUP_PARTITIONS",
    "DEFAULT_CHAT_HISTORY_DAYS",
    "DEFAULT_EXECUTION_LOG_DAYS",
    "POLICY_PATH",
    "RETAINABLE_PARTITIONS",
    "BackupFileEntry",
    "BackupManifest",
    "RestoreReport",
    "RetentionPolicy",
    "WipeReport",
    "retention_config_entries",
]

BACKUP_FORMAT_VERSION = 1
"""归档格式主版本（v1；恢复时主版本不一致即拒——A3）。"""

BACKUP_PARTITIONS: tuple[str, ...] = ("memory", "config", "chat_history", "reflection")
"""02 §8.1 备份分区表：四分区必含，``data_cache`` 可选，余者永不进归档（A1）。"""

DEFAULT_CHAT_HISTORY_DAYS = 365
DEFAULT_EXECUTION_LOG_DAYS = 90
"""留存默认值（02 §9「实现阶段定但须显式告知」——经 ConfigEntry 向双通道显式；A6）。"""

POLICY_PATH = "retention/policy.json"
"""``config`` 分区内留存策略文件的相对路径。"""

RETAINABLE_PARTITIONS: dict[str, str] = {
    "chat_history": "chat_history_days",
    "execution_log": "execution_log_days",
}
"""允许自动清理的分区 → RetentionPolicy 字段名。reflection / memory 不在其中（02 §9）。"""


class BackupFileEntry(BaseModel):
    """归档内一个文件的登记项（路径 + 大小 + 校验和 + 内容）。"""

    model_config = ConfigDict(frozen=True)

    path: str
    size: int = Field(ge=0)
    digest: str = Field(min_length=64, max_length=64)
    content_b64: str = ""
    """文件明文的 base64（归档 payload 整体再经 AES-256-GCM 加密）。"""

    def verify_content(self, raw: bytes) -> bool:
        """内容与登记项是否一致（大小 + SHA-256；恢复前校验用）。"""
        return len(raw) == self.size and compute_digest(raw) == self.digest


class BackupManifest(BaseModel):
    """归档清单（02 §8.1「归档清单：各分区版本、校验和、创建时间」）。"""

    model_config = ConfigDict(frozen=True)

    format_version: int = Field(default=BACKUP_FORMAT_VERSION, ge=1)
    created_at: datetime
    """归档创建时间（用户本地时区，01 §8）。"""
    partitions: dict[str, tuple[BackupFileEntry, ...]] = {}
    """分区名 → 文件登记项（仅备份分区表内分区）。"""
    includes_data_cache: bool = False

    @field_validator("created_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at 必须带时区语义（01 §8）")
        return v

    @property
    def file_count(self) -> int:
        return sum(len(v) for v in self.partitions.values())


class RestoreReport(BaseModel):
    """恢复结果（供上层显式告知用户还原范围）。"""

    model_config = ConfigDict(frozen=True)

    restored_partitions: tuple[str, ...]
    file_count: int = Field(ge=0)
    archived_at: datetime
    """归档的创建时间（告诉用户「这是何时的数据」）。"""


class WipeReport(BaseModel):
    """完全清空结果（删除范围 + 备份文件自理提示；02 §8.3）。"""

    model_config = ConfigDict(frozen=True)

    deleted_partitions: tuple[str, ...]
    """本次删除的分区（含 keyfile 指示见 detail）。"""
    detail: str = ""
    backup_reminder: str = (
        "备份文件位置由用户自行处理；产品不保留任何残留（02 §8.3）"
    )


class RetentionPolicy(BaseModel):
    """留存策略（02 §9：对话历史、执行日志清理周期可配）。"""

    model_config = ConfigDict(frozen=True)

    chat_history_days: int = Field(default=DEFAULT_CHAT_HISTORY_DAYS, ge=0)
    execution_log_days: int = Field(default=DEFAULT_EXECUTION_LOG_DAYS, ge=0)
    """保留天数；0 = 不自动清理（仅对本表内两分区有效）。"""


def retention_config_entries() -> tuple[ConfigEntry, ConfigEntry]:
    """留存两旋钮的 01 §7 ConfigEntry（供 L1 注册表 / L3 双通道显式告知默认值）。"""
    policy = ChangePolicy(requires_confirmation=True, record_history=True,
                          rollback_enabled=True)
    return (
        ConfigEntry(
            config_id="retention.chat_history_days",
            display_name="对话历史保留天数",
            value_schema={"type": "number", "minimum": 0},
            default=DEFAULT_CHAT_HISTORY_DAYS,
            description_for_chat=(
                "对话历史自动清理周期（天）；0 表示不自动清理；"
                f"当前默认 {DEFAULT_CHAT_HISTORY_DAYS} 天"
            ),
            panel_form_spec=PanelField(
                widget="number", label="对话历史保留天数",
                help_text="超过该天数的对话历史文件将被自动清理（默认 365 天）",
            ),
            scope="global",
            change_policy=policy,
        ),
        ConfigEntry(
            config_id="retention.execution_log_days",
            display_name="执行日志保留天数",
            value_schema={"type": "number", "minimum": 0},
            default=DEFAULT_EXECUTION_LOG_DAYS,
            description_for_chat=(
                "执行日志自动清理周期（天）；0 表示不自动清理；"
                f"当前默认 {DEFAULT_EXECUTION_LOG_DAYS} 天"
            ),
            panel_form_spec=PanelField(
                widget="number", label="执行日志保留天数",
                help_text="超过该天数的执行日志文件将被自动清理（默认 90 天）",
            ),
            scope="global",
            change_policy=policy,
        ),
    )
