"""信封的 JSON 形态与渲染语义（[01 §5]；[05 §4]）。

**前端不复制渲染语义表**：六态 → 呈现形态的唯一真相源是
:data:`st_agent.l3.dispatch.bus.RENDER_SEMANTICS`，本模块每出一条信封就把它随载荷下发，
前端只按 ``render.presentation`` / ``render.must_show`` 选 chrome。两条好处：改语义表
不必改 JS；两处不可能漂移。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.dispatch.bus import render_semantics

__all__ = ["envelope_payload"]


def _jsonable(value: Any) -> Any:
    """把信封里的值转成可 JSON 序列化的形态（pydantic 模型 / ``datetime`` / 容器）。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def envelope_payload(envelope: ResultEnvelope, *, llm_degraded: bool = False) -> dict[str, Any]:
    """把一个信封转成可 JSON 序列化的载荷，并附**服务端算出的**渲染语义。"""
    semantics = render_semantics(envelope, llm_degraded=llm_degraded)
    return {
        "status": envelope.status,
        "data": _jsonable(envelope.data),
        "reason": envelope.reason,
        "evidence_refs": [ref.model_dump(mode="json") for ref in envelope.evidence_refs],
        "as_of": _jsonable(envelope.as_of),
        "last_updated_at": _jsonable(envelope.last_updated_at),
        "log_ref": envelope.log_ref,
        "render": {
            "presentation": semantics.presentation,
            "must_show": list(semantics.must_show),
            "notice": semantics.notice,
        },
    }
