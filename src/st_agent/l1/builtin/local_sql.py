"""``local_sql`` 内置原语（T-AGT-010.1；[02 §5](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

把 L0 既有的只读查询面接进工具面：模型给一条 ``SELECT`` / ``WITH`` / ``PRAGMA`` /
``EXPLAIN`` 单语句，执行器经**注入的取数面**（``market_query``）调用
``query(sql, params, max_rows=…, timeout_ms=…)``，不直连文件或网络。

四条口径：

- **只读白名单由 L0 判定**——写语句与多语句返回 ``validation_failed`` 且不执行；
  两处不各写一份判据。
- **上限显式**——行数与执行时限由参数给出（缺省 200 行 / 5000 ms）；超限时 ok 载荷
  带 ``truncated`` / ``limit`` / ``hint``，超时走 ``failed`` 并给收窄建议，不静默截断。
- **可解释**——``sql`` 是**标量参数**，原文进入 ``TraceStep.input_digest`` 与
  ``SkillRun.params``；绑定参数走对象输入 ``params``，同在 ``SkillRun.inputs``；
  ``as_of`` / 行数在 ok 载荷与其摘要里。``ref=skill_run_id`` 可回溯全文。
- **权限为空**——只经注入的取数面消费本地缓存，不触文件 / 网络 / 命令出口
  （[01 §10](../../../docs/技术架构-v2/01-平台共享契约.md) / [D-031](../../../项目管理/决策日志.md)）。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["DEFAULT_MAX_ROWS", "DEFAULT_TIMEOUT_MS", "LOCAL_SQL"]

DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_MS = 5_000

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "columns": {"type": "array"},
        "rows": {"type": "array"},
        "row_count": {"type": "integer"},
        "sql": {"type": "string"},
        "params": {"type": "array"},
        "as_of": {"type": "string"},
        "truncated": {"type": "boolean"},
        "limit": {"type": "integer"},
        "hint": {"type": "string"},
    },
}


def _executor_factory(context: Any) -> Any:
    source = context["market"]

    def execute(ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
        sql = str(params.get("sql") or "").strip()
        if not sql:
            return ResultEnvelope.validation_failed(
                "local_sql 需要一条非空的只读 SQL（SELECT / WITH / PRAGMA / EXPLAIN 单语句）"
            )
        bind = tuple(ctx.inputs.get("params") or ())
        envelope = source.query(
            sql,
            bind,
            max_rows=int(params.get("max_rows") or DEFAULT_MAX_ROWS),
            timeout_ms=int(params.get("timeout_ms") or DEFAULT_TIMEOUT_MS),
        )
        if envelope.status != "ok":
            return envelope
        payload = dict(envelope.data or {})
        payload["sql"] = sql
        payload["params"] = list(bind)
        payload.setdefault("row_count", len(payload.get("rows") or ()))
        if envelope.as_of is not None:
            payload["as_of"] = envelope.as_of.isoformat()
        return ResultEnvelope.ok(
            payload, as_of=envelope.as_of, evidence_refs=envelope.evidence_refs
        )

    return execute


LOCAL_SQL = BuiltinPrimitive(
    base="sk_local_sql",
    name="本地只读查询",
    description=(
        "对本地市场库执行一条只读 SQL（SELECT / WITH / PRAGMA / EXPLAIN 单语句），"
        "返回列与行；结果超出行数或执行时限时显式标注截断并给出继续查询的方式"
    ),
    input_schema={"type": "object", "properties": {"params": {"type": "array"}}},
    output_schema=_OUTPUT_SCHEMA,
    parameters=(
        ParameterSpec(
            name="sql", type="string", default="",
            description="只读 SQL 原文（单语句；SELECT / WITH / PRAGMA / EXPLAIN）",
        ),
        ParameterSpec(
            name="max_rows", type="integer", default=DEFAULT_MAX_ROWS,
            min_value=1, max_value=2_000, description="单次返回的最大行数",
        ),
        ParameterSpec(
            name="timeout_ms", type="integer", default=DEFAULT_TIMEOUT_MS,
            min_value=100, max_value=30_000, description="执行时限（毫秒）",
        ),
    ),
    permissions=(),
    offline_level="full",
    requires=("market",),
    executor=_executor_factory,
)
