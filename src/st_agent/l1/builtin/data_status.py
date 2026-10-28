"""``data_status`` 内置原语（T-AGT-010.2；[02 §5](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

让模型先判断「有哪些表、多少行、多旧、缺哪个标的」，再决定去同步还是如实告知。
执行器只经注入的取数面调用 ``status_snapshot(dataset=…, max_codes=…)``——快照由
L0 **一次只读连接**取齐（``sync_state`` + 逐表行数 + 数据域截止 + 覆盖账本），
本原语不自己拼多轮查询。

两条状态语义（GWT-2 / GWT-3）：

- **已知空集 vs 数据缺失**——表存在、0 行且所属任务已成功同步才叫 ``known_empty``；
  表缺 / 任务未同步 / 未成功一律 ``missing``（L0 ``status_snapshot`` 判定）。
- **缺口标的**——覆盖账本中**无任何 ``ok`` 行**的码；不在 L1 假设目标代码集
  （关注池 / 持仓不可见），故不把「未同步的码」误报成全市场缺口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["DATA_STATUS", "DEFAULT_MAX_CODES"]

DEFAULT_MAX_CODES = 50

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "snapshot_id": {"type": "string"},
        "as_of": {"type": "object"},
        "tasks": {"type": "array"},
        "tables": {"type": "array"},
        "freshness": {"type": "array"},
        "coverage": {"type": "array"},
        "gap_codes": {"type": "array"},
        "gap_count": {"type": "integer"},
        "last_updated_at": {"type": "string"},
        "notes": {"type": "array"},
    },
}


def _executor_factory(context: Any) -> Any:
    source = context["market"]

    def execute(_ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
        dataset = str(params.get("dataset") or "").strip() or None
        snapshot = source.status_snapshot(
            dataset=dataset,
            max_codes=int(params.get("max_codes") or DEFAULT_MAX_CODES),
        )
        if not snapshot.get("db_exists"):
            now = datetime.now().astimezone()
            return ResultEnvelope.unavailable(
                "本地市场数据库尚未建立（首次同步未完成）",
                last_updated_at=now, as_of=now,
            )
        payload = {key: value for key, value in snapshot.items() if key != "db_exists"}
        as_of = _as_of_of(snapshot.get("last_updated_at"))
        if as_of is None:
            payload.pop("last_updated_at", None)
        return ResultEnvelope.ok(payload, as_of=as_of)

    return execute


def _as_of_of(value: Any) -> datetime | None:
    """``sync_state`` 的时间戳 → 带时区 ``datetime``（缺省 / 非法 → ``None``）。"""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.astimezone()


DATA_STATUS = BuiltinPrimitive(
    base="sk_data_status",
    name="数据现状",
    description=(
        "报告本地数据的逐任务 / 逐表行数、最后成功时间、数据域截止时间与覆盖缺口，"
        "并区分已知空集与数据缺失；用于判断是否需要同步或如实告知数据现状"
    ),
    input_schema={"type": "object", "properties": {}},
    output_schema=_OUTPUT_SCHEMA,
    parameters=(
        ParameterSpec(
            name="dataset", type="string", default="",
            description="只看某个数据集的覆盖账本（留空＝全部）",
        ),
        ParameterSpec(
            name="max_codes", type="integer", default=DEFAULT_MAX_CODES,
            min_value=1, max_value=500, description="缺口标的返回上限",
        ),
    ),
    permissions=(),
    offline_level="full",
    requires=("market",),
    executor=_executor_factory,
)
