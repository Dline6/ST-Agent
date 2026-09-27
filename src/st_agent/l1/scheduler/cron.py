"""cron 表达式求值（T-L1-005.1；03 §6 ``schedule.mode=cron``）。

**最小 5 段子集**：``分 时 日 月 周``（不认秒段、不认 ``@daily`` 之类的宏、
不认 ``JAN``/``MON`` 之类的名字——最小子集的边界写在这里，不靠猜）。
每段支持：

- ``*``（全域）
- ``a``（单值）
- ``a-b``（区间，起 > 止即拒）
- ``*/n`` 与 ``a-b/n``（步长，``n ≥ 1``）
- 上述原子用 ``,`` 串起的并集（如 ``1,5-10,*/20``）

取值闭区间：分 ``0-59``、时 ``0-23``、日 ``1-31``、月 ``1-12``、周 ``0-6``
（``0`` 即周日；``7`` 作为周日的别名收下）。**日与周同段语义**沿用 cron 惯例：
两者**都**受限时按「或」匹配，只其一受限时按「与」匹配。

.. warning::
   本件是**求值件**，不是调度器：不判到期、不落状态、不触发执行。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, model_validator

from st_agent.l1.scheduler.errors import CronSyntaxError
from st_agent.l1.scheduler.models import check_aware

__all__ = [
    "MAX_SEARCH_DAYS",
    "CronSpec",
]

#: 五段及其取值闭区间：``(段名, 下界, 上界)``
FIELDS: tuple[tuple[str, int, int], ...] = (
    ("分", 0, 59), ("时", 0, 23), ("日", 1, 31), ("月", 1, 12), ("周", 0, 6),
)

MAX_SEARCH_DAYS = 366 * 5
"""``next_after`` 的搜索上限（天）——5 年足以覆盖 ``2 月 29 日`` 这类最稀疏的合法式。"""


def _value(text: str, low: int, high: int, name: str, atom: str,
           *, sunday_alias: bool = False) -> int:
    """解析一个单值原子（非数字 / 越界 → ``CronSyntaxError``）。"""
    if not text.isdigit():
        raise CronSyntaxError(f"{name}段原子 {atom!r} 含非数字取值 {text!r}（本子集不认名字与宏）")
    value = int(text)
    if sunday_alias and value == 7:
        value = 0
    if value < low or value > high:
        raise CronSyntaxError(f"{name}段原子 {atom!r} 取值 {value} 越界（合法区间 {low}-{high}）")
    return value


def _step(text: str, name: str, atom: str) -> int:
    """解析步长（非数字 / 0 → ``CronSyntaxError``）。"""
    if not text.isdigit() or int(text) == 0:
        raise CronSyntaxError(f"{name}段原子 {atom!r} 的步长 {text!r} 须为 ≥1 的整数")
    return int(text)


def _parse_field(text: str, low: int, high: int, name: str,
                 *, sunday_alias: bool = False) -> tuple[frozenset[int], bool]:
    """解析一段 → ``(取值集合, 是否由裸 ``*`` 覆盖全域)``。"""
    if not text:
        raise CronSyntaxError(f"{name}段不得为空")
    values: set[int] = set()
    star = False
    for atom in text.split(","):
        if not atom:
            raise CronSyntaxError(f"{name}段含空原子：{text!r}")
        body, _, step_text = atom.partition("/")
        step = _step(step_text, name, atom) if "/" in atom else 1
        if body == "*":
            start, end = low, high
            star = True
        else:
            bounds = body.split("-")
            if len(bounds) == 1:
                start = end = _value(bounds[0], low, high, name, atom,
                                     sunday_alias=sunday_alias)
            elif len(bounds) == 2:
                start = _value(bounds[0], low, high, name, atom,
                               sunday_alias=sunday_alias)
                end = _value(bounds[1], low, high, name, atom,
                             sunday_alias=sunday_alias)
            else:
                raise CronSyntaxError(f"{name}段原子 {atom!r} 形态不识别（区间至多一个 -）")
            if start > end:
                raise CronSyntaxError(f"{name}段原子 {atom!r} 区间起止倒置")
        values.update(range(start, end + 1, step))
    return frozenset(values), star


class CronSpec(BaseModel):
    """一个已解析的 cron 表达式（最小 5 段子集）。

    :param expression: 归一化后的表达式原文（空白收成单空格）
    """

    model_config = ConfigDict(frozen=True)

    expression: str
    minute: frozenset[int]
    hour: frozenset[int]
    day_of_month: frozenset[int]
    month: frozenset[int]
    day_of_week: frozenset[int]
    dom_restricted: bool
    """日段是否受限（裸 ``*`` 即不受限）——与周段共同决定「或 / 与」语义。"""
    dow_restricted: bool

    @model_validator(mode="after")
    def _non_empty(self) -> "CronSpec":
        for label, values in (("分", self.minute), ("时", self.hour),
                              ("日", self.day_of_month), ("月", self.month),
                              ("周", self.day_of_week)):
            if not values:
                raise CronSyntaxError(f"{label}段的取值集合为空")
        return self

    # ───────────────────────── 构造 ─────────────────────────

    @classmethod
    def parse(cls, expression: str) -> "CronSpec":
        """解析表达式（非法 → ``CronSyntaxError``）。

        只收**恰好 5 段**；段数与取值域的边界即本件声明的支持面。
        """
        if not isinstance(expression, str):
            raise CronSyntaxError(f"cron 表达式须为字符串，收到 {type(expression).__name__}")
        parts = expression.split()
        if len(parts) != len(FIELDS):
            raise CronSyntaxError(
                f"cron 表达式须为 {len(FIELDS)} 段（分 时 日 月 周），"
                f"收到 {len(parts)} 段：{expression!r}"
            )
        parsed = [
            _parse_field(text, low, high, name,
                         sunday_alias=(name == "周"))
            for text, (name, low, high) in zip(parts, FIELDS)
        ]
        return cls(
            expression=" ".join(parts),
            minute=parsed[0][0], hour=parsed[1][0], day_of_month=parsed[2][0],
            month=parsed[3][0], day_of_week=parsed[4][0],
            dom_restricted=not parsed[2][1], dow_restricted=not parsed[4][1],
        )

    # ───────────────────────── 求值 ─────────────────────────

    def matches(self, moment: datetime) -> bool:
        """该时刻是否命中（时刻须带时区；精确到分钟，秒与微秒不参与判定）。"""
        check_aware(moment, label="待判定时刻")
        if moment.minute not in self.minute or moment.hour not in self.hour:
            return False
        if moment.month not in self.month:
            return False
        return self._day_hit(moment.date())

    def next_after(self, moment: datetime) -> datetime | None:
        """**严格晚于** ``moment`` 的下一个命中时刻（无 → ``None``）。

        以分钟为步长语义：结果落在整分。搜索上限 ``MAX_SEARCH_DAYS`` 天。
        """
        check_aware(moment, label="起点时刻")
        start = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        tzinfo = moment.tzinfo
        day = start.date()
        for _ in range(MAX_SEARCH_DAYS + 1):
            if day.month in self.month and self._day_hit(day):
                for hour in sorted(self.hour):
                    for minute in sorted(self.minute):
                        candidate = datetime(day.year, day.month, day.day,
                                             hour, minute, tzinfo=tzinfo)
                        if candidate >= start:
                            return candidate
            day += timedelta(days=1)
        return None

    # ───────────────────────── 内部 ─────────────────────────

    def _day_hit(self, day: date) -> bool:
        """日 / 周段的命中判定（两者都受限时按「或」，只其一受限时按「与」）。"""
        dom_hit = day.day in self.day_of_month
        dow_hit = (day.weekday() + 1) % 7 in self.day_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom_hit or dow_hit
        if self.dom_restricted:
            return dom_hit
        if self.dow_restricted:
            return dow_hit
        return True
