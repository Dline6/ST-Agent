"""分享卡片（[09 §2](../../../docs/技术架构-v2/09-生态与分享.md)）。

「可选生成」的分享卡片＝**描述 + 校验和 + 下载指引**，用户自行发布到任何外部渠道
（微信群 / 论坛 / GitHub）——[09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的生态边界：
产品只保证导入导出与安全校验能力，**不经手任何数据、不内置发布通道**（卡片是纯文本，
本模块不触网）。

措辞**中性**（[01 §6](../../docs/技术架构-v2/01-平台共享契约.md) / 铁律 2）：无第一人称、
无情感、无对话体——卡片里唯一的动态部分是分享物的名称与描述，它们在导出时已过
`NeutralityGuard.check_name`（[`ShareExporter`](export.py)）。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from st_agent.eco.container import ShareContainer

__all__ = [
    "DOWNLOAD_HINT",
    "MEMORY_FRAGMENT_DESCRIPTION",
    "MEMORY_FRAGMENT_TITLE",
    "ShareCard",
]

MEMORY_FRAGMENT_TITLE = "记忆公开片段"
"""`.stmem` 的卡片标题（记忆片段本体没有名称，用固定的中性功能化说法）。"""

MEMORY_FRAGMENT_DESCRIPTION = "经隐私过滤的记忆节点与相接边"
"""`.stmem` 的卡片描述（同上）。"""

DOWNLOAD_HINT = "该文件由分享者经外部渠道提供，导入前请核对上方校验和。"
"""下载指引（中性措辞；产品不经手文件本身，[09 §2](../../docs/技术架构-v2/09-生态与分享.md)）。"""


class ShareCard(BaseModel):
    """一张分享卡片（值对象；文本形态见 :meth:`to_text`）。"""

    model_config = ConfigDict(frozen=True)

    title: str
    description: str
    checksum: Annotated[str, Field(min_length=1)]
    """容器校验和（与 `manifest.checksum` 同一个数——收件人据此核对文件）。"""
    file_name: str
    """建议文件名（注册名 / 标识 + 扩展名，见 :meth:`for_container`）。"""
    download_hint: str = DOWNLOAD_HINT

    @classmethod
    def for_container(cls, container: ShareContainer) -> "ShareCard":
        """由容器生成卡片（描述与名称取自本体；`.stmem` 用固定的中性说法）。"""
        payload = container.payload
        title = getattr(payload, "name", None)
        description = getattr(payload, "description", None)
        if not isinstance(title, str) or not title:
            title = MEMORY_FRAGMENT_TITLE
        if not isinstance(description, str) or not description:
            description = MEMORY_FRAGMENT_DESCRIPTION
        return cls(
            title=title,
            description=description,
            checksum=container.checksum(),
            file_name=f"{_stem(payload, container)}{container.extension}",
            download_hint=DOWNLOAD_HINT,
        )

    def to_text(self) -> str:
        """卡片文本（逐行：名称 / 描述 / 建议文件名 / 校验和 / 下载指引）。"""
        return "\n".join(
            (
                self.title,
                self.description,
                f"文件名：{self.file_name}",
                f"校验和：{self.checksum}",
                self.download_hint,
            )
        )


def _stem(payload: object, container: ShareContainer) -> str:
    """建议文件名的词干：优先取本体的标识（`sk_…` / `wf_…` / `lens_…`）。

    标识是 ASCII 且形态受契约约束（[01 §1](../../docs/技术架构-v2/01-平台共享契约.md)），
    故直接用作文件名是安全的；`.stmem` 无标识，用固定词干加时间戳（创建时间取自 manifest，
    故同一容器每次生成得同一名字——卡片可复现）。
    """
    for attr in ("skill_id", "flow_id", "lens_id"):
        value = getattr(payload, attr, None)
        if isinstance(value, str) and value:
            return value
    stamp = container.manifest.created_at.strftime("%Y%m%d%H%M%S")
    return f"mem_{stamp}"
