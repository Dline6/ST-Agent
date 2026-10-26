"""自定义代码输入解析（[D-132]）。

接受 ``sh.600000`` / ``sz.000001`` / ``600000`` 等写法；裸六位按号段推断交易所
（与 [`l0.info.sources.to_stock_id`](../info/sources.py) 同一口径）。非法行与重复项
显式返回，**不静默丢弃**。
"""

from __future__ import annotations

import re
from typing import Any

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import StockId
from st_agent.l0.info.sources import to_stock_id

__all__ = ["parse_custom_codes"]

_SEPARATORS = re.compile(r"[\s,，;；]+")
_BARE = re.compile(r"^\d{6}$")


def parse_custom_codes(text: str) -> dict[str, Any]:
    """把粘贴/文件文本解析成合法代码集。

    返回 ``{"codes": tuple, "invalid": tuple[dict], "duplicates": tuple}``。
    只接受六位数字或 ``<exchange>.<六位>``；交易所前缀按既有号段规则推断。
    """
    codes: list[str] = []
    invalid: list[dict[str, str]] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for raw in _SEPARATORS.split(str(text or "")):
        token = raw.strip().strip("\"'").strip()
        if not token:
            continue
        code = _normalize(token)
        if code is None:
            invalid.append({
                "value": token,
                "reason": "须为 sh./sz./bj. + 6 位数字，或 6 位数字",
            })
            continue
        if code in seen:
            if code not in duplicates:
                duplicates.append(code)
            continue
        seen.add(code)
        codes.append(code)
    return {
        "codes": tuple(codes),
        "invalid": tuple(invalid),
        "duplicates": tuple(duplicates),
    }


def _normalize(token: str) -> str | None:
    """单条输入 → ``<exchange>.<六位>``；非法返回 ``None``。"""
    if "." not in token and not _BARE.match(token):
        return None
    try:
        code = to_stock_id(token)
        StockId.of(code)
    except (ContractViolation, ValueError):
        return None
    return code
