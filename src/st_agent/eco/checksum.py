"""分享物容器的校验和口径（[09 §1](../../../docs/技术架构-v2/09-生态与分享.md) 的 `manifest.checksum`）。

**算法**：sha256，十六进制 64 位；**基**＝把 `manifest.checksum` 置空后的
`{header, payload, manifest}` 规范化 JSON。三点理由：

1. **避开自指**——校验和住在 manifest 里，若把它自己算进被摘要的内容就成循环；
   置空即得一个稳定基。
2. **任一字节的改动都会被查出**——header、payload、manifest 的其余字段全在基里，
   不只覆盖载荷（GWT-4 的「改动载荷任一处」）。
3. **与格式细节解耦**——基是**规范化** JSON（`sort_keys` + 紧凑分隔符 + 不转义非
   ASCII），故缩进 / 键序 / 转义方式的差异不改变校验和，内容变才变。

**与 L2 的口径同一**：[`ImportOrigin.checksum`](../../l2/memory/models.py) 记的就是
「分享**文件**校验和（sha256 十六进制 64 位，09 §1 manifest）」——即本模块产出的这个
数。导入侧（校验和复核）与导出侧必须同一份实现，故全平台只此一处，不另造副本。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from st_agent.eco.errors import ShareFormatError

__all__ = [
    "CHECKSUM_PATTERN",
    "checksum_basis",
    "container_checksum",
    "sha256_hex",
    "split_document",
]

CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
"""校验和形态（sha256 十六进制小写 64 位；与 L2 `_CHECKSUM_PATTERN` 同口径）。"""

_DOCUMENT_SEGMENTS = ("header", "payload", "manifest")


def sha256_hex(data: bytes) -> str:
    """十六进制小写 sha256（64 位）。"""
    return hashlib.sha256(data).hexdigest()


def checksum_basis(
    header: Mapping[str, Any], payload: Any, manifest: Mapping[str, Any]
) -> bytes:
    """校验和的基（规范化 JSON 字节）——`manifest.checksum` 先置空再摘要。"""
    basis = {
        "header": dict(header),
        "payload": payload,
        "manifest": {**dict(manifest), "checksum": ""},
    }
    return json.dumps(
        basis, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def split_document(blob: bytes) -> tuple[dict, Any, dict]:
    """把容器字节读成 ``(header, payload, manifest)`` 三段（**只验段的有无**，不建模型）。

    非 JSON / 非对象 / 缺段即 `ShareFormatError`——这一层先挡住「根本不是本族容器」
    （如 [02 §8.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的备份归档：它是 JSON，
    但没有 `header` 段），模型层的结构校验另由 :mod:`st_agent.eco.container` 承担。
    """
    try:
        doc = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ShareFormatError(f"分享物容器不是合法 JSON（{exc}）") from exc
    if not isinstance(doc, dict):
        raise ShareFormatError("分享物容器的外层须为 JSON 对象")
    missing = [k for k in _DOCUMENT_SEGMENTS if k not in doc]
    if missing:
        raise ShareFormatError(
            f"分享物容器缺段 {missing}（须含 {list(_DOCUMENT_SEGMENTS)}）"
            "——若这是备份归档，两者格式同族但互不通用（09 §1 / 02 §8.1）"
        )
    header, manifest = doc["header"], doc["manifest"]
    if not isinstance(header, dict) or not isinstance(manifest, dict):
        raise ShareFormatError("分享物容器的 header / manifest 须为 JSON 对象")
    return header, doc["payload"], manifest


def container_checksum(blob: bytes) -> str:
    """由容器字节**重算**校验和（复核用：与 `manifest.checksum` 比对）。"""
    header, payload, manifest = split_document(blob)
    return sha256_hex(checksum_basis(header, payload, manifest))
