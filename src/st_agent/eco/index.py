"""官方 Skill 索引与生态边界（[09 §4](../../../docs/技术架构-v2/09-生态与分享.md) · [09 §6](../../../docs/技术架构-v2/09-生态与分享.md)）。

**只读元数据、不经手文件**：索引列的是官方 Pack 更新与认证的社区 Skill 推荐
（描述 + **外部下载地址** + 校验和）；下载由用户直连外部渠道，文件到手后走
[`ShareImporter`](import_pipeline.py) 的导入校验。[09 §6](../../../docs/技术架构-v2/09-生态与分享.md) 的
生态边界即由此立：产品不做中央商店、不做付费交易、不做自动更新订阅。

**浏览经 L0 出网审计网关**（[09 §4](../../../docs/技术架构-v2/09-生态与分享.md) 2026-10-05 定案，
[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）：以 `kind=index_browse` 发出，
是该出网类目的**唯一调用点**——网关是平台唯一出网出口，索引浏览也不例外。审计
（默认可关）开启时留痕，可由 `gateway.query(kind="index_browse")` 查回。

**条目里的「校验和」与分享物校验和是同一个数**（[`eco.checksum`](checksum.py) 单一实现）：
受赠方据此核对拿到的文件——索引侧与导入侧各算一套就会让核对失去意义。

**不返回半截索引**：离线 / 无端点 / 条目非法一律显式 :class:`~st_agent.eco.errors.ShareIndexError`，
不以空列表冒充「索引里什么都没有」。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.eco.checksum import CHECKSUM_PATTERN
from st_agent.eco.errors import ShareIndexError
from st_agent.l0.net.models import NetworkRequestKind

__all__ = [
    "ECOSYSTEM_BOUNDARY",
    "EMPTY_STATE_TEXT",
    "INDEX_FORMAT_ID",
    "INDEX_INITIATOR",
    "INDEX_PURPOSE",
    "IndexDocument",
    "IndexEntry",
    "IndexEntryKind",
    "OfficialIndex",
    "has_third_party",
]

INDEX_FORMAT_ID = "st-agent-skill-index"
"""索引文档的格式标识（与分享物容器、备份归档各不同族）。"""

INDEX_INITIATOR = "eco-official-index"
"""出网发起方标识（[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的 ``initiator``）。"""

INDEX_PURPOSE = "官方 Skill 索引浏览"
"""出网目的说明（人可读、中性、无内容字段，[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。"""

EMPTY_STATE_TEXT = "你的 Skill 库目前只有官方 Pack——可浏览官方 Skill 索引了解可获取的能力。"
"""新用户空状态文案（[story-10](../../../docs/PRD-v2-Agent/story-10-skill-sharing.md) 空状态）。"""

ECOSYSTEM_BOUNDARY: tuple[str, ...] = (
    "不做中央 Skill 商店（不做产品方托管的上传 / 下载 / 评分 / 评论服务端）",
    "不做付费 Skill 交易",
    "不做 Skill 自动更新订阅（重新导入由用户手动发起）",
    "社区互动（评分、讨论）走外部渠道；产品只保证导入导出与安全校验能力",
)
"""生态边界「不做」清单（[09 §6](../../../docs/技术架构-v2/09-生态与分享.md) 逐条对应，措辞中性）。"""

IndexEntryKind = Literal["pack_update", "community_skill"]
"""索引条目的两类（09 §4）：官方 Pack 更新信息 / 认证的社区 Skill 推荐。"""


class IndexEntry(BaseModel):
    """索引里的一条只读条目（[09 §4](../../../docs/技术架构-v2/09-生态与分享.md)）。

    - `community_skill`：**外部下载地址与校验和必填**——受赠方靠它们定位与核对文件
    - `pack_update`：经官方渠道分发，下载地址可选（有则展示，无则只给版本与变更说明）
    """

    model_config = ConfigDict(frozen=True)

    kind: IndexEntryKind
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    """条目版本（[01 §9](../../../docs/技术架构-v2/01-平台共享契约.md) 主.次语义）。"""
    download_url: str | None = None
    """外部下载地址（只作展示与指引；本模块**不**取用）。"""
    checksum: str | None = None
    """分享文件校验和（sha256 hex 64；与 [`eco.checksum`](checksum.py) 同一算法）。"""
    changelog: str = ""
    impact: str = ""

    @model_validator(mode="after")
    def _download_pair_required_for_community(self) -> "IndexEntry":
        if self.kind != "community_skill":
            return self
        if not self.download_url:
            raise ShareIndexError(
                f"认证社区 Skill 条目 {self.name!r} 缺外部下载地址——"
                "09 §4 要求给出「描述 + 外部下载地址 + 校验和」"
            )
        if not self.checksum or not CHECKSUM_PATTERN.match(self.checksum):
            raise ShareIndexError(
                f"认证社区 Skill 条目 {self.name!r} 的校验和缺失或形态非法"
                "（须为 sha256 十六进制 64 位，与分享物校验和同一算法）"
            )
        return self


class IndexDocument(BaseModel):
    """索引文档（拉回来的那一份；[09 §4](../../docs/技术架构-v2/09-生态与分享.md)）。"""

    model_config = ConfigDict(frozen=True)

    format_id: str = INDEX_FORMAT_ID
    version: str = "1.0"
    entries: tuple[IndexEntry, ...] = ()

    @model_validator(mode="after")
    def _format_id(self) -> "IndexDocument":
        if self.format_id != INDEX_FORMAT_ID:
            raise ShareIndexError(
                f"格式标识 {self.format_id!r} 不是官方 Skill 索引"
                f"（期望 {INDEX_FORMAT_ID!r}）"
            )
        return self


def has_third_party(skills: Any) -> bool:
    """Skill 库是否已含**第三方导入物**（[story-10](../../../docs/PRD-v2-Agent/story-10-skill-sharing.md) 空状态判据）。

    :param skills: `SkillRegistry`（鸭子类型，只需 `list_all`）
    """
    try:
        return any(d.source == "imported" for d in skills.list_all())
    except AttributeError as exc:  # pragma: no cover - 注入面形态不符
        raise ShareIndexError(f"Skill 库读面不可用（需 list_all）：{exc}") from exc


class OfficialIndex:
    """官方 Skill 索引的只读浏览面（[09 §4](../../../docs/技术架构-v2/09-生态与分享.md)）。

    :param gateway: [`EgressGateway`](../l0/net/gateway.py)（唯一出网出口与审计点）
    :param host: 索引端点的主机（只登记主机名，不登记完整 URL，[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）
    :param fetch: 取值面——返回索引文档**文本**的零参可调用（真实 HTTP 实现由装配方注入；
        本层不内置任何端点，故未注入即显式失败）
    :param timeout_ms: 单次请求超时

    未注入 `gateway` / `fetch` ⇒ `browse()` 显式 `ShareIndexError`（不静默返回空索引）。
    """

    def __init__(
        self,
        *,
        gateway: Any = None,
        host: str = "",
        fetch: Callable[[], str] | None = None,
        timeout_ms: int = 60_000,
    ) -> None:
        self._gateway = gateway
        self._host = host
        self._fetch = fetch
        self._timeout_ms = timeout_ms

    def browse(self) -> tuple[IndexEntry, ...]:
        """拉取并解析索引（**只读**；经网关以 `kind=index_browse` 留痕）。

        :raises ShareIndexError: 接线缺失 / 网关判不可用 / 文档非法
        """
        if self._gateway is None:
            raise ShareIndexError(
                "未注入 EgressGateway——索引浏览须经 L0 出网审计网关（02 §6），不另开出口"
            )
        if self._fetch is None:
            raise ShareIndexError(
                "未配置索引端点取值面（fetch）——本层不内置端点，无法浏览索引"
            )
        holder: dict[str, str] = {}

        def sender(_kind: NetworkRequestKind, _host: str, _timeout_ms: int) -> tuple[
            int, int, Iterable[str]
        ]:
            text = self._fetch()  # type: ignore[misc]
            holder["body"] = text
            return (0, len(text.encode("utf-8")), [text])

        envelope = self._gateway.execute(
            "index_browse",
            self._host,
            initiator=INDEX_INITIATOR,
            purpose=INDEX_PURPOSE,
            timeout_ms=self._timeout_ms,
            sender=sender,
        )
        if envelope.status != "ok":
            raise ShareIndexError(
                f"官方索引不可用（{envelope.status}）：{envelope.reason}——"
                "索引浏览经 L0 出网审计网关，离线或无端点时不返回半截索引"
            )
        return self._parse(holder.get("body", ""))

    @staticmethod
    def _parse(text: str) -> tuple[IndexEntry, ...]:
        try:
            payload: Any = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise ShareIndexError(f"索引文档不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ShareIndexError(f"索引文档须为 JSON 对象，得到 {type(payload).__name__}")
        try:
            document = IndexDocument(**payload)
        except ValidationError as exc:
            raise ShareIndexError(f"索引文档结构非法：{exc}") from exc
        return document.entries
