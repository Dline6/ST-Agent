"""``data_sync`` 内置原语（T-AGT-013.1；[02 §5](../../../docs/技术架构-v2/02-L0-本地优先基座.md)、
[D-145](../../../项目管理/决策日志.md)）。

按**数据集 id** 发起一次后台同步批次，并如实回报「同步中 / 已完成 / 失败」：

- **单飞**——已有批次运行时显式拒绝（:class:`SyncBusyError`），不排队、不并发：整库
  工作副本的取-改-写不可并发覆盖（02 §5）。
- **等待策略可指定**——缺省不等待，返回 ``running`` + ``job_id``；``wait_seconds`` > 0
  时最多等该秒数，超时仍如实回运行态（**不伪装完成**）。
- **写类动作**——它写缓存、出网，出厂清单判 ``collaborative-required``，循环碰到它即
  交还用户确认（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。
- **端口后置绑定**——``DataSyncRunner`` 由 M5 组合根造出，经 ``L1Runtime.bind_data_sync``
  注入；未绑定时显式 ``unavailable`` 并点名未接线，不臆造运行态。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.sync_runner import SyncBusyError, SyncJobError
from st_agent.l1.builtin.data_jobs import (
    DATA_INPUT_SCHEMA,
    DATA_PARAMETERS,
    DataParamError,
    items_from_params,
)
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["DATA_SYNC", "DEFAULT_WAIT_SECONDS", "MAX_WAIT_SECONDS"]

DEFAULT_WAIT_SECONDS = 0
MAX_WAIT_SECONDS = 300
_POLL_SECONDS = 0.1

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string"},
        "state": {"type": "string"},
        "order": {"type": "array"},
        "auto_added": {"type": "array"},
        "estimate": {"type": "object"},
        "results": {"type": "object"},
        "current": {"type": "string"},
        "started_at": {"type": "string"},
        "finished_at": {"type": "string"},
        "error": {"type": "string"},
        "as_of": {"type": "string"},
    },
}


def _executor_factory(context: Any) -> Any:
    port = context["sync"]

    def execute(_ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
        runner = port.get()
        if runner is None:
            return _not_wired()
        try:
            items, custom_text = items_from_params(params, _ctx.inputs)
            started = runner.start_items(items, custom_text=custom_text)
        except DataParamError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        except SyncBusyError as exc:
            return _busy(str(exc))
        except SyncJobError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        wait = int(params.get("wait_seconds") or DEFAULT_WAIT_SECONDS)
        wait = max(0, min(wait, MAX_WAIT_SECONDS))
        if wait:
            _wait_for(runner, wait)
        job = runner.job() or {}
        payload = _payload(job, fallback=started)
        as_of = _as_of_of(job.get("as_of"))
        return ResultEnvelope.ok(payload, as_of=as_of)

    return execute


def _wait_for(runner: Any, seconds: int) -> None:
    """等到批次结束或超时（超时不是失败——运行态如实返回）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = runner.job()
        if job is None or job.get("state") != "running":
            return
        time.sleep(_POLL_SECONDS)


def _payload(job: dict[str, Any], *, fallback: dict[str, Any]) -> dict[str, Any]:
    source = job or fallback
    return {
        "job_id": source.get("job_id"),
        "state": source.get("state"),
        "order": list(source.get("order") or ()),
        "auto_added": list(source.get("auto_added") or ()),
        "estimate": source.get("estimate") or {},
        "results": source.get("results") or {},
        "current": source.get("current") or "",
        "started_at": source.get("started_at") or "",
        "finished_at": source.get("finished_at") or "",
        "error": source.get("error") or "",
        "as_of": source.get("as_of") or "",
    }


def _not_wired() -> ResultEnvelope:
    now = datetime.now().astimezone()
    return ResultEnvelope.unavailable(
        "同步执行面尚未接线（T-AGT-013.1：组合根未把 DataSyncRunner 绑进 L1）"
        "——本次未发起任何同步",
        last_updated_at=now, as_of=now,
    )


def _busy(reason: str) -> ResultEnvelope:
    now = datetime.now().astimezone()
    return ResultEnvelope.unavailable(
        f"{reason}（不并发：同一时刻只允许一个同步批次，02 §5）",
        last_updated_at=now, as_of=now,
    )


def _as_of_of(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.astimezone()


DATA_SYNC = BuiltinPrimitive(
    base="sk_data_sync",
    name="触发数据同步",
    description=(
        "按数据集 id 发起一次本地数据同步批次，返回批次号与运行态（同步中 / 已完成 / 失败）；"
        "已有批次运行时拒绝并发；可指定等待秒数，超时仍如实返回运行态"
    ),
    input_schema=DATA_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=DATA_PARAMETERS + (
        ParameterSpec(
            name="wait_seconds", type="integer", default=DEFAULT_WAIT_SECONDS,
            min_value=0, max_value=MAX_WAIT_SECONDS,
            description="最多等待的秒数（0 = 只发起不等待，返回 job_id 与运行态）",
        ),
    ),
    permissions=(),
    offline_level="full",
    requires=("sync",),
    executor=_executor_factory,
)
