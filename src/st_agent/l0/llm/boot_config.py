"""LLM 引导配置的持久化记录（[02 §4](../../../docs/技术架构-v2/02-L0-本地优先基座.md)；[D-129]）。

只存 ``base_url`` / ``model``——**不含任何凭据**（Key 的凭据本体只落加密凭据库）。
落 ``config`` 分区 ``llm-boot.json``，故随备份 / 导出；``.env`` 存在时仍以 ``.env`` 为准，
本记录是「换机恢复后没有 ``.env``」时的回退输入。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = ["BOOT_CONFIG_PATH", "LlmBootConfig", "load_boot_config", "save_boot_config"]

BOOT_CONFIG_PATH = "llm-boot.json"


class LlmBootConfigError(ValueError):
    """引导配置记录非法或损坏。"""


class LlmBootConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    updated_at: datetime

    @property
    def hint(self) -> str:
        """中性一行（不含任何凭据）。"""
        return f"{self.model} @ {self.base_url}"


def load_boot_config(store) -> LlmBootConfig | None:
    """读引导配置；不存在 → ``None``，损坏 → ``LlmBootConfigError``（不静默）。"""
    try:
        raw = store.get("config", BOOT_CONFIG_PATH)
    except KeyError:
        return None
    try:
        return LlmBootConfig.model_validate_json(raw.decode("utf-8"))
    except (ValueError, ValidationError, UnicodeDecodeError) as exc:
        raise LlmBootConfigError(f"LLM 引导配置记录损坏（{BOOT_CONFIG_PATH}）：{exc}") from exc


def save_boot_config(store, *, base_url: str, model: str) -> LlmBootConfig:
    """写入引导配置（整体覆盖；调用方须先校验取值）。"""
    record = LlmBootConfig(
        base_url=str(base_url).strip(),
        model=str(model).strip(),
        updated_at=datetime.now().astimezone(),
    )
    store.put("config", BOOT_CONFIG_PATH, record.model_dump_json().encode("utf-8"))
    return record
