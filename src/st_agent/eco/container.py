"""四类分享物统一容器（[09 §1](../../../docs/技术架构-v2/09-生态与分享.md)）。

四类分享物（`.stskill` / `.stflow` / `.stlens` / `.stmem`）**同构三段**——一套实现，
不是四份：

| 段 | 内容 |
| --- | --- |
| `header` | 格式标识（:data:`SHARE_FORMAT_ID`）+ 分享物种类 + 格式版本（[01 §9](../../../docs/技术架构-v2/01-平台共享契约.md)） |
| `payload` | 分享物本体（四类各一种形状，见 :data:`PAYLOAD_MODELS`） |
| `manifest` | 作者声明 · 创建时间 · 依赖声明（`skill_id` 清单）· 来源追溯 · 校验和 |

**明文可检视**：磁盘形态是 UTF-8 的 JSON 文档（缩进两格），无加密层——与
[02 §8.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的备份归档**同族但互不通用**：
备份是加密容器且含私有敏感数据，分享文件是明文且经强制隐私过滤。两者都以 JSON 承载，
故区分靠 `header.format_id`（备份无 `header` 段），不靠「能否解 JSON」。

**校验和**见 :mod:`st_agent.eco.checksum`（`manifest.checksum` 与 L2 的
`ImportOrigin.checksum` 是同一个数）。

**版本读取判据**（[01 §9](../../docs/技术架构-v2/01-平台共享契约.md)）：同一**主版本**可读
（次版本更高 ⇒ 照读，未知键按前向兼容忽略）；**主版本**不同 ⇒
:class:`~st_agent.eco.errors.ShareVersionError`——文件是好的，只是本机读不懂。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.registry_types import SemVer
from st_agent.eco.checksum import (
    CHECKSUM_PATTERN,
    checksum_basis,
    sha256_hex,
    split_document,
)
from st_agent.eco.errors import ShareExportError, ShareFormatError, ShareVersionError
from st_agent.l1.skills.ids import check_skill_id
from st_agent.l1.workflow.models import WorkflowDAG
from st_agent.l2.memory.sharing import FragmentPayload
from st_agent.l4.lens import Lens

__all__ = [
    "CONTAINER_VERSION",
    "PAYLOAD_MODELS",
    "SHARE_EXTENSIONS",
    "SHARE_FORMAT_ID",
    "SHARE_KINDS",
    "SUPPORTED_MAJOR",
    "ShareContainer",
    "ShareHeader",
    "ShareKind",
    "ShareManifest",
    "extension_for",
    "kind_of",
]

ShareKind = Literal["skill", "flow", "lens", "mem"]
"""四类分享物（[09 §1](../../../docs/技术架构-v2/09-生态与分享.md) 的表）。"""

SHARE_KINDS: tuple[ShareKind, ...] = ("skill", "flow", "lens", "mem")

SHARE_EXTENSIONS: dict[str, str] = {
    "skill": ".stskill",
    "flow": ".stflow",
    "lens": ".stlens",
    "mem": ".stmem",
}
"""种类 → 扩展名（09 §1 的四类文件）。"""

SHARE_FORMAT_ID = "st-agent-share"
"""格式标识（`header` 段）——与备份归档 `st-agent-backup/v1` 区分开。"""

CONTAINER_VERSION = "1.0"
"""当前容器格式版本（01 §9 主.次语义）。"""

SUPPORTED_MAJOR = 1
"""本机可读的主版本（01 §9：主版本变更 = 契约不兼容，需人工确认）。"""

PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "skill": SkillDescriptor,
    "flow": WorkflowDAG,
    "lens": Lens,
    "mem": FragmentPayload,
}
"""四类本体（全部是已交付的上游模型；容器不另造一套）。"""


def extension_for(kind: str) -> str:
    """该种类的文件扩展名（未知种类 → ``ShareFormatError``）。"""
    try:
        return SHARE_EXTENSIONS[kind]
    except KeyError as exc:
        raise ShareFormatError(
            f"未知分享物种类 {kind!r}（四类之一：{list(SHARE_KINDS)}）"
        ) from exc


def kind_of(payload: object) -> str:
    """该本体属哪一类（打包时**推断**，调用方无需也无法传错）。"""
    for kind, model in PAYLOAD_MODELS.items():
        if isinstance(payload, model):
            return kind
    raise ShareFormatError(
        f"未知分享物本体 {type(payload).__name__}"
        f"（须为 {[m.__name__ for m in PAYLOAD_MODELS.values()]} 之一）"
    )


def _require_tz(value: datetime, label: str) -> datetime:
    """时间须带时区（[01 §8](../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    if value.tzinfo is None:
        raise ShareFormatError(f"{label} 须带时区（01 §8）")
    return value


class ShareHeader(BaseModel):
    """容器 `header` 段：格式标识 + 分享物种类 + 格式版本。

    **允许未知键**（pydantic 缺省 ignore）：次版本升级可以加字段，旧读者须照读
    （01 §9「次版本 = 兼容性增强」）——故此处**不**用 `extra="forbid"`。
    """

    model_config = ConfigDict(frozen=True)

    format_id: Annotated[str, Field(min_length=1)] = SHARE_FORMAT_ID
    share_type: ShareKind
    version: Annotated[str, Field(min_length=1)] = CONTAINER_VERSION

    @field_validator("format_id")
    @classmethod
    def _format_id_fixed(cls, v: str) -> str:
        if v != SHARE_FORMAT_ID:
            raise ShareFormatError(
                f"格式标识 {v!r} 不是分享物容器（期望 {SHARE_FORMAT_ID!r}）"
                "——备份归档与本族同以 JSON 承载，故只能靠标识区分（09 §1 / 02 §8.1）"
            )
        return v

    @field_validator("version")
    @classmethod
    def _version_shape(cls, v: str) -> str:
        try:
            SemVer.parse(v)
        except ValueError as exc:
            raise ShareFormatError(f"格式版本 {v!r} 非法（01 §9 主.次）：{exc}") from exc
        return v


class ShareManifest(BaseModel):
    """容器 `manifest` 段：元数据 + 来源追溯 + 校验和。

    与 [09 §1](../../docs/技术架构-v2/09-生态与分享.md) 的字段一一对应：

    | 09 §1 | 此处 |
    | --- | --- |
    | 作者声明 | :attr:`author` |
    | 创建时间 | :attr:`created_at` |
    | 依赖声明（所需 `skill_id` 清单） | :attr:`dependencies` |
    | 来源追溯（分享者标识、原始出处链） | :attr:`provenance` 的 `sharer` / `origin_chain` |
    | 校验和 | :attr:`checksum` |

    `provenance` 另一半（`imported_at` / `checksum`）是**导入侧**记账——导入时刻与
    当时核过的校验和由导入方（[09 §3](../../docs/技术架构-v2/09-生态与分享.md)）落账，
    导出物本身不带（此处 `imported_at` 恒 `None`，校验和由 :attr:`checksum` 承载，
    避免同一个数在两处各存一份而漂移）。**允许未知键**，理由同 :class:`ShareHeader`。
    """

    model_config = ConfigDict(frozen=True)

    author: Annotated[str, Field(min_length=1, max_length=128)]
    created_at: datetime
    dependencies: tuple[str, ...] = ()
    provenance: Provenance = Field(default_factory=Provenance)
    checksum: str = ""

    @field_validator("created_at")
    @classmethod
    def _created_at_tz(cls, v: datetime) -> datetime:
        return _require_tz(v, "创建时间 created_at")

    @field_validator("dependencies")
    @classmethod
    def _deps_are_skill_ids(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for dep in v:
            try:
                check_skill_id(dep)
            except ValueError as exc:
                raise ShareFormatError(
                    f"依赖声明含非 skill_id 条目 {dep!r}（09 §1 的「所需 skill_id 清单」）：{exc}"
                ) from exc
        return v

    @field_validator("checksum")
    @classmethod
    def _checksum_shape(cls, v: str) -> str:
        if v and not CHECKSUM_PATTERN.match(v):
            raise ShareFormatError(f"校验和须为 sha256 十六进制小写 64 位，得到 {v!r}")
        return v


class ShareContainer(BaseModel):
    """四类分享物的统一容器（值对象；磁盘形态见 :meth:`to_bytes`）。

    构造期即校验 **`payload` 与 `header.share_type` 一致**——两者分头构造再拼装
    是最容易漂的一处，故在此钉死。
    """

    model_config = ConfigDict(frozen=True)

    header: ShareHeader
    payload: Any
    manifest: ShareManifest

    @model_validator(mode="after")
    def _payload_matches_type(self) -> "ShareContainer":
        expected = PAYLOAD_MODELS[self.header.share_type]
        if not isinstance(self.payload, expected):
            raise ShareFormatError(
                f"share_type={self.header.share_type!r} 的 payload 须为 "
                f"{expected.__name__}，得到 {type(self.payload).__name__}"
            )
        return self

    # ───────────────────────── 打包 ─────────────────────────

    @classmethod
    def pack(
        cls,
        payload: object,
        *,
        author: str,
        sharer: str | None = None,
        dependencies: Sequence[str] = (),
        origin_chain: Sequence[str] = (),
        created_at: datetime | None = None,
        share_type: str | None = None,
    ) -> "ShareContainer":
        """把本体打包成容器（校验和在打包时算出并写入 `manifest`）。

        :param payload: 四类本体之一（种类由 :func:`kind_of` 推断；显式传 `share_type`
            只作交叉核对，不符即拒）
        :param author: 作者声明（09 §1）
        :param sharer: 本次分享者标识（空 = 分享物不带分享者，如自用留档）
        :param dependencies: 依赖声明（所需 `skill_id` 清单）
        :param origin_chain: **已过手**的分享者序列（不含本次分享者），见
            :func:`st_agent.eco.export.appended_chain`
        :param created_at: 创建时间（缺省取本机当前时刻，01 §8）
        """
        kind = kind_of(payload)
        if share_type is not None and share_type != kind:
            raise ShareFormatError(
                f"显式 share_type={share_type!r} 与本体类型推断值 {kind!r} 不符"
            )
        header = ShareHeader(share_type=kind)
        manifest = ShareManifest(
            author=author,
            created_at=created_at if created_at is not None else _now(),
            dependencies=tuple(dependencies),
            provenance=Provenance(sharer=sharer, origin_chain=tuple(origin_chain)),
        )
        document = {
            "header": header.model_dump(mode="json"),
            "payload": _payload_json(payload),
            "manifest": manifest.model_dump(mode="json"),
        }
        checksum = sha256_hex(
            checksum_basis(document["header"], document["payload"], document["manifest"])
        )
        return cls(
            header=header,
            payload=payload,
            manifest=manifest.model_copy(update={"checksum": checksum}),
        )

    # ───────────────────────── 读取 ─────────────────────────

    @classmethod
    def from_bytes(cls, blob: bytes) -> "ShareContainer":
        """读回容器（顺序即判据：**先完整性与版本，后结构**）。

        1. 三段齐备（:func:`~st_agent.eco.checksum.split_document`）——挡住非本族容器
        2. `header` 结构与**主版本**
        3. `manifest` 结构
        4. **校验和**——文件被改动或损坏在此拦下（先于载荷解析，故「任一处被改」都能
           稳定报出是校验问题，而不是先撞上某处的结构错误）
        5. `payload` 按 `share_type` 解析成对应本体
        """
        raw_header, raw_payload, raw_manifest = split_document(blob)
        header = _build(ShareHeader, raw_header, "header")
        _check_version(header)
        manifest = _build(ShareManifest, raw_manifest, "manifest")
        actual = sha256_hex(checksum_basis(raw_header, raw_payload, raw_manifest))
        if actual != manifest.checksum:
            raise ShareFormatError(
                f"校验和不符：manifest 载 {manifest.checksum or '(空)'}"
                f"，实算 {actual}——文件被改动或损坏"
            )
        try:
            model = PAYLOAD_MODELS[header.share_type]
        except KeyError as exc:
            raise ShareFormatError(
                f"header.share_type={header.share_type!r} 不是四类之一 {list(SHARE_KINDS)}"
            ) from exc
        payload = _build(model, raw_payload, "payload")
        return cls(header=header, payload=payload, manifest=manifest)

    # ───────────────────────── 产出 ─────────────────────────

    def to_document(self) -> dict:
        """容器的 JSON 形态（三段；键序固定 header → payload → manifest）。"""
        return {
            "header": self.header.model_dump(mode="json"),
            "payload": _payload_json(self.payload),
            "manifest": self.manifest.model_dump(mode="json"),
        }

    def to_bytes(self) -> bytes:
        """磁盘形态：UTF-8、缩进两格的 JSON（**明文可检视**，09 §1）。"""
        return (
            json.dumps(self.to_document(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

    def checksum(self) -> str:
        """本容器的校验和（= `manifest.checksum`；重算值应与它一致）。"""
        return self.manifest.checksum

    @property
    def share_type(self) -> str:
        """本容器的种类（四类之一）。"""
        return self.header.share_type

    @property
    def extension(self) -> str:
        """本容器的文件扩展名（`.stskill` 等）。"""
        return extension_for(self.header.share_type)

    def save_to(self, path: Path | str) -> Path:
        """把容器写到**调用方指定**的路径（[09 §2](../../docs/技术架构-v2/09-生态与分享.md)「可复制到任何位置」）。

        只写这一个文件：不落 `Store`、不自建目录或索引。父目录不存在即显式报错
        ——导出物是用户自持的明文副本，位置由用户定，产品不自作主张在其树外造目录。
        """
        target = Path(path)
        if not target.parent.is_dir():
            raise ShareExportError(
                f"目标目录不存在：{target.parent}——导出位置由调用方负责（不自建目录）"
            )
        target.write_bytes(self.to_bytes())
        return target


def _now() -> datetime:
    """本机当前时刻（带本地时区，01 §8）。"""
    return datetime.now().astimezone()


def _payload_json(payload: object) -> Any:
    """本体 → JSON 可表达形态（四类皆为 pydantic 模型）。"""
    return payload.model_dump(mode="json")  # type: ignore[attr-defined]


def _build(model: type[BaseModel], data: Any, label: str) -> Any:
    """按模型构造一段（结构非法 → ``ShareFormatError``，不外泄 pydantic 内部结构）。"""
    if not isinstance(data, Mapping):
        raise ShareFormatError(f"{label} 段须为 JSON 对象，得到 {type(data).__name__}")
    try:
        return model(**dict(data))
    except ValidationError as exc:
        raise ShareFormatError(f"{label} 段结构非法：{exc}") from exc


def _check_version(header: ShareHeader) -> None:
    """主版本判据（01 §9）：同主版本可读，异主版本显式拒。"""
    version = SemVer.parse(header.version)
    if version.major != SUPPORTED_MAJOR:
        raise ShareVersionError(
            f"容器格式主版本 {version.major} 与本机支持的 {SUPPORTED_MAJOR} 不兼容"
            "（01 §9：主版本变更 = 契约不兼容，需人工确认）"
        )
