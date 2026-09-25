"""分区加密清单（02 §2.3 损坏恢复的校验载体）。

每个分区内维护一份 ``manifest.bin``（AES-256-GCM 加密）：
- 逐文件登记 相对路径 + 大小 + SHA-256 校验和
- 清单自身带 GCM tag：清单被篡改 → 打开清单即失败
- 文件本体篡改 → 打开时逐文件校验和比对失败 → 定位到分区 + 文件

打开存储时逐分区校验（§2.3「任一分区校验失败 → 从最近备份恢复 + 明确告知
损坏范围」）：损坏定位到分区级并显式报告（``StorageCorruptionError``），
其余分区不受影响。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from st_agent.l0.storage.crypto import open_bytes, seal_bytes
from st_agent.l0.storage.errors import CryptoError

__all__ = [
    "MANIFEST_NAME",
    "ManifestEntry",
    "PartitionManifest",
    "compute_digest",
]

MANIFEST_NAME = "manifest.bin"
"""清单文件在各分区目录内的固定名。"""


def compute_digest(data: bytes) -> str:
    """文件内容校验和（SHA-256 hex）。"""
    return hashlib.sha256(data).hexdigest()


class ManifestEntry(BaseModel):
    """清单里的一个文件登记项。"""

    model_config = ConfigDict(frozen=True)

    path: str
    """分区内的相对路径（POSIX 风格，仅一层或多层相对名，禁绝对路径）。"""
    size: int = Field(ge=0)
    digest: str = Field(min_length=64, max_length=64)
    """内容 SHA-256 hex。"""

    @field_validator("path")
    @classmethod
    def _path_relative(cls, v: str) -> str:
        if not v or "\\" in v or v.startswith(("/", "~")) or ".." in v.split("/"):
            raise ValueError(f"清单路径须为分区内安全相对路径: {v!r}")
        return v


class PartitionManifest(BaseModel):
    """一个分区的完整清单（frozen；写入口在 Store，保证清单-文件同批落盘）。"""

    model_config = ConfigDict(frozen=True)

    entries: tuple[ManifestEntry, ...] = ()

    def entry_for(self, path: str) -> ManifestEntry | None:
        return next((e for e in self.entries if e.path == path), None)

    def paths(self) -> frozenset[str]:
        return frozenset(e.path for e in self.entries)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> "PartitionManifest":
        return cls.model_validate_json(raw)

    def seal(self, key: bytes) -> bytes:
        """清单序列化 + AES-256-GCM 加密（AAD 绑定「这是清单」防挪用）。"""
        return seal_bytes(key, self.to_json().encode("utf-8"), aad=b"st-agent/manifest")

    @classmethod
    def open(cls, key: bytes, sealed: bytes) -> "PartitionManifest":
        """解密并解析清单；篡改 → ``CryptoError``。"""
        raw = open_bytes(key, sealed, aad=b"st-agent/manifest")
        return cls.from_json(raw.decode("utf-8"))

    @classmethod
    def build(cls, files: dict[str, bytes]) -> "PartitionManifest":
        """由 ``{相对路径: 内容}`` 构建清单（写入路径的伴生物）。"""
        entries = tuple(
            ManifestEntry(path=p, size=len(d), digest=compute_digest(d))
            for p, d in sorted(files.items())
        )
        return cls(entries=entries)


def _dupe_paths(data: dict[str, Any]) -> None:  # pragma: no cover
    """pydantic 层兜底：清单 JSON 里出现重复路径直接拒。"""
    paths = [e["path"] for e in data.get("entries", [])]
    if len(paths) != len(set(paths)):
        raise ValueError("清单内出现重复路径")
