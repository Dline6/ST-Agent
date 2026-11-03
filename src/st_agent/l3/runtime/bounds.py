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
- **取值域＝整数 ≥1**——下限沿用循环入口的校验（「上界是预算；到界即终止」）；
  :data:`MAX_STEPS_CEILING` 等三条**建议上限**只作提示（2026-11-02 起，[D-161](../../../项目管理/决策日志.md)），
  超出仍可保存并生效，不做硬拒绝。

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
from st_agent.l3.runtime.loop import (
    DEFAULT_LLM_MAX_ATTEMPTS,
    DEFAULT_LLM_RETRY_BACKOFF_MS,
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_STEPS,
)

__all__ = [
    "CHANGE_PREFIX",
    "CONTEXT_BUDGET_CEILING",
    "CONTEXT_BUDGET_CONFIG_ID",
    "INVESTIGATE_CONFIG_PREFIX",
    "LLM_IDLE_TIMEOUT_CONFIG_ID",
    "LLM_MAX_ATTEMPTS_CONFIG_ID",
    "LLM_RETRY_BACKOFF_CONFIG_ID",
    "LLM_TIMEOUT_CONFIG_ID",
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

CONTEXT_BUDGET_CONFIG_ID = "investigate.context_budget"
"""工作上下文预算的 ``config_id``（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)；
落盘 ``config`` 分区 ``investigate/context_budget.json``）。"""

LLM_TIMEOUT_CONFIG_ID = "investigate.llm_timeout_ms"
"""单次 LLM 调用的**总时限**（毫秒；[D-165](../../项目管理/决策日志.md)）。"""

LLM_IDLE_TIMEOUT_CONFIG_ID = "investigate.llm_idle_timeout_ms"
"""单次 LLM 调用的**空闲时限**（毫秒；连续无任何活动即判超时）。"""

LLM_MAX_ATTEMPTS_CONFIG_ID = "investigate.llm_max_attempts"
"""单轮模型调用的**最多尝试次数**（含首次；仅可重试失败退避重试）。"""

LLM_RETRY_BACKOFF_CONFIG_ID = "investigate.llm_retry_backoff_ms"
"""重试退避基数（毫秒；第 n 次重试等待 n × 基数）。"""

DEFAULT_LLM_TIMEOUT_MS = 300_000
"""LLM 调用总时限缺省（5 分钟；显著高于端点常见的 60 秒，给长推理留出空间）。"""

DEFAULT_LLM_IDLE_TIMEOUT_MS = 90_000
"""LLM 调用空闲时限缺省（90 秒；只要还有心跳 / 推理增量就算活动）。"""

CHANGE_PREFIX = "investigate-change/"
"""本族变更留痕（``ChangeRecord``）的落盘目录前缀。"""

MAX_STEPS_CEILING = 32
"""步数上界的**建议上限**（防跑飞的提示值，缺省 8 的 4×）；2026-11-02 起只作建议，超出仍可保存（[D-161](../../../项目管理/决策日志.md)）。"""

MAX_LLM_CALLS_CEILING = 64
"""LLM 调用次数上界的**建议上限**（防跑飞的提示值，缺省 12 的 5×）。"""

CONTEXT_BUDGET_CEILING = 128000
"""上下文封顶的**建议上限**（token 数；2026-11-03 起 `investigate.context_budget`
是可选封顶，0＝按端点窗口自动派生——[`T-AGT-033.1`](../../../项目管理/tasks/T-AGT-033.1-预算信封与计量口径.md)）。"""


def _system_now() -> datetime:
    return datetime.now().astimezone()


def _check(value: object, *, config_id: str, minimum: int = 1) -> int:
    """取值域门：整数、非布尔、``≥minimum``（非法即抛，**不静默截断 / 回退**）。"""
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise AgentRuntimeValidationError(
            f"{config_id} 须为 ≥{minimum} 的整数，得到 {value!r}"
        )
    return value


_MAX_STEPS_ENTRY = ConfigEntry(
    config_id=MAX_STEPS_CONFIG_ID,
    display_name="自主查证步数上界",
    value_schema={"type": "integer", "minimum": 1},
    default=DEFAULT_MAX_STEPS,
    description_for_chat=(
        "自主查证循环一次最多执行多少步（每步＝调用一个工具）。"
        f"缺省 {DEFAULT_MAX_STEPS}；到界即终止并如实告知「达到上界」，不假装完成。"
        "步数与上下文信封共同约束一次运行：上下文按端点窗口自动派生、按轮压缩，"
        "不需要用户另配固定字符预算。"
        f"建议不超过 {MAX_STEPS_CEILING}；超出仍可保存"
    ),
    panel_form_spec=PanelField(
        widget="number",
        label="自主查证步数上界",
        help_text=(
            f"一次自主查证最多执行的步数；缺省 {DEFAULT_MAX_STEPS}，"
            f"建议不超过 {MAX_STEPS_CEILING}（可超出）"
        ),
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)
"""步数上界条目（[05 §3.2](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的无 target 意图问项来源之一，落 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

_MAX_LLM_CALLS_ENTRY = ConfigEntry(
    config_id=MAX_LLM_CALLS_CONFIG_ID,
    display_name="自主查证调用次数上界",
    value_schema={"type": "integer", "minimum": 1},
    default=DEFAULT_MAX_LLM_CALLS,
    description_for_chat=(
        "自主查证循环一次最多向模型发多少轮请求。"
        f"缺省 {DEFAULT_MAX_LLM_CALLS}（有意高于步数上界——模型每执行一步要问一轮，"
        "收尾还要再问一轮）；到界即终止并如实告知。"
        f"建议不超过 {MAX_LLM_CALLS_CEILING}；超出仍可保存"
    ),
    panel_form_spec=PanelField(
        widget="number",
        label="自主查证调用次数上界",
        help_text=(
            f"一次自主查证最多向模型发的请求轮数；缺省 {DEFAULT_MAX_LLM_CALLS}，"
            f"建议不超过 {MAX_LLM_CALLS_CEILING}（可超出）"
        ),
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)
"""LLM 调用次数上界条目（同上一目，落 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

_CONTEXT_BUDGET_ENTRY = ConfigEntry(
    config_id=CONTEXT_BUDGET_CONFIG_ID,
    display_name="自主查证上下文封顶（token，0＝自动）",
    value_schema={"type": "integer", "minimum": 0},
    default=0,
    description_for_chat=(
        "自主查证循环每一轮的上下文硬边界默认由**端点窗口 × 安全比例**派生；"
        "本项可选地再封一个 token 上限，0 表示不封顶、完全按端点窗口自动派生。"
        "超限时先舍记忆、再舍最早的历史消息、最后压缩更早步骤，"
        "并在运行报告里如实标注被舍项。"
        f"建议不超过 {CONTEXT_BUDGET_CEILING} tokens；超出仍可保存"
    ),
    panel_form_spec=PanelField(
        widget="number",
        label="自主查证上下文封顶（token）",
        help_text=(
            "0＝按端点窗口自动派生；大于 0 时作为额外的 token 硬顶。"
            f"建议不超过 {CONTEXT_BUDGET_CEILING}（可超出）"
        ),
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)
"""工作上下文封顶条目（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)，落 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

_LLM_TIMEOUT_ENTRY = ConfigEntry(
    config_id=LLM_TIMEOUT_CONFIG_ID,
    display_name="单次 LLM 调用总时限（毫秒）",
    value_schema={"type": "integer", "minimum": 1},
    default=DEFAULT_LLM_TIMEOUT_MS,
    description_for_chat=(
        "自主查证循环里单次模型调用的总时限（毫秒）。"
        f"缺省 {DEFAULT_LLM_TIMEOUT_MS}；到时限即中止，并如实记录实际耗时与失败阶段"
    ),
    panel_form_spec=PanelField(
        widget="number", label="单次 LLM 调用总时限（毫秒）",
        help_text=f"缺省 {DEFAULT_LLM_TIMEOUT_MS}（5 分钟）；整数且 ≥1",
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)

_LLM_IDLE_TIMEOUT_ENTRY = ConfigEntry(
    config_id=LLM_IDLE_TIMEOUT_CONFIG_ID,
    display_name="单次 LLM 调用空闲时限（毫秒）",
    value_schema={"type": "integer", "minimum": 1},
    default=DEFAULT_LLM_IDLE_TIMEOUT_MS,
    description_for_chat=(
        "连续多久没有任何活动（心跳 / 推理增量 / 正文）即判超时，单位毫秒。"
        f"缺省 {DEFAULT_LLM_IDLE_TIMEOUT_MS}；只按「无活动」判，不按总耗时误杀长思考"
    ),
    panel_form_spec=PanelField(
        widget="number", label="单次 LLM 调用空闲时限（毫秒）",
        help_text=f"缺省 {DEFAULT_LLM_IDLE_TIMEOUT_MS}（90 秒）；整数且 ≥1",
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)

_LLM_MAX_ATTEMPTS_ENTRY = ConfigEntry(
    config_id=LLM_MAX_ATTEMPTS_CONFIG_ID,
    display_name="单轮 LLM 调用最多尝试次数",
    value_schema={"type": "integer", "minimum": 1},
    default=DEFAULT_LLM_MAX_ATTEMPTS,
    description_for_chat=(
        "单轮模型调用最多尝试几次（含首次），只对超时 / 429 / 5xx / 连接重置重试。"
        f"缺省 {DEFAULT_LLM_MAX_ATTEMPTS}；每次尝试计一次调用次数上界，不计步"
    ),
    panel_form_spec=PanelField(
        widget="number", label="单轮 LLM 调用最多尝试次数",
        help_text=f"缺省 {DEFAULT_LLM_MAX_ATTEMPTS}；整数且 ≥1",
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)

_LLM_RETRY_BACKOFF_ENTRY = ConfigEntry(
    config_id=LLM_RETRY_BACKOFF_CONFIG_ID,
    display_name="LLM 重试退避基数（毫秒）",
    value_schema={"type": "integer", "minimum": 0},
    default=DEFAULT_LLM_RETRY_BACKOFF_MS,
    description_for_chat=(
        "第 n 次重试前等待 n × 该基数（毫秒）。"
        f"缺省 {DEFAULT_LLM_RETRY_BACKOFF_MS}；0 表示不等待"
    ),
    panel_form_spec=PanelField(
        widget="number", label="LLM 重试退避基数（毫秒）",
        help_text=f"缺省 {DEFAULT_LLM_RETRY_BACKOFF_MS}；整数且 ≥0",
    ),
    scope="global",
    change_policy=ChangePolicy(requires_confirmation=True),
)

_ENTRIES: dict[str, ConfigEntry] = {
    MAX_STEPS_CONFIG_ID: _MAX_STEPS_ENTRY,
    MAX_LLM_CALLS_CONFIG_ID: _MAX_LLM_CALLS_ENTRY,
    CONTEXT_BUDGET_CONFIG_ID: _CONTEXT_BUDGET_ENTRY,
    LLM_TIMEOUT_CONFIG_ID: _LLM_TIMEOUT_ENTRY,
    LLM_IDLE_TIMEOUT_CONFIG_ID: _LLM_IDLE_TIMEOUT_ENTRY,
    LLM_MAX_ATTEMPTS_CONFIG_ID: _LLM_MAX_ATTEMPTS_ENTRY,
    LLM_RETRY_BACKOFF_CONFIG_ID: _LLM_RETRY_BACKOFF_ENTRY,
}

_DEFAULTS: dict[str, int] = {
    MAX_STEPS_CONFIG_ID: DEFAULT_MAX_STEPS,
    MAX_LLM_CALLS_CONFIG_ID: DEFAULT_MAX_LLM_CALLS,
    CONTEXT_BUDGET_CONFIG_ID: 0,
    LLM_TIMEOUT_CONFIG_ID: DEFAULT_LLM_TIMEOUT_MS,
    LLM_IDLE_TIMEOUT_CONFIG_ID: DEFAULT_LLM_IDLE_TIMEOUT_MS,
    LLM_MAX_ATTEMPTS_CONFIG_ID: DEFAULT_LLM_MAX_ATTEMPTS,
    LLM_RETRY_BACKOFF_CONFIG_ID: DEFAULT_LLM_RETRY_BACKOFF_MS,
}

_MINIMUMS: dict[str, int] = {
    config_id: 0 if config_id in (LLM_RETRY_BACKOFF_CONFIG_ID, CONTEXT_BUDGET_CONFIG_ID) else 1
    for config_id in _DEFAULTS
}


def loop_bounds_config_entries() -> tuple[ConfigEntry, ...]:
    """各条目的**规范形态**（缺省＝运行期缺省；当前取值经 :meth:`LoopBounds.entry` 取）。"""
    return tuple(_ENTRIES.values())


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

    def context_budget_tokens(self) -> int:
        """生效的**上下文封顶**（token；0 即不封顶、按端点窗口自动派生）。

        未落值 → 缺省 0；损坏 / 越界 → 显式抛。
        """
        return self._read(CONTEXT_BUDGET_CONFIG_ID)

    def budget_chars(self) -> int:
        """兼容读面——返 0 表示「未封顶」；**不再**是字符预算。

        新调用方请用 :meth:`context_budget_tokens`；本方法只为既有调用方保留一个
        可迁移的读面，装配路径已改为按端点窗口派生信封。
        """
        return self._read(CONTEXT_BUDGET_CONFIG_ID)

    def llm_timeout_ms(self) -> int:
        """生效的 **LLM 调用总时限**（缺省 300000）。"""
        return self._read(LLM_TIMEOUT_CONFIG_ID)

    def llm_idle_timeout_ms(self) -> int:
        """生效的 **LLM 调用空闲时限**（缺省 90000）。"""
        return self._read(LLM_IDLE_TIMEOUT_CONFIG_ID)

    def llm_max_attempts(self) -> int:
        """生效的**单轮最多尝试次数**（缺省 3）。"""
        return self._read(LLM_MAX_ATTEMPTS_CONFIG_ID)

    def llm_retry_backoff_ms(self) -> int:
        """生效的**重试退避基数**（缺省 500；0 表示不等待）。"""
        return self._read(LLM_RETRY_BACKOFF_CONFIG_ID)

    def entries(self) -> tuple[ConfigEntry, ...]:
        """各条目（`default` 携**当前生效值**，供面板 / 对话通道展示）。"""
        return tuple(  # type: ignore[return-value]
            self.entry(config_id) for config_id in _ENTRIES
        )

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

    def set_context_budget(
        self, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次**工作上下文预算**（值未变 → ``None``）。"""
        return self._set(CONTEXT_BUDGET_CONFIG_ID, value, trace_id=trace_id)

    def set_llm_timeout_ms(
        self, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次 **LLM 调用总时限**（值未变 → ``None``）。"""
        return self._set(LLM_TIMEOUT_CONFIG_ID, value, trace_id=trace_id)

    def set_llm_idle_timeout_ms(
        self, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次 **LLM 调用空闲时限**（值未变 → ``None``）。"""
        return self._set(LLM_IDLE_TIMEOUT_CONFIG_ID, value, trace_id=trace_id)

    def set_llm_max_attempts(
        self, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次**单轮最多尝试次数**（值未变 → ``None``）。"""
        return self._set(LLM_MAX_ATTEMPTS_CONFIG_ID, value, trace_id=trace_id)

    def set_llm_retry_backoff_ms(
        self, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次**重试退避基数**（值未变 → ``None``）。"""
        return self._set(LLM_RETRY_BACKOFF_CONFIG_ID, value, trace_id=trace_id)

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
        return _check(value, config_id=config_id, minimum=_MINIMUMS[config_id])

    def _set(self, config_id: str, value: object, *, trace_id: str | None) -> ChangeRecord | None:
        value = _check(value, config_id=config_id, minimum=_MINIMUMS[config_id])
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
