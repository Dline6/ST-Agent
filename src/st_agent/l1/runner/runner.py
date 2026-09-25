"""Skill 执行流水线（T-L1-001.2；03 §1.2）。

标准流水线（一次 ``skill_run_id``）：解析 → 参数确认 → 依赖 DAG 解析 →
权限检查 → 执行与留痕。

- 解析：``match`` 按查询文本匹配 SkillDescriptor（GWT-2 调度匹配口径）
- 参数确认：``confirm_card`` 返回参数确认卡数据；``run`` 内经
  ``registry.validate_call_params`` 合并默认值（GWT-2/GWT-3/GWT-7）
- 依赖解析：沿描述体 ``dependencies`` 递归执行；成环即拒；缺失即拒；
  上游非 ok/empty → 下游 ``dependency_failed``（GWT-4）
- 权限检查：描述体声明的权限须逐项在已批准集合内，否则
  ``validation_failed`` 拦截（01 §10，A6）
- 执行与留痕：执行器为同步 callable（A4）；输出统一 ``ResultEnvelope``；
  ``SkillRun`` 落 ``execution_log`` 分区并追加 ``skill_run`` TraceStep
  （GWT-6）

布局（只经 ``Store`` 读写，不直连文件系统）：
- SkillRun 记录 → ``execution_log`` 分区 ``skill-run/<skill_run_id>.json``
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import SkillRunId, TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace, TraceStep, digest_of
from st_agent.l1.runner.errors import (
    CycleDetectedError,
    RunnerValidationError,
)
from st_agent.l1.runner.models import RUN_PREFIX, checked_skill_run
from st_agent.l1.skills.errors import (
    SkillNotFoundError,
    SkillValidationError,
)
from st_agent.l1.skills.ids import check_skill_id

__all__ = [
    "RUN_PREFIX",
    "RunOutcome",
    "SkillContext",
    "SkillExecutor",
    "SkillRunner",
]

#: 执行器签名：(完整参数, 上下文) -> 结果信封（A4）
SkillExecutor = Callable[["SkillContext", dict[str, Any]], ResultEnvelope]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class SkillContext(BaseModel):
    """执行器上下文（A4：LLM/网关经此注入，执行器不直连外部）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    skill_id: str
    skill_run_id: str
    trace_id: str
    initiator: str = ""
    purpose: str = ""
    upstream: dict[str, ResultEnvelope] = {}
    """已执行的上游依赖输出（``{skill_id: envelope}``，拓扑序）。"""
    llm: Any = None
    """``LlmClient`` 句柄（无则为 None，执行器须显式处理）。"""
    gateway: Any = None
    """``EgressGateway`` 句柄（无则为 None，执行器须显式处理）。"""


class RunOutcome(BaseModel):
    """一次 ``run`` 调用的完整产出（信封 + 推理链 + 留痕定位）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    skill_id: str
    skill_run_id: str
    envelope: ResultEnvelope
    trace: Trace


class SkillRunner:
    """Skill 执行流水线门面（03 §1.2；持久化只经 ``Store``）。"""

    def __init__(self, store, registry, *, llm=None, gateway=None) -> None:
        self._store = store
        self._registry = registry
        self._llm = llm
        self._gateway = gateway
        self._executors: dict[str, SkillExecutor] = {}

    # ───────────────────────── 执行器注册（A4） ─────────────────────────

    def register_executor(self, skill_id: str, fn: SkillExecutor) -> None:
        """注册一个 Skill 的同步执行器（同 skill_id 重复注册即覆盖）。"""
        check_skill_id(skill_id)
        if not callable(fn):
            raise RunnerValidationError(f"执行器须为 callable：{skill_id!r}")
        self._executors[skill_id] = fn

    # ───────────────────────── 解析与参数确认（GWT-2） ───────────────────

    def match(self, query: str) -> Any | None:
        """按查询文本匹配最相关的 SkillDescriptor（无候选 → None）。

        口径：查询与「名称 + 描述」按字符 bigram 重叠度打分，
        取最高者；并列时按 skill_id 字典序取首个（确定性）。
        """
        query = (query or "").strip()
        if not query:
            return None
        grams = _bigrams(query)
        if not grams:
            return None
        best: Any | None = None
        best_key: tuple[int, str] = (-1, "")
        for desc in self._registry.list_all():
            overlap = len(grams & _bigrams(f"{desc.name} {desc.description}"))
            key = (overlap, desc.skill_id)
            if overlap > 0 and (best is None or key > best_key):
                best, best_key = desc, key
        return best

    def confirm_card(self, skill_id: str, values: Mapping[str, Any] | None = None) -> dict:
        """返回参数确认卡数据（GWT-2：参数名/默认值/可调范围）。

        ``values`` 为用户在卡上调整的值（可为空即全默认值）；
        超范围 → ``SkillValidationError``（调用方包 ``validation_failed``）。
        """
        descriptor = self._registry.get(skill_id)  # 不存在 → SkillNotFoundError
        merged = self._registry.validate_call_params(skill_id, dict(values or {}))
        return {
            "skill_id": skill_id,
            "parameters": [
                {
                    "name": spec.name,
                    "type": spec.type,
                    "default": merged[spec.name],
                    "min_value": spec.min_value,
                    "max_value": spec.max_value,
                    "choices": list(spec.choices),
                    "description": spec.description,
                }
                for spec in descriptor.parameters
            ],
        }

    # ───────────────────────── 执行入口（GWT-3/4/6） ─────────────────────

    def run(
        self,
        skill_id: str,
        values: Mapping[str, Any] | None = None,
        *,
        trace: Trace | None = None,
        approved_permissions: tuple[str, ...] | list[str] = (),
        initiator: str = "",
        purpose: str = "",
    ) -> RunOutcome:
        """执行一个 Skill（完整流水线；运行时失败一律走信封，不抛异常）。

        :param trace: 调用方传入的推理链（None 即新建一条）；
            返回的 ``RunOutcome.trace`` 为追加本次步骤后的新链
        :param approved_permissions: 用户已批准的权限声明集合（A6 逐项核对）
        """
        try:
            check_skill_id(skill_id)
        except SkillValidationError as exc:
            raise RunnerValidationError(str(exc)) from exc
        chain = trace if trace is not None else Trace(trace_id=TraceId.generate())
        approved = frozenset(approved_permissions)
        envelope, chain, run_id = self._execute(
            skill_id, dict(values or {}), chain, approved,
            initiator=initiator, purpose=purpose, stack=(),
        )
        return RunOutcome(
            skill_id=skill_id, skill_run_id=run_id,
            envelope=envelope, trace=chain,
        )

    # ───────────────────────── 内部：递归执行 ─────────────────────────

    def _execute(
        self, skill_id: str, values: dict[str, Any], chain: Trace,
        approved: frozenset[str], *, initiator: str, purpose: str,
        stack: tuple[str, ...],
    ) -> tuple[ResultEnvelope, Trace, str]:
        """递归执行单个 Skill → (信封, 新链, skill_run_id)。"""
        if skill_id in stack:
            raise CycleDetectedError(
                f"依赖成环：{' → '.join((*stack, skill_id))}（03 §1.2 DAG 约束）"
            )
        run_id = SkillRunId.generate().value
        log_ref = f"{RUN_PREFIX}{run_id}.json"
        started = time.monotonic()

        def finish(envelope: ResultEnvelope, upstream: tuple[str, ...],
                   params: dict[str, Any]) -> tuple[ResultEnvelope, Trace, str]:
            duration = int((time.monotonic() - started) * 1000)
            record = checked_skill_run(
                skill_run_id=run_id, skill_id=skill_id,
                trace_id=chain.trace_id.value, params=params,
                upstream=upstream, envelope=envelope,
                duration_ms=duration, started_at=_now(),
            )
            self._store.put(
                "execution_log", log_ref, record.model_dump_json().encode("utf-8"))
            step = TraceStep(
                step_type="skill_run", ref=run_id,
                input_digest=digest_of({"skill_id": skill_id, "params": params}),
                output_digest=digest_of(envelope.model_dump(mode="json")),
                duration_ms=duration, timestamp=_now(),
            )
            return envelope, chain.append_step(step), run_id

        # ── 描述体（缺失 → validation_failed） ──
        try:
            descriptor = self._registry.get(skill_id)
        except SkillNotFoundError as exc:
            return finish(
                ResultEnvelope.validation_failed(f"Skill {skill_id!r} 未注册：{exc}"),
                (), dict(values))

        # ── 参数确认（超范围 → validation_failed，不执行） ──
        try:
            params = self._registry.validate_call_params(skill_id, values)
        except SkillValidationError as exc:
            return finish(
                ResultEnvelope.validation_failed(f"参数校验失败：{exc}"), (), dict(values))

        # ── 依赖解析（递归；上游失败 → dependency_failed） ──
        upstream: dict[str, ResultEnvelope] = {}
        ordered: list[str] = []
        for dep in descriptor.dependencies:
            try:
                check_skill_id(dep)
            except SkillValidationError as exc:
                return finish(
                    ResultEnvelope.validation_failed(
                        f"依赖声明非法 {dep!r}：{exc}"), tuple(ordered), params)
            try:
                env, chain, _ = self._execute(
                    dep, {}, chain, approved,
                    initiator=initiator, purpose=purpose,
                    stack=(*stack, skill_id))
            except CycleDetectedError as exc:
                return finish(
                    ResultEnvelope.validation_failed(str(exc)),
                    tuple(ordered), params)
            upstream[dep] = env
            ordered.append(dep)
            if env.status not in ("ok", "empty"):
                return finish(
                    ResultEnvelope.dependency_failed(
                        f"依赖失败：{dep!r} 执行{env.status}，下游 {skill_id!r} "
                        f"拒绝用错误数据继续（原因：{env.reason}）",
                        log_ref=log_ref),
                    tuple(ordered), params)

        # ── 权限检查（未批准 → validation_failed，不执行） ──
        missing = [p for p in descriptor.permissions if p not in approved]
        if missing:
            return finish(
                ResultEnvelope.validation_failed(
                    f"Skill {skill_id!r} 缺少已批准权限：{missing}（01 §10）"),
                tuple(ordered), params)

        # ── 执行（无执行器/抛裸异常/非法返回 → failed，带 log_ref） ──
        fn = self._executors.get(skill_id)
        if fn is None:
            return finish(
                ResultEnvelope.failed(
                    f"Skill {skill_id!r} 未注册执行器，无法执行", log_ref=log_ref),
                tuple(ordered), params)
        ctx = SkillContext(
            skill_id=skill_id, skill_run_id=run_id,
            trace_id=chain.trace_id.value,
            initiator=initiator, purpose=purpose,
            upstream=dict(upstream), llm=self._llm, gateway=self._gateway)
        try:
            envelope = fn(ctx, dict(params))
        except Exception as exc:  # 执行器抛裸异常 → 显式 failed（禁止静默失败）
            return finish(
                ResultEnvelope.failed(
                    f"Skill {skill_id!r} 执行异常：{exc}", log_ref=log_ref),
                tuple(ordered), params)
        if not isinstance(envelope, ResultEnvelope):
            return finish(
                ResultEnvelope.failed(
                    f"Skill {skill_id!r} 执行器返回非法类型 "
                    f"{type(envelope).__name__}（须为 ResultEnvelope）",
                    log_ref=log_ref),
                tuple(ordered), params)
        return finish(envelope, tuple(ordered), params)


def _bigrams(text: str) -> set[str]:
    """字符 bigram 集合（``match`` 重叠度口径；空白压缩）。"""
    squashed = "".join(text.split())
    return {squashed[i:i + 2] for i in range(len(squashed) - 1)}
