"""自主查证循环的**双上界** 01 §7 配置项（T-AGT-007；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md) / [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。

两道**上界**——**步数**与 **LLM 调用次数**——在 [`T-AGT-004.2`](../../../项目管理/tasks/T-AGT-004.2-循环驱动、协议端口与上界.md)
已做成 [`run_agent_loop`](loop.py) 的注入参数（带缺省）。本模块把两条取值**登记为 01 §7
配置项**（``investigate.max_steps`` / ``investigate.max_llm_calls``），使端用户可经 L1
统一配置注册表门面（对话 / 面板双通道）调整——**长期偏好**语义，与 [05 §3.2](../../../docs/技术架构-v2/05-L3-对话主入口.md)
的**意图级参数**（单次请求问项）是两回事，故 `investigate` 的确认卡仍只有「意图」一条目。

**两条口径**（[T-AGT-007](../../../项目管理/tasks/T-AGT-007-循环上界开放为配置项.md) A3 / A4）：

- **缺省单一事实源**——条目 `default` 直接取 [`run_agent_loop`](loop.py) 的
  :data:`~st_agent.l3.runtime.loop.DEFAULT_MAX_STEPS` / :data:`~st_agent.l3.runtime.loop.DEFAULT_MAX_LLM_CALLS`，
  **不另立数字**（否则登记面与运行期缺省会分叉）。
- **取值域＝整数 ≥1 且 ≤ 上限**——下限沿用循环入口的校验（「上界是预算；到界即终止」）；
  上限是**防跑飞护栏**（这正是上界当初被称「系统预算」的原因），取值属实现口径。

owner 住 L3（[铁律 7](../../../项目管理/工程宪法.md)：L1 不得 import L3），族适配器见
:mod:`~st_agent.l3.runtime.registry_adapter`，由**组合根注入** L1 门面——先例为 L2 / L5 / L6 各族。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l1.registry.naming import (
    CHANGE_PARTITION,
    CONFIG_PARTITION,
    config_path,
    new_change_id,
)
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.loop import DEFAULT_MAX_LLM_CALLS, DEFAULT_MAX_STEPS

__all__ = [
    "CHANGE_PREFIX",
    "INVESTIGATE_CONFIG_PREFIX",
    "MAX_LLM_CALLS_CEILING",
    "MAX_LLM_CALLS_CONFIG_ID",
    "MAX_STEPS_CEILING",
    "MAX_STEPS_CONFIG_ID",
    "LoopBounds",
    "loop_bounds_config_entries",
]

INVESTIGATE_CONFIG_PREFIX = "investigate."
"""本族的 ``config_id`` 前缀（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的点分族名）。"""

MAX_STEPS_CONFIG_ID = "investigate.max_steps"
"""步数上界的 ``config_id``（落盘 ``config`` 分区 ``investigate/max_steps.json``）。"""

MAX_LLM_CALLS_CONFIG_ID = "investigate.max_llm_calls"
"""LLM 调用次数上界的 ``config_id``（落盘 ``investigate/max_llm_calls.json``）。"""

CHANGE_PREFIX = "investigate-change/"
"""本族变更留痕（``ChangeRecord``）的落盘目录前缀。"""

MAX_STEPS_CEILING = 32
"""步数上界的**上限**（防跑飞；缺省 8 的 4×）。取值属实现口径（[T-AGT-007](../../../项目管理/tasks/T-AGT-007-循环上界开放为配置项.md) A4）。"""

MAX_LLM_CALLS_CEILING = 64
"""LLM 调用次数上界的**上限**（防跑飞；缺省 12 的 5×）。"""


def _system_now() -> datetime:
    return datetime.now().astimezone()


def _check(value: object, *, config_id: str, ceiling: int) -> int:
    """取值域门：整数、非布尔、``1..ceiling``（非法即抛，**不静默截断 / 回退**）。"""
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= ceiling:
        raise AgentRuntimeValidationError(
            f"{config_id} 须为 1..{ceiling} 的整数（上界是防跑飞预算），得到 {value!r}"
        )
    return value


_MAX_STEPS_ENTRY = ConfigEntry(
    config_id=MAX_STEPS_CONFIG_ID,
    display_name="自主查证步数上界",
    value_schema={"type": "integer", "minimum": 1, "maximum": MAX_STEPS_CEILING},
    default=DEFAULT_MAX_STEPS,
    description_for_chat=(
        "自主查证循环一次最多执行多少步（每步＝调用一个工具）。"
        f"缺省 {DEFAULT_MAX_STEPS}；到界即终止并如实告知「达到上界」，不假装完成。"
        f"取值范围 1–{MAX_STEPS_CEILING}"
    ),
    panel_form_spec=PanelField(
        widget="number",
        label="自主查证步数上界",
        help_text=(
            f"一次自主查证最多执行的步数（1–{MAX_STEPS_CEILING}）；缺省 {DEFAULT_MAX_STEPS}"
        ),
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)
"""步数上界条目（[05 §3.2](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的无 target 意图问项来源之一，落 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

_MAX_LLM_CALLS_ENTRY = ConfigEntry(
    config_id=MAX_LLM_CALLS_CONFIG_ID,
    display_name="自主查证调用次数上界",
    value_schema={"type": "integer", "minimum": 1, "maximum": MAX_LLM_CALLS_CEILING},
    default=DEFAULT_MAX_LLM_CALLS,
    description_for_chat=(
        "自主查证循环一次最多向模型发多少轮请求。"
        f"缺省 {DEFAULT_MAX_LLM_CALLS}（有意高于步数上界——模型每执行一步要问一轮，"
        "收尾还要再问一轮）；到界即终止并如实告知。"
        f"取值范围 1–{MAX_LLM_CALLS_CEILING}"
    ),
    panel_form_spec=PanelField(
        widget="number",
        label="自主查证调用次数上界",
        help_text=(
            f"一次自主查证最多向模型发的请求轮数（1–{MAX_LLM_CALLS_CEILING}）；"
            f"缺省 {DEFAULT_MAX_LLM_CALLS}"
        ),
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)
"""LLM 调用次数上界条目（同上一目，落 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

_ENTRIES: dict[str, ConfigEntry] = {
    MAX_STEPS_CONFIG_ID: _MAX_STEPS_ENTRY,
    MAX_LLM_CALLS_CONFIG_ID: _MAX_LLM_CALLS_ENTRY,
}

_DEFAULTS: dict[str, int] = {
    MAX_STEPS_CONFIG_ID: DEFAULT_MAX_STEPS,
    MAX_LLM_CALLS_CONFIG_ID: DEFAULT_MAX_LLM_CALLS,
}

_CEILINGS: dict[str, int] = {
    MAX_STEPS_CONFIG_ID: MAX_STEPS_CEILING,
    MAX_LLM_CALLS_CONFIG_ID: MAX_LLM_CALLS_CEILING,
}


def loop_bounds_config_entries() -> tuple[ConfigEntry, ...]:
    """两条目的**规范形态**（缺省＝循环入口缺省；当前取值经 :meth:`LoopBounds.entry` 取）。"""
    return (_MAX_STEPS_ENTRY, _MAX_LLM_CALLS_ENTRY)


class LoopBounds:
    """双上界的 owner——01 §7 登记条目的读写与变更留痕（[T-AGT-007](../../../项目管理/tasks/T-AGT-007-循环上界开放为配置项.md)）。

    落值走 ``config`` 分区、留痕走 ``execution_log`` 分区（``investigate-change/``）。
    **值未变不留痕、不落盘**（与各族既有取向一致）。读面缺省回落、损坏**显式抛**
    （不静默回退，同 L0 出网审计的口径）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    # ───────────────────────── 读 ─────────────────────────

    def steps(self) -> int:
        """生效的**步数**上界（未落值 → 缺省 8；损坏 / 越界 → 显式抛）。"""
        return self._read(MAX_STEPS_CONFIG_ID)

    def llm_calls(self) -> int:
        """生效的 **LLM 调用次数**上界（未落值 → 缺省 12；损坏 / 越界 → 显式抛）。"""
        return self._read(MAX_LLM_CALLS_CONFIG_ID)

    def entries(self) -> tuple[ConfigEntry, ...]:
        """两条目（`default` 携**当前生效值**，供面板 / 对话通道展示）。"""
        return (self.entry(MAX_STEPS_CONFIG_ID), self.entry(MAX_LLM_CALLS_CONFIG_ID))  # type: ignore[return-value]

    def entry(self, config_id: str) -> ConfigEntry | None:
        """按 ``config_id`` 取条目；不属本族 → ``None``。"""
        canonical = _ENTRIES.get(config_id)
        if canonical is None:
            return None
        return canonical.model_copy(update={"default": self._read(config_id)})

    # ───────────────────────── 写 ─────────────────────────

    def set_steps(self, value: object, *, trace_id: str | None = None) -> ChangeRecord | None:
        """落一次**步数**上界（值未变 → ``None``）。"""
        return self._set(MAX_STEPS_CONFIG_ID, value, trace_id=trace_id)

    def set_llm_calls(self, value: object, *, trace_id: str | None = None) -> ChangeRecord | None:
        """落一次 **LLM 调用次数**上界（值未变 → ``None``）。"""
        return self._set(MAX_LLM_CALLS_CONFIG_ID, value, trace_id=trace_id)

    # ───────────────────────── 内部 ─────────────────────────

    def _read(self, config_id: str) -> int:
        path = config_path(config_id)
        try:
            raw = self._store.get(CONFIG_PARTITION, path)
        except KeyError:
            return _DEFAULTS[config_id]
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AgentRuntimeValidationError(
                f"{config_id} 的配置项损坏（{path}）：{exc}"
            ) from exc
        return _check(value, config_id=config_id, ceiling=_CEILINGS[config_id])

    def _set(self, config_id: str, value: object, *, trace_id: str | None) -> ChangeRecord | None:
        value = _check(value, config_id=config_id, ceiling=_CEILINGS[config_id])
        old = self._read(config_id)
        if old == value:
            return None
        self._store.put(
            CONFIG_PARTITION, config_path(config_id),
            json.dumps(value).encode("utf-8"),
        )
        change = ChangeRecord(
            change_id=new_change_id(), config_id=config_id,
            old_value=old, new_value=value, applied_at=self._now().isoformat(),
            trace_ref=trace_id,
        )
        self._store.put(
            CHANGE_PARTITION, f"{CHANGE_PREFIX}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"),
        )
        return change
