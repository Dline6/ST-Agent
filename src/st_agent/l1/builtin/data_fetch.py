"""``data_fetch`` 内置原语（T-AGT-013.2；[02 §5](../../../docs/技术架构-v2/02-L0-本地优先基座.md)、
[D-145](../../../项目管理/决策日志.md)）。

按**数据集 id** 同步取一批：经既有 adapter（字段映射、防封、落库、水位全在里面）
取数 → 落缓存 → 回写 ``sync_state`` → 以最后成功时间作 ``as_of``。原语**不直连**
网络或文件；每一次抓取仍是一条 [02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)
出网审计。

四条口径：

- **未登记域显式拒**——`unavailable` 并点名该 id，不猜相近域、不回落其他数据集。
- **单飞**——与后台批次共用同一把锁；运行中批次存在即拒（整库工作副本不可并发）。
- **写类动作**——落缓存、出网 ⇒ 出厂清单判 ``collaborative-required``，循环需交还确认。
- **端口后置绑定**——``DataSyncRunner`` 未绑定即 ``unavailable`` + 点名未接线。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.sync_runner import SyncBusyError, SyncJobError
from st_agent.l1.builtin.data_jobs import (
    DATA_INPUT_SCHEMA,
    DATA_PARAMETERS,
    DataParamError,
    items_from_params,
)
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["DATA_FETCH"]

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset": {"type": "string"},
        "job_id": {"type": "string"},
        "state": {"type": "string"},
        "results": {"type": "object"},
        "order": {"type": "array"},
        "auto_added": {"type": "array"},
        "rows": {"type": "integer"},
        "watermark": {"type": "string"},
        "as_of": {"type": "string"},
    },
}


def _executor_factory(context: Any) -> Any:
    port = context["sync"]

    def execute(ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
        runner = port.get()
        if runner is None:
            return _not_wired()
        dataset = str(params.get("dataset") or "").strip()
        try:
            items, custom_text = items_from_params(params, ctx.inputs)
            snapshot = runner.fetch_items(items, custom_text=custom_text)
        except DataParamError as exc:
            return _unavailable(str(exc))
        except SyncBusyError as exc:
            return _unavailable(f"{exc}（不并发：同一时刻只允许一个同步批次，02 §5）")
        except SyncJobError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        payload = _payload(dataset, snapshot)
        as_of = _as_of_of(snapshot.get("as_of"))
        problems = _problems(snapshot)
        if problems:
            return _unavailable(
                f"取数未全部成功：{'；'.join(problems)}", as_of=as_of
            )
        return ResultEnvelope.ok(payload, as_of=as_of)

    return execute


def _payload(dataset: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    results = snapshot.get("results") or {}
    rows = sum(
        int(item.get("rows") or 0) for item in results.values()
        if isinstance(item, dict)
    )
    watermark = next(
        (
            str(item.get("watermark"))
            for item in results.values()
            if isinstance(item, dict) and item.get("watermark")
        ),
        "",
    )
    return {
        "dataset": dataset,
        "job_id": snapshot.get("job_id") or "",
        "state": snapshot.get("state") or "",
        "results": results,
        "order": list(snapshot.get("order") or ()),
        "auto_added": list(snapshot.get("auto_added") or ()),
        "rows": rows,
        "watermark": watermark,
        "as_of": snapshot.get("as_of") or "",
    }


def _problems(snapshot: dict[str, Any]) -> list[str]:
    """逐任务非 ``ok`` 的结果 → 显式原因（不把失败当成功、不含糊）。"""
    out: list[str] = []
    if snapshot.get("state") != "done":
        out.append(f"批次状态 {snapshot.get('state')!r}")
    if snapshot.get("error"):
        out.append(str(snapshot["error"]))
    for key, item in (snapshot.get("results") or {}).items():
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        # `empty` 是合法的「已知空集」（01 §5），不是失败——照实保留在逐任务结果里。
        if status not in ("ok", "empty"):
            out.append(
                f"{key}：{status}"
                + (f"（{item.get('reason')}）" if item.get("reason") else "")
            )
    return out


def _not_wired() -> ResultEnvelope:
    return _unavailable(
        "取数执行面尚未接线（T-AGT-013.2：组合根未把 DataSyncRunner 绑进 L1）"
        "——本次未取任何数据"
    )


def _unavailable(reason: str, *, as_of: datetime | None = None) -> ResultEnvelope:
    return ResultEnvelope.unavailable(
        reason, last_updated_at=as_of or datetime.now().astimezone(), as_of=as_of
    )


def _as_of_of(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.astimezone()


DATA_FETCH = BuiltinPrimitive(
    base="sk_data_fetch",
    name="按域取数",
    description=(
        "按数据集 id 经既有数据源适配器同步取一批并落本地缓存，返回逐任务结果、行数与"
        "数据截止时间；未登记的数据集会显式拒绝并点名"
    ),
    input_schema=DATA_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=DATA_PARAMETERS,
    permissions=(),
    offline_level="full",
    requires=("sync",),
    executor=_executor_factory,
)
