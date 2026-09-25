"""数据源缓存清洗层（数据库设计 00 全局规则 + 06 逐接口映射）。

BaoStock 全接口返回**字符串**；数值列落库前转 ``REAL``/``INTEGER``，
日期归一为 ``YYYY-MM-DD``。本模块把「空串→NULL、`—` 负号、`或`多值取首值、
不截断精度」的清洗规则固化为单一入口，同步管道与测试共用同一口径。

- ``clean_str``：空串/全空白 → ``None``（含 ``"nan"``/``"None"`` 哨兵容错）
- ``clean_num``：``—`` 前缀替换为 ``-`` 后转浮点；``或``多值取第一个数值；
  空 → ``None``（**入库不做四舍五入**，保留源精度）
- ``clean_int``：同上转整数（类型/状态/交易日类字段）
- ``normalize_minute_ts``：分钟线 ``time``（``YYYYMMDDHHMMSSsss``）→
  ``YYYY-MM-DD HH:MM:SS``（截去毫秒）
"""

from __future__ import annotations

import re

__all__ = [
    "clean_int",
    "clean_num",
    "clean_str",
    "normalize_date",
    "normalize_minute_ts",
]

_DASHES = ("—", "–", "－")
"""源数据中出现过的前导负号变体（00 全局清洗规则）。"""

_NULL_TOKENS = {"", "nan", "none", "null", "nat"}
"""转小写后视为缺失的字符串哨兵。"""


def _is_null_token(raw: str) -> bool:
    return raw.strip().lower() in _NULL_TOKENS


def _first_numeric_token(raw: str) -> str:
    """「或」多值取第一个数值（00 规则固定不可配置）。

    例：``"0.6813或0.71915"`` → ``"0.6813"``。
    """
    head = raw.split("或")[0]
    # 描述文本中可能夹杂单位后缀，取首个数字子串
    match = re.search(r"[+-]?(\d+(\.\d*)?|\.\d+)", head)
    return match.group(0) if match else ""


def _denormalize_sign(raw: str) -> str:
    text = raw.strip()
    for dash in _DASHES:
        if text.startswith(dash):
            return "-" + text[len(dash):]
    return text


def clean_str(raw: object) -> str | None:
    """字符串列清洗：空串/空白/哨兵 → ``None``，其余原样去首尾空白。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if _is_null_token(text):
        return None
    return text


def clean_num(raw: object) -> float | None:
    """数值列清洗：空 → ``None``；``—`` 负号归一；``或``多值取首值。

    不截断、不补零，原样落库（00 全局规则）。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if _is_null_token(text):
        return None
    text = _denormalize_sign(text)
    if "或" in text:
        text = _first_numeric_token(text)
        if not text:
            return None
    try:
        return float(text)
    except ValueError:
        token = _first_numeric_token(text)
        return float(token) if token else None


def clean_int(raw: object) -> int | None:
    """整数列清洗（类型/状态/交易日类 ``'1'``/``'0'`` 转 INTEGER）。"""
    value = clean_num(raw)
    return None if value is None else int(value)


def normalize_date(raw: object) -> str | None:
    """日期归一：``YYYY-MM-DD`` 原样（去空白）；8 位紧凑 ``YYYYMMDD`` 加横线。

    空 → ``None``。不做历法校验（合法性由 SQLite CHECK / 业务层判定）。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if _is_null_token(text):
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def normalize_minute_ts(raw: object) -> str | None:
    """分钟线 ``time`` 归一：``YYYYMMDDHHMMSSsss`` → ``YYYY-MM-DD HH:MM:SS``。

    截去毫秒（06 映射契约）；空 → ``None``。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if _is_null_token(text):
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) < 14:
        return None
    core = digits[:14]
    return (f"{core[:4]}-{core[4:6]}-{core[6:8]} "
            f"{core[8:10]}:{core[10:12]}:{core[12:14]}")
