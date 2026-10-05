"""生态与分享（ECO 层）——[09-生态与分享](../../../docs/技术架构-v2/09-生态与分享.md)。

本地优先约束下不做中央商店服务端（[story-10](../../../docs/PRD-v2-Agent/story-10-skill-sharing.md)）：
分享 = **本地文件**。本包交付其中两段（[`T-ECO-001`](../../../项目管理/tasks/T-ECO-001-分享物类型格式导出流程来源追溯链.md)）：

- **统一容器**（[09 §1](../../../docs/技术架构-v2/09-生态与分享.md)）：四类分享物
  `.stskill` / `.stflow` / `.stlens` / `.stmem` 同构的 `header` / `payload` / `manifest`
  三段，明文可检视、与备份归档互不通用——[:mod:`container`](container.py) +
  [:mod:`checksum`](checksum.py)
- **导出流程**（[09 §2](../../../docs/技术架构-v2/09-生态与分享.md)）与**出处链**（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md)）：
  从四个既有门面取材、导出前过中性化校验、`.stmem` 走强制三步——[:mod:`export`](export.py) +
  [:mod:`card`](card.py)（分享卡片）

导入校验流水线、官方 Skill 索引与生态边界（09 §3 / §4 / §6）归
[`T-ECO-002`](../../../项目管理/tasks/T-ECO-002-导入校验流水线官方Skill索引生态边界.md)。

层次：`eco` 是层表的最上层（[`tests/test_layering.py`](../../../tests/test_layering.py)
的 `LAYER_ORDER`），只向下消费契约与各层，不被任何层依赖。
"""

from st_agent.eco.card import ShareCard
from st_agent.eco.checksum import container_checksum
from st_agent.eco.container import (
    CONTAINER_VERSION,
    PAYLOAD_MODELS,
    SHARE_EXTENSIONS,
    SHARE_FORMAT_ID,
    SHARE_KINDS,
    SUPPORTED_MAJOR,
    ShareContainer,
    ShareHeader,
    ShareKind,
    ShareManifest,
    extension_for,
    kind_of,
)
from st_agent.eco.errors import (
    EcoError,
    ShareExportError,
    ShareFormatError,
    ShareVersionError,
)
from st_agent.eco.export import ShareExporter, appended_chain

__all__ = [
    "CONTAINER_VERSION",
    "PAYLOAD_MODELS",
    "SHARE_EXTENSIONS",
    "SHARE_FORMAT_ID",
    "SHARE_KINDS",
    "SUPPORTED_MAJOR",
    "EcoError",
    "ShareCard",
    "ShareContainer",
    "ShareExportError",
    "ShareExporter",
    "ShareFormatError",
    "ShareHeader",
    "ShareKind",
    "ShareManifest",
    "ShareVersionError",
    "appended_chain",
    "container_checksum",
    "extension_for",
    "kind_of",
]
