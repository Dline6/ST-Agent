"""同步标的注册表与解析（[D-131] / [D-132]）。

标的只能在 L0 解析：UI 不直接读库、不自己拼 SQL。本模块把「全部 A 股 / ST /
三大宽基 / 关注池 / 自定义代码 / 其他」统一解析成代码集与可用性，
供 ``DataSyncRunner`` 的预检与批次计划消费。

ST 同步池取 ``v_st_sync_pool``（``bs_st_list`` 的名称快照），与分析区的
``v_st_universe``（日线 ``is_st``）是两套口径，不互相冒充。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from st_agent.l0.market.codes import parse_custom_codes

__all__ = [
    "TARGETS",
    "TARGET_IDS",
    "TargetPool",
    "TargetSpec",
    "resolve_target",
    "resolve_targets",
]


@dataclass(frozen=True)
class TargetSpec:
    """一个可选标的（展示与解析能力的单一登记项）。"""

    target_id: str
    label: str
    kind: str
    description: str = ""


TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("all_a", "全部 A 股", "codes", "在市股票（type=1、status=1）"),
    TargetSpec("st", "ST 股票", "codes", "BaoStock 名称匹配名单，快照时点为准"),
    TargetSpec("hs300", "沪深300", "codes", "最新一期成分"),
    TargetSpec("sz50", "上证50", "codes", "最新一期成分"),
    TargetSpec("zz500", "中证500", "codes", "最新一期成分"),
    TargetSpec("watchlist", "关注池", "codes", "持仓 + 关注"),
    TargetSpec("custom", "自定义代码", "codes", "粘贴或文件导入"),
    TargetSpec("other", "其他", "other", "全市场 / 快照 / 信息面"),
)

TARGET_IDS: tuple[str, ...] = tuple(t.target_id for t in TARGETS)
_SPECS = {t.target_id: t for t in TARGETS}


@dataclass(frozen=True)
class TargetPool:
    """一次标的解析结果（可用性三态与来源时间显式）。"""

    target_id: str
    label: str
    codes: tuple[str, ...] = ()
    count: int = 0
    available: bool = False
    as_of: str | None = None
    source: str = ""
    reason: str = ""
    invalid: tuple[dict[str, str], ...] = ()
    duplicates: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "label": self.label,
            "count": self.count,
            "available": self.available,
            "as_of": self.as_of,
            "source": self.source,
            "reason": self.reason,
            "invalid": list(self.invalid),
            "duplicates": list(self.duplicates),
        }


def resolve_targets(
    db: Any,
    *,
    watchlist: Callable[[], tuple[str, ...]] | None = None,
    custom_text: str = "",
) -> dict[str, TargetPool]:
    """解析全部标的（预检/总览共用；只读，不触网）。"""
    custom = parse_custom_codes(custom_text)
    return {
        "all_a": _all_a(db),
        "st": _st(db),
        "hs300": _index(db, "hs300"),
        "sz50": _index(db, "sz50"),
        "zz500": _index(db, "zz500"),
        "watchlist": _watchlist(watchlist),
        "custom": _custom(custom),
        "other": TargetPool(
            "other", "其他", available=True, source="本地",
            reason="该组只承载全市场 / 快照 / 信息面数据集",
        ),
    }


def resolve_target(
    db: Any,
    target_id: str,
    *,
    watchlist: Callable[[], tuple[str, ...]] | None = None,
    custom_text: str = "",
) -> TargetPool:
    """解析单个标的；未知 id → ``MarketValidationError``。"""
    from st_agent.l0.market.errors import MarketValidationError

    if target_id not in _SPECS:
        raise MarketValidationError(
            f"未知同步标的 {target_id!r}；合法值 = {list(TARGET_IDS)}"
        )
    return resolve_targets(db, watchlist=watchlist, custom_text=custom_text)[target_id]


def _all_a(db: Any) -> TargetPool:
    if not db.exists():
        return TargetPool("all_a", "全部 A 股", available=False,
                          reason="本地市场数据库尚未建立")
    try:
        with db.connect() as con:
            state = con.execute(
                "SELECT last_status FROM sync_state WHERE task_key='bs_security_basic'"
            ).fetchone()
            if state is None or state[0] != "ok":
                return TargetPool(
                    "all_a", "全部 A 股", available=False,
                    reason="证券主档尚未完成全量同步（先同步证券主档）",
                )
            codes = tuple(
                r[0] for r in con.execute(
                    "SELECT code FROM security WHERE type=1 AND status=1 "
                    "ORDER BY code"
                ).fetchall()
            )
    except sqlite3.OperationalError as exc:
        return TargetPool("all_a", "全部 A 股", available=False,
                          reason=f"证券主档读取失败：{exc}")
    return TargetPool(
        "all_a", "全部 A 股", codes, len(codes), True,
        source="security（在市）",
        reason="" if codes else "证券主档为空（先同步证券主档）",
    )


def _st(db: Any) -> TargetPool:
    pool = db.st_sync_pool() if hasattr(db, "st_sync_pool") else {}
    codes = tuple(pool.get("codes") or ())
    return TargetPool(
        "st", "ST 股票", codes, int(pool.get("count") or len(codes)),
        bool(pool.get("available")), pool.get("as_of"),
        source=pool.get("source") or "BaoStock 名称匹配（快照）",
        reason=pool.get("reason") or "",
    )


def _index(db: Any, index_key: str) -> TargetPool:
    label = _SPECS[index_key].label
    if not db.exists():
        return TargetPool(index_key, label, available=False,
                          reason="本地市场数据库尚未建立")
    try:
        with db.connect() as con:
            row = con.execute(
                "SELECT max(update_date) FROM index_constituent WHERE index_key=?",
                (index_key,),
            ).fetchone()
            as_of = row[0] if row and row[0] else None
            codes = tuple(
                r[0] for r in con.execute(
                    "SELECT code FROM index_constituent WHERE index_key=? "
                    "AND update_date=(SELECT max(update_date) FROM index_constituent "
                    "WHERE index_key=?) ORDER BY code",
                    (index_key, index_key),
                ).fetchall()
            )
    except sqlite3.OperationalError as exc:
        return TargetPool(index_key, label, available=False,
                          reason=f"成分快照读取失败：{exc}")
    return TargetPool(
        index_key, label, codes, len(codes), bool(codes), as_of,
        source=f"index_constituent/{index_key}",
        reason="" if codes else "成分快照为空（先同步该指数成分）",
    )


def _watchlist(fn: Callable[[], tuple[str, ...]] | None) -> TargetPool:
    if fn is None:
        return TargetPool("watchlist", "关注池", available=False,
                          reason="关注面未注入")
    try:
        codes = tuple(dict.fromkeys(str(c).strip() for c in fn() if str(c).strip()))
    except Exception as exc:  # noqa: BLE001 — 关注面读不出按空集，原因显式
        return TargetPool("watchlist", "关注池", available=False,
                          reason=f"关注面读取失败：{exc}")
    return TargetPool(
        "watchlist", "关注池", codes, len(codes), bool(codes),
        source="持仓 + 关注",
        reason="" if codes else "关注池为空（持仓 + 关注均无证券）",
    )


def _custom(parsed: Mapping[str, Any]) -> TargetPool:
    codes = tuple(parsed.get("codes") or ())
    invalid = tuple(parsed.get("invalid") or ())
    duplicates = tuple(parsed.get("duplicates") or ())
    reasons: list[str] = []
    if invalid:
        reasons.append(f"{len(invalid)} 行格式非法")
    if not codes:
        reasons.append("没有合法代码")
    return TargetPool(
        "custom", "自定义代码", codes, len(codes), bool(codes),
        source="粘贴 / 文件导入",
        reason="；".join(reasons),
        invalid=invalid, duplicates=duplicates,
    )
