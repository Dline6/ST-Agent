"""L5 注意力预算（[07 §2](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

**情境 × 时段 × 内容类型**三维矩阵：每个格位给出**允许单独触达的级别集合**与**节奏上限**。
本模块只做两件事——把格位答出来（:meth:`AttentionBudget.resolve`）、把矩阵读出来
（:meth:`AttentionBudget.map`）；**不做计数**（去重、合并、排队与日报本体归
[07 §5–§6](../../../docs/技术架构-v2/07-L5-主动触达.md)，[`T-L5-003`](../../../项目管理/tasks/T-L5-003-每日报告去重频控推送疲劳监控.md)），
故判定是**纯函数**：同一输入恒得同一结论。

四条口径（[D-081](../../../项目管理/决策日志.md)）：

- **时段＝时钟区间** `HH:MM–HH:MM`（支持跨零点，如 `23:00–07:00`）——用户配置的「每天早晨 8 点」
  与格位边界由此**同一口径**；**内容类型＝受控枚举** `long_form` / `brief` / `interactive` /
  `retrospective`，**不接受自由字符串**（错字会静默造出永不命中的空格位）。
- **时段集合须覆盖全天且互不重叠**——判定是全函数（任一时刻恰命中一个时段），
  不留第三种「没命中任何格位」的状态（[00 §6 失败显式化](../../../docs/技术架构-v2/00-架构总览.md)）。
  想「这段时间不要打扰」就把该格位配成只放行 `emergency`，无需开洞。
- **任何格位都放行 `emergency`**——注意力预算是「哪些**额外**级别可以单独触达」，
  **不挡紧急信号**（[story-07](../../../docs/PRD-v2-Agent/story-07-ambient-delivery.md)：「盘中只推紧急」「休假模式只推最紧急」）。
- **缺省少而准**——未配置的格位只放行 `emergency`、节奏上限 1，其余级别一律**汇总进日报**
  （`dispatch="daily_report"`，不丢弃）；内置覆盖仅三条，各有 Story 出处（见 `:data:`DEFAULT_CELLS``）。

配置经 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 注册（见 `:mod:`registry_adapter``）：
三个条目，`current-mode` 是可写标量（委托 :meth:`AttentionBudget.switch_mode`），
`slots` 与 `matrix` 的取值是对象 ⇒ 按 01 §7 **不在统一标量落值面内**，写面归本类的
`set_*` API。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l5.budget_store import CONFIG_PREFIX, AttentionBudgetStore
from st_agent.l5.errors import BudgetValidationError
from st_agent.l5.signal import SignalLevel

__all__ = [
    "CONTENT_TYPES",
    "DEFAULT_CELL",
    "DEFAULT_CELLS",
    "DEFAULT_MODE",
    "DEFAULT_SLOTS",
    "MATRIX_CONFIG_ID",
    "MODE_CONFIG_ID",
    "SLOTS_CONFIG_ID",
    "SITUATION_MODES",
    "AttentionBudget",
    "BudgetCell",
    "BudgetCellView",
    "BudgetKey",
    "BudgetMap",
    "BudgetVerdict",
    "ContentType",
    "SituationMode",
    "TimeSlot",
    "default_budget",
]

SituationMode = Literal["workday", "weekend", "business_trip", "vacation", "urgent"]
"""情境模式（07 §2）：工作日 / 周末 / 出差 / 休假 / 紧急。"""

ContentType = Literal["long_form", "brief", "interactive", "retrospective"]
"""内容类型（07 §2）：长内容 / 短提示 / 交互式 / 周期回顾。"""

SITUATION_MODES: tuple[SituationMode, ...] = (
    "workday", "weekend", "business_trip", "vacation", "urgent",
)
CONTENT_TYPES: tuple[ContentType, ...] = (
    "long_form", "brief", "interactive", "retrospective",
)
LEVELS: tuple[SignalLevel, ...] = ("emergency", "important", "routine")

DEFAULT_MODE: SituationMode = "workday"
"""缺省情境（未配置时的当日情境）。"""

MODE_CONFIG_ID = f"{CONFIG_PREFIX}current-mode"
"""当前情境模式的条目 id（可写标量，01 §7 逐条目一次落值）。"""
SLOTS_CONFIG_ID = f"{CONFIG_PREFIX}slots"
"""时段集合的条目 id（取值为对象 ⇒ 只读声明面，写面归 :meth:`AttentionBudget.set_slots`）。"""
MATRIX_CONFIG_ID = f"{CONFIG_PREFIX}matrix"
"""预算矩阵的条目 id（取值为对象 ⇒ 只读声明面，写面归 :meth:`AttentionBudget.set_cell`）。"""


class TimeSlot(BaseModel):
    """一个时段（半开区间 `[start, end)`；`start > end` 即跨零点）。"""

    model_config = ConfigDict(frozen=True)

    slot_id: Annotated[str, Field(min_length=1, max_length=64)]
    """时段标识（**单段名**：不得含 `.` / `/` / `\\` / 空白——它会进点分 `config_id` 与矩阵键）。"""
    label: Annotated[str, Field(min_length=1)]
    """中性展示名（供面板与财报式读面使用）。"""
    start: time
    """起始时刻（本机墙钟，01 §8）。"""
    end: time
    """结束时刻（**不含**；等于 `start` 即零长，拒绝）。"""

    @model_validator(mode="after")
    def _shape(self) -> "TimeSlot":
        if self.start == self.end:
            raise ContractViolation(f"时段 {self.slot_id} 起止相同（零长时段覆盖不了任何时刻）")
        if any(sep in self.slot_id for sep in (".", "/", "\\")) or " " in self.slot_id:
            raise ContractViolation(
                f"时段 id 须为单段名（不得含 . / \\ 或空白）：{self.slot_id!r}"
            )
        return self

    def covers(self, at: time) -> bool:
        """`at` 是否落在本时段内（跨零点时段两段都算）。"""
        if self.start < self.end:
            return self.start <= at < self.end
        return at >= self.start or at < self.end

    def payload(self) -> dict[str, str]:
        """落盘形态（`HH:MM` 字符串——人可读、可被面板直接消费）。"""
        return {
            "slot_id": self.slot_id, "label": self.label,
            "start": self.start.strftime("%H:%M"), "end": self.end.strftime("%H:%M"),
        }


class BudgetCell(BaseModel):
    """一个预算格位：可**单独触达**的级别集合 + 节奏上限。"""

    model_config = ConfigDict(frozen=True)

    allowed_levels: frozenset[SignalLevel]
    """允许单独触达的级别（**恒含 `emergency`**——预算不挡紧急信号）。"""
    max_per_slot: int = Field(ge=1)
    """本格位在该时段内**最多单独触达几条**（上限，交下游计数；本层不记账）。"""

    @model_validator(mode="after")
    def _emergency_always_admitted(self) -> "BudgetCell":
        if "emergency" not in self.allowed_levels:
            raise ContractViolation(
                "格位必须放行 emergency——注意力预算是「哪些额外级别可单独触达」，"
                "不挡紧急信号（07 §2）"
            )
        return self

    def payload(self) -> dict[str, Any]:
        """落盘形态（级别集合按 `LEVELS` 顺序枚举——顺序稳定，便于人读与比对）。"""
        return {
            "allowed_levels": [lv for lv in LEVELS if lv in self.allowed_levels],
            "max_per_slot": self.max_per_slot,
        }


class BudgetKey(BaseModel):
    """一个格位的坐标（情境 × 时段 × 内容类型）。"""

    model_config = ConfigDict(frozen=True)

    mode: SituationMode
    slot_id: str
    content_type: ContentType


# ───────────────────────── 内置缺省（三条覆盖各有 Story 出处） ─────────────────────────

DEFAULT_SLOTS: tuple[TimeSlot, ...] = (
    TimeSlot(slot_id="morning_commute", label="早通勤", start=time(7, 0), end=time(9, 30)),
    TimeSlot(slot_id="market_hours", label="盘中", start=time(9, 30), end=time(15, 0)),
    TimeSlot(slot_id="afternoon", label="午后", start=time(15, 0), end=time(19, 0)),
    TimeSlot(slot_id="evening", label="晚间", start=time(19, 0), end=time(22, 30)),
    TimeSlot(slot_id="night", label="夜间", start=time(22, 30), end=time(7, 0)),
)
"""缺省五段（07 §2 的两轴口径）：覆盖全天、互不重叠，「夜间」跨零点。"""

DEFAULT_CELL = BudgetCell(allowed_levels=frozenset({"emergency"}), max_per_slot=1)
"""缺省格位：只放行 `emergency`、节奏上限 1 ⇒ 其余级别**汇总进日报**（「少而准」）。"""

_ALL_LEVELS = frozenset({"emergency", "important", "routine"})

DEFAULT_CELLS: dict[BudgetKey, BudgetCell] = {
    # story-07：「早通勤 20 分钟能看长内容」
    BudgetKey(mode="workday", slot_id="morning_commute", content_type="long_form"):
        BudgetCell(allowed_levels=frozenset(_ALL_LEVELS), max_per_slot=2),
    # story-07：「晚上 30 分钟交互式对话」
    BudgetKey(mode="workday", slot_id="evening", content_type="interactive"):
        BudgetCell(allowed_levels=frozenset(_ALL_LEVELS), max_per_slot=2),
    # story-07：「周末长周期回顾」
    BudgetKey(mode="weekend", slot_id="afternoon", content_type="retrospective"):
        BudgetCell(allowed_levels=frozenset(_ALL_LEVELS), max_per_slot=2),
}
"""内置覆盖**仅三条**，逐条对应 [story-07](../../docs/PRD-v2-Agent/story-07-ambient-delivery.md)
的例；其余格位一律走 :data:`DEFAULT_CELL`（缺省少而准，不替用户把矩阵填满）。"""


class BudgetCellView(BaseModel):
    """矩阵里的一格（格位 + **来源标注**——面板据此区分「你配的」与「缺省」）。"""

    model_config = ConfigDict(frozen=True)

    key: BudgetKey
    allowed_levels: frozenset[SignalLevel]
    max_per_slot: int
    source: Literal["configured", "default"]
    """`configured` ＝用户配过（落盘覆盖或内置覆盖）；`default` ＝走缺省格位。"""


class BudgetMap(BaseModel):
    """「注意力预算地图」的可读投影（情境 × 时段 × 内容类型的完整叉积）。

    **渲染**归表现层（[00 §1.1](../../../docs/技术架构-v2/00-架构总览.md)）——本层只给可读数据面。
    """

    model_config = ConfigDict(frozen=True)

    modes: tuple[SituationMode, ...]
    slots: tuple[TimeSlot, ...]
    content_types: tuple[ContentType, ...]
    current_mode: SituationMode
    """当前情境（面板默认展开的那一屏）。"""
    cells: tuple[BudgetCellView, ...]
    """完整叉积，按 情境 → 时段（起止时刻） → 内容类型 排序。"""

    def view(self, key: BudgetKey) -> BudgetCellView | None:
        """按坐标取一格（不属本图的坐标 → ``None``）。"""
        for cell in self.cells:
            if cell.key == key:
                return cell
        return None


class BudgetVerdict(BaseModel):
    """一次格位判定的结论（纯函数产出；不含任何计数状态）。"""

    model_config = ConfigDict(frozen=True)

    key: BudgetKey
    """命中的格位坐标。"""
    allowed_levels: frozenset[SignalLevel]
    max_per_slot: int
    source: Literal["configured", "default"]
    admitted: bool
    """该级别是否**可单独触达**（`False` 不等于丢弃——见 `dispatch`）。"""
    dispatch: Literal["separate", "daily_report"]
    """`separate` ＝单独触达；`daily_report` ＝**汇总进日报**（07 §4：不丢弃）。"""
    reason: str
    """中性陈述式理由（可解释性落点）。"""


class AttentionBudget:
    """注意力预算门面（07 §2）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（判定照常，但不落盘、不留痕）
    :param slots: 时段集合（缺省 :data:`DEFAULT_SLOTS`）；须覆盖全天且互不重叠
    :param cells: 内置覆盖格位（缺省 :data:`DEFAULT_CELLS`；供测试与产品口径调整）
    :param fallback: 缺省格位（缺省 :data:`DEFAULT_CELL`）
    :param mode: 缺省情境（缺省 `workday`；落盘条目存在时以盘为准）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        slots: Sequence[TimeSlot] | None = None,
        cells: Mapping[BudgetKey, BudgetCell] | None = None,
        fallback: BudgetCell | None = None,
        mode: SituationMode | None = None,
        now: Any = None,
    ) -> None:
        self._store = AttentionBudgetStore(store, now=now) if store is not None else None
        self._slots = tuple(slots) if slots is not None else DEFAULT_SLOTS
        _validate_slots(self._slots)
        self._builtin_cells = dict(cells) if cells is not None else dict(DEFAULT_CELLS)
        self._fallback = DEFAULT_CELL if fallback is None else fallback
        self._mode = DEFAULT_MODE if mode is None else _as_mode(mode)

    # ───────────────────────── 情境模式 ─────────────────────────

    @property
    def mode(self) -> SituationMode:
        """当前情境模式（落盘条目优先；取不到即内存态，不臆造）。"""
        if self._store is None:
            return self._mode
        return _as_mode(self._store.current(MODE_CONFIG_ID, self._mode), fallback=True)

    def switch_mode(
        self, mode: str, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """切换情境模式（**下一次判定即生效**）；落盘则同时留痕。

        :param mode: 五情境之一；非法即拒（不静默回落）
        :param trace_ref: 关联推理链（如适用）
        :returns: ``ChangeRecord``；无 ``Store`` 或取值未变 → ``None``
        """
        target = _as_mode(mode)
        self._mode = target
        if self._store is None:
            return None
        entry = self._entry(MODE_CONFIG_ID, target)
        return self._store.set(entry, previous=self._store.current(MODE_CONFIG_ID, None),
                               trace_ref=trace_ref)

    # ───────────────────────── 读面 ─────────────────────────

    def slots(self) -> tuple[TimeSlot, ...]:
        """当前时段集合（落盘覆盖优先，缺省回落内置）。"""
        return self._current_slots()

    def cell(self, key: BudgetKey) -> BudgetCell:
        """按坐标取格位（未配置 → 缺省格位）。"""
        return self._cell_for(key)[0]

    def map(self) -> BudgetMap:
        """「注意力预算地图」的完整投影（情境 × 时段 × 内容类型）。"""
        stored = self._stored_cells()      # 一次读盘，供全部格位共用（矩阵是纯投影，不逐格读）
        slots = self._sorted_slots()
        cells: list[BudgetCellView] = []
        for mode in SITUATION_MODES:
            for slot in slots:
                for content_type in CONTENT_TYPES:
                    key = BudgetKey(mode=mode, slot_id=slot.slot_id, content_type=content_type)
                    cell, source = self._cell_for(key, stored)
                    cells.append(
                        BudgetCellView(
                            key=key, allowed_levels=cell.allowed_levels,
                            max_per_slot=cell.max_per_slot, source=source,
                        )
                    )
        return BudgetMap(
            modes=SITUATION_MODES,
            slots=slots,
            content_types=CONTENT_TYPES,
            current_mode=self.mode,
            cells=tuple(cells),
        )

    def resolve(self, *, level: str, content_type: str, at: Any) -> BudgetVerdict:
        """按（级别 · 内容类型 · 时刻）判定该信号如何触达（07 §2 的格位判定）。

        :param level: `emergency` / `important` / `routine`
        :param content_type: `long_form` / `brief` / `interactive` / `retrospective`
        :param at: 本机墙钟时刻（`datetime.time` 或 `HH:MM` 字符串）
        :raises BudgetValidationError: 级别 / 内容类型 / 时刻任一不合法（不猜、不回落）
        """
        lvl = _as_level(level)
        ctype = _as_content_type(content_type)
        moment = _as_time(at)
        mode = self.mode
        slot = self._slot_at(moment)
        key = BudgetKey(mode=mode, slot_id=slot.slot_id, content_type=ctype)
        cell, source = self._cell_for(key)
        admitted = lvl in cell.allowed_levels
        dispatch: Literal["separate", "daily_report"] = (
            "separate" if admitted else "daily_report"
        )
        return BudgetVerdict(
            key=key, allowed_levels=cell.allowed_levels, max_per_slot=cell.max_per_slot,
            source=source, admitted=admitted, dispatch=dispatch,
            reason=_reason(mode, slot, ctype, lvl, cell, admitted),
        )

    # ───────────────────────── 写面（01 §7：对象取值的写面归本类） ─────────────────────────

    def set_slots(
        self, slots: Sequence[Any], *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """替换时段集合（须覆盖全天且互不重叠）；越界即拒，不落盘不留痕。

        入参接受 :class:`TimeSlot` 或 `{"slot_id": …}` 映射（对话 / 面板两通道给的
        都是 JSON 形态的映射）——非法即逐类点名，不静默落一个残缺的时段表。
        """
        candidate = tuple(_slot_from_value(item) for item in slots)
        _validate_slots(candidate)
        if self._store is None:
            self._slots = candidate
            return None
        entry = self._entry(SLOTS_CONFIG_ID, _slots_payload(candidate))
        return self._store.set(entry, previous=_slots_payload(self._current_slots()),
                               trace_ref=trace_ref)

    def set_cell(
        self, key: BudgetKey, cell: Any, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """覆盖一个格位（越界即拒、不落盘不留痕）；取值未变 → ``None``。

        入参接受 :class:`BudgetCell` 或 `{"allowed_levels": [...], "max_per_slot": n}`
        映射（同上：两条通道给的都是 JSON 形态）。
        """
        self._require_known(key)
        target = _cell_from_value(cell)
        overrides = dict(self._stored_cells())
        overrides[key] = target
        if self._store is None:
            self._builtin_cells[key] = target
            return None
        entry = self._entry(MATRIX_CONFIG_ID, _cells_payload(overrides))
        return self._store.set(
            entry, previous=_cells_payload(self._stored_cells()), trace_ref=trace_ref
        )

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本层登记进 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的三个条目（含当前取值）。"""
        return (
            self._entry(MODE_CONFIG_ID, self.mode),
            self._entry(SLOTS_CONFIG_ID, _slots_payload(self._current_slots())),
            self._entry(MATRIX_CONFIG_ID, _cells_payload(self._stored_cells())),
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        """按 `config_id` 取条目登记形态；不属本层 → ``None``；**损坏 → 校验错**（不静默）。

        盘上有条目即以盘为准（含用户改过的值）；无盘或条目未落盘 → 回落到本层的**声明形态**
        （缺省值），故「有配置」与「无配置」在两种通道下都可区分。
        """
        if not config_id.startswith(CONFIG_PREFIX):
            return None
        if self._store is not None:
            stored = self._store.entry(config_id)
            if stored is not None:
                return stored
        for entry in self.entries():
            if entry.config_id == config_id:
                return entry
        return None

    # ───────────────────────── 内部 ─────────────────────────

    def _entry(self, config_id: str, default: Any) -> ConfigEntry:
        return ConfigEntry(
            config_id=config_id,
            display_name=_DISPLAY_NAMES[config_id],
            value_schema=_VALUE_SCHEMAS[config_id],
            default=default,
            description_for_chat=_CHAT_DESCRIPTIONS[config_id],
            panel_form_spec=_PANEL_FORMS[config_id],
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )

    def _current_slots(self) -> tuple[TimeSlot, ...]:
        """落盘时段集合优先；取不到（无盘 / 缺失 / 损坏）→ 内存态。"""
        if self._store is None:
            return self._slots
        raw = self._store.current(SLOTS_CONFIG_ID, None)
        if raw is None:
            return self._slots
        try:
            stored = _slots_from_payload(raw)
        except BudgetValidationError:
            return self._slots
        return stored

    def _stored_cells(self) -> dict[BudgetKey, BudgetCell]:
        """落盘覆盖格位（无盘 / 缺失 / 损坏 → 空集，不臆造）。"""
        if self._store is None:
            return {}
        raw = self._store.current(MATRIX_CONFIG_ID, None)
        if raw is None:
            return {}
        try:
            return _cells_from_payload(raw)
        except BudgetValidationError:
            return {}

    def _cell_for(
        self, key: BudgetKey, stored: Mapping[BudgetKey, BudgetCell] | None = None
    ) -> tuple[BudgetCell, Literal["configured", "default"]]:
        """格位取值：落盘覆盖 > 内置覆盖 > 缺省格位（来源标注随之）。

        :param stored: 已读出的落盘覆盖（``map`` 一次读盘后复用；缺省则现读）。
        """
        if stored is None:
            stored = self._stored_cells()
        if key in stored:
            return stored[key], "configured"
        if key in self._builtin_cells:
            return self._builtin_cells[key], "configured"
        return self._fallback, "default"

    def _sorted_slots(self) -> tuple[TimeSlot, ...]:
        return tuple(sorted(self._current_slots(), key=lambda s: (s.start, s.end)))

    def _slot_at(self, at: time) -> TimeSlot:
        for slot in self._sorted_slots():
            if slot.covers(at):
                return slot
        # _validate_slots 保证覆盖全天，故此处不可达；真到了就显式失败，不猜。
        raise BudgetValidationError(
            f"时刻 {at.strftime('%H:%M')} 未落在任何时段内——时段集合须覆盖全天（07 §2）"
        )

    def _require_known(self, key: BudgetKey) -> None:
        known = {slot.slot_id for slot in self._current_slots()}
        if key.slot_id not in known:
            raise BudgetValidationError(
                f"未知时段 id {key.slot_id!r}（现有：{' / '.join(sorted(known))}）"
            )


def default_budget(**kwargs: Any) -> AttentionBudget:
    """内置缺省预算（01 §7 的条目形态完好、无用户覆盖时的起点）。"""
    return AttentionBudget(**kwargs)


# ───────────────────────── 取值解释（越界一律显式拒） ─────────────────────────


def _as_mode(value: Any, *, fallback: bool = False) -> SituationMode:
    if value in SITUATION_MODES:
        return value  # type: ignore[return-value]
    if fallback:
        # 读面：盘上留了非法值 → 回落缺省（条目仍可经 entry() 查到，不停摆）
        return DEFAULT_MODE
    raise BudgetValidationError(
        f"情境模式须为 {' / '.join(SITUATION_MODES)} 之一，收到 {value!r}"
    )


def _as_level(value: Any) -> SignalLevel:
    if value not in LEVELS:
        raise BudgetValidationError(f"信号级别须为 {' / '.join(LEVELS)} 之一，收到 {value!r}")
    return value  # type: ignore[return-value]


def _as_content_type(value: Any) -> ContentType:
    if value not in CONTENT_TYPES:
        raise BudgetValidationError(
            f"内容类型须为 {' / '.join(CONTENT_TYPES)} 之一，收到 {value!r}"
        )
    return value  # type: ignore[return-value]


def _as_time(value: Any) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            hour, minute = value.strip().split(":")
            return time(int(hour), int(minute))
        except (ValueError, AttributeError) as exc:
            raise BudgetValidationError(f"时刻须为 HH:MM，收到 {value!r}") from exc
    raise BudgetValidationError(f"时刻须为 datetime.time 或 HH:MM 字符串，收到 {value!r}")


def _slot_from_value(value: Any) -> TimeSlot:
    """入参 → :class:`TimeSlot`（接受值对象或 `{slot_id, label, start, end}` 映射）。"""
    if isinstance(value, TimeSlot):
        return value
    if not isinstance(value, Mapping):
        raise BudgetValidationError(
            f"时段须为 TimeSlot 或 {{slot_id, label, start, end}} 映射，"
            f"收到 {type(value).__name__}"
        )
    missing = [name for name in ("slot_id", "label", "start", "end") if name not in value]
    if missing:
        raise BudgetValidationError(f"时段缺字段：{' / '.join(missing)}")
    try:
        return TimeSlot(
            slot_id=value["slot_id"], label=value["label"],
            start=_as_time(value["start"]), end=_as_time(value["end"]),
        )
    except (ValidationError, ValueError) as exc:
        raise BudgetValidationError(f"时段取值非法：{exc}") from exc


def _cell_from_value(value: Any) -> BudgetCell:
    """入参 → :class:`BudgetCell`（接受值对象或 `{allowed_levels, max_per_slot}` 映射）。"""
    if isinstance(value, BudgetCell):
        return value
    if not isinstance(value, Mapping):
        raise BudgetValidationError(
            f"格位须为 BudgetCell 或 {{allowed_levels, max_per_slot}} 映射，"
            f"收到 {type(value).__name__}"
        )
    missing = [name for name in ("allowed_levels", "max_per_slot") if name not in value]
    if missing:
        raise BudgetValidationError(f"格位缺字段：{' / '.join(missing)}")
    raw_levels = value["allowed_levels"]
    if not isinstance(raw_levels, (list, tuple, set, frozenset)):
        raise BudgetValidationError(
            f"allowed_levels 须为级别列表，收到 {type(raw_levels).__name__}"
        )
    try:
        return BudgetCell(
            allowed_levels=frozenset(raw_levels), max_per_slot=value["max_per_slot"]
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise BudgetValidationError(f"格位取值非法：{exc}") from exc


def _validate_slots(slots: Sequence[TimeSlot]) -> None:
    """时段集合须覆盖全天且互不重叠（判定是全函数的前提，07 §2）。"""
    if not slots:
        raise BudgetValidationError("时段集合不得为空——判定须覆盖全天（07 §2）")
    ids = [slot.slot_id for slot in slots]
    duplicated = {sid for sid in ids if ids.count(sid) > 1}
    if duplicated:
        raise BudgetValidationError(f"时段 id 重复：{' / '.join(sorted(duplicated))}")
    ordered = sorted(slots, key=lambda s: (s.start, s.end))
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.end != nxt.start:
            raise BudgetValidationError(
                f"时段集合未覆盖全天或相互重叠：{prev.slot_id} 止于 "
                f"{prev.start.strftime('%H:%M')}–{prev.end.strftime('%H:%M')}，"
                f"而 {nxt.slot_id} 起于 {nxt.start.strftime('%H:%M')}"
            )
    if ordered[-1].end != ordered[0].start:
        raise BudgetValidationError(
            f"时段集合未覆盖全天：末段 {ordered[-1].slot_id} 止于 "
            f"{ordered[-1].end.strftime('%H:%M')}，首段 {ordered[0].slot_id} 起于 "
            f"{ordered[0].start.strftime('%H:%M')}"
        )


def _slots_payload(slots: Iterable[TimeSlot]) -> dict[str, Any]:
    return {"slots": [slot.payload() for slot in slots]}


def _cells_payload(cells: Mapping[BudgetKey, BudgetCell]) -> dict[str, Any]:
    """覆盖格位的落盘形态——**只存用户配过的**（缺省不进盘，故「有配置」与「无配置」在盘上可分）。

    格位坐标以 `mode` / `slot_id` / `content_type` 三个键表达，`matrix` 面板件与
    变更留痕都直接读它。
    """
    items: list[dict[str, Any]] = []
    for key, cell in sorted(
        cells.items(), key=lambda kc: (kc[0].mode, kc[0].slot_id, kc[0].content_type)
    ):
        items.append({
            "mode": key.mode, "slot_id": key.slot_id, "content_type": key.content_type,
            **cell.payload(),
        })
    return {"cells": items}


def _slots_from_payload(raw: Any) -> tuple[TimeSlot, ...]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("slots"), Sequence):
        raise BudgetValidationError(f"slots 条目取值须为 {{'slots': [...]}}，收到 {raw!r}")
    slots: list[TimeSlot] = []
    for index, item in enumerate(raw["slots"]):
        try:
            slots.append(_slot_from_value(item))
        except BudgetValidationError as exc:
            raise BudgetValidationError(f"slots[{index}] 不合约：{exc}") from exc
    _validate_slots(tuple(slots))
    return tuple(slots)


def _cells_from_payload(raw: Any) -> dict[BudgetKey, BudgetCell]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("cells"), Sequence):
        raise BudgetValidationError(f"matrix 条目取值须为 {{'cells': [...]}}，收到 {raw!r}")
    cells: dict[BudgetKey, BudgetCell] = {}
    for index, item in enumerate(raw["cells"]):
        if not isinstance(item, Mapping):
            raise BudgetValidationError(f"cells[{index}] 须为映射，收到 {type(item).__name__}")
        missing = [
            name for name in ("mode", "slot_id", "content_type") if name not in item
        ]
        if missing:
            raise BudgetValidationError(f"cells[{index}] 缺字段：{' / '.join(missing)}")
        try:
            key = BudgetKey(
                mode=_as_mode(item["mode"]), slot_id=item["slot_id"],
                content_type=_as_content_type(item["content_type"]),
            )
            cells[key] = _cell_from_value(item)
        except BudgetValidationError as exc:
            raise BudgetValidationError(f"cells[{index}] 不合约：{exc}") from exc
    return cells


def _reason(
    mode: SituationMode, slot: TimeSlot, content_type: ContentType,
    level: SignalLevel, cell: BudgetCell, admitted: bool,
) -> str:
    """中性陈述式理由（无第一人称、无情感、无对话体）。"""
    window = f"{slot.start.strftime('%H:%M')}–{slot.end.strftime('%H:%M')}"
    allowed = " / ".join(lv for lv in LEVELS if lv in cell.allowed_levels)
    head = (
        f"情境「{mode}」· 时段「{slot.label}」({window}) · 内容「{content_type}」格位："
        f"单独触达级别 {allowed}，节奏上限 {cell.max_per_slot} 条/时段"
    )
    if admitted:
        return f"{head}；本条级别「{level}」在格位内 → 单独触达"
    return f"{head}；本条级别「{level}」不在格位内 → 汇总进日报（不丢弃）"


# ───────────────────────── 01 §7 条目的登记形态（三处静态表） ─────────────────────────

_DISPLAY_NAMES = {
    MODE_CONFIG_ID: "情境模式",
    SLOTS_CONFIG_ID: "时段集合",
    MATRIX_CONFIG_ID: "注意力预算矩阵",
}

_VALUE_SCHEMAS: dict[str, dict[str, Any]] = {
    MODE_CONFIG_ID: {"type": "string", "enum": list(SITUATION_MODES)},
    SLOTS_CONFIG_ID: {"type": "object"},
    MATRIX_CONFIG_ID: {"type": "object"},
}

_CHAT_DESCRIPTIONS = {
    MODE_CONFIG_ID: "当前情境模式（工作日 / 周末 / 出差 / 休假 / 紧急），决定按哪一套注意力预算放行",
    SLOTS_CONFIG_ID: "一天切成哪几个时段（时钟区间，覆盖全天），预算矩阵按它分层",
    MATRIX_CONFIG_ID: "情境 × 时段 × 内容类型的预算格位：每格放行哪些级别、最多单独触达几条",
}

_PANEL_FORMS = {
    MODE_CONFIG_ID: PanelField(
        widget="select", label="情境模式", help_text="切换后下一次判定即按新情境的预算",
        choices=SITUATION_MODES,
    ),
    SLOTS_CONFIG_ID: PanelField(
        widget="text", label="时段集合",
        help_text="时钟区间列表（HH:MM–HH:MM），须覆盖全天且互不重叠",
    ),
    MATRIX_CONFIG_ID: PanelField(
        widget="matrix", label="注意力预算矩阵",
        help_text="情境 × 时段 × 内容类型；每格给出允许单独触达的级别与节奏上限",
    ),
}
