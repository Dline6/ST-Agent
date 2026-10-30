"""``sk_result_read`` 内置原语（T-AGT-017.2；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md) / [01 §5](../../../docs/技术架构-v2/01-平台共享契约.md)）。

超阈值的单条结果在循环工作上下文里只留截断件 + 指针；本原语补上**回读通路**：
按 ``skill_run_id`` 取回 ``SkillRunner`` 已落盘的记录，把载荷按 ``offset`` / ``max_chars``
分片交回模型。四条口径：

- **唯一真相源**——完整载荷在 ``execution_log/skill-run/<skill_run_id>.json``（[03 §1]），
  **不复制字节、不新增分区**（[D-090] ⑧）；记录被留存策略清理时显式 ``unavailable``。
- **分片与预览同源**——回读的是与截断预览**同一确定性序列化**（``ensure_ascii=False,
  sort_keys=True``），故分片可拼接、可与预览对齐（两处实现由测试钉住）。
- **越界拦截**——``offset`` 为负或大于总长 → ``validation_failed``；``max_chars`` 上限
  8000，防止一次回读又把上下文撑爆。
- **只接受契约形态的 id**——``skill_run_id`` 经 [01 §1] 的 ``SkillRunId`` 校验，**不拼任意
  路径**（拒绝 ``../`` 一类越界读）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from st_agent.contracts.identifiers import SkillRunId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.builtin.models import BuiltinPrimitive
from st_agent.l1.runner.models import RUN_PREFIX, SkillRun

__all__ = [
    "DEFAULT_RESULT_READ_CHARS",
    "MAX_RESULT_READ_CHARS",
    "RESULT_READ",
    "ResultAccess",
    "ResultAccessError",
]

DEFAULT_RESULT_READ_CHARS = 2000
"""单次回读的缺省分片长度（字符数）——够读一段证据，不一次塞满窗口。"""

MAX_RESULT_READ_CHARS = 8000
"""单次回读的分片上限（字符数；超过即拒，不静默截断）。"""


class ResultAccessError(Exception):
    """回读失败（``validation`` = id 形态非法；其余 = 记录取不到 / 形态坏）。"""

    def __init__(self, message: str, *, validation: bool = False) -> None:
        super().__init__(message)
        self.validation = validation


class ResultAccess:
    """执行记录回读端口（只读 ``execution_log`` 的 ``skill-run/`` 记录）。"""

    def __init__(self, store: Any) -> None:
        self._store = store

    def load(self, skill_run_id: str) -> SkillRun:
        """按契约形态校验 id 并取回执行记录（非法 / 缺失 / 形态坏分别显式失败）。"""
        try:
            SkillRunId.of(skill_run_id)
        except Exception as exc:  # noqa: BLE001 —— 契约校验失败统一归为 id 形态非法
            raise ResultAccessError(
                f"skill_run_id {skill_run_id!r} 形态非法（01 §1）：{exc}", validation=True
            ) from exc
        path = f"{RUN_PREFIX}{skill_run_id}.json"
        try:
            raw = self._store.get("execution_log", path)
        except Exception as exc:  # noqa: BLE001 —— 取不到即显式不可用，不返回空壳
            raise ResultAccessError(
                f"执行记录 {path} 取不到（可能已被留存策略清理）：{exc}"
            ) from exc
        try:
            return SkillRun.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 —— 形态坏同样是显式不可用
            raise ResultAccessError(f"执行记录 {path} 形态坏：{exc}") from exc


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill_run_id": {
            "type": "string",
            "description": "要回读的执行标识（工作上下文截断件里的 skill_run_id）",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "起点（字符偏移；取自截断件的 next_offset）",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_RESULT_READ_CHARS,
            "default": DEFAULT_RESULT_READ_CHARS,
            "description": f"本次回读的最大字符数（缺省 {DEFAULT_RESULT_READ_CHARS}，上限 {MAX_RESULT_READ_CHARS}）",
        },
    },
    "required": ["skill_run_id"],
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill_run_id": {"type": "string"},
        "skill_id": {"type": "string"},
        "status": {"type": "string"},
        "total_chars": {"type": "integer"},
        "offset": {"type": "integer"},
        "next_offset": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "text": {"type": "string"},
    },
}


def _executor_factory(context: Any) -> Any:
    """把注入的 :class:`ResultAccess` 收进执行器（循环经 L1 流水线调用）。"""
    results: ResultAccess = context["results"]

    def execute(ctx: Any, _params: dict[str, Any]) -> ResultEnvelope:
        inputs = dict(getattr(ctx, "inputs", {}) or {})
        raw_id = inputs.get("skill_run_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return ResultEnvelope.validation_failed(
                "sk_result_read 缺少 skill_run_id（要回读哪一次执行？）"
            )
        offset, error = _normalize_offset(inputs.get("offset"))
        if error:
            return ResultEnvelope.validation_failed(error)
        max_chars, error = _normalize_max_chars(inputs.get("max_chars"))
        if error:
            return ResultEnvelope.validation_failed(error)
        try:
            run = results.load(raw_id.strip())
        except ResultAccessError as exc:
            if exc.validation:
                return ResultEnvelope.validation_failed(str(exc))
            return ResultEnvelope.unavailable(str(exc), last_updated_at=_now())
        if run.envelope.status != "ok":
            return ResultEnvelope.empty(
                f"该次执行的结论是 {run.envelope.status}，没有可回读的载荷（01 §5：载荷只属于 ok）"
            )
        payload = _payload_text(run.envelope.data)
        total = len(payload)
        if offset > total:
            return ResultEnvelope.validation_failed(
                f"offset {offset} 越过结果末尾（总长 {total} 字符；越界回读被拒绝）"
            )
        chunk = payload[offset:offset + max_chars]
        next_offset = offset + len(chunk)
        return ResultEnvelope.ok({
            "skill_run_id": run.skill_run_id,
            "skill_id": run.skill_id,
            "status": run.envelope.status,
            "total_chars": total,
            "offset": offset,
            "next_offset": next_offset,
            "truncated": next_offset < total,
            "text": chunk,
        })

    return execute


def _normalize_offset(raw: Any) -> tuple[int, str]:
    """``offset`` 归一化（缺省 0；负值 / 非整数即拒）。"""
    if raw is None:
        return 0, ""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0, f"offset 须为整数，得到 {type(raw).__name__}"
    if raw < 0:
        return 0, f"offset {raw} 为负（不得小于 0）"
    return raw, ""


def _normalize_max_chars(raw: Any) -> tuple[int, str]:
    """``max_chars`` 归一化（缺省 2000；越界 / 非整数即拒）。"""
    if raw is None:
        return DEFAULT_RESULT_READ_CHARS, ""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0, f"max_chars 须为整数，得到 {type(raw).__name__}"
    if raw < 1 or raw > MAX_RESULT_READ_CHARS:
        return 0, (
            f"max_chars {raw} 越界（取值域 1–{MAX_RESULT_READ_CHARS}；"
            "要更多内容请用 next_offset 续读）"
        )
    return raw, ""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8] 口径；``unavailable`` 须标注最后更新时间）。"""
    return datetime.now().astimezone()


def _payload_text(data: Any) -> str:
    """载荷的确定性 JSON 文本（与 ``l3/runtime/context.py`` 的 ``_json`` 同参）。

    两处分居 L1 / L3（[铁律 7]：L1 不得 import L3），故各留一份实现、由测试钉住
    「截断预览是回读文本的前缀」——口径漂移会立刻红。
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=repr)


RESULT_READ = BuiltinPrimitive(
    base="sk_result_read",
    name="结果回读",
    description=(
        "按 skill_run_id 回读一次已外置的大结果，用 offset / max_chars 分片；"
        "越界会被拒绝，续读请用返回的 next_offset"
    ),
    input_schema=_INPUT_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    parameters=(),
    permissions=(),
    offline_level="full",
    requires=("results",),
    executor=_executor_factory,
)
