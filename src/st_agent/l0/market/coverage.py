"""逐码覆盖账本与缺口计划（[D-132]）。

覆盖行记录「某数据集对某只码已成功取过的区间与指标组」；计划按
「目标单元 − 已覆盖单元」生成请求窗口，近期按刷新视界重拉，``force=True``
时忽略账本。全局水位仍是新鲜度锚点，本模块只服务取数计划。

单元格式：``day`` → ``YYYY-MM-DD``；``quarter`` → ``YYYYQn``；``year`` → ``YYYY``。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from st_agent.l0.market.datasets import normalize_groups

__all__ = [
    "CoverageBook",
    "canonical_groups",
    "record_rows",
]

SOURCE_ID = "baostock"


def canonical_groups(dataset_id: str, groups: Iterable[str] | None) -> str:
    """规范指标组串（排序、逗号分隔；无组数据集为 ``all``）。"""
    return ",".join(sorted(normalize_groups(dataset_id, list(groups) if groups else None)))


def _covers(have: str, want: str) -> bool:
    """已覆盖组是否包含请求组（``all`` 视为全集）。"""
    if want in ("", "all") or have == "all":
        return True
    have_set = {g for g in have.split(",") if g}
    want_set = {g for g in want.split(",") if g}
    return want_set <= have_set


def _merge_days(days: list[str], merge_gap: int) -> list[tuple[str, str]]:
    """把日期列表合并成连续区间；相邻间隔 ≤ ``merge_gap`` 日历天则合并。"""
    if not days:
        return []
    days = sorted(dict.fromkeys(days))
    spans: list[tuple[str, str]] = []
    start = prev = days[0]
    for current in days[1:]:
        if date.fromisoformat(current) <= date.fromisoformat(prev) + timedelta(days=merge_gap):
            prev = current
            continue
        spans.append((start, prev))
        start = prev = current
    spans.append((start, prev))
    return spans


def _quarter_index(value: str) -> int:
    year, _, quarter = value.partition("Q")
    return int(year) * 4 + int(quarter)


def _year_index(value: str) -> int:
    return int(value)


def _unit_index(unit: str, value: str) -> int:
    return _quarter_index(value) if unit == "quarter" else _year_index(value)


def _merge_units(units: list[str], unit: str) -> list[tuple[str, str]]:
    """把季度/年份列表合并成连续区间（相邻单元合并）。"""
    if not units:
        return []
    values = sorted(dict.fromkeys(units), key=lambda v: _unit_index(unit, v))
    spans: list[tuple[str, str]] = []
    start = prev = values[0]
    for current in values[1:]:
        if _unit_index(unit, current) <= _unit_index(unit, prev) + 1:
            prev = current
            continue
        spans.append((start, prev))
        start = prev = current
    spans.append((start, prev))
    return spans


@dataclass
class CoverageBook:
    """一次只读连接内的覆盖查询（按数据集 + 变体 + 请求组）。"""

    dataset_id: str
    variant: str = ""
    groups: str = "all"
    rows: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    @classmethod
    def load(
        cls, con: sqlite3.Connection, dataset_id: str, *,
        variant: str = "", groups: Iterable[str] | None = None,
    ) -> "CoverageBook":
        wanted = canonical_groups(dataset_id, groups)
        book = cls(dataset_id, variant, wanted)
        try:
            rows = con.execute(
                "SELECT code, span_start, span_end, groups FROM sync_coverage "
                "WHERE dataset_id=? AND variant=? AND status='ok'",
                (dataset_id, variant),
            ).fetchall()
        except sqlite3.OperationalError:
            return book
        for code, start, end, have in rows:
            if _covers(str(have), wanted):
                book.rows.setdefault(str(code), []).append((str(start), str(end)))
        return book

    def covered(self, code: str) -> list[tuple[str, str]]:
        return sorted(self.rows.get(code, ()))

    def missing_days(
        self, code: str, days: list[str], *,
        merge_gap: int = 7, refresh_days: int = 0, force: bool = False,
    ) -> list[tuple[str, str]]:
        """目标交易日 − 已覆盖日；近期 ``refresh_days`` 个交易日始终重拉。"""
        if not days:
            return []
        if force:
            return _merge_days(list(days), merge_gap)
        spans = self.covered(code)
        index = 0
        missing: list[str] = []
        for day in sorted(dict.fromkeys(days)):
            while index < len(spans) and spans[index][1] < day:
                index += 1
            if index < len(spans) and spans[index][0] <= day <= spans[index][1]:
                continue
            missing.append(day)
        if refresh_days > 0:
            missing.extend(sorted(dict.fromkeys(days))[-refresh_days:])
        return _merge_days(missing, merge_gap)

    def missing_units(
        self, code: str, units: list[str], *,
        unit: str, refresh_units: int = 0, force: bool = False,
    ) -> list[str]:
        """目标季度/年份 − 已覆盖单元；近期 ``refresh_units`` 个单元始终重拉。"""
        if not units:
            return []
        values = sorted(dict.fromkeys(units), key=lambda v: _unit_index(unit, v))
        if force:
            return values
        spans = self.covered(code)
        missing = [
            value for value in values
            if not any(
                _unit_index(unit, start) <= _unit_index(unit, value) <= _unit_index(unit, end)
                for start, end in spans
            )
        ]
        if refresh_units > 0:
            missing.extend(values[-refresh_units:])
        return sorted(dict.fromkeys(missing), key=lambda v: _unit_index(unit, v))

    def missing_range(
        self, code: str, start: str, end: str, *,
        refresh_start: str | None = None, force: bool = False,
    ) -> list[tuple[str, str]]:
        """日期区间 − 已覆盖区间；``refresh_start`` 起的近期区间始终重拉。"""
        if force:
            return [(start, end)]
        spans = sorted(self.covered(code))
        missing: list[tuple[str, str]] = []
        cursor = start
        for span_start, span_end in spans:
            if span_end < cursor:
                continue
            if span_start > end:
                break
            if span_start > cursor:
                missing.append((cursor, _previous_day(span_start)))
            cursor = max(cursor, _next_day(span_end))
            if cursor > end:
                break
        if cursor <= end:
            missing.append((cursor, end))
        if refresh_start is not None and refresh_start <= end:
            missing.append((max(refresh_start, start), end))
        return _merge_ranges(missing)


def _next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _previous_day(value: str) -> str:
    return (date.fromisoformat(value) - timedelta(days=1)).isoformat()


def _merge_ranges(spans: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """合并重叠/相邻日期区间（用于缺口计划，不用于覆盖写回）。"""
    if not spans:
        return []
    spans = sorted(spans)
    merged: list[tuple[str, str]] = [spans[0]]
    for start, end in spans[1:]:
        prev_start, prev_end = merged[-1]
        if date.fromisoformat(start) <= date.fromisoformat(prev_end) + timedelta(days=1):
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def record_rows(con: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """写入覆盖行并按同组相邻区间合并；返回写入的原始行数。

    行形如 ``{code, dataset_id, variant, unit, span_start, span_end, groups,
    status, source_id, checked_at}``。只对 ``status='ok'`` 的行做合并。
    """
    rows = list(rows)
    if not rows:
        return 0
    con.executemany(
        "INSERT INTO sync_coverage(code, dataset_id, variant, unit, span_start,"
        " span_end, groups, status, source_id, checked_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(code, dataset_id, variant, unit, span_start, span_end, groups) "
        "DO UPDATE SET status=excluded.status, source_id=excluded.source_id,"
        " checked_at=excluded.checked_at",
        [
            (r["code"], r["dataset_id"], r.get("variant", ""), r["unit"],
             r["span_start"], r["span_end"], r["groups"], r.get("status", "ok"),
             r.get("source_id", SOURCE_ID), r.get("checked_at") or _now_iso())
            for r in rows
        ],
    )
    _compact(con, rows)
    return len(rows)


def _compact(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    """同 code/dataset/variant/unit/groups 的 ``ok`` 区间合并（严格相邻/重叠）。"""
    keys = {
        (r["code"], r["dataset_id"], r.get("variant", ""), r["unit"], r["groups"])
        for r in rows
    }
    for code, dataset_id, variant, unit, groups in keys:
        try:
            existing = con.execute(
                "SELECT span_start, span_end FROM sync_coverage WHERE code=? "
                "AND dataset_id=? AND variant=? AND unit=? AND groups=? "
                "AND status='ok'",
                (code, dataset_id, variant, unit, groups),
            ).fetchall()
        except sqlite3.OperationalError:
            return
        if len(existing) < 2:
            continue
        if unit == "day":
            merged = _merge_days(
                [d for start, end in existing
                 for d in _expand_days(start, end)],
                merge_gap=1,
            )
        else:
            merged = _merge_units(
                [u for start, end in existing
                 for u in _expand_units(start, end, unit)],
                unit,
            )
        con.execute(
            "DELETE FROM sync_coverage WHERE code=? AND dataset_id=? AND variant=? "
            "AND unit=? AND groups=? AND status='ok'",
            (code, dataset_id, variant, unit, groups),
        )
        con.executemany(
            "INSERT INTO sync_coverage(code, dataset_id, variant, unit, span_start,"
            " span_end, groups, status, source_id, checked_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?)",
            [(code, dataset_id, variant, unit, start, end, groups, SOURCE_ID,
              _now_iso()) for start, end in merged],
        )


def _expand_days(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [
        (first + timedelta(days=i)).isoformat()
        for i in range((last - first).days + 1)
    ]


def _expand_units(start: str, end: str, unit: str) -> list[str]:
    if unit == "year":
        return [str(v) for v in range(int(start), int(end) + 1)]
    first, last = _quarter_index(start), _quarter_index(end)
    out: list[str] = []
    for index in range(first, last + 1):
        year = (index - 1) // 4
        quarter = (index - 1) % 4 + 1
        out.append(f"{year}Q{quarter}")
    return out


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
